"""管理侧子命令处理器：上报、推进、导出、导入、配置。

统一签名 (event, args)：args 为去掉 /成长 与子命令后的参数列表，
由 main.py 分发器解析并在分发器统一鉴权（handler 内不再重复检查）。
"""

from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.api.message_components import File

from ..config.defaults import DEFAULT_CONFIG, validate_and_cast
from ..services import rule_parser
from ..utils.messages import WARN, usage
from ..utils.security import fmt_xp, parse_date, parse_num

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

# 配置查看（/成长 配置 无参）的功能域分组；schema 新增键自动落入"其他"
_CONFIG_GROUPS = [
    ("基础", ("group_whitelist", "admin_ids")),
    ("规则与成长期", ("default_level_xp", "advance_default_carryover")),
    (
        "展示与转发",
        ("rank_page_size", "forward_threshold", "forward_node_max_chars", "forward_max_nodes"),
    ),
    (
        "导入·列位",
        (
            "import_col_type", "import_col_stat", "import_col_name", "import_col_xp",
            "import_col_period", "import_col_threshold", "import_col_band_min",
            "import_col_band_max", "import_col_uid", "import_col_name_player",
            "import_col_team", "import_col_match_date", "import_col_match_uid",
        ),
    ),
    (
        "导入·安全",
        (
            "import_require_confirm", "import_max_rows", "import_batch_size",
            "import_max_file_size_mb", "import_max_files",
        ),
    ),
]


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


def _format_config_value(key: str, value) -> str:
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value) if value else "（空）"
    if key == "default_level_xp":
        return fmt_xp(value)
    return str(value)


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

    @property
    def export_service(self):
        return self._plugin.export_service

    async def _is_admin(self, event) -> bool:
        if event.is_admin():
            return True
        qq = event.get_sender_id()
        admins = self._plugin.config_cache.get("admin_ids", []) or []
        return qq in [str(a) for a in admins]

    # ─── 比赛上报 ──────────────────────────────────────────

    async def record(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if len(args) < 3:
            yield event.plain_result(
                usage("上报", "<球员ID> <日期> <数据项=值>...", "/成长 上报 p01 2026-08-14 进球=2 助攻=1")
            )
            return
        player_uid = args[0].strip()
        try:
            match_date = parse_date(args[1])
        except ValueError as e:
            yield event.plain_result(f"{WARN}日期错误: {e}")
            return
        stats = {}
        opponent = ""
        for token in args[2:]:
            if "=" not in token:
                yield event.plain_result(f"{WARN}参数需为 数据项=值 或 对手=xxx: {token}")
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
            f"数据经验 +{fmt_xp(result['stat_xp'])}",
        ]
        if result["awarded"]:
            rule = await self.growth.get_rule() or {}
            stat_defs = rule.get("stats", {})
            period_label = {"period": "成长期内", "career": "生涯", "match": "单场"}
            for m in result["awarded"]:
                if m["period"] == "match":
                    name = stat_defs.get(m["stat"], {}).get("name", m["stat"])
                    lines.append(
                        f"🎉 单场达标: {name} {fmt_xp(m['value'])}≥{fmt_xp(m['threshold'])}"
                        f" → +{fmt_xp(m['xp'])} 经验"
                    )
                elif "step" in m:
                    name = stat_defs.get(m["stat"], {}).get("name", m["stat"])
                    lines.append(
                        f"🎉 重复奖励达成: {name} {period_label[m['period']]}"
                        f"每累计 {fmt_xp(m['step'])} 次 ×{m['count']} → +{fmt_xp(m['gain'])} 经验"
                    )
                else:
                    keys = m.get("stat_keys") or [m["stat"]]
                    name = "+".join(stat_defs.get(k, {}).get("name", k) for k in keys)
                    lines.append(
                        f"🎉 达成里程碑: {name} {period_label[m['period']]}"
                        f"累计 {fmt_xp(m['threshold'])} → +{fmt_xp(m['xp'])} 经验"
                    )
            lines.append(
                f"本次共 +{fmt_xp(result['total_xp'])} 经验（含奖励 {fmt_xp(result['bonus_xp'])}）"
            )
        else:
            lines.append(f"本次共 +{fmt_xp(result['total_xp'])} 经验")
        lines.append(
            f"当前 等级 {result['level']} · 本期经验 {fmt_xp(result['xp'])}"
            f" · 生涯经验 {fmt_xp(result['xp_total'])}"
        )
        yield event.plain_result("\n".join(lines))

    # ─── 成长期推进 ────────────────────────────────────────

    async def advance(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not args:
            yield event.plain_result(usage("推进", "<新名称> [保留|清零]", "/成长 推进 第二期 保留"))
            return
        new_name = args[0].strip()
        default_carry = _as_bool(
            self._plugin.config_cache.get("advance_default_carryover"), True
        )
        carryover = default_carry
        if len(args) >= 2:
            opt = args[1].strip()
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
            f"结算: 每级 {fmt_xp(result['level_xp'])} 经验",
            f"升级球员: {result['upgraded']} 名",
        ]
        if result["carryover"]:
            lines.append(f"溢出经验已结转（共 {fmt_xp(result['carried_total'])}）")
        else:
            lines.append("溢出经验已清零（等级保留）")
        # 自动导出刚结束的成长期（发群失败仅提示路径，不影响推进结果）
        export_note = await self._auto_export(event, result["closed"]["period_no"])
        if export_note:
            lines.append(export_note)
        yield event.plain_result("\n".join(lines))

    async def _auto_export(self, event, period_no: int) -> str | None:
        """推进后自动导出刚结束成长期的成长数据文件（发群优先，失败附服务器路径）。"""
        try:
            export = await self.export_service.build_export(period_no)
        except Exception as e:
            logger.error(f"推进自动导出失败: {e}")
            return None
        sent = False
        try:
            await event.send(
                MessageChain(chain=[File(name=export["path"].name, file=str(export["path"]))])
                .message(f"📤 成长期#{period_no} 成长数据已导出")
            )
            sent = True
        except Exception as e:
            logger.warning(f"导出文件发群失败，已保存服务器: {e}")
        if sent:
            return None
        return f"📁 成长数据文件已保存: {export['path']}（平台不支持自动发文件，可手动下载）"

    async def export(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        period_no = None
        if args:
            if len(args) > 1 or not args[0].isdigit():
                yield event.plain_result(usage("导出", "[期号]", "/成长 导出 2"))
                return
            period_no = int(args[0])
        try:
            export = await self.export_service.build_export(period_no)
        except ValueError as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        except Exception as e:
            logger.error(f"Growth export error: {e}")
            yield event.plain_result(f"{WARN}导出失败: {e}")
            return
        lines = [f"📤 {export['title']}（{len(export['rows'])} 人）"]
        yield event.chain_result([File(name=export["path"].name, file=str(export["path"]))])
        lines.append(f"📁 服务器备份: {export['path']}（平台不支持自动发文件时可手动下载）")
        yield event.plain_result("\n".join(lines))

    # ─── 导入：无参=列表 / <文件名>=预览 / 确认 <文件名>=执行 ──

    async def import_files(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not args:
            async for r in self._import_list(event):
                yield r
            return
        if args[0] == "确认":
            async for r in self._import_confirm(event, args[1:]):
                yield r
            return
        async for r in self._import_preview(event, args):
            yield r

    async def _import_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        pending = await self.dao.list_pending()
        if not pending:
            yield event.plain_result(
                "暂无待确认的导入。\n" + usage("导入", "<文件名> [类型]", "/成长 导入 规则_a.json")
            )
            return
        lines = ["【待确认导入】"]
        for p in pending:
            lines.append(f"· {p['id']}. [{_KIND_NAME.get(p['kind'], p['kind'])}] {p['file_name']}")
        yield event.plain_result(
            "\n".join(lines) + "\n回复 /成长 导入 确认 <文件名> [类型] 执行"
        )

    async def _import_preview(self, event: AstrMessageEvent, args: list[str]) -> AsyncGenerator[MessageEventResult, None]:
        if len(args) > 2:
            yield event.plain_result(usage("导入", "<文件名> [类型]", "/成长 导入 规则_a.json"))
            return
        file_name = args[0].strip()
        kind = self._resolve_kind(args[1].strip() if len(args) >= 2 else None, file_name)
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
        note = ""
        try:
            await self.dao.insert_pending(kind, file_name, preview, event.get_sender_id())
        except Exception as e:
            logger.error(f"Insert pending error: {e}")
            note = f"\n{WARN}登记写入失败（{e}），该文件不会出现在待确认列表，可直接用上面的确认命令执行"
        yield event.plain_result(
            f"📄 {file_name}（{_KIND_NAME[kind]}）\n{preview}\n"
            f"回复 /成长 导入 确认 {file_name} 执行导入{note}"
        )

    async def _import_confirm(self, event: AstrMessageEvent, args: list[str]) -> AsyncGenerator[MessageEventResult, None]:
        if not args:
            yield event.plain_result(
                usage("导入 确认", "<文件名> [类型]", "/成长 导入 确认 规则_a.json")
            )
            return
        if len(args) > 2:
            yield event.plain_result(
                usage("导入 确认", "<文件名> [类型]", "/成长 导入 确认 规则_a.json")
            )
            return
        file_name = args[0].strip()
        kind = self._resolve_kind(args[1].strip() if len(args) >= 2 else None, file_name)
        # 用不过滤状态的查询：文件已导入/驳回时守卫能看见，拒绝重复确认；
        # get_pending_by_filename 只返回 pending 行，转 done 后查不到、守卫会失效。
        pending = await self.dao.get_latest_import_by_filename(file_name)
        if pending is not None:
            kind = kind or pending["kind"]
        # 与 WebUI 确认端点对齐：已导入/已驳回的登记不能重复确认，
        # 防止旧文件被二次执行；未登记的文件名仍允许直接确认（聊天侧通道）。
        if pending is not None and pending["status"] != "pending":
            yield event.plain_result(
                f"{WARN}该文件已处理过（状态：{'已导入' if pending['status'] == 'done' else '已驳回'}），"
                "不能重复确认；请重新预览生成新登记"
            )
            return
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

    # ─── 配置：无参=分组全量 / 单键=查看 / 键值=设置 ─────────

    async def config(
        self, event: AstrMessageEvent, args: list[str]
    ) -> AsyncGenerator[MessageEventResult, None]:
        if not args:
            async for r in self._config_view_all(event):
                yield r
            return
        if len(args) == 1:
            async for r in self._config_view_one(event, args[0].strip()):
                yield r
            return
        # 设置：从原始消息重切，保留第三个参数（值）中的空格
        parts = event.get_message_str().split(maxsplit=3)
        if len(parts) < 4:
            yield event.plain_result(usage("配置", "[键] [值]", "/成长 配置 rank_page_size 20"))
            return
        key, raw = parts[2].strip(), parts[3].strip()
        try:
            value = validate_and_cast(key, raw)
        except ValueError as e:
            yield event.plain_result(f"{WARN}{e}")
            return
        await self._plugin._persist_config(key, value)
        yield event.plain_result(f"✅ 配置已更新: {key} = {_format_config_value(key, value)}")

    async def _config_view_all(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        cfg = self._plugin.config_cache
        grouped = {k for _, keys in _CONFIG_GROUPS for k in keys}
        groups = list(_CONFIG_GROUPS)
        others = tuple(k for k in DEFAULT_CONFIG if k not in grouped)
        if others:
            groups.append(("其他", others))
        lines = ["【成长系统配置】（/成长 配置 <键> <值> 可修改）"]
        for title, keys in groups:
            lines.append(f"▸ {title}")
            for k in keys:
                lines.append(f"· {k} = {_format_config_value(k, cfg.get(k))}")
        yield event.plain_result("\n".join(lines))

    async def _config_view_one(self, event: AstrMessageEvent, key: str) -> AsyncGenerator[MessageEventResult, None]:
        if key not in DEFAULT_CONFIG:
            yield event.plain_result(f"{WARN}未知配置项: {key}")
            return
        yield event.plain_result(
            f"{key} = {_format_config_value(key, self._plugin.config_cache.get(key))}"
        )
