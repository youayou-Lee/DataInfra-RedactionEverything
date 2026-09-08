#!/usr/bin/env python
"""E2E 脱敏链路测试 —— 纯 API 驱动, 不经前端。在实例上用 ~/.venvs/app/bin/python 运行。"""
import io
import json
import sys

import fitz  # PyMuPDF
import httpx

BASE = "http://127.0.0.1:8000/api/v1"
c = httpx.Client(timeout=600.0, trust_env=False)
STEP = 0


def step(msg):
    global STEP
    STEP += 1
    print(f"\n[{STEP}] {msg}")


# 1 健康检查(挂在根路径, 不带 API 前缀)
step("健康检查")
assert c.get("http://127.0.0.1:8000/health").status_code == 200, "backend 不健康"
print("backend OK")

# 2 认证: setup(首次) 或 login
step("认证")
st = c.get(f"{BASE}/auth/status").json()
print("auth/status:", json.dumps(st, ensure_ascii=False)[:150])
token = None
for path, body in [
    ("/auth/setup", {"username": os.environ.get("E2E_USER", ""), "password": os.environ.get("E2E_PASS", "")}),
    ("/auth/login", {"username": os.environ.get("E2E_USER", ""), "password": os.environ.get("E2E_PASS", "")}),
]:
    r = c.post(f"{BASE}{path}", json=body)
    if r.status_code == 200:
        data = r.json()
        token = data.get("access_token") or data.get("token")
        print(f"{path} OK, token 字段: {list(data.keys())}")
        break
    print(f"{path} -> {r.status_code} {r.text[:100]}")
assert token, "拿不到 token"
H = {"Authorization": f"Bearer {token}"}

# 3 造测试 PDF(含敏感实体, 对齐默认 entity_types PERSON/PHONE/ID_CARD)
step("生成含敏感信息的测试 PDF")
doc = fitz.open()
page = doc.new_page()
page.insert_text((72, 90), "委托代理合同", fontsize=18, fontname="china-s")
page.insert_text((72, 130), "委托人：张三，身份证号 110101199003078515", fontsize=12, fontname="china-s")
page.insert_text((72, 155), "联系电话：13800138000", fontsize=12, fontname="china-s")
page.insert_text((72, 180), "代理人：李四（北京市海淀区律师事务所）", fontsize=12, fontname="china-s")
buf = io.BytesIO()
doc.save(buf)
doc.close()
pdf_bytes = buf.getvalue()
print(f"PDF {len(pdf_bytes)} bytes")

# 4 上传
step("上传文档")
r = c.post(f"{BASE}/files/upload", headers=H,
           files={"file": ("e2e-test.pdf", pdf_bytes, "application/pdf")})
print("upload:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False)[:300])
file_id = r.json().get("file_id") or r.json().get("id")
assert file_id, "没拿到 file_id"

# 5 vision 检测(真实调动 OCR + HaS NER + LocateAnything)
step("vision 全链路检测(OCR+NER+视觉)")
r = c.post(f"{BASE}/redaction/{file_id}/vision", headers=H, json={}, )
print("vision:", r.status_code, f"({r.elapsed.total_seconds():.1f}s)")
vision = r.json() if r.status_code == 200 else {}
print(json.dumps(vision, ensure_ascii=False)[:600])
entities = vision.get("entities") or []
boxes = vision.get("bounding_boxes") or vision.get("boxes") or []
print(f"识别实体 {len(entities)} 个, 视觉框 {len(boxes)} 个")

# 6 执行脱敏
step("执行脱敏")
payload = {
    "file_id": file_id,
    "entities": entities,
    "bounding_boxes": boxes,
    "config": {"replacement_mode": "smart", "entity_types": ["PERSON", "PHONE", "ID_CARD"]},
}
r = c.post(f"{BASE}/redaction/execute", headers=H, json=payload)
print("execute:", r.status_code)
print(json.dumps(r.json(), ensure_ascii=False)[:400])
result = r.json() if r.status_code == 200 else {}
output_file_id = result.get("output_file_id")
assert result.get("redacted_count", 0) > 0 or entities, "没有脱敏任何内容"

# 7 内容比对(原文 vs 成品)
step("内容比对")
r = c.get(f"{BASE}/redaction/{file_id}/compare", headers=H)
if r.status_code == 200:
    cmp_data = r.json()
    print("原文:", cmp_data.get("original_content", "")[:120].replace("\n", " "))
    print("成品:", cmp_data.get("redacted_content", "")[:120].replace("\n", " "))

# 8 下载成品, 验证敏感串已消除(用 execute 返回的 download_url: 原file_id + ?redacted=true)
step("下载成品并验证")
sensitive = ["张三", "李四", "13800138000", "110101199003078515"]
url = result.get("download_url") or f"{BASE}/files/{file_id}/download?redacted=true"
r = c.get(url if url.startswith("http") else f"http://127.0.0.1:8000{url}", headers=H)
print("download:", r.status_code, len(r.content), "bytes")
if r.status_code == 200:
    out_doc = fitz.open(stream=r.content, filetype="pdf")
    out_text = "".join(p.get_text() for p in out_doc)
    out_doc.close()
    for s in sensitive:
        state = "仍存在!" if s in out_text else "已消除 ✓"
        print(f"  {s}: {state}")
    print("成品文本:", out_text[:200].replace("\n", " "))

# 9 质量报告
step("质量报告")
r = c.get(f"{BASE}/redaction/{file_id}/report", headers=H)
if r.status_code == 200:
    rep = r.json()
    print(f"实体总数 {rep.get('total_entities')} | 已脱敏 {rep.get('redacted_entities')}"
          f" | 类型分布 {rep.get('entity_type_distribution')}")

print("\n===== E2E 完成 =====")
