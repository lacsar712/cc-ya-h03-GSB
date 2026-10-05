"""H03 回归：偏航读数沿「入库 → 投影 → 列表/详情/创建响应」全链路保真。

覆盖：
- 投影不抹空、不清零（0.0 是合法读数）；
- 投影半途失败整体作废，绝不产出半空行；
- 列表 / 详情 / 创建接口都返回真实读数；
- observer 只读 403，technician 仍可正常报送；
- worker 认领处理不改写读数。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt

import api
import worker
from projection import LOG_FIELDS, ProjectionError, project_log, project_log_list


# ---------------------------------------------------------------- 内存假库

class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeTx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConn:
    """只实现 api/worker 实际用到的 SQL 形态，数据存内存。"""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def commit(self):
        pass

    def transaction(self):
        return FakeTx(self)

    def execute(self, query, params=None):
        q = " ".join(query.split())
        params = params or ()
        if q.startswith("CREATE TABLE"):
            return FakeResult()
        if "SELECT COUNT(*) AS n FROM yaw_logs" in q:
            return FakeResult([{"n": len(self.store)}])
        if "ORDER BY id DESC" in q:
            rows = [dict(r) for r in self.store.values()]
            rows.sort(key=lambda r: r["id"], reverse=True)
            return FakeResult(rows)
        if "WHERE id = %s" in q and "UPDATE" not in q:
            row = self.store.get(params[0])
            return FakeResult([dict(row)] if row else [])
        if q.startswith("INSERT INTO yaw_logs"):
            head, vals = q.split("VALUES", 1)
            cols = [c.strip() for c in head.split("(", 1)[1].rsplit(")", 1)[0].split(",")]
            lp, rp = vals.index("("), vals.index(")")
            tokens = [t.strip() for t in vals[lp + 1 : rp].split(",")]
            params_iter = iter(params)
            values = []
            for tok in tokens:
                if tok == "%s":
                    values.append(next(params_iter))
                elif tok.upper() == "NULL":
                    values.append(None)
                elif tok.startswith("'") and tok.endswith("'"):
                    values.append(tok[1:-1])
                else:
                    values.append(tok)
            new_id = max(self.store, default=0) + 1
            row = dict(zip(cols, values))
            row.setdefault("id", new_id)
            for col in LOG_FIELDS:
                row.setdefault(col, None)
            self.store[row["id"]] = row
            return FakeResult([dict(row)])
        if q.startswith("SELECT id, turbine_code, yaw_err_deg FROM yaw_logs"):
            for row in self.store.values():
                if row["status"] == "pending":
                    return FakeResult([dict(row)])
            return FakeResult()
        if q.startswith("UPDATE yaw_logs SET status = 'done'"):
            verdict, reason, processed_at, row_id = params
            self.store[row_id].update(
                status="done", verdict=verdict, reason=reason, processed_at=processed_at
            )
            return FakeResult()
        raise AssertionError(f"未预期的 SQL: {q}")


@pytest.fixture
def store(monkeypatch):
    data = {}
    monkeypatch.setattr(api, "connect", lambda: FakeConn(data))
    monkeypatch.setattr(worker, "connect", lambda: FakeConn(data))
    return data


def auth(sub, role):
    token = jwt.encode(
        {
            "sub": sub,
            "role": role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        api.SECRET,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


WRITER = auth("technician", "writer")
READER = auth("observer", "reader")


def as_async(coro_factory):
    return asyncio.run(coro_factory())


# ---------------------------------------------------------------- 投影单测

def full_row(**over):
    row = {
        "id": 1,
        "turbine_code": "W01",
        "yaw_err_deg": 0.4,
        "status": "done",
        "verdict": "合格",
        "reason": "ok",
        "created_by": "technician",
        "created_at": "t",
        "processed_at": "t",
    }
    row.update(over)
    return row


@pytest.mark.parametrize("value", [0.0, 0, 0.4, -1.5, 3.2, -3.2, 12.75])
def test_projection_preserves_readings(value):
    out = project_log(full_row(yaw_err_deg=value))
    assert out["yaw_err_deg"] == value


def test_projection_does_not_mutate_input():
    row = full_row()
    out = project_log(row)
    assert out is not row
    assert row["turbine_code"] == "W01"


def test_projection_missing_field_fails_whole_row():
    row = full_row()
    del row["yaw_err_deg"]
    with pytest.raises(ProjectionError):
        project_log(row)


@pytest.mark.parametrize("bad", [None, "", "0.4", float("nan"), float("inf"), True])
def test_projection_rejects_non_reading(bad):
    with pytest.raises(ProjectionError):
        project_log(full_row(yaw_err_deg=bad))


def test_list_projection_is_atomic_no_half_rows():
    good = full_row(id=1, yaw_err_deg=0.4)
    bad = full_row(id=2, yaw_err_deg=None)
    with pytest.raises(ProjectionError):
        project_log_list([good, bad])
    # 坏行之前已组装好的行也不得作为结果外泄
    with pytest.raises(ProjectionError):
        project_log_list([bad, good])


# ---------------------------------------------------------------- 接口链路

async def _startup_and_list(headers):
    # Quart 的 test_client 不触发 before_serving，种子逻辑显式启动一次。
    await api.startup()
    async with api.app.test_client() as client:
        res = await client.get("/api/logs", headers=headers)
        return res, await res.get_json()


def test_seed_and_list_return_real_readings(store):
    res, body = as_async(lambda: _startup_and_list(WRITER))
    assert res.status_code == 200
    by_code = {r["turbine_code"]: r for r in body}
    assert by_code["W01"]["yaw_err_deg"] == 0.4
    assert by_code["W07"]["yaw_err_deg"] == 3.2


def test_list_zero_reading_is_not_blanked(store):
    store[1] = full_row(id=1, turbine_code="W00", yaw_err_deg=0.0)
    res, body = as_async(lambda: _startup_and_list(WRITER))
    assert res.status_code == 200
    assert body[0]["yaw_err_deg"] == 0.0


def test_list_projection_failure_is_all_or_nothing(store):
    # 一行读数损坏：整批失败，响应是错误对象而不是混着半空行的列表。
    store[1] = full_row(id=1, turbine_code="W09", yaw_err_deg=None)
    store[2] = full_row(id=2, turbine_code="W10", yaw_err_deg=0.4)
    res, body = as_async(lambda: _startup_and_list(WRITER))
    assert res.status_code == 500
    assert isinstance(body, dict) and "detail" in body


async def _detail(headers, log_id):
    async with api.app.test_client() as client:
        res = await client.get(f"/api/logs/{log_id}", headers=headers)
        return res, await res.get_json()


def test_detail_returns_real_reading(store):
    store[7] = full_row(id=7, turbine_code="W07", yaw_err_deg=3.2)
    res, body = as_async(lambda: _detail(WRITER, 7))
    assert res.status_code == 200
    assert body["yaw_err_deg"] == 3.2


def test_detail_zero_reading(store):
    store[3] = full_row(id=3, turbine_code="W03", yaw_err_deg=0.0)
    res, body = as_async(lambda: _detail(WRITER, 3))
    assert res.status_code == 200
    assert body["yaw_err_deg"] == 0.0


def test_detail_missing_404(store):
    res, body = as_async(lambda: _detail(WRITER, 999))
    assert res.status_code == 404
    assert "detail" in body


def test_detail_requires_login(store):
    store[1] = full_row()
    res, _ = as_async(lambda: _detail({}, 1))
    assert res.status_code == 401


async def _create(headers, payload):
    async with api.app.test_client() as client:
        res = await client.post("/api/logs", json=payload, headers=headers)
        return res, await res.get_json()


def test_technician_can_submit_and_response_keeps_reading(store):
    res, body = as_async(
        lambda: _create(WRITER, {"turbine_code": "W12", "yaw_err_deg": -2.25})
    )
    assert res.status_code == 201
    assert body["turbine_code"] == "W12"
    assert body["yaw_err_deg"] == -2.25
    assert body["status"] == "pending"
    assert body["verdict"] is None
    assert list(store.values())[0]["yaw_err_deg"] == -2.25


def test_technician_submit_zero(store):
    res, body = as_async(
        lambda: _create(WRITER, {"turbine_code": "W00", "yaw_err_deg": 0})
    )
    assert res.status_code == 201
    assert body["yaw_err_deg"] == 0.0


def test_observer_cannot_submit(store):
    res, body = as_async(
        lambda: _create(READER, {"turbine_code": "W12", "yaw_err_deg": 1.0})
    )
    assert res.status_code == 403
    assert store == {}


def test_submit_requires_writer_fields(store):
    res, _ = as_async(lambda: _create(WRITER, {"turbine_code": "", "yaw_err_deg": 1}))
    assert res.status_code == 400
    res, _ = as_async(
        lambda: _create(WRITER, {"turbine_code": "W1", "yaw_err_deg": "abc"})
    )
    assert res.status_code == 400


# ---------------------------------------------------------------- worker 链路

def test_worker_processes_without_touching_reading(store):
    conn = FakeConn(store)
    store[1] = full_row(
        id=1,
        turbine_code="W12",
        yaw_err_deg=-2.25,
        status="pending",
        verdict=None,
        reason=None,
        processed_at=None,
    )
    assert worker.claim_and_process(conn) is True
    row = store[1]
    assert row["yaw_err_deg"] == -2.25
    assert row["status"] == "done"
    assert row["verdict"] == "偏航超差"
    assert row["processed_at"] is not None


def test_worker_zero_reading_judged_ok(store):
    conn = FakeConn(store)
    store[1] = full_row(
        id=1, turbine_code="W00", yaw_err_deg=0.0, status="pending",
        verdict=None, reason=None, processed_at=None,
    )
    assert worker.claim_and_process(conn) is True
    assert store[1]["yaw_err_deg"] == 0.0
    assert store[1]["verdict"] == "合格"
