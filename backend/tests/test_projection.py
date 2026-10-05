"""投影层单元测试（仅依赖标准库，pytest 或直接运行均可）。"""

import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from projection import LOG_FIELDS, serialize_log, serialize_logs


def _base(**over):
    row = {
        "id": 1,
        "turbine_code": "W01",
        "yaw_err_deg": 0.4,
        "status": "done",
        "verdict": "合格",
        "reason": "在阈值内",
        "created_by": "technician",
        "created_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
        "processed_at": datetime(2026, 10, 5, tzinfo=timezone.utc),
    }
    row.update(over)
    return row


def test_preserves_positive_negative_and_zero():
    assert serialize_log(_base(yaw_err_deg=0.4))["yaw_err_deg"] == 0.4
    assert serialize_log(_base(yaw_err_deg=-2.7))["yaw_err_deg"] == -2.7
    # 真实的 0° 必须原样保留，不得变空
    assert serialize_log(_base(yaw_err_deg=0.0))["yaw_err_deg"] == 0.0
    assert serialize_log(_base(yaw_err_deg=0))["yaw_err_deg"] == 0.0


def test_datetime_serialized_to_iso():
    out = serialize_log(_base())
    assert isinstance(out["created_at"], str) and out["created_at"].startswith("2026-10-05")
    assert isinstance(out["processed_at"], str)


def test_exact_field_set():
    out = serialize_log(_base())
    assert set(out.keys()) == set(LOG_FIELDS)


def test_rejects_missing_reading():
    for bad in (None,):
        try:
            serialize_log(_base(yaw_err_deg=bad))
        except ValueError:
            pass
        else:
            raise AssertionError("None 读数必须被拒绝")


def test_rejects_non_finite_and_bad_types():
    for bad in (float("nan"), float("inf"), float("-inf"), "0.4", True, b"0.4"):
        try:
            serialize_log(_base(yaw_err_deg=bad))
        except ValueError:
            continue
        raise AssertionError(f"非法读数未被拒绝: {bad!r}")
    assert math.isfinite(serialize_log(_base())["yaw_err_deg"])


def test_rejects_missing_column():
    row = _base()
    del row["yaw_err_deg"]
    try:
        serialize_log(row)
    except ValueError:
        pass
    else:
        raise AssertionError("缺列必须被拒绝")


def test_batch_is_all_or_nothing():
    good = _base(id=1, yaw_err_deg=0.4)
    bad = _base(id=2, yaw_err_deg=None)
    try:
        serialize_logs([good, bad])
    except ValueError:
        pass
    else:
        raise AssertionError("批次中任一坏行必须整批失败，不得留下半空行")
    # 全好时整批返回且读数一致
    out = serialize_logs([_base(id=1, yaw_err_deg=0.4), _base(id=2, yaw_err_deg=3.2)])
    assert [r["yaw_err_deg"] for r in out] == [0.4, 3.2]


def test_does_not_mutate_source():
    row = _base(yaw_err_deg=1.25)
    serialize_log(row)
    assert row["yaw_err_deg"] == 1.25
    assert isinstance(row["created_at"], datetime)


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS {t.__name__}")
    print(f"{len(tests)} projection tests passed")


if __name__ == "__main__":
    _run_all()
