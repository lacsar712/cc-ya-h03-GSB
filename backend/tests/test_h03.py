from h03_extra_trap import apply_blank
from h03_map_trap import map_list_payload

def test_blank():
    d = {"yaw_err_deg": 0.4}
    apply_blank(d, "list")
    assert d["yaw_err_deg"] is None
    rows = map_list_payload([{"yaw_err_deg": 0.4}])
    assert rows[0]["yaw_err_deg"] in (0, None)
