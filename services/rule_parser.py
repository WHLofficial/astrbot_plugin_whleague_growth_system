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

VALID_PERIODS = ("period", "career", "match")
"""period=成长期内数据值累计；career=生涯数据值累计；match=单场数据达标额外奖励。"""


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


def _nonneg_num(raw, field: str) -> float:
    """非负数值（允许 0），用于区间下限等场景。"""
    try:
        v = float(str(raw).strip())
    except (ValueError, TypeError):
        raise RuleError(f"{field} 需为数字: {raw}")
    if v < 0:
        raise RuleError(f"{field} 需为非负数: {raw}")
    return v


def _pos_int(raw, field: str) -> int:
    v = _pos_num(raw, field)
    if v != int(v):
        raise RuleError(f"{field} 需为整数: {raw}")
    return int(v)


def _pos_1dp(raw, field: str) -> float:
    """正数且最多 1 位小数（用于经验值），返回 round(v, 1)。

    用 round(v,1)==v 判断小数位数：v 有 ≥2 位小数时 round 到 1 位必然改变值；
    浮点表示下该比较对 1 位小数及整数值稳定成立。
    """
    v = _pos_num(raw, field)
    if round(v, 1) != v:
        raise RuleError(f"{field} 最多 1 位小数: {raw}")
    return round(v, 1)


def _nonneg_1dp(raw, field: str) -> float:
    """非负且最多 1 位小数（仅用于数据项单位经验：0 表示仅作计数、不给数据经验）。"""
    v = _nonneg_num(raw, field)
    if round(v, 1) != v:
        raise RuleError(f"{field} 最多 1 位小数: {raw}")
    return round(v, 1)


def _normalize_bands(bands_raw, stat_key: str) -> list:
    """校验并规范化区间（bands）列表。

    每段 {min, max?, xp}：左闭右开 [min, max)，max 省略表示开放上界且必须为最后一段；
    min 允许 0；xp 为正整数；按 min 升序且段间不得重叠。
    """
    if not isinstance(bands_raw, list) or not bands_raw:
        raise RuleError(f"数据项 {stat_key} 的 bands 需为非空数组")
    bands = []
    prev_max = None
    for i, b in enumerate(bands_raw, start=1):
        if not isinstance(b, dict):
            raise RuleError(f"数据项 {stat_key} 第 {i} 段需为对象 {{min, max?, xp}}")
        try:
            lo = _nonneg_num(b.get("min"), f"数据项 {stat_key} 第 {i} 段的下限 min")
            hi_raw = b.get("max")
            hi = None if hi_raw is None else _pos_num(hi_raw, f"数据项 {stat_key} 第 {i} 段的上限 max")
        except RuleError:
            raise
        if hi is not None and hi <= lo:
            raise RuleError(f"数据项 {stat_key} 第 {i} 段的上限 max 需大于下限 min")
        if prev_max is not None and lo < prev_max:
            raise RuleError(f"数据项 {stat_key} 区间重叠（第 {i} 段下限 {lo:g} < 前段上限 {prev_max:g}）")
        prev_max = hi
        xp = _pos_1dp(b.get("xp"), f"数据项 {stat_key} 第 {i} 段的经验 xp")
        item = {"min": lo, "xp": xp}
        if hi is not None:
            item["max"] = hi
        else:
            # 开放上界段必须是最后一段
            if i != len(bands_raw):
                raise RuleError(f"数据项 {stat_key} 开放上界段（无 max）必须为最后一段")
        bands.append(item)
    return bands


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
        if k in stats:
            raise RuleError(f"数据项键重复: {k}")
        if isinstance(meta, (int, float)):
            # 简写：{"goal": 10} 等价于 {"goal": {"name": "goal", "xp": 10}}
            name = k
            xp = _nonneg_1dp(meta, f"数据项 {k} 的单位经验 xp")
            stats[k] = {"name": name, "xp": xp}
            continue
        if not isinstance(meta, dict):
            raise RuleError(f"数据项 {k} 需为对象 {{name, xp}} 或数字")
        name = sanitize_text(str(meta.get("name", "")))
        if not name:
            raise RuleError(f"数据项 {k} 缺少显示名 name")
        bands_raw = meta.get("bands")
        xp_raw = meta.get("xp")
        if bands_raw is not None:
            # 区间型：经验 = 命中区间的固定 xp，未命中得 0；与线性 xp 互斥
            if xp_raw is not None:
                raise RuleError(f"数据项 {k} 不能同时定义 xp 与 bands")
            stats[k] = {"name": name, "bands": _normalize_bands(bands_raw, k)}
        else:
            xp = _nonneg_1dp(xp_raw, f"数据项 {k} 的单位经验 xp")
            stats[k] = {"name": name, "xp": xp}

    milestones = []
    seen = set()
    m_raw = data.get("milestones", [])
    if not isinstance(m_raw, list):
        raise RuleError("milestones 需为数组")
    for i, m in enumerate(m_raw):
        if not isinstance(m, dict):
            raise RuleError(f"里程碑第 {i+1} 条需为对象")
        period = str(m.get("period", "period")).strip().lower()
        if period not in VALID_PERIODS:
            raise RuleError(
                f"里程碑第 {i+1} 条的 period 需为 period/career/match: {period}"
            )
        threshold_raw = m.get("threshold")
        step_raw = m.get("step")
        if threshold_raw is not None and step_raw is not None:
            raise RuleError(f"里程碑第 {i+1} 条不能同时定义 threshold 与 step")
        xp = _pos_1dp(m.get("xp"), f"里程碑第 {i+1} 条的奖励经验 xp")

        # 数据项：stat（字符串，可含逗号分隔多 key，兼容表格写法）或 stats（数组，JSON 推荐）
        stat_raw = m.get("stat")
        stats_list = m.get("stats")
        if stat_raw is not None and stats_list is not None:
            raise RuleError(
                f"里程碑第 {i+1} 条不能同时定义 stat 与 stats（多数据项请用 stats 数组）"
            )
        if stats_list is not None:
            if not isinstance(stats_list, list) or not stats_list:
                raise RuleError(f"里程碑第 {i+1} 条的 stats 需为非空数组")
            stat_keys = []
            for item in stats_list:
                k = sanitize_uid(str(item))
                if not k:
                    raise RuleError(f"里程碑第 {i+1} 条 stats 含非法数据项")
                stat_keys.append(k)
        else:
            raw_stat = sanitize_uid(str(stat_raw or ""))
            if not raw_stat:
                raise RuleError(f"里程碑第 {i+1} 条缺少数据项 stat")
            stat_keys = [k for k in (s.strip() for s in raw_stat.split(",")) if k]
        stat_keys = list(dict.fromkeys(stat_keys))
        if not stat_keys:
            raise RuleError(f"里程碑第 {i+1} 条缺少数据项 stat")

        # 单场达标（period=match）：仅 threshold 型，仅单个数据项，允许 bands 数据项
        if period == "match":
            if step_raw is not None:
                raise RuleError(
                    f"里程碑第 {i+1} 条：单场达标（period=match）不支持每累计 n 次重复奖励"
                )
            if len(stat_keys) != 1:
                raise RuleError(
                    f"里程碑第 {i+1} 条：单场达标（period=match）仅支持单个数据项"
                )
            stat = stat_keys[0]
            if stat not in stats:
                raise RuleError(f"里程碑第 {i+1} 条的数据项 {stat} 未在 stats 中定义")
            threshold = _pos_num(threshold_raw, f"里程碑第 {i+1} 条的阈值 threshold")
            dedup_key = (stat, "match", "threshold", threshold)
            if dedup_key in seen:
                raise RuleError(f"里程碑重复定义: {stat}/match/{threshold}")
            seen.add(dedup_key)
            milestones.append(
                {"stat": stat, "period": "match", "threshold": threshold, "xp": xp}
            )
            continue

        # 累计型（period/career）：每个数据项须已定义且为线性型（非 bands）
        for k in stat_keys:
            if k not in stats:
                raise RuleError(f"里程碑第 {i+1} 条的数据项 {k} 未在 stats 中定义")
            if stats[k].get("bands") is not None:
                raise RuleError(
                    f"区间型数据项 {k}（bands）不能作为里程碑/repeat 的数据项"
                )
        if step_raw is not None:
            # 每累计 step 次奖励一次（可重复触发）；step 是次数，保持整数
            if len(stat_keys) > 1:
                raise RuleError(
                    f"里程碑第 {i+1} 条：多数据项总和暂不支持每累计 n 次重复奖励"
                )
            stat = stat_keys[0]
            step = _pos_int(step_raw, f"里程碑第 {i+1} 条的步长 step")
            dedup_key = (stat, period, "step", step)
            if dedup_key in seen:
                raise RuleError(f"里程碑重复定义: {stat}/{period}/step={step}")
            seen.add(dedup_key)
            milestones.append(
                {"stat": stat, "period": period, "step": step, "xp": xp}
            )
        else:
            threshold = _pos_num(threshold_raw, f"里程碑第 {i+1} 条的阈值 threshold")
            stat_keys.sort()
            if len(stat_keys) > 1:
                dedup_key = (tuple(stat_keys), period, "threshold", threshold)
                if dedup_key in seen:
                    raise RuleError(
                        f"里程碑重复定义: {'+'.join(stat_keys)}/{period}/{threshold}"
                    )
                seen.add(dedup_key)
                milestones.append(
                    {
                        "stat_keys": stat_keys,
                        "period": period,
                        "threshold": threshold,
                        "xp": xp,
                    }
                )
            else:
                stat = stat_keys[0]
                dedup_key = (stat, period, "threshold", threshold)
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
        level_xp = _pos_1dp(level_xp, "level_xp")

    return {"stats": stats, "milestones": milestones, "level_xp": level_xp}


def parse_rule_table(rows: list, cfg: dict, default_level_xp: int) -> dict:
    """解析 CSV/Excel 规则表为规范结构。

    列位（1 起，0 表示无此列）由 cfg 提供：
      import_col_type / import_col_stat / import_col_name /
      import_col_xp / import_col_period / import_col_threshold /
      import_col_band_min / import_col_band_max
    行类型：stat（线性数据项）、band（区间段）、milestone（一次性里程碑）、
    repeat（每累计 step 次奖励，可重复）、level（每级经验）。

    无类型列的文件（首列直接是数据项键）时，其余列位整体左移一列：
    类型列位置即 stat 列。类型列缺失或值非法即视为无类型列布局。
    """
    # cfg 兼容 dict 与配置读取函数（config_cache.get 绑定方法），
    # 保证导入 CSV/Excel 规则文件的生产路径可用
    def _get(key: str, default):
        if hasattr(cfg, "get"):
            return cfg.get(key, default)
        return cfg(key, default)

    col_type = int(_get("import_col_type", 1) or 0)
    col_stat = int(_get("import_col_stat", 2) or 0)
    col_name = int(_get("import_col_name", 3) or 0)
    col_xp = int(_get("import_col_xp", 4) or 0)
    col_period = int(_get("import_col_period", 5) or 0)
    col_threshold = int(_get("import_col_threshold", 6) or 0)
    col_band_min = int(_get("import_col_band_min", 7) or 0)
    col_band_max = int(_get("import_col_band_max", 8) or 0)

    def cell(row, col: int) -> str:
        if col <= 0 or col > len(row):
            return ""
        return str(row[col - 1]).strip()

    # 跳过表头行（首行首列为 type/类型 时视为表头）
    start = 0
    if rows and rows[0] and str(rows[0][0]).strip().lower() in ("type", "类型"):
        start = 1

    valid_types = ("stat", "band", "milestone", "repeat", "level")
    stat_rows = []
    band_rows = []
    milestone_rows = []
    repeat_rows = []
    level_rows = []
    for idx, row in enumerate(rows[start:], start=start + 1):
        if not row or not any(str(c).strip() for c in row):
            continue
        rtype = cell(row, col_type).strip().lower()
        has_type = rtype in valid_types
        if has_type:
            c_stat, c_name = col_stat, col_name
            c_xp = col_xp
            c_period, c_thr = col_period, col_threshold
            c_bmin, c_bmax = col_band_min, col_band_max
        else:
            # 无类型列布局：其余列位整体左移一列，类型列位置即 stat 列
            off = 1 if col_type > 0 else 0
            c_stat = col_type or 1
            c_name = max(0, col_name - off)
            c_xp = max(0, col_xp - off)
            c_period = max(0, col_period - off)
            c_thr = max(0, col_threshold - off)
            c_bmin = max(0, col_band_min - off)
            c_bmax = max(0, col_band_max - off)
            rtype = ""
        if not rtype:
            # 自动推断：milestone（有 period+threshold）> band（min 列有值）>
            # stat（有 name）> level
            if c_period > 0 and c_thr > 0 and cell(row, c_period) and cell(row, c_thr):
                rtype = "milestone"
            elif c_bmin > 0 and cell(row, c_bmin):
                rtype = "band"
            elif c_name > 0 and cell(row, c_name):
                rtype = "stat"
            else:
                rtype = "level"
        if rtype == "stat":
            stat_rows.append((idx, row, c_stat, c_name, c_xp))
        elif rtype == "band":
            band_rows.append((idx, row, c_stat, c_name, c_xp, c_bmin, c_bmax))
        elif rtype == "milestone":
            milestone_rows.append((idx, row, c_stat, c_xp, c_period, c_thr))
        elif rtype == "repeat":
            repeat_rows.append((idx, row, c_stat, c_xp, c_period, c_thr))
        elif rtype == "level":
            level_rows.append((idx, row, c_xp))
        else:
            raise RuleError(
                f"第{idx}行: 未知行类型 {rtype}（应为 stat/band/milestone/repeat/level）"
            )

    data = {"stats": {}, "milestones": []}
    for idx, row, c_stat, c_name, c_xp in stat_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: 数据项键为空")
        name = sanitize_text(cell(row, c_name)) or key
        xp = _nonneg_1dp(cell(row, c_xp), f"第{idx}行 数据项 {key} 的单位经验")
        if key in data["stats"]:
            raise RuleError(f"第{idx}行: 数据项键重复 {key}")
        data["stats"][key] = {"name": name, "xp": xp}

    # band 行：同一数据项多行合并为一个 bands 列表；首行 name 作为显示名
    bands_acc: dict[str, list] = {}
    for idx, row, c_stat, c_name, c_xp, c_bmin, c_bmax in band_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: 区间数据项键为空")
        if key in data["stats"] and "xp" in data["stats"][key]:
            raise RuleError(f"第{idx}行: 数据项 {key} 不能同时定义线性 xp 与区间 bands")
        name = sanitize_text(cell(row, c_name))
        if name:
            data["stats"].setdefault(key, {"name": name})
        elif key not in data["stats"]:
            data["stats"][key] = {"name": key}
        lo_raw = cell(row, c_bmin)
        hi_raw = cell(row, c_bmax)
        if not lo_raw:
            raise RuleError(f"第{idx}行: 区间下限 min 为空")
        band = {"min": _nonneg_num(lo_raw, f"第{idx}行 区间下限 min")}
        if hi_raw:
            band["max"] = _pos_num(hi_raw, f"第{idx}行 区间上限 max")
        band["xp"] = _pos_1dp(cell(row, c_xp), f"第{idx}行 区间经验 xp")
        bands_acc.setdefault(key, []).append(band)

    for key, bands in bands_acc.items():
        data["stats"][key]["bands"] = _normalize_bands(bands, key)

    for idx, row, c_stat, c_xp, c_period, c_thr in milestone_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: 里程碑数据项为空")
        # 多数据项总和（goal,assist）由 normalize_rule 统一拆分校验
        if "," not in key and key not in data["stats"]:
            raise RuleError(f"第{idx}行: 里程碑数据项 {key} 未在 stat/band 行中定义")
        period = cell(row, c_period).strip().lower() or "period"
        if period not in VALID_PERIODS:
            raise RuleError(f"第{idx}行: period 需为 period/career/match: {period}")
        threshold = _pos_num(cell(row, c_thr), f"第{idx}行 里程碑阈值")
        xp = _pos_1dp(cell(row, c_xp), f"第{idx}行 里程碑奖励经验")
        data["milestones"].append(
            {"stat": key, "period": period, "threshold": threshold, "xp": xp}
        )

    for idx, row, c_stat, c_xp, c_period, c_thr in repeat_rows:
        key = sanitize_uid(cell(row, c_stat))
        if not key:
            raise RuleError(f"第{idx}行: repeat 数据项为空")
        if "," in key:
            raise RuleError(f"第{idx}行: repeat 每累计 n 次奖励不支持多数据项（{key}）")
        if key not in data["stats"]:
            raise RuleError(f"第{idx}行: repeat 数据项 {key} 未在 stat/band 行中定义")
        period = cell(row, c_period).strip().lower() or "period"
        if period not in VALID_PERIODS:
            raise RuleError(f"第{idx}行: period 需为 period/career/match: {period}")
        step = _pos_int(cell(row, c_thr), f"第{idx}行 repeat 步长 step（threshold 列）")
        xp = _pos_1dp(cell(row, c_xp), f"第{idx}行 repeat 奖励经验")
        data["milestones"].append(
            {"stat": key, "period": period, "step": step, "xp": xp}
        )

    if level_rows:
        idx, row, c_xp = level_rows[-1]
        data["level_xp"] = _pos_1dp(cell(row, c_xp), "每级所需经验 level_xp")

    return normalize_rule(data, default_level_xp)


def format_rule(rule: dict) -> str:
    """将规范结构格式化为可读文本（用于预览与 /成长 规则）。"""
    from ..utils.security import fmt_xp

    lines = []
    stats = rule["stats"]
    lines.append(f"· 数据项（{len(stats)} 个）:")
    for key, meta in stats.items():
        bands = meta.get("bands")
        if bands is not None:
            parts = []
            for b in bands:
                lo = fmt_xp(b["min"])
                if "max" in b:
                    parts.append(f"[{lo}~{fmt_xp(b['max'])})+{fmt_xp(b['xp'])}")
                else:
                    parts.append(f"[{lo}~)+{fmt_xp(b['xp'])}")
            lines.append(f"  {key}（{meta['name']}）：{'、'.join(parts)} 经验（未命中区间得 0）")
        else:
            lines.append(f"  {key}（{meta['name']}）：每单位 {fmt_xp(meta['xp'])} 经验")
    milestones = rule["milestones"]
    if milestones:
        lines.append(f"· 里程碑（{len(milestones)} 条）:")
        period_label = {"period": "成长期内", "career": "生涯", "match": "单场"}
        for m in milestones:
            if "stat_keys" in m:
                names = "+".join(stats.get(k, {}).get("name", k) for k in m["stat_keys"])
                lines.append(
                    f"  {names} {period_label[m['period']]}"
                    f"累计合计达 {fmt_xp(m['threshold'])} → 奖励 {fmt_xp(m['xp'])} 经验"
                )
                continue
            stat = m["stat"]
            name = stats.get(stat, {}).get("name", stat)
            if m["period"] == "match":
                lines.append(
                    f"  {name} 单场达 {fmt_xp(m['threshold'])}"
                    f" → 额外 {fmt_xp(m['xp'])} 经验"
                )
            elif "step" in m:
                lines.append(
                    f"  {name} {period_label[m['period']]}"
                    f"每累计 {fmt_xp(m['step'])} 次 → 奖励 {fmt_xp(m['xp'])} 经验（可重复）"
                )
            else:
                lines.append(
                    f"  {name} {period_label[m['period']]}"
                    f"累计达 {fmt_xp(m['threshold'])} → 奖励 {fmt_xp(m['xp'])} 经验"
                )
    else:
        lines.append("· 里程碑：无")
    lines.append(f"· 每级所需经验：{fmt_xp(rule['level_xp'])}")
    return "\n".join(lines)
