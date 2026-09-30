from h03_extra_trap import apply_blank
from h03_queue_blank import project_surfaces

def map_list_payload(items: list) -> list:
    return [project_surfaces(apply_blank(dict(it), "list")) for it in items]

def expose_list(rows: list) -> list:
    return map_list_payload(rows)
