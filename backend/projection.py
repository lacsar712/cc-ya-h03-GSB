"""入库行 → 对外读视图的唯一投影。

数据库行经此模块投影到「列表 / 详情 / 创建响应」三个出口，规则：

- 保真：``yaw_err_deg``（偏航读数，度）原样透传，不抹空、不清零、
  不四舍五入。0.0° 是合法读数，必须以 0 返回。
- 原子：整批投影要么全部成功，要么整体抛 ``ProjectionError``，
  绝不返回/残留部分投影出的半空行；单行也是先在新字典上组装，
  成功后才整体返回，调用方拿不到半成品。
- 纯函数：不就地修改入参，不触库、不触网，半途失败不留副作用。
"""

# 对外读视图字段，顺序即投影顺序。
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


class ProjectionError(RuntimeError):
    """投影无法产出完整读视图（缺字段/读数非法等）。上层应整体失败，不得降级成半空行。"""


def _is_real_number(value) -> bool:
    # bool 是 int 的子类，显式排除；NaN/Inf 也不允许出现在读视图里。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return False
    return True


def project_log(row: dict) -> dict:
    """把单行入库记录投影成对外读视图。失败抛 ProjectionError，不留半成品。"""
    if not isinstance(row, dict):
        raise ProjectionError(f"投影行必须是字典，收到 {type(row).__name__}")
    missing = [f for f in LOG_FIELDS if f not in row]
    if missing:
        raise ProjectionError(f"投影行缺少字段: {', '.join(missing)}")

    # 先在全新字典上组装，校验通过后才返回，调用方永远拿不到半投影对象。
    out = {f: row[f] for f in LOG_FIELDS}
    if not _is_real_number(out["yaw_err_deg"]):
        raise ProjectionError(
            f"记录 id={out.get('id')} 的偏航读数非法: {out['yaw_err_deg']!r}"
        )
    return out


def project_log_list(rows) -> list:
    """整批投影。任意一行失败即整体抛 ProjectionError，绝不返回部分行。"""
    projected: list = []
    for index, row in enumerate(rows):
        try:
            projected.append(project_log(row))
        except ProjectionError as exc:
            raise ProjectionError(f"列表投影在第 {index} 行失败，整批作废: {exc}") from exc
    return projected
