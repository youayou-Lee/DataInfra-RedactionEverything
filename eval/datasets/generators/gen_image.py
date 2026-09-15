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
    """字体解析：已知 Noto CJK 路径（index=2 = SC）→ fc-match 中文字体 → 任意字体。

    样张重建用 CJK 字形才与入库版一致；测试渲染只依赖画布尺寸与文件魔数，
    退化为非 CJK 字体不影响单测判定。
    """
    import subprocess

    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, FONT_SIZE, index=2)  # index=2: SC
    try:
        for query in (":lang=zh", "sans-serif"):
            out = subprocess.run(["fc-match", "-f", "%{file}", query],
                                 capture_output=True, text=True, timeout=10)
            font_path = out.stdout.strip()
            if out.returncode == 0 and font_path and Path(font_path).exists():
                return ImageFont.truetype(font_path, FONT_SIZE)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass  # 无 fontconfig（如精简 CI 容器）
    raise RuntimeError("未找到任何可用 TrueType 字体，无法渲染合成图片样张")


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
