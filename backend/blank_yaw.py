"""Blank yaw_err_deg on list / create projections."""

def blank_value(_v):
    return None

def blank_list_item(item: dict) -> None:
    item["yaw_err_deg"] = blank_value(item.get("yaw_err_deg"))

def blank_create_item(item: dict) -> None:
    item["yaw_err_deg"] = blank_value(item.get("yaw_err_deg"))

def should_blank_path(path: str) -> bool:
    return path in {"list", "create"}
