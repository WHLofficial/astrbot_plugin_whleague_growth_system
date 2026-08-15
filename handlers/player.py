"""玩家侧命令处理器（只读查询）。"""

from collections.abc import AsyncGenerator

from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..utils.messages import build_help, usage
from ..utils.security import fmt_xp


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

    async def help(self, event: AstrMessageEvent, is_admin: bool = False) -> AsyncGenerator[MessageEventResult, None]:
        yield event.plain_result(build_help(is_admin))

    async def show_rule(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        rule = await self.growth.get_rule()
        if rule is None:
            yield event.plain_result(
                "尚未导入成长规则。请管理员在群内发送 规则_*.json/csv/xlsx 文件，"
                "或使用 /成长导入文件 <文件名> 预览后确认导入。"
            )
            return
        from ..services import rule_parser

        yield event.plain_result(f"【当前成长规则】\n{rule_parser.format_rule(rule)}")

    async def query_player(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result(usage("成长查询", "<球员ID>", "/成长查询 p01"))
            return
        player_uid = parts[1].strip()
        profile = await self.growth.get_profile(player_uid)
        if profile is None:
            yield event.plain_result(f"未找到球员 {player_uid}，可用 /成长球员 查看球员名单。")
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
            period_label = {"period": "成长期内", "career": "生涯"}
            lines.append(f"已达成里程碑（{len(awards)} 项）:")
            for a in awards[:10]:
                lines.append(f"· {a['stat_key']} {period_label.get(a['period'], a['period'])}"
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
            yield event.plain_result(f"{title}\n暂无球员数据，请管理员导入球员库后查看。")
            return
        lines = [f"{title}（第 {result['page']}/{result['total_pages']} 页）"]
        for i, p in enumerate(rows, start=(result["page"] - 1) * page_size + 1):
            val = p["xp_total"] if mode == "career" else p["xp"]
            lines.append(f"{i}. {p['name']}({p['player_uid']}) Lv{p['level']} 经验 {fmt_xp(val)}")
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
        parts = event.get_message_str().split()
        # 带期号：查看该成长期结果明细
        if len(parts) >= 2 and parts[1].strip().isdigit():
            period_no = int(parts[1].strip())
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
        summaries = st["summaries"]
        if summaries:
            lines.append("历史成长期:")
            for s in summaries[:5]:
                lines.append(
                    f"· #{s['period_no']} {s['name']}：{s['player_count']}人"
                    f" · 升级{s['upgraded_count']}人 · 期末总经验 {fmt_xp(s['xp_total'])}"
                )
            lines.append("回复 /成长期状态 <期号> 查看该期球员明细")
        yield event.plain_result("\n".join(lines))
