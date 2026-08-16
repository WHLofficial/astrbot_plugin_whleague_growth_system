"""成长业务核心：规则获取、比赛录入（经验/里程碑）、成长期推进、查询与排行。"""

import json

from ..db.dao import GrowthDAO
from ..db.connection import DatabaseManager

_PAGE_SIZE_DEFAULT = 10


class GrowthService:
    def __init__(self, db: DatabaseManager, dao: GrowthDAO, cfg_get):
        self._db = db
        self._dao = dao
        self._cfg_get = cfg_get
        """配置读取函数（self.config_cache.get），用于取默认 level_xp 与翻页大小。"""

    def _default_level_xp(self) -> float:
        try:
            return round(float(self._cfg_get("default_level_xp", 100) or 100), 1)
        except (TypeError, ValueError):
            return 100.0

    def _page_size(self) -> int:
        try:
            v = int(self._cfg_get("rank_page_size", _PAGE_SIZE_DEFAULT) or _PAGE_SIZE_DEFAULT)
        except (TypeError, ValueError):
            v = _PAGE_SIZE_DEFAULT
        return v if v > 0 else _PAGE_SIZE_DEFAULT

    # ─── 规则 ──────────────────────────────────────────────

    async def get_rule(self) -> dict | None:
        row = await self._dao.get_latest_rule()
        if row is None:
            return None
        try:
            rule = json.loads(row["payload_json"])
        except (ValueError, TypeError):
            return None
        return rule if isinstance(rule, dict) else None

    async def save_rule(self, rule: dict, label: str, imported_by: str) -> None:
        await self._dao.save_rule(label, json.dumps(rule, ensure_ascii=False), imported_by)

    # ─── 比赛录入 ──────────────────────────────────────────

    async def record_match(
        self,
        player_uid: str,
        match_date: str,
        opponent: str,
        stats: dict,
        created_by: str,
    ) -> dict:
        """录入/覆盖一位球员单场比赛数据。

        事务内：校验球员与数据项 → 计算 stat_xp → 写/覆盖 appearance 与 stats
        → 里程碑检查（未颁发且达标则颁发）→ 增量更新 players.xp / xp_total。
        """
        rule = await self.get_rule()
        if rule is None:
            raise ValueError("尚未导入成长规则，请先导入规则文件")

        unknown = [k for k in stats if k not in rule["stats"]]
        if unknown:
            raise ValueError(
                f"数据项未在规则中定义: {', '.join(unknown)}（可用 /成长规则 查看）"
            )

        period = await self._dao.get_current_period()
        period_no = period["period_no"] if period else 1

        result = await self._db.execute_transaction(
            lambda conn: self._record_one(
                conn, rule, player_uid, match_date, opponent, stats, created_by, period_no
            )
        )
        return result

    async def _record_one(
        self,
        conn,
        rule: dict,
        player_uid: str,
        match_date: str,
        opponent: str,
        stats: dict,
        created_by: str,
        period_no: int,
    ) -> dict:
        player = await self._dao.get_player_conn(conn, player_uid)
        if player is None or not player["active"]:
            raise ValueError(f"球员不存在或已停用: {player_uid}（可用 /成长球员 查看名单）")

        match = await self._dao.get_match(conn, match_date, opponent)
        if match is None:
            match_id = await self._dao.create_match(conn, match_date, opponent, created_by)
        else:
            match_id = match["id"]

        old = await self._dao.get_appearance(conn, match_id, player_uid)
        old_stat_xp = round(float(old["stat_xp"]), 1) if old else 0.0
        # 单场达标奖励随出场记录持久化（bonus_xp 列）：覆盖时回退旧值，
        # 读取存储值而非重算，保证与当时规则冻结一致（改规则后重录仍正确回收）
        old_match_bonus = round(float(old["bonus_xp"]), 1) if old else 0.0

        stat_xp = 0.0
        for key, value in stats.items():
            meta = rule["stats"][key]
            bands = meta.get("bands")
            if bands is not None:
                # 区间型：经验 = 命中区间的固定 xp，未命中得 0
                for b in bands:
                    if value >= b["min"] and ("max" not in b or value < b["max"]):
                        stat_xp += b["xp"]
                        break
            else:
                stat_xp += round(float(value) * meta["xp"], 1)
        stat_xp = round(stat_xp, 1)
        match_bonus = self._match_bonus(rule, stats)

        appearance_id = await self._dao.upsert_appearance(
            conn, match_id, player_uid, period_no, stat_xp, match_bonus,
            round(stat_xp + match_bonus, 1), created_by,
        )
        await self._dao.delete_appearance_stats(conn, appearance_id)
        for key, value in stats.items():
            await self._dao.insert_match_stat(conn, appearance_id, key, float(value))

        # 里程碑检查（基于写入后的累计值）
        bonus = 0.0
        awarded = []
        for m in rule["milestones"]:
            if m["period"] == "match":
                # 单场达标：基于本次上报数据值，达标即奖励（每场判断，天然幂等）
                value = float(stats.get(m["stat"], 0.0))
                if value >= m["threshold"]:
                    bonus = round(bonus + m["xp"], 1)
                    awarded.append({**m, "value": value})
                continue
            pno = period_no if m["period"] == "period" else 0
            if "stat_keys" in m:
                # 多数据项总和里程碑：各项累计值之和，一次性颁发（stat_key 存排序后逗号串）
                total = await self._dao.sum_stat_values(
                    conn, player_uid, m["stat_keys"],
                    period_no if m["period"] == "period" else None,
                )
                stat_key = ",".join(m["stat_keys"])
                existing = await self._dao.get_award(
                    conn, player_uid, m["period"], stat_key, m["threshold"], pno
                )
                if existing:
                    continue
                if total >= m["threshold"]:
                    await self._dao.insert_award(
                        conn, player_uid, pno, m["period"], stat_key,
                        m["threshold"], m["xp"], match_id,
                    )
                    bonus = round(bonus + m["xp"], 1)
                    awarded.append(m)
                continue
            total = await self._dao.sum_stat_value(
                conn, player_uid, m["stat"],
                period_no if m["period"] == "period" else None,
            )
            if "step" in m:
                # 每累计 step 次奖励一次（可重复触发）；+1e-9 防浮点下取整误差
                target = int(total // m["step"] + 1e-9)
                if target <= 0:
                    continue
                ra = await self._dao.get_repeat_award(
                    conn, player_uid, m["period"], m["stat"], m["step"], pno
                )
                awarded_count = int(ra["awarded_count"]) if ra else 0
                if target > awarded_count:
                    gain = round((target - awarded_count) * m["xp"], 1)
                    bonus = round(bonus + gain, 1)
                    await self._dao.upsert_repeat_award(
                        conn, player_uid, pno, m["period"], m["stat"],
                        m["step"], m["xp"], target,
                    )
                    awarded.append({**m, "count": target - awarded_count, "gain": gain})
            else:
                # 一次性里程碑：仅未颁发且达标时颁发（幂等）
                existing = await self._dao.get_award(
                    conn, player_uid, m["period"], m["stat"], m["threshold"], pno
                )
                if existing:
                    continue
                if total >= m["threshold"]:
                    await self._dao.insert_award(
                        conn, player_uid, pno, m["period"], m["stat"], m["threshold"], m["xp"], match_id
                    )
                    bonus = round(bonus + m["xp"], 1)
                    awarded.append(m)

        delta = round((stat_xp + bonus) - (old_stat_xp + old_match_bonus), 1)
        level = int(player["level"])
        xp = max(0.0, round(float(player["xp"]) + delta, 1))
        xp_total = max(0.0, round(float(player["xp_total"]) + delta, 1))
        await self._dao.update_player_progress(conn, player_uid, level, xp, xp_total)

        return {
            "player_uid": player_uid,
            "name": player["name"],
            "match_date": match_date,
            "opponent": opponent,
            "stat_xp": stat_xp,
            "match_bonus": match_bonus,
            "bonus_xp": bonus,
            "total_xp": round(stat_xp + bonus, 1),
            "awarded": awarded,
            "level": level,
            "xp": xp,
            "xp_total": xp_total,
        }

    def _match_bonus(self, rule: dict, stats: dict) -> float:
        """单场达标额外经验合计（period=match 的里程碑，基于单场数据值）。"""
        total = 0.0
        for m in rule["milestones"]:
            if m.get("period") != "match":
                continue
            if float(stats.get(m["stat"], 0.0)) >= m["threshold"]:
                total = round(total + m["xp"], 1)
        return total

    async def record_match_batch(self, entries: list, created_by: str) -> dict:
        """批量录入（文件导入），整个批次一个事务，失败整体回滚。

        entries: [{"player_uid", "match_date", "opponent", "stats": {key: value}}, ...]
        player_uid 可为球员 ID 或球员姓名（见 _resolve_player）。
        """
        if not entries:
            return {"ok": 0, "errors": [], "results": []}
        rule = await self.get_rule()
        if rule is None:
            raise ValueError("尚未导入成长规则，请先导入规则文件")
        for e in entries:
            unknown = [k for k in e["stats"] if k not in rule["stats"]]
            if unknown:
                raise ValueError(
                    f"球员 {e['player_uid']} 数据项未在规则中定义: {', '.join(unknown)}"
                )

        period = await self._dao.get_current_period()
        period_no = period["period_no"] if period else 1

        async def _tx(conn):
            from ..utils.security import normalize_name

            players = await self._dao.list_all_active_players_conn(conn)
            # 索引只建一次：批量导入 5000 行 × 全量球员时避免每行重建
            uid_map = {p["player_uid"]: p for p in players}
            by_name: dict[str, list] = {}
            for p in players:
                by_name.setdefault(normalize_name(p["name"]), []).append(p)
            results = []
            for e in entries:
                uid = self._resolve_player(e["player_uid"], players, uid_map, by_name)
                r = await self._record_one(
                    conn, rule, uid, e["match_date"],
                    e.get("opponent", ""), e["stats"], created_by, period_no,
                )
                results.append(r)
            return results

        results = await self._db.execute_transaction(_tx)
        return {"ok": len(results), "errors": [], "results": results}

    def _resolve_player(self, ref: str, players: list, uid_map: dict, by_name: dict) -> str:
        """解析比赛导入中的球员引用：先按 UID 精确匹配，再按姓名匹配（含容错）。

        姓名匹配：归一化（小写去分隔符）精确优先；否则长度分级编辑距离容错
        （<4 字符仅精确；4~9 允许 1 处；≥10 允许 2 处）。命中多个报错提示用球员ID。
        """
        from ..utils.security import name_similar, normalize_name

        hit = uid_map.get(ref)
        if hit is not None:
            return hit["player_uid"]
        exact = by_name.get(normalize_name(ref))
        if exact:
            if len(exact) > 1:
                names = "、".join(p["name"] for p in exact)
                raise ValueError(f"存在多个同名球员（{names}），请使用球员ID")
            return exact[0]["player_uid"]
        fuzzy = [p for p in players if name_similar(p["name"], ref)]
        if len(fuzzy) > 1:
            names = "、".join(p["name"] for p in fuzzy)
            raise ValueError(f"存在多个相近球员（{names}），请使用球员ID")
        if fuzzy:
            return fuzzy[0]["player_uid"]
        raise ValueError(f"未找到球员: {ref}（可用 /成长球员 查看名单）")
    # ─── 成长期推进 ────────────────────────────────────────

    async def advance_period(self, new_name: str, carryover: bool) -> dict:
        """推进成长期：等级折算（逐级消耗、只升不降），溢出按 carryover 结转或清零。

        结算、归档旧成长期、开启新成长期在同一事务内完成；事务内重新校验当前
        成长期，防止并发推进产生多个当前期。
        """
        current = await self._dao.get_current_period()
        if current is None:
            raise ValueError("不存在当前成长期，无法推进")

        rule = await self.get_rule()
        level_xp = rule["level_xp"] if rule else self._default_level_xp()
        if not level_xp or level_xp <= 0:
            level_xp = self._default_level_xp()
        # 经验与每级经验均 ≤1 位小数：放大 10 倍用整数运算，避免浮点整除/取模误差
        lv10 = int(round(float(level_xp) * 10))

        async def _tx(conn):
            # 事务内重新读取并校验：必须仍是同一当前成长期，
            # 防止并发推进已抢先开启新期时二次结算
            row = await self._dao.get_current_period_conn(conn)
            if row is None or row["period_no"] != current["period_no"]:
                raise ValueError("成长期已被并发推进，请稍后重试")
            period_no = row["period_no"]
            players = await self._dao.iter_active_players(conn)
            upgraded = 0
            carried = 0
            for p in players:
                uid = p["player_uid"]
                old_level = int(p["level"])
                xp_period = round(float(p["xp"]), 1)
                xp10 = int(round(xp_period * 10))
                gained = xp10 // lv10
                overflow = (xp10 % lv10) / 10
                new_level = old_level + gained
                new_xp = overflow if carryover else 0
                # 期末快照（结算前写入，等级/本期经验/结转可追溯）
                await self._dao.insert_period_snapshot(
                    conn, period_no, uid, new_level, gained,
                    xp_period, overflow if carryover else 0,
                )
                await self._dao.update_player_progress(
                    conn, uid, new_level, new_xp, float(p["xp_total"])
                )
                if gained > 0:
                    upgraded += 1
                if carryover:
                    carried = round(carried + overflow, 1)
            await self._dao.close_period_conn(conn, period_no)
            next_no = (await self._dao.max_period_no_conn(conn)) + 1
            await self._dao.create_period_conn(conn, next_no, new_name)
            return upgraded, carried, next_no, row

        upgraded, carried, next_no, row = await self._db.execute_transaction(_tx)
        return {
            "closed": row,
            "opened_no": next_no,
            "opened_name": new_name,
            "upgraded": upgraded,
            "carried_total": carried,
            "carryover": carryover,
            "level_xp": level_xp,
        }

    # ─── 查询与排行 ────────────────────────────────────────

    async def get_profile(self, player_uid: str) -> dict | None:
        player = await self._dao.get_player(player_uid)
        if player is None:
            return None
        awards = await self._dao.list_awards(player_uid)
        repeat_awards = await self._dao.list_repeat_awards(player_uid)
        appearances = await self._dao.list_player_appearances(player_uid, limit=10)
        return {
            "player": player,
            "awards": awards,
            "repeat_awards": repeat_awards,
            "appearances": appearances,
        }

    async def rank(self, mode: str, page: int) -> dict:
        page_size = self._page_size()
        if mode == "career":
            rows = await self._dao.list_players_by_total(page, page_size)
            total = await self._dao.count_players()
        else:
            rows = await self._dao.list_players_by_xp(page, page_size)
            total = await self._dao.count_players()
        total_pages = max(1, (total + page_size - 1) // page_size)
        return {"rows": rows, "page": page, "total_pages": total_pages, "total": total}

    async def period_status(self) -> dict:
        current = await self._dao.get_current_period()
        periods = await self._dao.list_periods()
        count = await self._dao.count_players()
        rule = await self.get_rule()
        summaries = []
        for p in periods:
            if p["is_current"]:
                continue
            row = await self._dao.summarize_period(p["period_no"])
            summaries.append(
                {
                    "period_no": p["period_no"],
                    "name": p["name"],
                    "started_at": p["started_at"],
                    "ended_at": p["ended_at"],
                    "player_count": int(row["player_count"]) if row else 0,
                    "upgraded_count": int(row["upgraded_count"]) if row else 0,
                    "xp_total": round(float(row["xp_total"]), 1) if row else 0.0,
                }
            )
        return {
            "current": current,
            "periods": periods,
            "player_count": count,
            "current_xp": await self._dao.sum_current_xp(),
            "rule": rule,
            "summaries": summaries,
        }

    async def period_result(self, period_no: int) -> dict | None:
        """返回指定成长期的球员明细（无快照/期号不存在返回 None）。"""
        period = await self._db.fetchone(
            "SELECT * FROM growth_periods WHERE period_no=?", (period_no,)
        )
        if period is None:
            return None
        rows = await self._dao.list_period_snapshots(period_no)
        if not rows:
            return None
        return {"period": period, "rows": rows}
