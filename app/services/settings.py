"""台参数：压实阈值、切流账册与参数维护。

阈值就是「口径」：碎文件目录（lake_files）与压实计划（compact_plans）在
入库 / 建单时钉住当时阈值。改阈值只走**切流账册**这一条道：

1. 保存阈值时先拿写锁（BEGIN IMMEDIATE），并发仓管排队提交，凭证链式追加，
   不会互相覆盖；
2. 阈值真变化才写一条 ``threshold_cutovers`` 凭证（旧值 → 新值），记下切换依据；
3. 在途计划与在册碎文件同步切到新阈值，并盖上凭证号（切流芯片）；
4. 仍按旧口径发起的挂入会被拦下，并在 ``cutover_rejections`` 留下凭证号。
"""
from __future__ import annotations

from datetime import datetime

from app.db import begin_immediate, get_db
from app.services.errors import ServiceError

THRESHOLD_KEY = "compact_threshold_kb"
# 未完成 / 未作废的计划都算在途，跟着切流走。
IN_FLIGHT_PLAN_STATUSES = ("草稿", "已发布", "执行中")
ACTIVE_FILE_STATUSES = ("碎文件", "已入计划")


def _parse_threshold(raw) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError) as exc:
        raise ServiceError("阈值必须是正整数（单位 KB）") from exc
    if value <= 0:
        raise ServiceError("阈值必须是正整数（单位 KB）")
    return value


def current_threshold(conn) -> int:
    row = conn.execute(
        "SELECT value FROM lab_settings WHERE key=?", (THRESHOLD_KEY,)
    ).fetchone()
    if not row:
        raise ServiceError("台参数缺少压实阈值", 500)
    return int(row["value"])


def latest_cutover(conn) -> dict | None:
    row = conn.execute(
        "SELECT * FROM threshold_cutovers ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def latest_cutover_code(conn) -> str:
    row = latest_cutover(conn)
    return row["cutover_code"] if row else ""


def list_settings(db_path: str | None = None) -> list[dict]:
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
        return [dict(r) for r in rows]


def settings_snapshot(db_path: str | None = None) -> dict:
    """台参数设置屏：参数项 + 现行阈值 + 最近一次切流凭证。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT key, value, label FROM lab_settings ORDER BY key"
        ).fetchall()
        return {
            "items": [dict(r) for r in rows],
            "current_threshold_kb": current_threshold(conn),
            "latest_cutover": latest_cutover(conn),
        }


def _next_cutover_code(conn) -> str:
    day = datetime.now().strftime("%Y%m%d")
    prefix = f"CT-{day}-"
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM threshold_cutovers WHERE cutover_code LIKE ?",
        (prefix + "%",),
    ).fetchone()["c"]
    return f"{prefix}{n + 1:03d}"


def _apply_threshold_cutover(
    conn, old_kb: int, new_kb: int, operator: str, note: str
) -> dict:
    code = _next_cutover_code(conn)
    conn.execute(
        "INSERT INTO threshold_cutovers"
        "(cutover_code, old_threshold_kb, new_threshold_kb, operator, note) "
        "VALUES (?,?,?,?,?)",
        (code, old_kb, new_kb, operator or "", note or ""),
    )
    places = ",".join("?" for _ in IN_FLIGHT_PLAN_STATUSES)
    plans = conn.execute(
        f"UPDATE compact_plans SET threshold_kb=?, cutover_code=? "
        f"WHERE status IN ({places})",
        (new_kb, code, *IN_FLIGHT_PLAN_STATUSES),
    )
    fplaces = ",".join("?" for _ in ACTIVE_FILE_STATUSES)
    files = conn.execute(
        f"UPDATE lake_files SET threshold_kb=?, cutover_code=? "
        f"WHERE status IN ({fplaces})",
        (new_kb, code, *ACTIVE_FILE_STATUSES),
    )
    return {
        "cutover_code": code,
        "old_threshold_kb": old_kb,
        "new_threshold_kb": new_kb,
        "migrated_plans": plans.rowcount,
        "migrated_files": files.rowcount,
    }


def update_setting(
    key: str,
    value: str,
    operator: str = "",
    note: str = "",
    db_path: str | None = None,
) -> dict:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        raise ServiceError("参数键与值不能为空")
    if key == THRESHOLD_KEY:
        return _save_threshold(value, operator, note, db_path)
    with get_db(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM lab_settings WHERE key=?", (key,)
        ).fetchone()
        if not row:
            raise ServiceError("参数不存在", 404)
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?", (value, key)
        )
        item = dict(
            conn.execute(
                "SELECT key, value, label FROM lab_settings WHERE key=?", (key,)
            ).fetchone()
        )
        return {"item": item, "cutover": None}


def _save_threshold(
    raw_value: str, operator: str, note: str, db_path: str | None
) -> dict:
    new_kb = _parse_threshold(raw_value)
    with get_db(db_path) as conn:
        # 写锁：两位仓管同时保存时排队提交；后来者读到前者已提交的新阈值，
        # 凭证按链式追加，不会被覆盖。
        begin_immediate(conn)
        old_kb = current_threshold(conn)
        conn.execute(
            "UPDATE lab_settings SET value=? WHERE key=?",
            (str(new_kb), THRESHOLD_KEY),
        )
        cutover = None
        if new_kb != old_kb:
            cutover = _apply_threshold_cutover(
                conn, old_kb, new_kb, operator, note
            )
        item = dict(
            conn.execute(
                "SELECT key, value, label FROM lab_settings WHERE key=?",
                (THRESHOLD_KEY,),
            ).fetchone()
        )
        return {"item": item, "cutover": cutover}


def list_cutovers(db_path: str | None = None) -> dict:
    """切流账册：凭证流水 + 旧口径挂入拦截记录。"""
    with get_db(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM threshold_cutovers ORDER BY id DESC"
        ).fetchall()
        rejections = conn.execute(
            "SELECT * FROM cutover_rejections ORDER BY id DESC LIMIT 50"
        ).fetchall()
        return {
            "current_threshold_kb": current_threshold(conn),
            "items": [dict(r) for r in rows],
            "rejections": [dict(r) for r in rejections],
        }


def log_rejection(
    conn,
    plan_id: int,
    cutover_code: str,
    attempted_kb: int | None,
    current_kb: int,
    operator: str,
    reason: str,
) -> None:
    conn.execute(
        "INSERT INTO cutover_rejections"
        "(plan_id, cutover_code, attempted_kb, current_kb, operator, reason) "
        "VALUES (?,?,?,?,?,?)",
        (plan_id, cutover_code or "", attempted_kb, current_kb, operator or "", reason),
    )


def record_rejection(
    plan_id: int,
    cutover_code: str,
    attempted_kb: int | None,
    current_kb: int,
    operator: str,
    reason: str,
    db_path: str | None = None,
) -> None:
    """独立事务写拦截凭证。

    挂入 / 启动会被 ServiceError 回滚，凭证不能跟主事务一起丢，
    因此用单独连接提交，保证「拦下必留凭证号」。
    """
    with get_db(db_path) as conn:
        log_rejection(
            conn, plan_id, cutover_code, attempted_kb, current_kb, operator, reason
        )
