"""合成 RTF 生成器（Issue #46 格式矩阵）：payload 行 → RTF（\\uc1\\uN? CJK 转义）。

CJK 用 Word 标准的 \\ansicpg936 + \\uc1\\u<signed16>? 形式书写——这正是后端朴素
正则解析的风险点（设计文档 §2/§3）：`\\[a-z]+\\d*` 会把 `\\u27861?` 整段剥掉。
N 为 16 位有符号数（>32767 时减 65536），`?` 为 \\uc1 消费的回退字符。
"""

from __future__ import annotations

from pathlib import Path

_RTF_HEADER = r"{\rtf1\ansi\ansicpg936\deff0"
_RTF_FONTTBL = r"{\fonttbl{\f0\fnil\fcharset134 SimSun;}}"
_RTF_FOOTER = "}"


def _escape(text: str) -> str:
    """行文本 → RTF 转义串：ASCII 直写（\\\\{}` 转义），CJK 全部 \\uc1\\uN?。"""
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if code < 128:
            if ch in "\\{}":
                out.append("\\" + ch)
            else:
                out.append(ch)
        else:
            n = code if code < 32768 else code - 65536
            out.append(f"\\u{n}?")
    return "".join(out)


def build_rtf(out_path: Path, *, lines: list[str]) -> str:
    """生成 RTF 文件并返回其文本（供 GT 自检与单测解码比对）。"""
    parts = [_RTF_HEADER, _RTF_FONTTBL, r"\fs32\f0"]
    for line in lines:
        parts.append(_escape(line) + r"\par")
    parts.append(_RTF_FOOTER)
    content = "\n".join(parts)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="ascii", newline="\r\n")
    return content


def decode_rtf(content: str) -> str:
    """按 Word 规则解 \\uN? 转义回 Unicode（单测/自检用，非通用 RTF 解析器）。"""
    import re

    def _sub(match: re.Match) -> str:
        n = int(match.group(1))
        return chr(n if n >= 0 else n + 65536)

    # \uc1 → 转义后恰好消费 1 个回退字符（本生成器固定写 `?`）
    content = re.sub(r"\\u(-?\d+)\?", _sub, content)
    return content.replace("\\par", "\n")
