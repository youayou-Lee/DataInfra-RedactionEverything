#!/usr/bin/env python3
"""扫描件成品泄漏审计：上传→vision→execute→下载成品→逐页OCR→正则找残留PII。
对图片型 PDF 的真审计（文本层提取对扫描件无效）。
用法: python3 audit_scan_output.py <pdf路径> [更多pdf...]
"""
import base64
import io
import re
import sys
import time

import fitz
import httpx

BASE = "http://127.0.0.1:8000/api/v1"
OCR = "http://127.0.0.1:8082"
c = httpx.Client(timeout=1800.0, trust_env=False)

RE_ID = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
RE_MO = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_BK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def find_pii(text):
    hits = {"id_card": RE_ID.findall(text), "mobile": RE_MO.findall(text), "bank_card": RE_BK.findall(text)}
    return {k: v for k, v in hits.items() if v}


r = None
token = None
for path, body in [("/auth/setup", {"username": "apitester", "password": "ApiTest#2026"}),
                   ("/auth/login", {"username": "apitester", "password": "ApiTest#2026"})]:
    r = c.post(f"{BASE}{path}", json=body)
    if r.status_code == 200:
        token = (r.json() or {}).get("access_token") or (r.json() or {}).get("token")
        break
assert token, "认证失败"
H = {"Authorization": f"Bearer {token}"}

for pdf in sys.argv[1:]:
    name = pdf.split("/")[-1]
    raw = open(pdf, "rb").read()
    print(f"\n########## {name} ##########", flush=True)

    up = c.post(f"{BASE}/files/upload", headers=H, files={"file": (name, raw, "application/pdf")})
    fid = up.json().get("file_id") or up.json().get("id")
    t0 = time.perf_counter()
    v = c.post(f"{BASE}/redaction/{fid}/vision", headers=H, json={}).json()
    boxes = v.get("bounding_boxes") or []
    ex = c.post(f"{BASE}/redaction/execute", headers=H, json={
        "file_id": fid, "entities": v.get("entities") or [], "bounding_boxes": boxes,
        "config": {"replacement_mode": "smart",
                   "entity_types": ["PERSON", "PHONE", "ID_CARD", "ADDRESS", "ORG", "BANK_CARD"]}}).json()
    url = ex.get("download_url") or f"{BASE}/files/{fid}/download?redacted=true"
    out = c.get(url if url.startswith("http") else f"http://127.0.0.1:8000{url}", headers=H).content
    print(f"框 {len(boxes)} | 脱敏 {ex.get('redacted_count')} | 成品 {len(out)//1024}KB | 管道 {time.perf_counter()-t0:.0f}s", flush=True)

    # 成品逐页 OCR 审计
    doc = fitz.open(stream=out, filetype="pdf")
    total_hits, per_page = {}, []
    for i, page in enumerate(doc):
        png = page.get_pixmap(dpi=130).tobytes("png")
        b64 = base64.b64encode(png).decode()
        try:
            r = c.post(f"{OCR}/structure", json={"image": b64}, timeout=300)
            items = (r.json() or {}).get("boxes") or (r.json() or {}).get("items") or []
            text = "".join(str(b.get("text", "")) for b in items)
        except Exception as exc:
            text = ""
            per_page.append(f"p{i+1}:OCR错误:{str(exc)[:40]}")
            continue
        hits = find_pii(text)
        if hits:
            total_hits = {k: total_hits.get(k, []) + v for k, v in hits.items()}
            per_page.append(f"p{i+1}: {hits}")
    doc.close()
    if total_hits:
        print("  ⚠ 成品OCR发现残留:", total_hits, flush=True)
        for pp in per_page:
            if pp and not pp.endswith("OCR错误"):
                print("   ", pp, flush=True)
    else:
        print("  ✓ 成品OCR审计通过：未发现残留身份证/手机/银行卡模式", flush=True)
    for pp in per_page:
        if pp.endswith(tuple("0123456789")) is False and "OCR错误" in pp:
            print("   ", pp, flush=True)
print("\nAUDIT_DONE", flush=True)
