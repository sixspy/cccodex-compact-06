"""重写合并：按计划执行合并任务。

在途单口径必须与现行阈值对齐才允许启动；任务行记下执行时的阈值与
切流凭证号（切流芯片），便于对账时追到具体口径。
"""
from __future__ import annotations

from app.db import get_db
from app.services import settings as settings_svc
from app.services.errors import ServiceError


def list_jobs(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT j.*, p.plan_code, p.target_table FROM rewrite_jobs j "
            "JOIN compact_plans p ON p.id=j.plan_id ORDER BY j.id DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def start_rewrite(
    plan_id: int, operator: str = "", db_path: str | None = None
) -> dict:
    with get_db(db_path) as conn:
        plan = conn.execute(
            "SELECT * FROM compact_plans WHERE id=?", (plan_id,)
        ).fetchone()
        if not plan:
            raise ServiceError("计划不存在", 404)
        if plan["status"] not in ("已发布", "执行中"):
            raise ServiceError("仅已发布计划可启动合并", 409)
        running = conn.execute(
            "SELECT id FROM rewrite_jobs WHERE plan_id=? AND status IN ('待执行','执行中')",
            (plan_id,),
        ).fetchone()
        if running:
            raise ServiceError("已有在途合并任务", 409)
        current_kb = settings_svc.current_threshold(conn)
        latest_code = settings_svc.latest_cutover_code(conn)
        plan_kb = int(plan["threshold_kb"] or 0)
        plan_code = plan["cutover_code"] or ""
        if plan_kb != current_kb or plan_code != latest_code:
            reason = (
                f"在途单口径 {plan_kb} KB / 凭证 {plan_code or '无'} "
                f"与现行 {current_kb} KB / 凭证 {latest_code or '无'} 不一致"
            )
            settings_svc.record_rejection(
                plan_id=plan_id,
                cutover_code=latest_code,
                attempted_kb=plan_kb,
                current_kb=current_kb,
                operator=operator,
                reason=reason,
                db_path=db_path,
            )
            raise ServiceError(
                f"启动被拦：{reason}。切流凭证号 {latest_code or '无'}", 409
            )
        items = conn.execute(
            "SELECT f.* FROM plan_items i JOIN lake_files f ON f.id=i.file_id "
            "WHERE i.plan_id=? AND f.status='已入计划'",
            (plan_id,),
        ).fetchall()
        if not items:
            raise ServiceError("计划下无可合并碎文件", 409)
        total = sum(int(x["size_bytes"]) for x in items)
        cur = conn.execute(
            "INSERT INTO rewrite_jobs"
            "(plan_id, status, merged_bytes, message, threshold_kb, cutover_code, started_at) "
            "VALUES (?, '执行中', 0, '合并进行中', ?, ?, datetime('now'))",
            (plan_id, current_kb, latest_code),
        )
        job_id = cur.lastrowid
        conn.execute(
            "UPDATE compact_plans SET status='执行中' WHERE id=?", (plan_id,)
        )
        # baseline: synchronous success path (出题可改失败半态)
        for row in items:
            conn.execute(
                "UPDATE lake_files SET status='已合并' WHERE id=?", (row["id"],)
            )
        conn.execute(
            "UPDATE rewrite_jobs SET status='成功', merged_bytes=?, message=?, "
            "finished_at=datetime('now') WHERE id=?",
            (total, f"已合并 {len(items)} 个碎文件", job_id),
        )
        conn.execute(
            "UPDATE compact_plans SET status='已完成' WHERE id=?", (plan_id,)
        )
        return dict(
            conn.execute(
                "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )


def fail_open_job(job_id: int, reason: str, db_path: str | None = None) -> dict:
    """预留：标记失败（基线测试与切口）。"""
    with get_db(db_path) as conn:
        job = conn.execute(
            "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
        ).fetchone()
        if not job:
            raise ServiceError("任务不存在", 404)
        conn.execute(
            "UPDATE rewrite_jobs SET status='失败', message=?, finished_at=datetime('now') "
            "WHERE id=?",
            (reason or "合并失败", job_id),
        )
        return dict(
            conn.execute(
                "SELECT * FROM rewrite_jobs WHERE id=?", (job_id,)
            ).fetchone()
        )
