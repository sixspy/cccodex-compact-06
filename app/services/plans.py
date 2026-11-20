"""压实计划：创建、发布、挂文件。

计划在建单时钉住当时的阈值与切流凭证号（切流芯片）。挂入碎文件时，
服务端先核**计划口径**与**现行阈值 / 最近切流凭证**是否对齐；偏了直接挡下，
并把这次旧口径尝试记进切流账册（带凭证号）。
"""
from __future__ import annotations

from app.db import get_db
from app.services import settings as settings_svc
from app.services.errors import ServiceError

ATTACHABLE_STATUSES = ("草稿", "已发布")


def _plan_payload(row) -> dict:
    return dict(row)


def list_plans(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT p.*, "
            "(SELECT COUNT(*) FROM plan_items i WHERE i.plan_id=p.id) AS item_count, "
            "(SELECT COALESCE(SUM(f.size_bytes),0) FROM plan_items i "
            " JOIN lake_files f ON f.id=i.file_id WHERE i.plan_id=p.id) AS plan_bytes "
            "FROM compact_plans p ORDER BY p.id"
        ).fetchall()
        current_kb = settings_svc.current_threshold(conn)
        latest_code = settings_svc.latest_cutover_code(conn)
        out = []
        for r in rows:
            item = dict(r)
            item["current_threshold_kb"] = current_kb
            item["threshold_in_sync"] = (
                int(item.get("threshold_kb") or 0) == current_kb
                and (item.get("cutover_code") or "") == latest_code
            )
            out.append(item)
        return out


def create_plan(
    plan_code: str,
    title: str,
    target_table: str,
    note: str = "",
    db_path: str | None = None,
) -> dict:
    plan_code = (plan_code or "").strip()
    title = (title or "").strip()
    target_table = (target_table or "").strip()
    if not plan_code or not title or not target_table:
        raise ServiceError("计划编号、标题、目标表必填")
    with get_db(db_path) as conn:
        current_kb = settings_svc.current_threshold(conn)
        cutover_code = settings_svc.latest_cutover_code(conn)
        try:
            cur = conn.execute(
                "INSERT INTO compact_plans"
                "(plan_code, title, status, target_table, note, threshold_kb, cutover_code) "
                "VALUES (?,?, '草稿', ?, ?, ?, ?)",
                (plan_code, title, target_table, note or "", current_kb, cutover_code),
            )
        except Exception as exc:
            raise ServiceError(f"创建计划失败：{exc}") from exc
        return dict(
            conn.execute(
                "SELECT * FROM compact_plans WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        )


def _check_threshold_coherence(
    conn, plan, client_kb, operator: str, db_path: str | None = None
) -> None:
    """现行阈值与在途单口径必须对齐；偏了就挡挂入并留切流凭证号。"""
    current_kb = settings_svc.current_threshold(conn)
    latest_code = settings_svc.latest_cutover_code(conn)
    plan_kb = int(plan["threshold_kb"] or 0)
    plan_code = plan["cutover_code"] or ""

    reasons = []
    if plan_kb != current_kb:
        reasons.append(
            f"在途单口径 {plan_kb} KB 与现行阈值 {current_kb} KB 不一致"
        )
    if plan_code != latest_code:
        reasons.append(
            f"在途单切流凭证 {plan_code or '无'} 已过期"
            f"（现行 {latest_code or '无'}）"
        )
    if client_kb is not None and int(client_kb) != current_kb:
        reasons.append(
            f"页面口径 {int(client_kb)} KB 非现行阈值 {current_kb} KB"
        )
    if not reasons:
        return
    reason = "；".join(reasons)
    # 独立事务落凭证：主事务随后会被 ServiceError 回滚，凭证不能跟着丢。
    settings_svc.record_rejection(
        plan_id=plan["id"],
        cutover_code=latest_code,
        attempted_kb=client_kb if client_kb is not None else plan_kb,
        current_kb=current_kb,
        operator=operator,
        reason=reason,
        db_path=db_path,
    )
    raise ServiceError(
        f"挂入被拦：{reason}。请按现行阈值重挂；切流凭证号 {latest_code or '无'}",
        409,
    )


def attach_loose_files(
    plan_id: int,
    threshold_kb: int | None = None,
    operator: str = "",
    db_path: str | None = None,
) -> dict:
    with get_db(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        if plan["status"] not in ATTACHABLE_STATUSES:
            raise ServiceError("当前计划状态不可挂文件", 409)
        _check_threshold_coherence(conn, plan, threshold_kb, operator, db_path)
        loose = conn.execute(
            "SELECT id FROM lake_files WHERE table_name=? AND status='碎文件'",
            (plan["target_table"],),
        ).fetchall()
        attached = 0
        for row in loose:
            exists = conn.execute(
                "SELECT 1 FROM plan_items WHERE plan_id=? AND file_id=?",
                (plan_id, row["id"]),
            ).fetchone()
            if exists:
                continue
            conn.execute(
                "INSERT INTO plan_items(plan_id, file_id) VALUES (?,?)",
                (plan_id, row["id"]),
            )
            conn.execute(
                "UPDATE lake_files SET status='已入计划' WHERE id=?", (row["id"],)
            )
            attached += 1
        return {"plan_id": plan_id, "attached": attached}


def publish_plan(plan_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        if plan["status"] != "草稿":
            raise ServiceError("仅草稿可发布", 409)
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM plan_items WHERE plan_id=?", (plan_id,)
        ).fetchone()["c"]
        if n <= 0:
            raise ServiceError("空计划不可发布", 409)
        conn.execute(
            "UPDATE compact_plans SET status='已发布' WHERE id=?", (plan_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
            ).fetchone()
        )


def plan_detail(plan_id: int, db_path: str | None = None) -> dict:
    with get_db(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        items = conn.execute(
            "SELECT f.* FROM plan_items i JOIN lake_files f ON f.id=i.file_id "
            "WHERE i.plan_id=? ORDER BY f.id",
            (plan_id,),
        ).fetchall()
        current_kb = settings_svc.current_threshold(conn)
        latest = settings_svc.latest_cutover(conn)
        detail = _plan_payload(plan)
        detail["current_threshold_kb"] = current_kb
        detail["threshold_in_sync"] = (
            int(detail["threshold_kb"] or 0) == current_kb
            and (detail["cutover_code"] or "")
            == (latest["cutover_code"] if latest else "")
        )
        return {
            "plan": detail,
            "items": [dict(x) for x in items],
            "latest_cutover": latest,
        }
