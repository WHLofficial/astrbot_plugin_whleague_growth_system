"""玩家侧命令处理器（只读查询）。"""

from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageEventResult


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

    async def help(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        yield event.plain_result(
            "【成长系统】\n"
            "· /成长：帮助\n"
            "· /成长规则：查看当前规则\n"
            "· /成长查询 <球员ID>：球员成长档案\n"
            "· /成长排行 [页]：当期经验排行\n"
            "· /成长排行 生涯 [页]：生涯经验排行\n"
            "· /成长球员 [页]：球员名单\n"
            "· /成长期状态：当前成长期信息\n\n"
            "管理命令：\n"
            "· /成长上报 <球员ID> <日期> <数据项=值>...\n"
            "· /成长推进 <新名称> [保留|清零]\n"
            "· /成长导入文件 <文件名> [类型]\n"
            "· /成长确认导入 <文件名> [类型]\n"
            "· /成长导入列表\n\n"
            "群内发送 规则_*.json/csv/xlsx、球员_*.csv/xlsx、比赛_*.csv/xlsx 文件可自动识别并预览导入。"
        )

    async def show_rule(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        rule = await self.growth.get_rule()
        if rule is None:
            yield event.plain_result("尚未导入成长规则。请管理员群内发送 规则_*.json/csv/xlsx 文件导入。")
            return
        from ..services import rule_parser

        yield event.plain_result(f"【当前成长规则】\n{rule_parser.format_rule(rule)}")

    async def query_player(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result("用法: /成长查询 <球员ID>")
            return
        player_uid = parts[1].strip()
        profile = await self.growth.get_profile(player_uid)
        if profile is None:
            yield event.plain_result(f"球员不存在: {player_uid}")
            return
        p = profile["player"]
        lines = [
            f"【{p['name']}】({p['player_uid']})",
            f"球队: {p['team'] or '无'}",
            f"等级: {p['level']}",
            f"本期经验: {p['xp']}",
            f"生涯经验: {p['xp_total']}",
        ]
        awards = profile["awards"]
        if awards:
            period_label = {"period": "成长期内", "career": "生涯"}
            lines.append(f"已达成里程碑（{len(awards)} 项）:")
            for a in awards[:10]:
                lines.append(f"· {a['stat_key']} {period_label.get(a['period'], a['period'])}"
                             f"累计 {a['threshold']:g}（+{a['xp']}）")
        app = profile["appearances"]
        if app:
            lines.append("最近比赛:")
            for a in app[:10]:
                lines.append(f"· {a['match_date']} vs {a['opponent'] or '?'} 经验 +{a['total_xp']}")
        yield event.plain_result("\n".join(lines))

    async def rank(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        mode = "period"
        page = 1
        if len(parts) >= 2:
            if parts[1].strip() == "生涯":
                mode = "career"
                if len(parts) >= 3 and parts[2].strip().isdigit():
                    page = int(parts[2].strip())
            elif parts[1].strip().isdigit():
                page = int(parts[1].strip())
        result = await self.growth.rank(mode, max(1, page))
        rows = result["rows"]
        page_size = self.growth._page_size()
        title = "【成长排行·生涯】" if mode == "career" else "【成长排行·本期】"
        if not rows:
            yield event.plain_result(f"{title}\n暂无球员数据。")
            return
        lines = [f"{title}（第 {result['page']}/{result['total_pages']} 页）"]
        for i, p in enumerate(rows, start=(result["page"] - 1) * page_size + 1):
            val = p["xp_total"] if mode == "career" else p["xp"]
            lines.append(f"{i}. {p['name']}({p['player_uid']}) Lv{p['level']} 经验 {val}")
        yield event.plain_result("\n".join(lines))

    async def list_players(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        page = int(parts[1].strip()) if len(parts) >= 2 and parts[1].strip().isdigit() else 1
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

    async def period_status(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        st = await self.growth.period_status()
        cur = st["current"]
        rule = st["rule"]
        lines = []
        if cur:
            lines.append(f"【当前成长期】#{cur['period_no']} {cur['name']}（起始 {cur['started_at']}）")
        lines.append(f"球员数: {st['player_count']}")
        if rule:
            lines.append(f"每级所需经验: {rule['level_xp']}")
        else:
            lines.append("成长规则: 未导入")
        if len(st["periods"]) > 1:
            lines.append("历史成长期:")
            for p in st["periods"][1:5]:
                lines.append(f"· #{p['period_no']} {p['name']}（{p['started_at']} ~ {p['ended_at'] or '进行中'}）")
        yield event.plain_result("\n".join(lines))

    async def import_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        pending = await self.dao.list_pending()
        if not pending:
            yield event.plain_result("暂无待确认的导入。")
            return
        lines = ["【待确认导入】"]
        kind_name = {"rule": "规则", "players": "球员", "matches": "比赛"}
        for p in pending:
            lines.append(f"· {p['id']}. [{kind_name.get(p['kind'], p['kind'])}] {p['file_name']}")
        yield event.plain_result("\n".join(lines) + "\n回复 /成长确认导入 <文件名> [类型] 执行")
