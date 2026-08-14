"""规则解析与校验：将 JSON / CSV / Excel 规则源解析为规范结构。

规范结构:
    {
        "stats": {"goal": {"name": "进球", "xp": 10}, ...},
        "milestones": [{"stat": "goal", "period": "period", "threshold": 10, "xp": 50}, ...],
        "level_xp": 100
    }
"""

import json

from ..utils.security import sanitize_text, sanitize_uid

VALID_PERIODS = ("period", "career")
"""period=成长期内数据值累计；career=生涯数据值累计。"""


class RuleError(ValueError):
    """规则内容非法。"""


def _pos_num(raw, field: str) -> float:
    try:
        v = float(str(raw).strip())
    except (ValueError, TypeError):
        raise RuleError(f"{field} 需为数字: {raw}")
    if v <= 0:
        raise RuleError(f"{field} 需为正数: {raw}")
    return v


def _pos_int(raw, field: str) -> int:
    v = _pos_num(raw, field)
    if v != int(v):
        raise RuleError(f"{field} 需为整数: {raw}")
    return int(v)


def parse_rule_json(text: str, default_level_xp: int) -> dict:
    """解析 JSON 规则文本并校验。"""
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        raise RuleError("JSON 解析失败")
    if not isinstance(data, dict):
        raise RuleError("规则 JSON 需为对象")
    return normalize_rule(data, default_level_xp)


def normalize_rule(data: dict, default_level_xp: int) -> dict:
    """校验并规范化规则结构，补齐 level_xp 默认值。"""
    stats_raw = data.get("stats")
    if not isinstance(stats_raw, dict) or not stats_raw:
        raise RuleError("stats 需为非空对象（数据项 → {name, xp}）")
    stats = {}
    for key, meta in stats_raw.items():
        k = sanitize_uid(str(key))
        if not k:
            raise RuleError(f"数据项键非法: {key}")
        if isinstance(meta, (int, float)):
            # 简写：{"goal": 10} 等价于 {"goal": {"name": "goal", "xp": 10}}
            name = k
            xp = _pos_int(meta, f"数据项 {k} 的单位经验 xp")
        elif isinstance(meta, dict):
            name = sanitize_text(str(meta.get("name", "")))
            if not name:
                raise RuleError(f"数据项 {k} 缺少显示名 name")
            xp = _pos_int(meta.get("xp"), f"数据项 {k} 的单位经验 xp")
        else:
            raise RuleError(f"数据项 {k} 需为对象 {{name, xp}} 或数字")
        if k in stats:
            raise RuleError(f"数据项键重复: {k}")
        stats[k] = {"name": name, "xp": xp}

    milestones = []
    seen = set()
    m_raw = data.get("milestones", [])
    if not isinstance(m_raw, list):
        raise RuleError("milestones 需为数组")
    for i, m in enumerate(m_raw):
        if not isinstance(m, dict):
            raise RuleError(f"里程碑第 {i+1} 条需为对象")
        stat = sanitize_uid(str(m.get("stat", "")))
        if not stat:
            raise RuleError(f"里程碑第 {i+1} 条缺少数据项 stat")
        if stat not in stats:
            raise RuleError(f"里程碑第 {i+1} 条的数据项 {stat} 未在 stats 中定义")
        period = str(m.get("period", "period")).strip().lower()
        if period not in VALID_PERIODS:
            raise RuleError(f"里程碑第 {i+1} 条的 period 需为 period 或 career: {period}")
        threshold = _pos_num(m.get("threshold"), f"里程碑第 {i+1} 条的阈值 threshold")
        xp = _pos_int(m.get("xp"), f"里程碑第 {i+1} 条的奖励经验 xp")
        dedup_key = (stat, period, threshold)
        if dedup_key in seen:
            raise RuleError(f"里程碑重复定义: {stat}/{period}/{threshold}")
        seen.add(dedup_key)
        milestones.append(
            {"stat": stat, "period": period, "threshold": threshold, "xp": xp}
        )

    level_xp = data.get("level_xp")
    if level_xp is None:
        level_xp = default_level_xp
    else:
        level_xp = _pos_int(level_xp, "level_xp")

    return {"stats": stats, "milestones": milestones, "level_xp": level_xp}


def parse_rule_table(rows: list, cfg: dict, default_level_xp: int) -> dict:
    """解析 CSV/Excel 规则表为规范结构。

    列位（1 起，0 表示无此列）由 cfg 提供：
      import_col_type / import_col_stat / import_col_name /
      import_col_xp / import_col_period / import_col_threshold
    行类型：stat（数据项）、milestone（里程碑）、level（每级经验）。

    无类型列的文件（首列直接是数据项键）时，其余列位整体左移一列：
    类型列位置即 stat 列。类型列缺失或值非法即视为无类型列布局。
    """
    col_type = int(cfg.get("import_col_type", 1) or 0)
    col_stat = int(cfg.get("import_col_stat", 2) or 0)
    col_name = int(cfg.get("import_col_name", 3) or 0)
    col_xp = int(cfg.get("import_col_xp", 4) or 0)
    col_period = int(cfg.get("import_col_period", 5) or 0)
    col_threshold = int(cfg.get("import_col_threshold", 6) or 0)

    def cell(row, col: int) -> str:
        if col <= 0 or col > len(row):
            return ""
        return str(row[col - 1]).strip()

    # 跳过表头行（首行首列为 type/类型 时视为表头）
    start = 0
    if rows and rows[0] and str(rows[0][0]).strip().lower() in ("type", "类型"):
        start = 1

    stat_rows = []
    milestone_rows = []
    level_rows = []
    for idx, row in enumerate(rows[start:], start=start + 1):
        if not row or not any(str(c).strip() for c in row):
            continue
        rtype = cell(row, col_type).strip().lower()
        has_type = rtype in ("stat", "milestone", "level")
        if has_type:
            c_stat, c_name = col_stat, col_name
            c_xp, c_period, c_thr = col_xp, col_period, col_threshold
        else:
            # 无类型列布局：其余列位整体左移一列，类型列位置即 stat 列
            off = 1 if col_type > 0 else 0
            c_stat = col_type or 1
            c_name = max(0, col_name - off)
            c_xp = max(0, col_xp - off)
            c_period = max(0, col_period - off)
            c_thr = max(0, col_threshold - off)
            rtype = ""
        if not rtype:
            # 自动推断：有 period+threshold → milestone；有 name → stat；否则 level
            if c_period > 0 and c_thr > 0 and cell(row, c_period) and cell(row, c_thr):
                rtype = "milestone"
            elif c_name > 0 and cell(row, c_name):
                rtype = "stat"
            else:
                rtype = "level"
        if rtype == "stat":
            stat_rows.append((idx, row, c_stat, c_name, c_xp))
        elif rtype == "milestone":
            milestone_rows.append((idx, row, c_stat, c_xp, c_period, c_thr))
        elif rtype == "level":
            level_rows.append((idx, row, c_xp))
        else:
            raise RuleError(f"第{idx}行: 未知行类型 {rtype}（应为 stat/milestone/level）")

    data = {"stats": {}, "milestones": []}
    for idx, row, c_stat, c_name, c_xp in stat_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: 数据项键为空")
        name = sanitize_text(cell(row, c_name)) or key
        xp = _pos_int(cell(row, c_xp), f"第{idx}行 数据项 {key} 的单位经验")
        if key in data["stats"]:
            raise RuleError(f"第{idx}行: 数据项键重复 {key}")
        data["stats"][key] = {"name": name, "xp": xp}

    for idx, row, c_stat, c_xp, c_period, c_thr in milestone_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: 里程碑数据项为空")
        if key not in data["stats"]:
            raise RuleError(f"第{idx}行: 里程碑数据项 {key} 未在 stat 行中定义")
        period = cell(row, c_period).strip().lower() or "period"
        if period not in VALID_PERIODS:
            raise RuleError(f"第{idx}行: period 需为 period/career: {period}")
        threshold = _pos_num(cell(row, c_thr), f"第{idx}行 里程碑阈值")
        xp = _pos_int(cell(row, c_xp), f"第{idx}行 里程碑奖励经验")
        data["milestones"].append(
            {"stat": key, "period": period, "threshold": threshold, "xp": xp}
        )

    if level_rows:
        idx, row, c_xp = level_rows[-1]
        data["level_xp"] = _pos_int(cell(row, c_xp), "每级所需经验 level_xp")

    return normalize_rule(data, default_level_xp)


def format_rule(rule: dict) -> str:
    """将规范结构格式化为可读文本（用于预览与 /成长规则）。"""
    lines = []
    stats = rule["stats"]
    lines.append(f"· 数据项（{len(stats)} 个）:")
    for key, meta in stats.items():
        lines.append(f"  {key}（{meta['name']}）：每单位 {meta['xp']} 经验")
    milestones = rule["milestones"]
    if milestones:
        lines.append(f"· 里程碑（{len(milestones)} 条）:")
        period_label = {"period": "成长期内", "career": "生涯"}
        for m in milestones:
            stat = stats.get(m["stat"], {})
            lines.append(
                f"  {stat.get('name', m['stat'])} {period_label.get(m['period'], m['period'])}"
                f"累计达 {m['threshold']:g} → 奖励 {m['xp']} 经验"
            )
    else:
        lines.append("· 里程碑：无")
    lines.append(f"· 每级所需经验：{rule['level_xp']}")
    return "\n".join(lines)
