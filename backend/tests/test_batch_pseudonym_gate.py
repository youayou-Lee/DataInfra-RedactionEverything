"""批量×化名门控（preview2.0.0 第一版范围决策）。

同批多文件的化名对齐（T3：job 级统一映射、预览与成品同源）未上线前，
批量任务禁止 replacement_mode=pseudonym——旧路径按文件独立分配化名，
同一人在不同文件会分到不同化名，内部汇报材料自相矛盾。
单文件（单次处理/Playground，非 job 路径）不受此门控影响。
create / update_draft / submit 三入口统一校验：update_draft 是前端提交主路径
的配置写入口（先 PUT config 再 submit），漏掉即绕过。
"""
from __future__ import annotations

import pytest

from app.services import job_management_service as jms
from app.services.job_store import JobStore, JobType


def _store(tmp_path):
    return JobStore(str(tmp_path / "jobs.sqlite3"))


def _create(store, config):
    return jms.create_job(
        store=store, job_type_str="text_batch", title="t",
        config=config, skip_item_review=False, priority=0,
    )


def test_create_job_rejects_pseudonym(tmp_path):
    with pytest.raises(ValueError, match="暂未上线"):
        _create(_store(tmp_path), {"replacement_mode": "pseudonym"})
    with pytest.raises(ValueError, match="暂未上线"):
        _create(_store(tmp_path), {"replacement_mode": " Pseudonym "})


def test_create_job_allows_other_modes(tmp_path):
    for mode in ("structured", "smart", "mask", None):
        config = {} if mode is None else {"replacement_mode": mode}
        row = _create(_store(tmp_path), config)
        assert row["id"]


def test_update_draft_cannot_flip_to_pseudonym(tmp_path):
    """评审 I-1 回归：PUT /jobs/{id} 是前端提交主路径的配置写入口，必须同门控。"""
    store = _store(tmp_path)
    jid = _create(store, {"replacement_mode": "structured"})["id"]
    with pytest.raises(ValueError, match="暂未上线"):
        jms.update_draft(store, jid, {"config": {"replacement_mode": "pseudonym"}})
    with pytest.raises(ValueError, match="暂未上线"):
        jms.update_draft(store, jid, {"replacement_mode": "pseudonym"})


def test_submit_job_rejects_legacy_pseudonym_draft(tmp_path):
    """提交口兜底：门控前建的存量 pseudonym 草稿（直写 store 绕过 create 门控）在提交时拦截。"""
    store = _store(tmp_path)
    jid = store.create_job(
        job_type=JobType.TEXT_BATCH,
        title="legacy", config={"replacement_mode": "pseudonym"}, owner_id="admin",
    )
    store.add_item(jid, "f1")
    with pytest.raises(ValueError, match="暂未上线"):
        jms.submit_job(store, jid)
