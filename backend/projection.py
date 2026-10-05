"""入库行 → 对外结构的统一投影。

只读投影，绝不修改数据库行；对 yaw_err_deg 做严格校验，
任一记录缺列/为空/非有限数即整批拒绝（不返回任何半空行）。
"""

import math

# 对外暴露的列（与列表、创建接口返回一致）
LOG_FIELDS = (
    "id",
    "turbine_code",
    "yaw_err_deg",
    "status",
    "verdict",
    "reason",
    "created_by",
    "created_at",
    "processed_at",
)


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def serialize_log(row: dict) -> dict:
    """投影单条记录。读数缺失或非法时抛 ValueError，绝不产出半空行。"""
    if not isinstance(row, dict):
        raise ValueError("日志记录格式非法")

    missing = [k for k in LOG_FIELDS if k not in row]
    if missing:
        raise ValueError(f"日志记录缺少字段: {', '.join(missing)}")

    reading = row["yaw_err_deg"]
    # 入库层 yaw_err_deg 为 NOT NULL；None/NaN/inf 一律视为投影失败
    if reading is None or not isinstance(reading, (int, float)) or isinstance(reading, bool):
        raise ValueError("偏航读数缺失或非法")
    if not math.isfinite(float(reading)):
        raise ValueError("偏航读数非有限数")

    return {
        "id": row["id"],
        "turbine_code": row["turbine_code"],
        "yaw_err_deg": float(reading),
        "status": row["status"],
        "verdict": row["verdict"],
        "reason": row["reason"],
        "created_by": row["created_by"],
        "created_at": _iso(row["created_at"]),
        "processed_at": _iso(row["processed_at"]),
    }


def serialize_logs(rows) -> list:
    """整批投影：任一条失败即整体抛错，不留半批/半空行。"""
    return [serialize_log(r) for r in rows]
