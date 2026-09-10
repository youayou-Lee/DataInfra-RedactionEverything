#!/usr/bin/env python3
"""真实案卷批量 E2E —— 上传→vision全链路→执行脱敏→下载→残留PII正则审计。
在实例上用 ~/.venvs/app/bin/python 运行（需 fitz/httpx）。
用法: python3 e2e_real_cases.py [cases目录] [最大页数]
"""
import glob
import io
import json
import re
import sys
import time

import fitz  # PyMuPDF
import httpx

BASE = "http://127.0.0.1:8000/api/v1"
CASES = sys.argv[1] if len(sys.argv) > 1 else "/root/cases"
MAX_PAGES = int(sys.argv[2]) if len(sys.argv) > 2 else 40
c = httpx.Client(timeout=1800.0, trust_env=False)

RE_ID = re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
RE_MO = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_BK = re.compile(r"(?<!\d)\d{16,19}(?!\d)")


def pii_stats(text):
    return {"id_card": len(RE_ID.findall(text)), "mobile": len(RE_MO.findall(text)),
            "bank_card": len(RE_BK.findall(text))}


# 认证
token = None
for path, body in [("/auth/setup", {"username": "apitester", "password": "ApiTest#2026"}),
                   ("/auth/login", {"username": "apitester", "password": "ApiTest#2026"})]:
    r = c.post(f"{BASE}{path}", json=body)
    if r.status_code == 200:
        token = (r.json() or {}).get("access_token") or (r.json() or {}).get("token")
        break
assert token, "认证失败"
H = {"Authorization": f"Bearer {token}"}
print("auth OK", flush=True)

report = []
for pdf in sorted(glob.glob(f"{CASES}/*.pdf")):
    name = pdf.split("/")[-1]
    raw = open(pdf, "rb").read()
    try:
        pages = fitz.open(stream=raw, filetype="pdf").page_count
    except Exception:
        pages = -1
    if pages > MAX_PAGES:
        print(f"[SKIP] {name} {pages}页 > {MAX_PAGES}", flush=True)
        continue

    ent = {"file": name, "pages": pages, "mb": round(len(raw) / 1048576, 1)}
    print(f"\n===== {name} ({pages}页, {ent['mb']}MB) =====", flush=True)

    # 1 上传
    t0 = time.perf_counter()
    r = c.post(f"{BASE}/files/upload", headers=H,
               files={"file": (name, raw, "application/pdf")})
    if r.status_code != 200:
        print(f"  upload FAIL {r.status_code}: {r.text[:120]}", flush=True)
        ent["error"] = f"upload {r.status_code}"
        report.append(ent)
        continue
    fid = r.json().get("file_id") or r.json().get("id")
    ent["upload_s"] = round(time.perf_counter() - t0, 1)

    # 2 原文 PII 基线（文本层）
    try:
        orig_text = "".join(p.get_text() for p in fitz.open(stream=raw, filetype="pdf"))
    except Exception:
        orig_text = ""
    ent["orig_pii_textlayer"] = pii_stats(orig_text)

    # 3 vision 全链路
    t0 = time.perf_counter()
    r = c.post(f"{BASE}/redaction/{fid}/vision", headers=H, json={})
    ent["vision_s"] = round(time.perf_counter() - t0, 1)
    if r.status_code != 200:
        print(f"  vision FAIL {r.status_code}: {r.text[:200]}", flush=True)
        ent["error"] = f"vision {r.status_code}: {r.text[:150]}"
        report.append(ent)
        continue
    v = r.json()
    entities = v.get("entities") or []
    boxes = v.get("bounding_boxes") or v.get("boxes") or []
    types = {}
    for e in entities:
        t = e.get("type") or e.get("entity_type") or "?"
        types[t] = types.get(t, 0) + 1
    ent["entities"] = len(entities)
    ent["entity_types"] = types
    ent["boxes"] = len(boxes)
    print(f"  vision {ent['vision_s']}s | 实体 {len(entities)} {types} | 框 {len(boxes)}"
          f" | {ent['vision_s'] / max(pages, 1):.1f}s/页", flush=True)

    # 4 执行脱敏
    t0 = time.perf_counter()
    r = c.post(f"{BASE}/redaction/execute", headers=H, json={
        "file_id": fid, "entities": entities, "bounding_boxes": boxes,
        "config": {"replacement_mode": "smart",
                   "entity_types": ["PERSON", "PHONE", "ID_CARD", "ADDRESS", "ORG", "BANK_CARD"]}})
    ent["execute_s"] = round(time.perf_counter() - t0, 1)
    if r.status_code != 200:
        print(f"  execute FAIL {r.status_code}: {r.text[:200]}", flush=True)
        ent["error"] = f"execute {r.status_code}"
        report.append(ent)
        continue
    result = r.json()
    redacted = result.get("redacted_count")
    ent["redacted_count"] = redacted
    url = result.get("download_url") or f"{BASE}/files/{fid}/download?redacted=true"

    # 5 下载成品 + 残留审计
    r = c.get(url if url.startswith("http") else f"http://127.0.0.1:8000{url}", headers=H)
    ent["output_kb"] = round(len(r.content) / 1024) if r.status_code == 200 else 0
    residual = {"id_card": -1, "mobile": -1, "bank_card": -1}
    if r.status_code == 200:
        try:
            out_text = "".join(p.get_text() for p in fitz.open(stream=r.content, filetype="pdf"))
            residual = pii_stats(out_text)
        except Exception as exc:
            residual = {"error": str(exc)[:80]}
    ent["residual_pii"] = residual
    ok = all(v == 0 for v in residual.values() if isinstance(v, int)) and len(entities) > 0
    print(f"  execute {ent['execute_s']}s | 脱敏 {redacted} | 成品 {ent['output_kb']}KB"
          f" | 残留 {residual} | {'✓ CLEAN' if ok else '⚠ 检查'}", flush=True)
    report.append(ent)

json.dump(report, open("/root/e2e_real_report.json", "w"), ensure_ascii=False, indent=1)
n_ok = sum(1 for e in report if not e.get("error"))
print(f"\n===== 汇总: {n_ok}/{len(report)} 成功 =====", flush=True)
for e in report:
    if e.get("error"):
        print(f"  ✗ {e['file']}: {e['error']}", flush=True)
    else:
        print(f"  {e['file'][:40]:40} {e['pages']:>3}页 vision {e['vision_s']:>6.1f}s "
              f"实体 {e['entities']:>3} 脱敏 {e.get('redacted_count', '?'):>3} "
              f"残留 {e['residual_pii']}", flush=True)
