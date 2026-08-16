"""配置加载与校验：从 _conf_schema.json 生成默认配置，提供 WebUI/命令写入的类型校验。"""

import json
import os

PLUGIN_VERSION = "0.5.0"
"""插件版本号，与 metadata.yaml 保持一致。"""

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_conf_schema.json"
)

_TYPE_DEFAULTS = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "list": [],
}

_TYPE_MAP = {
    "int": int,
    "float": float,
    "bool": bool,
    "string": str,
    "list": str,
}


def _load_schema() -> dict:
    if not os.path.exists(_SCHEMA_PATH):
        raise RuntimeError(f"缺少插件配置 schema 文件: {_SCHEMA_PATH}")
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        schema = json.load(f)
    for key, meta in schema.items():
        if meta.get("type") not in _TYPE_DEFAULTS:
            raise RuntimeError(f"配置项 {key} 的类型 {meta.get('type')} 不受支持")
    return schema


_SCHEMA = _load_schema()

DEFAULT_CONFIG = {
    key: meta.get("default", _TYPE_DEFAULTS[meta["type"]])
    for key, meta in _SCHEMA.items()
}

TYPE_MAP = {key: _TYPE_MAP[meta["type"]] for key, meta in _SCHEMA.items()}

_LIST_KEYS = tuple(key for key, meta in _SCHEMA.items() if meta["type"] == "list")

# 整数配置业务上限（超出拒绝）；未列出的整数键不设上限
_INT_UPPER_BOUNDS = {
    "rank_page_size": 100,
    "import_col_type": 30,
    "import_col_stat": 30,
    "import_col_name": 30,
    "import_col_xp": 30,
    "import_col_period": 30,
    "import_col_threshold": 30,
    "import_col_band_min": 30,
    "import_col_band_max": 30,
    "import_col_uid": 30,
    "import_col_name_player": 30,
    "import_col_team": 30,
    "import_col_match_date": 30,
    "import_col_match_uid": 30,
    "import_max_rows": 1_000_000,
    "import_batch_size": 100_000,
    "import_max_file_size_mb": 1024,
    "import_max_files": 10_000,
}

# 整数配置业务下限
_INT_LOWER_BOUNDS = {
    "rank_page_size": 1,
    "import_col_type": 0,
    "import_col_stat": 0,
    "import_col_name": 0,
    "import_col_xp": 0,
    "import_col_period": 0,
    "import_col_threshold": 0,
    "import_col_band_min": 0,
    "import_col_band_max": 0,
    "import_col_uid": 0,
    "import_col_name_player": 0,
    "import_col_team": 0,
    "import_col_match_date": 0,
    "import_col_match_uid": 0,
    "import_max_rows": 1,
    "import_batch_size": 1,
    "import_max_file_size_mb": 1,
    "import_max_files": 1,
}

# 浮点配置的允许区间（闭区间）
_FLOAT_RANGES = {
    "default_level_xp": (0.1, 1_000_000.0),
}


def parse_group_list(raw):
    """将配置中的列表解析为列表，兼容 JSON 数组或逗号分隔文本。"""
    if isinstance(raw, (list, tuple)):
        return [str(g) for g in raw if str(g).strip()]
    if not isinstance(raw, str):
        return []
    s = raw.strip()
    if not s:
        return []
    try:
        data = json.loads(s)
        if isinstance(data, list):
            return [str(g) for g in data if str(g).strip()]
    except json.JSONDecodeError:
        pass
    return [g.strip() for g in s.split(",") if g.strip()]


def validate_and_cast(key: str, raw: str):
    """校验并转换管理员通过设置命令传入的配置值。"""
    if key not in DEFAULT_CONFIG:
        raise ValueError(f"未知配置项: {key}")

    if key in _LIST_KEYS:
        return parse_group_list(raw)

    t = TYPE_MAP.get(key, str)
    if t is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
        raise ValueError(f"配置 {key} 需为布尔值 (true/false/1/0)")
    if t is int:
        try:
            parsed = int(raw.strip())
        except ValueError:
            raise ValueError(f"配置 {key} 需为整数")
        lower = _INT_LOWER_BOUNDS.get(key)
        if lower is not None and parsed < lower:
            raise ValueError(f"配置 {key} 不能小于 {lower}")
        upper = _INT_UPPER_BOUNDS.get(key)
        if upper is not None and parsed > upper:
            raise ValueError(f"配置 {key} 不能大于 {upper}")
        return parsed
    if t is float:
        try:
            parsed = float(raw.strip())
        except ValueError:
            raise ValueError(f"配置 {key} 需为数字")
        lo, hi = _FLOAT_RANGES.get(key, (None, None))
        if lo is not None and parsed < lo:
            raise ValueError(f"配置 {key} 不能小于 {lo}")
        if hi is not None and parsed > hi:
            raise ValueError(f"配置 {key} 不能大于 {hi}")
        return parsed
    return raw.strip()
