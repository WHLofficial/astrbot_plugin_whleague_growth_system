"""玩家侧子命令处理器（只读查询）。

统一签名 (event, args)：args 为去掉 /成长 与子命令后的参数列表，
由 main.py 分发器解析传入；参数校验失败一律报 usage（不静默回退）。
"""

from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..utils.messages import build_help, usage
from ..utils.security import find_by_name, fmt_xp

# 当前期每人明细的防御性行数上限（超出提示用导出获取完整数据；
# 长输出由转发卡片兜底防刷屏，正常规模联赛不会触达此上限）
_PERIOD_DETAIL_MAX_ROWS = 500


class PlayerHandler:
    def __init__(self, plugin):
        self._plugin = plugin

    @property
    def dao(self):
        return self._plugin.dao

    @property
    def growth(self):
        return self._plugin.growth_service

    @property
    def import_service(self):
        return self._plugin.import_service

    @property
    def export_service(self):
        return self._plugin.export_service

    async def help(
        self, event: AstrMessageEvent, args: list[str] | None = None
    ) -> AsyncGenerator[MessageEventResult, None]:
        is_admin = await self._plugin.admin_handler._is_admin(event)
        yield event.plain_result(build_help(is_admin))

    async def show_rule(
        self, event: AstrMessageEvent, args: list[str] | None = None
    ) -> AsyncGenerator[MessageEventResult, None]:
        rule = await self.growth.get_rule()
        if rule is None:
            yield event.plain_result(
                "尚未导入成长规则。请管理员在群内发送 规则_*.json/csv/xlsx 文件，"
                "或使用 /成长 导入 <文件名> 预览后确认导入。"
            )
            return
        from ..services import rule_parser

        yield event.plain_result(f"【当前成长规则】\n{rule_parser.format_rule(rule)}")

    # ─── 查询：UID 精确 → 姓名精确 → 姓名模糊 ───────────────

    @staticmethod
    def _match_by_name(ref: str, players: list) -> tuple:
        """按姓名在球员列表中定位（见 utils.security.find_by_name），返回 (player_uid, 错误消息)；均无则 (None, None)。"""

        def _names(matches: list) -> str:
            names = "、".join(f"{p['name']}({p['player_uid']})" for p in matches[:5])
            return names + " 等" if len(matches) > 5 else names

        matches, exact = find_by_name(ref, players)
        if not matches:
            return None, None
        if len(matches) > 1:
            label = "同名" if exact else "相近"
            return None, f"存在多个{label}球员: {_names(matches)}，请使用球员ID"
        return matches[0]["player_uid"], None

    async def query_player(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not args:
            yield event.plain_result(usage("查询", "<球员ID|姓名>", "/成长 查询 p01"))
            return
        ref = args[0].strip()
        profile = await self.growth.get_profile(ref)
        if profile is None:
            players = await self.dao.list_all_active_players()
            uid, err = self._match_by_name(ref, players)
            if err:
                yield event.plain_result(f"⚠️ {err}")
                return
            if uid is None:
                yield event.plain_result(f"未找到球员 {ref}，可用 /成长 球员 查看球员名单。")
                return
            profile = await self.growth.get_profile(uid)
            if profile is None:  # 防御：理论上不可达
                yield event.plain_result(f"未找到球员 {ref}，可用 /成长 球员 查看球员名单。")
                return
        p = profile["player"]
        lines = [
            f"【{p['name']}】({p['player_uid']})",
            f"球队: {p['team'] or '无'}",
            f"等级: {p['level']}",
            f"本期经验: {fmt_xp(p['xp'])}",
            f"生涯经验: {fmt_xp(p['xp_total'])}",
        ]
        awards = profile["awards"]
        if awards:
            rule = await self.growth.get_rule() or {}
            stats = rule.get("stats", {})
            period_label = {"period": "成长期内", "career": "生涯", "match": "单场"}
            lines.append(f"已达成里程碑（{len(awards)} 项）:")
            for a in awards[:10]:
                keys = a["stat_key"].split(",")
                label = "+".join(stats.get(k, {}).get("name", k) for k in keys)
                lines.append(f"· {label} {period_label.get(a['period'], a['period'])}"
                             f"累计 {fmt_xp(a['threshold'])}（+{fmt_xp(a['xp'])}）")
        repeat_awards = profile["repeat_awards"]
        if repeat_awards:
            period_label = {"period": "成长期内", "career": "生涯"}
            lines.append(f"重复奖励达成（{len(repeat_awards)} 项）:")
            for a in repeat_awards[:10]:
                lines.append(
                    f"· {a['stat_key']} {period_label.get(a['period'], a['period'])}"
                    f"已累计 {int(a['step']) * a['awarded_count']}（每 {fmt_xp(a['step'])} 次 +{fmt_xp(a['xp'])}）"
                )
        app = profile["appearances"]
        if app:
            lines.append("最近比赛:")
            for a in app[:10]:
                lines.append(f"· {a['match_date']} vs {a['opponent'] or '?'} 经验 +{fmt_xp(a['total_xp'])}")
        yield event.plain_result("\n".join(lines))

    # ─── 排行 / 名单 ────────────────────────────────────────

    async def rank(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        mode = "period"
        page = 1
        if args:
            if args[0] == "生涯":
                mode = "career"
                if len(args) > 1:
                    if len(args) > 2 or not args[1].isdigit():
                        yield event.plain_result(
                            usage("排行", "[页] 或 生涯 [页]", "/成长 排行 生涯 2")
                        )
                        return
                    page = int(args[1])
            elif len(args) == 1 and args[0].isdigit():
                page = int(args[0])
            else:
                yield event.plain_result(
                    usage("排行", "[页] 或 生涯 [页]", "/成长 排行 生涯 2")
                )
                return
        result = await self.growth.rank(mode, max(1, page))
        rows = result["rows"]
        page_size = self.growth._page_size()
        title = "【成长排行·生涯】" if mode == "career" else "【成长排行·本期】"
        if not rows:
            yield event.plain_result(f"{title}\n暂无球员数据，请管理员导入球员库后查看。")
            return
        lines = [f"{title}（第 {result['page']}/{result['total_pages']} 页）"]
        for i, p in enumerate(rows, start=(result["page"] - 1) * page_size + 1):
            val = p["xp_total"] if mode == "career" else p["xp"]
            lines.append(f"{i}. {p['name']}({p['player_uid']}) Lv{p['level']} 经验 {fmt_xp(val)}")
        yield event.plain_result("\n".join(lines))

    async def list_players(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        page = 1
        if args:
            if len(args) > 1 or not args[0].isdigit():
                yield event.plain_result(usage("球员", "[页]", "/成长 球员 2"))
                return
            page = int(args[0])
        page = max(1, page)
        page_size = self.growth._page_size()
        rows = await self.dao.list_players(page, page_size)
        total = await self.dao.count_players()
        total_pages = max(1, (total + page_size - 1) // page_size)
        if not rows:
            yield event.plain_result("暂无球员数据，请管理员导入球员库。")
            return
        lines = [f"【球员名单】（第 {page}/{total_pages} 页 · 共 {total} 人）"]
        for p in rows:
            lines.append(f"· {p['name']}({p['player_uid']}) {p['team'] or ''} Lv{p['level']}".rstrip())
        yield event.plain_result("\n".join(lines))

    # ─── 期：无参=当前期概况+明细+历史；期号=该期结算明细 ────

    async def period_status(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if args:
            if len(args) > 1 or not args[0].isdigit():
                yield event.plain_result(usage("期", "[期号]", "/成长 期 2"))
                return
            period_no = int(args[0])
            result = await self.growth.period_result(period_no)
            if result is None:
                yield event.plain_result(f"未找到成长期 #{period_no} 的结果（期号不存在或该期尚无快照）")
                return
            p = result["period"]
            rows = result["rows"]
            lines = [f"【成长期#{p['period_no']} {p['name']} 结果】（{len(rows)} 人）"]
            for s in rows:
                lines.append(
                    f"· {s['player_name']}({s['player_uid']}) 期末Lv{s['level_end']}"
                    f"（+{s['level_gained']}级）· 本期经验 {fmt_xp(s['xp_period'])}"
                    f" · 结转 {fmt_xp(s['xp_carryover'])}"
                )
            yield event.plain_result("\n".join(lines))
            return

        st = await self.growth.period_status()
        cur = st["current"]
        rule = st["rule"]
        lines = []
        if cur:
            lines.append(
                f"【当前成长期】#{cur['period_no']} {cur['name']}"
                f"（起始 {cur['started_at']}）"
            )
        lines.append(f"球员数: {st['player_count']} · 当前期总经验 {fmt_xp(st['current_xp'])}")
        if rule:
            lines.append(f"每级所需经验: {fmt_xp(rule['level_xp'])}")
        else:
            lines.append("成长规则: 未导入")
        # 当前期每人成长明细（原 /成长预览 并入；不分页，长输出自动转转发卡片）
        if cur is not None:
            rows = await self.export_service.rows_current()
            if rows:
                gained = round(sum(float(r["xp_gained"]) for r in rows), 1)
                lines.append(
                    f"本期成长数据（{len(rows)} 人 · 期内累计获得经验 {fmt_xp(gained)}）:"
                )
                for r in rows[:_PERIOD_DETAIL_MAX_ROWS]:
                    lines.append(
                        f"· {r['player_name']}({r['player_uid']}) "
                        f"期初 {fmt_xp(r['xp_start'])} | 获得 {fmt_xp(r['xp_gained'])}"
                        f" | 总 {fmt_xp(r['xp_end'])} | Lv{r['level']}"
                    )
                if len(rows) > _PERIOD_DETAIL_MAX_ROWS:
                    lines.append(
                        f"… 其余 {len(rows) - _PERIOD_DETAIL_MAX_ROWS} 人"
                        f"（完整数据可让管理员执行 /成长 导出）"
                    )
            else:
                lines.append("暂无球员数据，请管理员导入球员库后查看。")
        summaries = st["summaries"]
        if summaries:
            lines.append("历史成长期:")
            for s in summaries[:5]:
                lines.append(
                    f"· #{s['period_no']} {s['name']}：{s['player_count']}人"
                    f" · 升级{s['upgraded_count']}人 · 期末总经验 {fmt_xp(s['xp_total'])}"
                )
            lines.append("查看指定期明细: /成长 期 <期号>")
        yield event.plain_result("\n".join(lines))

    # ─── 赛程（主场营收插件联动，只读）───────────────────────

    async def show_fixtures(
        self, event: AstrMessageEvent, args: list[str] | None = None
    ) -> AsyncGenerator[MessageEventResult, None]:
        """查看联赛赛程：/成长 赛程 [轮次]；轮次支持文字前缀（如「顶级9」）。"""
        from ..services.revenue_bridge import RevenueBridge

        bridge: RevenueBridge = self._plugin.revenue_bridge
        if bridge is None or not await bridge.is_available():
            yield event.plain_result(
                "未检测到主场营收插件数据库（astrbot_plugin_whleague_revenue_system），"
                "赛程联动不可用。"
            )
            return
        state = await bridge.get_league_state()
        raw = (args[0].strip() if args else "")
        rounds = await bridge.list_rounds()
        fixtures = await bridge.list_fixtures(
            round_no=int(raw) if raw.isdigit() and raw else None
        )
        if fixtures is None:
            yield event.plain_result("读取赛程失败，请检查主场插件数据库。")
            return

        def round_label(no) -> str:
            text = str(no)
            return text

        lines = []
        if state:
            season_name = str(state.get("season_name") or "").strip()
            suffix = f"「{season_name}」" if season_name else ""
            lines.append(
                f"【赛程】第{state.get('season_number')}赛季{suffix} "
                f"第{state.get('window_seq')}窗口"
            )
        else:
            lines.append("【赛程】")
        selected: list[dict] = []
        for fx in fixtures:
            played = bool(fx.get("result"))
            if raw and not raw.isdigit() and str(fx.get("round_no")) != raw:
                continue  # 文字前缀轮次：按字符串原样过滤
            if played and fx.get("score"):
                selected.append({**fx, "_tag": f"✅ {fx['home_team']} {fx['score']} {fx['away_team']}"})
            elif played:
                selected.append({**fx, "_tag": f"✔ 已录赛果 {fx['home_team']} vs {fx['away_team']}"})
            else:
                selected.append({**fx, "_tag": f"⬜ {fx['home_team']} vs {fx['away_team']}"})
        # 按轮次分组输出
        by_round: dict[str, list] = {}
        for fx in selected:
            by_round.setdefault(round_label(fx["round_no"]), []).append(fx)
        if raw:
            shown_rounds = [r for r in by_round]
        else:
            shown_rounds = sorted(by_round)
        for r in shown_rounds[:8]:
            items = by_round[r]
            total_played = sum(1 for it in items if it.get("result"))
            lines.append(f"— 第{r}轮（已打{total_played}/{len(items)}）—")
            comp_seen = ""
            for it in items:
                tag = it["_tag"]
                comp = str(it.get("competition") or "")
                prefix = f"[{comp}] " if comp != "联赛" else ""
                weather = str(it.get("weather") or "").strip()
                if weather:
                    tag += f"（{weather}）"
                lines.append(f"· {prefix}{tag}")
            if len(shown_rounds) > 8:
                continue
        if len(by_round) > 8 and len(lines) < 400:
            lines.append(f"… 其余 {len(by_round) - 8} 轮未展示，可指定轮次查看: /成长 赛程 <轮次>")
        if not lines[1:] and not state:
            lines.append("暂无赛程数据。")
        yield event.plain_result("\n".join(lines))
