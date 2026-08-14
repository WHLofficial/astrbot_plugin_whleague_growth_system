"""管理侧命令处理器：上报、推进、导入、配置。"""

from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult

from ..config.defaults import validate_and_cast
from ..services import rule_parser
from ..utils.messages import WARN, deny, usage
from ..utils.security import parse_date, parse_num

_KIND_ALIAS = {
    "规则": "rule",
    "rule": "rule",
    "球员": "players",
    "player": "players",
    "players": "players",
    "比赛": "matches",
    "match": "matches",
    "matches": "matches",
}

_KIND_NAME = {"rule": "规则", "players": "球员", "matches": "比赛"}


def _as_bool(value, default: bool) -> bool:
    """健壮解析布尔配置：支持 true/false/1/0/yes/no，其余回退默认。

    避免 WebUI 以字符串（如 "false"）传入时被 `bool("false")==True` 误判。
    """
    if isinstance(value, bool):
        return value
    low = str(value).strip().lower()
    if low in ("true", "1", "yes", "y", "on"):
        return True
    if low in ("false", "0", "no", "n", "off"):
        return False
    return default


class AdminHandler:
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

    async def _is_admin(self, event) -> bool:
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        admins = self._plugin.config_cache.get("admin_ids", []) or []
        return qq in [str(a) for a in admins]

    async def _deny(self, event) -> None:
        yield event.plain_result(deny())

    # ─── 待确认导入列表 ────────────────────────────────────

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

    # ─── 比赛上报 ──────────────────────────────────────────

    async def record(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 4:
            yield event.plain_result(
                usage("成长上报", "<球员ID> <日期> <数据项=值>...", "/成长上报 p01 2026-08-14 进球=2 助攻=1")
            )
            return
        player_uid = parts[1].strip()
        try:
            match_date = parse_date(parts[2])
        except ValueError as e:
            yield event.plain_result(f"{WARN}日期错误: {e}")
            return
        stats = {}
        opponent = ""
        for token in parts[3:]:
            if "=" not in token:
                yield event.plain_result(f"参数需为 数据项=值 或 对手=xxx: {token}")
                return
            k, v = token.split("=", 1)
            k = k.strip()
            v = v.strip()
            if k == "对手":
                opponent = v
                continue
            try:
                stats[k] = parse_num(v)
            except ValueError as e:
                yield event.plain_result(f"{WARN}{e}")
                return
        if not stats:
            yield event.plain_result("请至少提供一项数据，如 进球=2")
            return
        try:
            result = await self.growth.record_match(
                player_uid, match_date, opponent, stats, event.get_sender_id()
            )
        except ValueError as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        except Exception as e:
            logger.error(f"Growth record error: {e}")
            yield event.plain_result(f"{WARN}录入失败: {e}")
            return
        lines = [
            f"✅ 已录入 {result['name']}({result['player_uid']}) {result['match_date']}"
            f" vs {result['opponent'] or '?'}",
            f"数据经验 +{result['stat_xp']}",
        ]
        if result["awarded"]:
            period_label = {"period": "成长期内", "career": "生涯"}
            for m in result["awarded"]:
                lines.append(
                    f"🎉 达成里程碑: {m['stat_key']} {period_label[m['period']]}"
                    f"累计 {m['threshold']:g} → +{m['xp']} 经验"
                )
            lines.append(f"本次共 +{result['total_xp']} 经验（含奖励 {result['bonus_xp']}）")
        else:
            lines.append(f"本次共 +{result['total_xp']} 经验")
        lines.append(
            f"当前 等级 {result['level']} · 本期经验 {result['xp']} · 生涯经验 {result['xp_total']}"
        )
        yield event.plain_result("\n".join(lines))

    # ─── 成长期推进 ────────────────────────────────────────

    async def advance(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result(usage("成长推进", "<新名称> [保留|清零]", "/成长推进 第二期 保留"))
            return
        new_name = parts[1].strip()
        default_carry = _as_bool(
            self._plugin.config_cache.get("advance_default_carryover"), True
        )
        carryover = default_carry
        if len(parts) >= 3:
            opt = parts[2].strip()
            if opt in ("保留", "carry", "keep"):
                carryover = True
            elif opt in ("清零", "reset", "clear"):
                carryover = False
            else:
                yield event.plain_result(f"未知选项 {opt}（应为 保留 或 清零）")
                return
        try:
            result = await self.growth.advance_period(new_name, carryover)
        except ValueError as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        lines = [
            f"✅ 成长期推进完成",
            f"关闭: #{result['closed']['period_no']} {result['closed']['name']}",
            f"开启: #{result['opened_no']} {result['opened_name']}",
            f"结算: 每级 {result['level_xp']} 经验",
            f"升级球员: {result['upgraded']} 名",
        ]
        if result["carryover"]:
            lines.append(f"溢出经验已结转（共 {result['carried_total']}）")
        else:
            lines.append("溢出经验已清零（等级保留）")
        yield event.plain_result("\n".join(lines))

    # ─── 导入 ──────────────────────────────────────────────

    async def import_file(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result(usage("成长导入文件", "<文件名> [类型]", "/成长导入文件 规则_a.json"))
            return
        file_name = parts[1].strip()
        kind = self._resolve_kind(parts[2].strip() if len(parts) >= 3 else None, file_name)
        if kind is None:
            yield event.plain_result("无法确定导入类型，请指定 [类型]（规则/球员/比赛）")
            return
        try:
            file_path = self.import_service.check_file(file_name, kind)
            preview = await self.import_service.preview(file_path, kind)
        except (ValueError, FileNotFoundError, rule_parser.RuleError) as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        except Exception as e:
            logger.error(f"Import preview error: {e}")
            yield event.plain_result(f"{WARN}预览失败: {e}")
            return
        await self.dao.insert_pending(kind, file_name, preview, event.get_sender_id())
        yield event.plain_result(
            f"📄 {file_name}（{_KIND_NAME[kind]}）\n{preview}\n回复 /成长确认导入 {file_name} 执行导入"
        )

    async def confirm_import(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split()
        if len(parts) < 2:
            yield event.plain_result(usage("成长确认导入", "<文件名> [类型]", "/成长确认导入 规则_a.json"))
            return
        file_name = parts[1].strip()
        kind = self._resolve_kind(parts[2].strip() if len(parts) >= 3 else None, file_name)
        pending = await self.dao.get_pending_by_filename(file_name)
        if pending is not None:
            kind = kind or pending["kind"]
        if kind is None:
            yield event.plain_result("无法确定导入类型，请指定 [类型]（规则/球员/比赛）")
            return
        created_by = event.get_sender_id()
        try:
            if kind == "rule":
                result = await self.import_service.confirm_rule_import(file_name, kind, created_by)
                text = f"✅ 规则已更新（{file_name}）\n{rule_parser.format_rule(result['rule'])}"
            elif kind == "players":
                result = await self.import_service.confirm_players_import(file_name, kind, created_by)
                text = (
                    f"✅ 球员库已更新（{file_name}）: 新增 {result['added']} 名，"
                    f"更新 {result['updated']} 名"
                )
                if result["errors"]:
                    text += f"\n⚠️ 跳过 {len(result['errors'])} 行: {result['errors'][0]}"
            elif kind == "matches":
                result = await self.import_service.confirm_matches_import(file_name, kind, created_by)
                text = f"✅ 比赛数据已导入（{file_name}）: 成功 {result['ok']} 条"
                if result["errors"]:
                    text += f"\n⚠️ {len(result['errors'])} 行数据错误: {result['errors'][0]}"
                if result.get("skipped"):
                    text += f"\n跳过空行 {result['skipped']} 行"
            else:
                yield event.plain_result(f"未知导入类型: {kind}")
                return
        except (ValueError, FileNotFoundError, rule_parser.RuleError) as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        except Exception as e:
            logger.error(f"Import confirm error: {e}")
            yield event.plain_result(f"{WARN}导入失败: {e}")
            return
        if pending is not None:
            await self.dao.update_pending_status(pending["id"], "done")
        yield event.plain_result(text)

    def _resolve_kind(self, raw: str | None, file_name: str) -> str | None:
        if raw:
            return _KIND_ALIAS.get(raw.strip().lower())
        return self.import_service.kind_from_name(file_name)

    # ─── 配置 ──────────────────────────────────────────────

    async def set_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        parts = event.get_message_str().split(maxsplit=2)
        if len(parts) < 3:
            yield event.plain_result(usage("成长设置", "<键> <值>", "/成长设置 rank_page_size 20"))
            return
        key, raw = parts[1].strip(), parts[2].strip()
        try:
            value = validate_and_cast(key, raw)
        except ValueError as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        await self._plugin._persist_config(key, value)
        yield event.plain_result(f"✅ 配置已更新: {key} = {value}")

    async def view_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not await self._is_admin(event):
            async for r in self._deny(event):
                yield r
            return
        cfg = self._plugin.config_cache
        keys = [
            "default_level_xp",
            "advance_default_carryover",
            "group_whitelist",
            "admin_ids",
        ]
        lines = ["【成长系统配置】"]
        for k in keys:
            lines.append(f"· {k} = {cfg.get(k)}")
        yield event.plain_result("\n".join(lines))
