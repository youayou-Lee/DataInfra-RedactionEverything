"""合成图片生成器（Issue #46 格式矩阵）：payload 行 → 位图（7 种扩展名）。

PIL 渲染（设计文档 §3）：Noto Sans CJK 32px（≥28px 保证 OCR 可读）；
渲染质量不构成 FAIL 依据——检不出才是结论。A4 @150dpi 画布。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# PIL 保存格式名（jpeg/bmp 不支持部分模式，统一 RGB 消除差异）
EXT_TO_PIL_FORMAT = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".bmp": "BMP",
    ".gif": "GIF", ".webp": "WEBP", ".tif": "TIFF", ".tiff": "TIFF",
}

CANVAS_W, CANVAS_H = 1240, 1754  # A4 @150dpi
MARGIN = 64
FONT_SIZE = 32
LINE_STEP = 56

_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]


def _load_font() -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE, index=2)  # index=2: SC
    raise RuntimeError("未找到 Noto Sans CJK 字体，无法渲染合成图片样张")


def build_image(out_path: Path, *, lines: list[str]) -> dict:
    """渲染 payload 行到白底位图并按扩展名落盘。返回 {"size": [w, h]}（GT 载体校验用）。"""
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), color="white")
    draw = ImageDraw.Draw(img)
    font = _load_font()
    y = MARGIN
    for line in lines:
        draw.text((MARGIN, y), line, fill="black", font=font)
        y += LINE_STEP
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(out_path), format=EXT_TO_PIL_FORMAT[out_path.suffix.lower()])
    return {"size": [img.width, img.height]}
