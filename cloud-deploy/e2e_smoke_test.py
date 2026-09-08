#!/usr/bin/env python
"""E2E 脱敏链路冒烟测试 —— 纯 API 驱动, 不经前端。

在实例上运行: E2E_USER=xxx E2E_PASS=xxx ~/.venvs/app/bin/python e2e_smoke_test.py
任何一步失败都会计入 FAILS 并在结束时以非零码退出, 可直接用作 CI 门槛。
"""
import io
import os
import sys
import time

import fitz  # PyMuPDF
import httpx

BASE = "http://127.0.0.1:8000/api/v1"
FAILS: list[str] = []
STEP = 0


def check(cond: bool, ok_msg: str, fail_msg: str) -> None:
    if cond:
        print(f"  ✓ {ok_msg}")
    else:
        print(f"  ✗ {fail_msg}")
        FAILS.append(fail_msg)


def step(msg: str) -> None:
    global STEP
    STEP += 1
    print(f"\n[{STEP}] {msg}")


def main() -> int:
    user = os.environ.get("E2E_USER")
    password = os.environ.get("E2E_PASS")
    if not user or not password:
        print("需要 export E2E_USER / E2E_PASS（后端首次 setup 时创建的管理账号）")
        return 2

    # 1 健康检查(挂在根路径, 不带 API 前缀)
    step("健康检查")
    c = httpx.Client(timeout=600.0, trust_env=False)
    check(c.get("http://127.0.0.1:8000/health").status_code == 200, "backend OK", "backend 不健康")

    # 2 认证: 已设密码则 login, 未设则 setup 创建
    step("认证")
    token = None
    for path, body in (
        ("/auth/login", {"username": user, "password": password}),
        ("/auth/setup", {"username": user, "password": password}),
    ):
        r = c.post(f"{BASE}{path}", json=body)
        if r.status_code == 200:
            token = r.json().get("access_token")
            break
    check(bool(token), "拿到 JWT", "认证失败(login/setup 均未通过)")
    if not token:
        return 1
    H = {"Authorization": f"Bearer {token}"}

    # 2.5 NER 停止符直接验证(Issue #1 验收标准: finish_reason==stop 且无复读)
    step("NER EOS 停止符验证(Issue #1)")
    t0 = time.perf_counter()
    r = c.post("http://127.0.0.1:8080/v1/chat/completions", json={
        "messages": [{"role": "user", "content": "标注敏感实体: 张三2024年在北京起诉李四"}],
        "max_tokens": 150, "temperature": 0})
    if r.status_code == 200:
        ner = r.json()
        finish = ner["choices"][0]["finish_reason"]
        content = ner["choices"][0]["message"]["content"]
        elapsed = time.perf_counter() - t0
        check(finish == "stop", f"finish=stop ({elapsed:.1f}s)", f"finish={finish}(应为 stop)")
        check(content.count("{") <= 2, "输出无复读", "输出疑似复读(JSON 出现多次)")
    else:
        check(False, "", f"NER 服务不可达: HTTP {r.status_code}")

    # 3 造测试 PDF(含敏感实体, CJK 字体保证文字层可提取)
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
    print(f"  PDF {len(pdf_bytes)} bytes")

    # 4 上传
    step("上传文档")
    r = c.post(f"{BASE}/files/upload", headers=H,
               files={"file": ("e2e-test.pdf", pdf_bytes, "application/pdf")})
    check(r.status_code == 200, "上传成功", f"上传失败 HTTP {r.status_code}")
    file_id = (r.json() if r.status_code == 200 else {}).get("file_id")
    check(bool(file_id), f"file_id={file_id}", "未返回 file_id")
    if not file_id:
        return 1

    # 5 vision 全链路检测(真实调动 OCR + HaS NER + LocateAnything; 脱敏由 bounding_boxes 驱动)
    step("vision 全链路检测(OCR+NER+视觉)")
    t0 = time.perf_counter()
    r = c.post(f"{BASE}/redaction/{file_id}/vision", headers=H, json={})
    check(r.status_code == 200, f"检测完成 ({time.perf_counter()-t0:.1f}s)",
          f"vision 失败 HTTP {r.status_code}")
    boxes = (r.json() if r.status_code == 200 else {}).get("bounding_boxes", [])
    check(len(boxes) > 0, f"检出 {len(boxes)} 个目标", "未检出任何目标")
    if not boxes:
        return 1

    # 6 执行脱敏
    step("执行脱敏")
    r = c.post(f"{BASE}/redaction/execute", headers=H, json={
        "file_id": file_id,
        "bounding_boxes": boxes,
        "config": {"replacement_mode": "smart",
                   "entity_types": ["PERSON", "PHONE", "ID_CARD"]},
    })
    check(r.status_code == 200, "execute 成功", f"execute 失败 HTTP {r.status_code}: {r.text[:120]}")
    result = r.json() if r.status_code == 200 else {}
    check(result.get("redacted_count", 0) > 0,
          f"已脱敏 {result.get('redacted_count')} 处", "redacted_count 为 0")
    output_file_id = result.get("output_file_id", "")

    # 7 内容比对
    step("内容比对")
    r = c.get(f"{BASE}/redaction/{file_id}/compare", headers=H)
    check(r.status_code == 200, "compare 可用", f"compare 失败 HTTP {r.status_code}")
    if r.status_code == 200:
        cmp_data = r.json()
        print("  原文: " + cmp_data.get("original_content", "")[:80].replace("\n", " "))

    # 8 下载成品并验证敏感串消除(Issue #1 验收: 硬断言)
    step("下载成品并验证敏感串消除")
    url = result.get("download_url") or f"{BASE}/files/{file_id}/download?redacted=true"
    r = c.get(url if url.startswith("http") else f"http://127.0.0.1:8000{url}", headers=H)
    check(r.status_code == 200, f"下载 {len(r.content)} bytes", f"下载失败 HTTP {r.status_code}")
    if r.status_code == 200:
        out_doc = fitz.open(stream=r.content, filetype="pdf")
        out_text = "".join(p.get_text() for p in out_doc)
        out_doc.close()
        for s in ("张三", "李四", "13800138000", "110101199003078515"):
            check(s not in out_text, f"{s} 已消除", f"{s} 仍存在于成品!")

    # 9 质量报告
    step("质量报告")
    r = c.get(f"{BASE}/redaction/{file_id}/report", headers=H)
    check(r.status_code == 200, "report 可用", f"report 失败 HTTP {r.status_code}")
    if r.status_code == 200:
        rep = r.json()
        print(f"  实体总数 {rep.get('total_entities')} | 已脱敏 {rep.get('redacted_entities')}")

    print("\n===== E2E 结果 =====")
    if FAILS:
        print(f"失败 {len(FAILS)} 项:")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
