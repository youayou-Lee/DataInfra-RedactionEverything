"""
匿名化执行服务（薄编排层）
实际逻辑委托给 redaction 子包的三个专注模块：
  - replacement_strategy: 替换策略与实体映射
  - text_redactor: DOCX / PDF / TXT 文本替换
  - image_redactor: 图片区域匿名化
"""
import asyncio
import logging
import os
import re
import uuid
from typing import Any

import fitz

from app.core.config import settings
from app.models.schemas import (
    BoundingBox,
    Entity,
    FileType,
    RedactionConfig,
    ReplacementMode,
)
from app.services.redaction.image_redactor import ImageRedactorMixin

# ---- Re-export 公共符号，保持向后兼容 ----
from app.services.redaction.replacement_strategy import (  # noqa: F401
    RedactionContext,
    build_preview_entity_map,
)
from app.services.redaction.text_redactor import TextRedactorMixin
from app.services.vision_service import VisionService

logger = logging.getLogger(__name__)


class Redactor(TextRedactorMixin, ImageRedactorMixin):
    """匿名化执行器（编排入口）"""

    def __init__(self):
        self.vision_service = VisionService()

    def _resolve_existing_path(self, raw_path: Any, preferred_dir: str) -> str | None:
        """Resolve legacy relative storage paths against the configured storage directory."""
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None

        path = raw_path.strip()
        if os.path.isabs(path) and os.path.exists(path):
            return os.path.realpath(path)

        basename = os.path.basename(path)
        backend_root = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))
        project_root = os.path.realpath(os.path.join(backend_root, ".."))
        candidates: list[str] = []
        if basename:
            candidates.append(os.path.join(preferred_dir, basename))
            candidates.append(os.path.join(backend_root, os.path.basename(preferred_dir), basename))
            candidates.append(os.path.join(project_root, os.path.basename(preferred_dir), basename))
        if not os.path.isabs(path):
            candidates.append(os.path.join(preferred_dir, path))
            candidates.append(os.path.join(os.getcwd(), path))

        seen: set[str] = set()
        for candidate in candidates:
            real = os.path.realpath(candidate)
            if real in seen:
                continue
            seen.add(real)
            if os.path.exists(real):
                return real

        return os.path.realpath(path if os.path.isabs(path) else os.path.join(preferred_dir, basename or path))

    async def redact(
        self,
        file_info: dict,
        entities: list[Entity],
        bounding_boxes: list[BoundingBox],
        config: RedactionConfig,
    ) -> dict:
        """
        执行匿名化操作

        Args:
            file_info: 文件信息
            entities: 要匿名化的实体列表
            bounding_boxes: 要匿名化的图片区域列表
            config: 匿名化配置

        Returns:
            匿名化结果
        """
        file_type = file_info["file_type"]
        file_path = file_info["file_path"]
        is_scanned_flag = file_info.get("is_scanned")
        bbox_count = len(bounding_boxes or [])
        logger.info(
            "[redact:dispatch] raw file_type=%r is_scanned=%r bbox_count=%d",
            file_type, is_scanned_flag, bbox_count,
        )
        # 只处理选中的实体
        selected_entities = [e for e in entities if e.selected]
        selected_boxes = [b for b in bounding_boxes if b.selected]

        # 文本型 PDF 的双链路路由（Issue #61）：用户拉过框（bbox>0）说明走视觉
        # 标注，整份转图像管线真打码；MASK 模式同理——伪打码（星号文本）会把
        # 原文留在文本层里，必须按扫描件逻辑栅格化+马赛克。替换模式仍走文本链路。
        mask_boxes: list[BoundingBox] = []
        mask_missed: list[str] = []
        if file_type == FileType.PDF and (
            is_scanned_flag or bbox_count > 0
        ):
            file_type = FileType.PDF_SCANNED
            if config.replacement_mode == ReplacementMode.MASK and selected_entities:
                # 拉框路径的 MASK 同样要把选中实体定位成框一并栅格化：
                # 只吃手拉框会在「选了实体+残留旧框」时产出零打码的栅格化
                # 成品（评审 I2）。此路径无法整份回退文本链路（用户拉框
                # 本身要求图像化），定位失败的实体计入 residual 告警透出。
                mask_boxes, mask_missed = await asyncio.to_thread(
                    self._entities_to_norm_boxes, file_path, selected_entities
                )
                if mask_missed:
                    logger.warning(
                        "[redact:pdf-mask] bbox path %d/%d entities not locatable, "
                        "rasterized with user boxes only (surfaced as residual): %s",
                        len(mask_missed), len(selected_entities), mask_missed[:5],
                    )
        elif (
            file_type == FileType.PDF
            and config.replacement_mode == ReplacementMode.MASK
            and selected_entities
        ):
            # fitz 定位是同步密集操作，丢线程跑避免卡事件循环（评审 M2）
            mask_boxes, mask_missed = await asyncio.to_thread(
                self._entities_to_norm_boxes, file_path, selected_entities
            )
            if mask_missed:
                logger.warning(
                    "[redact:pdf-mask] %d/%d entities not locatable, whole file falls back to text mask: %s",
                    len(mask_missed), len(selected_entities), mask_missed[:5],
                )
                # 只要有定位失败的实体就整份回退文本链路：栅格化会把漏网实体
                # 变成可读明文像素（比文本层泄露更彻底），零容忍。
            else:
                file_type = FileType.PDF_SCANNED

        # 创建匿名化上下文
        context = RedactionContext(config.replacement_mode, word_pools=config.word_pools)
        context.set_custom_replacements(config.custom_replacements)

        # 生成输出文件路径
        output_file_id = str(uuid.uuid4())
        logger.info("[redact] file_path=%s file_type=%s output_file_id=%s", file_path, file_type, output_file_id)
        original_ext = os.path.splitext(file_path)[1]
        output_ext = original_ext
        if file_type == FileType.DOC:
            output_ext = ".docx"
        output_path = os.path.realpath(os.path.join(settings.OUTPUT_DIR, f"{output_file_id}{output_ext}"))

        redacted_count = 0

        if file_type == FileType.DOC:
            # 先将 .doc 转换为 .docx 再处理
            converted_path = await self._convert_doc_to_docx(file_path)
            if not converted_path or not os.path.exists(converted_path):
                raise ValueError("DOC 转换失败，无法匿名化")
            redacted_count = await self._redact_docx(
                converted_path, output_path, selected_entities, context
            )
            # 清理转换后的临时文件
            if converted_path != file_path:
                try:
                    os.remove(converted_path)
                except OSError:
                    pass
        elif file_type == FileType.DOCX:
            # Word 文档匿名化
            redacted_count = await self._redact_docx(
                file_path, output_path, selected_entities, context
            )
        elif file_type == FileType.TXT:
            # 纯文本匿名化（.txt, .md, .html, .rtf）
            redacted_count = await self._redact_txt(
                file_path, output_path, selected_entities, context
            )
        elif file_type == FileType.PDF:
            # PDF 文档匿名化（文本型，替换模式）：docx 回转链路，失败回退原位
            redacted_count = await self._redact_pdf_via_docx(
                file_path, output_path, selected_entities, context
            )
        elif file_type in [FileType.PDF_SCANNED, FileType.IMAGE]:
            # 图片/扫描件匿名化（文本型 PDF 的 MASK 真打码也路由到这里）；
            # 手拉框与实体定位框合并栅格化（不能 only-or：旧框残留+实体选中
            # 的组合曾产出零打码成品——评审 I2）
            redacted_count = await self._redact_image(
                file_path, file_type, selected_boxes + mask_boxes, output_path, config
            )

        watermark_text = (getattr(config, "watermark_text", None) or "").strip()
        if watermark_text and os.path.exists(output_path):
            # 水印失败不阻断匿名化交付，只响亮记录
            try:
                from app.services.redaction.watermark import apply_watermark

                apply_watermark(output_path, watermark_text)
            except Exception:
                logger.warning("watermark failed for %s", output_path, exc_info=True)

        # 导出后自检：成品全文中不应再出现任何被替换实体的原文。
        # mask_missed 是 MASK 路径无法定位的实体：文本链路（回退）下由
        # verify 覆盖式重建时也必须保留，栅格化路径下 verify 跳过、由它兜底。
        residual_entities: list[str] = list(mask_missed)
        verify_types = [FileType.PDF, FileType.DOCX, FileType.DOC, FileType.TXT]
        if file_type in verify_types and os.path.exists(output_path):
            verified = self._verify_export_residuals(
                output_path, context.entity_map, file_type
            )
            residual_entities = verified + [
                e for e in residual_entities
                if e not in verified and not any(e in v for v in verified)
            ]
            if residual_entities:
                logger.warning(
                    "[export-verify] %d entities still present in output %s: %s",
                    len(residual_entities), output_path, residual_entities[:10],
                )

        return {
            "output_file_id": output_file_id,
            "output_path": output_path,
            "redacted_count": redacted_count,
            "entity_map": context.entity_map,
            "residual_entities": residual_entities,
        }

    @staticmethod
    def _entities_to_norm_boxes(
        file_path: str, entities: list[Entity]
    ) -> tuple[list[BoundingBox], list[str]]:
        """文本型 PDF MASK 真打码的实体定位：按实体文本在**所有页**搜索，
        返回归一化坐标框（与视觉链路 BoundingBox 同构）。

        不信任实体登记页码：NER 漏检重复出现时只按登记页打码，会让其他页
        的同名原文以可读像素残留（评审 C1）。同一文本多处出现全部打框——
        多打是安全方向，且与替换模式全量替换语义一致。所有页都找不到的
        实体（跨行断开等）原样返回给调用方，由调用方决定降级路径——
        宁可降级不可静默漏打码。
        """
        # 执行路径框类型恒为 mask（既有语义）；实体类型仅预览定位端点使用
        return Redactor.locate_entity_texts(
            file_path, [(e.text, "mask") for e in entities if e.text], box_type="mask"
        )

    @staticmethod
    def locate_entity_texts(
        file_path: str,
        text_type_pairs: list[tuple[str, str]],
        box_type: str = "mask",
        source: str | None = None,
    ) -> tuple[list[BoundingBox], list[str]]:
        """实体文本 → 页面归一化框（#62 执行定位与 #66 预览定位共用同一核心，
        保证「所见框」与「执行时打码位置」零漂移）。

        text_type_pairs: [(实体文本, 实体类型)]，按唯一文本去重后全文所有页
        定位；返回 (boxes, 全文未命中的文本列表)。

        空白鲁棒（#66 验收反馈）：WPS/LibreOffice 产物的文本层常带怪空格
        （"2022 年1 月25 日"），NER 文本形态与之稍有差异就 0 命中、命中则
        返回逐字块碎矩形（一个日期 6 个框）。三段式定位：
        ①search_for 原样；②search_for 去空白形态；③字符级映射兜底
        （无空白页文本 find → 字符 bbox 回映）。命中后一律做行内合并——
        同一行内水平间隙 ≤ 平均字高 60% 的相邻碎块并成一个整框。
        """
        boxes: list[BoundingBox] = []
        missed: list[str] = []
        doc = fitz.open(file_path)
        try:
            unique: list[tuple[str, str]] = []
            seen: set[str] = set()
            for text, etype in text_type_pairs:
                if text and text not in seen:
                    seen.add(text)
                    unique.append((text, etype))
            # 每页字符索引惰性构建一次、全部实体复用（增量评审 I2：此前每
            # (实体×页) 都全量 rawdict 遍历，千实体大文档为分钟级 CPU）
            indices: dict[int, dict] = {}
            for text, etype in unique:
                found = False
                for page_no in range(1, len(doc) + 1):
                    page = doc[page_no - 1]
                    if page_no not in indices:
                        indices[page_no] = Redactor._build_page_char_index(page)
                    idx = indices[page_no]
                    rects = Redactor._locate_text_on_page(page, text, idx)
                    if not rects:
                        continue
                    found = True
                    pw, ph = page.rect.width, page.rect.height
                    for r in rects:
                        boxes.append(
                            BoundingBox(
                                id=f"{box_type}_{len(boxes)}",
                                x=r.x0 / pw,
                                y=r.y0 / ph,
                                width=r.width / pw,
                                height=r.height / ph,
                                page=page_no,
                                type=etype or box_type,
                                text=text,
                                selected=True,
                                source=source,
                            )
                        )
                if not found:
                    missed.append(text)
        finally:
            doc.close()
        return boxes, missed

    @staticmethod
    def _merge_rects_rowwise(rects: list[fitz.Rect]) -> list[fitz.Rect]:
        """同行相邻碎矩形合并：按 y 中心聚类成行，行内 x 排序后间隙
        ≤ 平均字高 60% 的相邻块并成整框；跨行保留多框（跨行实体本需多框）。"""
        if len(rects) <= 1:
            return list(rects)
        avg_h = sum(r.height for r in rects) / len(rects)
        rows: list[list[fitz.Rect]] = []
        for r in sorted(rects, key=lambda x: (x.y0 + x.y1) / 2):
            placed = False
            for row in rows:
                cy = (r.y0 + r.y1) / 2
                row_cy = sum((x.y0 + x.y1) / 2 for x in row) / len(row)
                if abs(cy - row_cy) <= avg_h * 0.6:
                    row.append(r)
                    placed = True
                    break
            if not placed:
                rows.append([r])
        merged: list[fitz.Rect] = []
        for row in rows:
            row.sort(key=lambda x: x.x0)
            cur = fitz.Rect(row[0])
            for nxt in row[1:]:
                if nxt.x0 - cur.x1 <= avg_h * 0.6:
                    cur |= nxt
                else:
                    merged.append(cur)
                    cur = fitz.Rect(nxt)
            merged.append(cur)
        return merged

    @staticmethod
    def _build_page_char_index(page: fitz.Page) -> dict:
        """页字符索引（惰性构建、同页全部实体复用——增量评审 I2）：
        hay = 无空白页文本（供字符串预判与 char-map 查找）；
        nospace_chars = [(字符, bbox, 行号)]；lines = 全字符行分组（数字锚点用）。"""
        try:
            raw = page.get_text("rawdict")
        except Exception:
            return {"hay": "", "nospace_chars": [], "lines": []}
        nospace_chars: list[tuple[str, fitz.Rect, int]] = []
        lines: list[list[tuple[str, fitz.Rect]]] = []
        for block in raw.get("blocks", []):
            for line in block.get("lines", []):
                line_chars: list[tuple[str, fitz.Rect]] = []
                for span in line.get("spans", []):
                    for ch in span.get("chars", []):
                        c = ch.get("c", "")
                        bbox = ch.get("bbox")
                        if not bbox:
                            continue
                        rect = fitz.Rect(bbox)
                        line_chars.append((c, rect))
                        if c.strip():
                            nospace_chars.append((c, rect, len(lines)))
                if line_chars:
                    lines.append(line_chars)
        return {
            "hay": "".join(c for c, _, _ in nospace_chars),
            "nospace_chars": nospace_chars,
            "lines": lines,
        }

    @staticmethod
    def _locate_text_on_page(
        page: fitz.Page,
        text: str,
        char_index: dict | None = None,
    ) -> list[fitz.Rect]:
        """单页定位一个实体文本：页索引 hay 预判 + 字符映射 + 数字锚点，
        结果行内合并。

        多形态合并不短路（#66 复验反馈）：同一实体文本在页面上可能以多种
        空格/换行形态出现多次——字符映射按无空白归一化查找可一次覆盖全部
        形态与位置；数字锚点仅在字面全空时兜底（NER 改写文本专用）。"""
        nospace = "".join(text.split())
        if not nospace:
            return []
        idx = char_index if char_index is not None else Redactor._build_page_char_index(page)
        if nospace not in idx["hay"]:
            # 页面无空白文本里没有该实体的任何形态：字面必不命中，
            # 直接走数字锚点（仅改写文本类），其余页跳过——这是性能闸门
            return Redactor._locate_via_numeric_anchor_idx(idx, text)
        rects = list(page.search_for(text))
        if nospace != text:
            rects += list(page.search_for(nospace))
        rects += Redactor._char_map_from_index(idx, nospace)
        return Redactor._merge_rects_rowwise(Redactor._dedupe_contained(rects))

    @staticmethod
    def _char_map_from_index(idx: dict, nospace_query: str) -> list[fitz.Rect]:
        """基于页索引的字符映射：无空白 hay 中找 query 的每次出现，
        命中字符按行号分段（跨行实体每行一个窄框，不做跨行并集）。"""
        if not nospace_query:
            return []
        rects: list[fitz.Rect] = []
        pos = idx["hay"].find(nospace_query)
        while pos != -1:
            hit = idx["nospace_chars"][pos : pos + len(nospace_query)]
            segments: dict[int, list[fitz.Rect]] = {}
            for _, r, ln in hit:
                segments.setdefault(ln, []).append(r)
            for seg in segments.values():
                union = fitz.Rect(seg[0])
                for r in seg[1:]:
                    union |= r
                rects.append(union)
            pos = idx["hay"].find(nospace_query, pos + 1)
        return rects

    @staticmethod
    def _locate_via_numeric_anchor_idx(idx: dict, text: str) -> list[fitz.Rect]:
        runs = re.findall(r"[0-9A-Za-z]{6,}", text)
        if len(runs) != 1:
            return []
        anchor = runs[0]
        hits: list[fitz.Rect] = []
        for line_chars in idx["lines"]:
            hay = "".join(c for c, _ in line_chars)
            if anchor in hay:
                union = fitz.Rect(line_chars[0][1])
                for _, r in line_chars[1:]:
                    union |= r
                hits.append(union)
        return hits

    @staticmethod
    def _locate_via_char_map(page: fitz.Page, text: str) -> list[fitz.Rect]:
        """兼容入口：无索引时临时构建。"""
        return Redactor._char_map_from_index(
            Redactor._build_page_char_index(page), "".join(text.split())
        )

    @staticmethod
    def _locate_via_numeric_anchor(page: fitz.Page, text: str) -> list[fitz.Rect]:
        return Redactor._locate_via_numeric_anchor_idx(
            Redactor._build_page_char_index(page), text
        )

    @staticmethod
    def _dedupe_contained(rects: list[fitz.Rect]) -> list[fitz.Rect]:
        """去重：被更大框包含（≥80% 面积）的矩形丢弃（search 与字符映射
        会对同一处出现各报一个框）。"""
        if len(rects) <= 1:
            return list(rects)
        kept: list[fitz.Rect] = []
        for r in sorted(rects, key=lambda x: -abs(x)):
            dup = False
            for k in kept:
                inter = fitz.Rect(r) & k
                if not inter.is_empty and abs(inter) >= 0.8 * abs(r):
                    dup = True
                    break
            if not dup:
                kept.append(r)
        return kept

    def _verify_export_residuals(
        self, output_path: str, entity_map: dict[str, str], file_type: FileType
    ) -> list[str]:
        """导出后自检：提取成品全文，返回仍残留原文的实体列表。

        只做文本层校验（PDF 图像遮挡不在本契约内）；残留仅告警不阻断交付。
        """
        try:
            text = self._extract_output_text(output_path, file_type)
        except Exception:
            logger.error("[export-verify] output text extraction FAILED, check inconclusive: %s",
                         output_path, exc_info=True)
            return []
        if not text:
            logger.error("[export-verify] empty output text, check inconclusive: %s", output_path)
            return []
        residuals = []
        normalized = re.sub(r"\s+", "", text)
        for orig, repl in entity_map.items():
            if not orig:
                continue
            # 设计性保留（替换词==原文，公共机构白名单 org_rules #56）：
            # 原文留在成品是预期行为，不判残留也不判落盘
            if orig == repl:
                continue
            # 原文残留检测（跨节点/跨行提取会插入空白，去空白比对；
            # ASCII 加词边界匹配原始文本以减少短词误报；替换词本身包含
            # 原文时（John→Johnson）归一化子串会误报，跳过该分支）
            norm_orig = re.sub(r"\s+", "", orig)
            norm_repl = re.sub(r"\s+", "", repl or "")
            leaked = bool(
                re.search(rf"(?<![0-9A-Za-z]){re.escape(orig)}(?![0-9A-Za-z])", text)
            ) or (norm_orig in normalized and norm_orig not in norm_repl)
            if leaked:
                residuals.append(orig)
                continue
            # 替换词落盘检测（PDF 提取可能在字符间插空白，去空白比对）
            if repl and re.sub(r"\s+", "", repl) not in normalized:
                residuals.append(f"[未落盘] {orig} -> {repl}")
        return residuals

    def _extract_output_text(self, output_path: str, file_type: FileType) -> str:
        if file_type == FileType.PDF:
            doc = fitz.open(output_path)
            try:
                return "\n".join(page.get_text() for page in doc)
            finally:
                doc.close()
        if file_type in [FileType.DOCX, FileType.DOC]:
            # 直接读包内全部 word/*.xml 的文本节点：覆盖正文/页眉页脚/批注/
            # 脚注尾注/文本框/修订历史（w:delText），比对象模型更全，
            # 自检必须不弱于改写器的覆盖面
            import zipfile

            from lxml import etree as _etree

            parts_text = []
            with zipfile.ZipFile(output_path) as zf:
                for name in zf.namelist():
                    if name.startswith("word/") and name.endswith(".xml"):
                        try:
                            root = _etree.fromstring(zf.read(name))
                        except _etree.XMLSyntaxError:
                            continue
                        parts_text.extend(t for t in root.itertext() if t)
            return "\n".join(parts_text)
        # TXT / MD / HTML / RTF
        with open(output_path, encoding="utf-8", errors="ignore") as f:
            return f.read()

    async def _convert_doc_to_docx(self, file_path: str) -> str | None:
        """将 .doc 转换为 .docx（复用 FileParser 逻辑）"""
        try:
            from app.services.file_parser import FileParser
            parser = FileParser()
            return await parser._convert_doc_to_docx(file_path)
        except (OSError, ValueError, KeyError) as e:
            logger.error("DOC 转换失败: %s", e)
            return None

    async def get_comparison(self, file_info: dict) -> dict:
        """
        获取匿名化前后对比数据
        """
        file_type = file_info["file_type"]
        original_path = self._resolve_existing_path(file_info.get("file_path"), settings.UPLOAD_DIR) or file_info["file_path"]
        redacted_path = self._resolve_existing_path(file_info.get("output_path"), settings.OUTPUT_DIR)
        redacted_text = file_info.get("redacted_text")

        if not redacted_path or not os.path.exists(redacted_path):
            raise ValueError("匿名化文件不存在")

        # 统一转为字符串比较（兼容枚举和字符串）
        ft = str(file_type.value) if hasattr(file_type, 'value') else str(file_type)
        # 防御：output 扩展名与 file_type 不匹配时（旧脏数据），返回提示对比
        out_ext = os.path.splitext(redacted_path)[1].lower() if redacted_path else ""
        text_exts = {".docx", ".doc", ".txt", ".pdf"}
        if out_ext and out_ext not in text_exts and ft in ("docx", "doc", "txt", "pdf", "pdf_scanned"):
            logger.warning("output ext %s mismatches file_type %s, returning placeholder compare", out_ext, ft)
            return {
                "original": file_info.get("content", "[原始文本不可用]"),
                "redacted": f"[匿名化输出类型不匹配 ({out_ext})，请重新执行匿名化]",
                "changes": [],
            }
        is_docx = ft in ("docx", "doc")
        is_txt = ft == "txt"
        is_pdf = ft in ("pdf", "pdf_scanned")

        logger.debug("Compare file_type=%s, ft=%s, is_docx=%s, is_pdf=%s, redacted_path=%s", file_type, ft, is_docx, is_pdf, redacted_path)

        original_content = ""
        redacted_content = ""

        # .doc 文件匿名化后输出为 .docx，原始内容从解析缓存读取
        original_content_cached = file_info.get("content", "")

        if redacted_text:
            redacted_content = redacted_text
            if is_docx:
                original_content = original_content_cached or self._safe_extract_text(original_path, ft)
            elif is_txt:
                original_content = original_content_cached or self._read_txt(original_path)
            elif is_pdf:
                original_content = original_content_cached or self._extract_pdf_text(original_path)
            else:
                original_content = "[图片文件，请查看预览]"
        elif is_docx:
            original_content = original_content_cached or self._safe_extract_text(original_path, ft)
            redacted_content = self._extract_docx_text(redacted_path)
        elif is_txt:
            original_content = original_content_cached or self._read_txt(original_path)
            redacted_content = self._read_txt(redacted_path)
        elif is_pdf:
            original_content = original_content_cached or self._extract_pdf_text(original_path)
            redacted_content = self._extract_pdf_text(redacted_path)
        else:
            original_content = "[图片文件，请查看预览]"
            redacted_content = "[已匿名化图片，请查看预览]"

        # 计算变更
        changes = self._compute_changes(
            original_content,
            redacted_content,
            file_info.get("entity_map", {}),
        )

        return {
            "original": original_content,
            "redacted": redacted_content,
            "changes": changes,
        }

    def _compute_changes(
        self,
        original: str,
        redacted: str,
        entity_map: dict[str, str],
    ) -> list[dict]:
        """计算变更列表"""
        changes = []

        for original_text, replacement in entity_map.items():
            # 计算出现次数
            count = original.count(original_text)
            if count > 0:
                changes.append({
                    "original": original_text,
                    "replacement": replacement,
                    "count": count,
                })

        return changes
