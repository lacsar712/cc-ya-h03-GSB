"""接口链路测试：GET /api/logs、POST /api/logs 的投影与鉴权。

用内存假连接替代 PostgreSQL，不依赖数据库；需在装好 quart 等依赖的环境运行：
    cd backend && pytest -q
"""

import asyncio
from datetime import datetime, timezone

import api


NOW = datetime(2026, 10, 5, tzinfo=timezone.utc)


class FakeResult:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._one


class FakeConn:
    """最小连接：记录插入，SELECT 列表返回预置行，COUNT 让 seed 跳过。"""

    def __init__(self, list_rows):
        self._list_rows = list_rows
        self.inserted = []

    def execute(self, sql, params=None, *a, **k):
        s = " ".join(sql.split())
        if s.startswith("SELECT COUNT(*)"):
            return FakeResult(one={"n": 99})  # 非空 → seed_if_empty 直接返回
        if "INSERT INTO yaw_logs" in s:
            code, yaw, username, created_at = params
            row = {
                "id": 100 + len(self.inserted) + 1,
                "turbine_code": code,
                "yaw_err_deg": yaw,
                "status": "pending",
                "verdict": None,
                "reason": None,
                "created_by": username,
                "created_at": created_at,
                "processed_at": None,
            }
            self.inserted.append(row)
            return FakeResult(one=dict(row))
        if "FROM yaw_logs" in s:
            return FakeResult(rows=list(self._list_rows))
        return FakeResult(rows=[])

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _rows():
    return [
        {"id": 1, "turbine_code": "W01", "yaw_err_deg": 0.4, "status": "done",
         "verdict": "合格", "reason": "在阈值内", "created_by": "technician",
         "created_at": NOW, "processed_at": NOW},
        {"id": 2, "turbine_code": "W07", "yaw_err_deg": 3.2, "status": "done",
         "verdict": "偏航超差", "reason": "超差", "created_by": "technician",
         "created_at": NOW, "processed_at": NOW},
        {"id": 3, "turbine_code": "W09", "yaw_err_deg": 0.0, "status": "done",
         "verdict": "合格", "reason": "恰为零", "created_by": "technician",
         "created_at": NOW, "processed_at": NOW},
        {"id": 4, "turbine_code": "W10", "yaw_err_deg": -2.5, "status": "done",
         "verdict": "偏航超差", "reason": "负向超差", "created_by": "technician",
         "created_at": NOW, "processed_at": NOW},
    ]


def _patch_db(monkeypatch, rows):
    conn = FakeConn(rows)
    monkeypatch.setattr(api, "connect", lambda *a, **k: conn)
    return conn


def _run(coro):
    return asyncio.run(coro)


async def _login(client, username, password):
    res = await client.post(
        "/api/auth/login", json={"username": username, "password": password}
    )
    assert res.status_code == 200
    data = await res.get_json()
    return {"Authorization": f"Bearer {data['access_token']}"}


def test_requires_login(monkeypatch):
    _patch_db(monkeypatch, _rows())

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            res = await client.get("/api/logs")
            assert res.status_code == 401

    _run(go())


def test_list_preserves_readings_and_is_stable_on_refresh(monkeypatch):
    _patch_db(monkeypatch, _rows())

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            headers = await _login(client, "observer", "obs123456")
            first = await client.get("/api/logs", headers=headers)
            assert first.status_code == 200
            data1 = await first.get_json()
            readings = {r["id"]: r["yaw_err_deg"] for r in data1}
            # 读数不得被置空/置零，真实 0° 保留
            assert readings == {1: 0.4, 2: 3.2, 3: 0.0, 4: -2.5}
            assert all(r["turbine_code"] for r in data1)

            # 换页/刷新后读数保持一致，不得无故变空或变零
            second = await client.get("/api/logs", headers=headers)
            data2 = await second.get_json()
            assert {r["id"]: r["yaw_err_deg"] for r in data2} == readings

    _run(go())


def test_list_projection_failure_returns_no_half_rows(monkeypatch):
    rows = _rows()
    rows.append({"id": 5, "turbine_code": "W11", "yaw_err_deg": None,
                 "status": "done", "verdict": None, "reason": None,
                 "created_by": "technician", "created_at": NOW, "processed_at": NOW})
    _patch_db(monkeypatch, rows)

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            headers = await _login(client, "observer", "obs123456")
            res = await client.get("/api/logs", headers=headers)
            # 半途失败：整批拒绝，不返回任何半空行（错误对象而非列表）
            assert res.status_code == 500
            data = await res.get_json()
            assert isinstance(data, dict) and "detail" in data

    _run(go())


def test_observer_cannot_submit(monkeypatch):
    _patch_db(monkeypatch, _rows())

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            headers = await _login(client, "observer", "obs123456")
            res = await client.post(
                "/api/logs", headers=headers,
                json={"turbine_code": "W20", "yaw_err_deg": 1.1},
            )
            assert res.status_code == 403

    _run(go())


def test_technician_submit_persists_and_returns_reading(monkeypatch):
    conn = _patch_db(monkeypatch, _rows())

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            headers = await _login(client, "technician", "tech123456")

            res = await client.post(
                "/api/logs", headers=headers,
                json={"turbine_code": "W21", "yaw_err_deg": -0.8},
            )
            assert res.status_code == 201
            data = await res.get_json()
            assert data["yaw_err_deg"] == -0.8  # 报送读数原样回显
            assert data["status"] == "pending"

            # 真实 0° 也必须能正常报送
            res0 = await client.post(
                "/api/logs", headers=headers,
                json={"turbine_code": "W22", "yaw_err_deg": 0},
            )
            assert res0.status_code == 201
            data0 = await res0.get_json()
            assert data0["yaw_err_deg"] == 0.0

            assert [r["yaw_err_deg"] for r in conn.inserted] == [-0.8, 0.0]

    _run(go())


def test_submit_rejects_non_numeric(monkeypatch):
    _patch_db(monkeypatch, _rows())

    async def go():
        async with api.app.test_app() as tapp:
            client = tapp.test_client()
            headers = await _login(client, "technician", "tech123456")
            res = await client.post(
                "/api/logs", headers=headers,
                json={"turbine_code": "W23", "yaw_err_deg": "abc"},
            )
            assert res.status_code == 400

    _run(go())
