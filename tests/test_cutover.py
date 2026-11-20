import pytest

from app.services import plans, rewrite, settings
from app.services.errors import ServiceError


def _threshold(db):
    return settings.settings_snapshot(db_path=db)["current_threshold_kb"]


def test_threshold_change_writes_ledger_and_cutover_chip(db):
    before = _threshold(db)
    assert before == 256
    p = plans.create_plan("CP-CUT-1", "切流计划", "dw_users", db_path=db)
    assert p["threshold_kb"] == 256

    res = settings.update_setting(
        "compact_threshold_kb", "512", operator="admin", note="口径收紧", db_path=db
    )
    cut = res["cutover"]
    assert cut is not None
    assert cut["old_threshold_kb"] == 256
    assert cut["new_threshold_kb"] == 512
    assert cut["migrated_plans"] >= 1
    assert _threshold(db) == 512

    # 在途单与在册碎件同步切口径，并盖上凭证号
    detail = plans.plan_detail(p["id"], db_path=db)
    assert detail["plan"]["threshold_kb"] == 512
    assert detail["plan"]["cutover_code"] == cut["cutover_code"]
    assert detail["plan"]["threshold_in_sync"] is True

    ledger = settings.list_cutovers(db_path=db)
    assert ledger["items"][0]["cutover_code"] == cut["cutover_code"]


def test_unchanged_threshold_writes_no_ledger(db):
    res = settings.update_setting(
        "compact_threshold_kb", "256", operator="admin", db_path=db
    )
    assert res["cutover"] is None
    assert settings.list_cutovers(db_path=db)["items"] == []


def test_old_caliber_attach_blocked_with_credential(db):
    p = plans.create_plan("CP-CUT-2", "旧口径计划", "dw_users", db_path=db)
    cut = settings.update_setting(
        "compact_threshold_kb", "512", operator="admin", db_path=db
    )["cutover"]

    # 在途单已被切到新口径，但页面拿着旧口径 256 来挂入 -> 拦下
    with pytest.raises(ServiceError) as exc:
        plans.attach_loose_files(
            p["id"], threshold_kb=256, operator="keeper-b", db_path=db
        )
    assert "挂入被拦" in exc.value.message
    assert cut["cutover_code"] in exc.value.message

    ledger = settings.list_cutovers(db_path=db)
    assert ledger["rejections"], "旧口径挂入必须留下拦截凭证"
    rej = ledger["rejections"][0]
    assert rej["cutover_code"] == cut["cutover_code"]
    assert rej["attempted_kb"] == 256
    assert rej["current_kb"] == 512

    # 按现行阈值再挂 -> 放行
    ok = plans.attach_loose_files(
        p["id"], threshold_kb=512, operator="keeper-b", db_path=db
    )
    assert ok["attached"] >= 1


def test_stale_plan_blocked_even_without_client_kb(db):
    p = plans.create_plan("CP-CUT-3", "漂移计划", "dw_payments", db_path=db)
    settings.update_setting(
        "compact_threshold_kb", "512", operator="admin", db_path=db
    )
    # 人为把计划口径改回旧值，模拟「在途单还按旧阈值挂件」
    from app.db import get_db

    with get_db(db) as conn:
        conn.execute(
            "UPDATE compact_plans SET threshold_kb=256, cutover_code='' WHERE id=?",
            (p["id"],),
        )
    with pytest.raises(ServiceError) as exc:
        plans.attach_loose_files(p["id"], operator="keeper-c", db_path=db)
    assert "挂入被拦" in exc.value.message


def test_concurrent_cutovers_do_not_overwrite_credential(db):
    import threading

    settings.update_setting("compact_threshold_kb", "256", db_path=db)
    errors = []

    def worker(value):
        try:
            settings.update_setting(
                "compact_threshold_kb", str(value), operator=f"keeper-{value}",
                db_path=db,
            )
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(v,)) for v in (512, 640, 768)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    ledger = settings.list_cutovers(db_path=db)
    # 每次真实变化各留一条凭证，不丢不覆盖
    assert len(ledger["items"]) == 3
    codes = [r["cutover_code"] for r in ledger["items"]]
    assert len(set(codes)) == 3
    # 串行化后凭证应成链：任一凭证的新值等于上一条的旧值（按写入顺序）
    chain = sorted(ledger["items"], key=lambda r: r["id"])
    for prev, cur in zip(chain, chain[1:]):
        assert cur["old_threshold_kb"] == prev["new_threshold_kb"]
    # 最终阈值是最后提交的那一档，且在三次写入之内
    assert _threshold(db) in (512, 640, 768)


def test_rewrite_blocked_on_stale_caliber(db):
    p = plans.create_plan("CP-CUT-4", "合并切流计划", "dw_payments", db_path=db)
    plans.attach_loose_files(p["id"], db_path=db)
    plans.publish_plan(p["id"], db_path=db)
    settings.update_setting(
        "compact_threshold_kb", "512", operator="admin", db_path=db
    )
    from app.db import get_db

    with get_db(db) as conn:
        conn.execute(
            "UPDATE compact_plans SET threshold_kb=256, cutover_code='' WHERE id=?",
            (p["id"],),
        )
    with pytest.raises(ServiceError) as exc:
        rewrite.start_rewrite(p["id"], operator="keeper", db_path=db)
    assert "启动被拦" in exc.value.message
