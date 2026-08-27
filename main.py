"""球员成长系统 for WHL：按规则将比赛数据换算为成长经验，支持成长期里程碑、等级与推进。

- 命令面为单一 /成长 + 两级子命令（玩家只读，管理子命令需管理员权限）
- 规则 / 球员库 / 比赛数据 均支持群内发文件导入（规则_/球员_/比赛_ 前缀自动识别）
- 规则格式: JSON / CSV / Excel；球员库与比赛数据: CSV / Excel
"""

from collections.abc import AsyncGenerator

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult, filter
from astrbot.api.event.filter import EventMessageType
from astrbot.api.message_components import File
from astrbot.api.star import Context, Star, register

from .config.defaults import DEFAULT_CONFIG, PLUGIN_VERSION
from .db.connection import DatabaseManager
from .db.dao import GrowthDAO
from .db.schema import init_schema
from .services.export_service import ExportService
from .services.growth_service import GrowthService
from .services.import_service import GrowthImportService
from .utils.forward import maybe_forward_result
from .utils.messages import build_help, deny

# 子命令别名 → 规范名（帮助中只展示规范名）
_SUBCOMMAND_ALIASES = {
    "help": "帮助",
    "期状态": "期",
    "预览": "期",
    "排名": "排行",
    "名单": "球员",
    "赛程表": "赛程",
    "对阵": "赛程",
    "设置": "配置",
}

# 旧平铺命令（v0.6.0 及之前，如 /成长排行）→ 新用法提示
_LEGACY_HINTS = {
    "成长帮助": "/成长",
    "成长规则": "/成长 规则",
    "成长查询": "/成长 查询 <球员ID|姓名>",
    "成长排行": "/成长 排行 [页]",
    "成长球员": "/成长 球员 [页]",
    "成长期状态": "/成长 期 [期号]",
    "成长预览": "/成长 期",
    "成长上报": "/成长 上报 <球员ID> <日期> <数据项=值>...",
    "成长推进": "/成长 推进 <新名称> [保留|清零]",
    "成长导出": "/成长 导出 [期号]",
    "成长导入列表": "/成长 导入",
    "成长导入文件": "/成长 导入 <文件名> [类型]",
    "成长确认导入": "/成长 导入 确认 <文件名> [类型]",
    "成长设置": "/成长 配置 <键> <值>",
    "成长查看配置": "/成长 配置",
}


def _is_group_allowed(cfg, group_id) -> bool:
    if group_id is None:
        return True
    whitelist = [str(g) for g in (cfg.get("group_whitelist") or [])]
    return not whitelist or group_id in whitelist


@register(
    "astrbot_plugin_whleague_growth_system",
    "WHLofficial",
    "球员成长系统：按可导入规则将比赛数据换算为成长经验，支持成长期里程碑、等级与成长期推进。",
    PLUGIN_VERSION,
)
class GrowthSystemPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self._config = config or {}
        self.config_cache = dict(DEFAULT_CONFIG)
        self.config_cache.update(
            {k: v for k, v in (config or {}).items() if v is not None}
        )
        self.db: DatabaseManager | None = None
        self.dao: GrowthDAO | None = None
        self.growth_service: GrowthService | None = None
        self.import_service: GrowthImportService | None = None
        self.export_service: ExportService | None = None
        self.revenue_bridge = None
        self.revenue_hooks = None
        self.player_handler = None
        self.admin_handler = None
        self._subs: dict = {}

    async def initialize(self) -> None:
        self.db = DatabaseManager()
        await self.db.init()
        await init_schema(self.db)
        self.dao = GrowthDAO(self.db)
        self.growth_service = GrowthService(self.db, self.dao, self.config_cache.get)
        self.import_service = GrowthImportService(
            self.db, self.dao, self.config_cache.get, self.growth_service
        )
        self.export_service = ExportService(self.db, self.dao)
        from .handlers.admin import AdminHandler
        from .handlers.player import PlayerHandler

        self.player_handler = PlayerHandler(self)
        self.admin_handler = AdminHandler(self)
        self._subs = self._build_subs()
        from .web_api import WebApi

        self.web_api = WebApi(self)

        # 主场营收插件联动（可选）：只读赛程桥 + 推进事件提醒
        from .services.revenue_bridge import RevenueBridge
        from .services.revenue_hooks import RevenueHooks

        self.revenue_bridge = RevenueBridge(self.config_cache)
        self.revenue_hooks = RevenueHooks(self)
        await self.revenue_hooks.start()

        logger.info("Growth system plugin initialized (v%s).", PLUGIN_VERSION)

    def _build_subs(self) -> dict:
        """子命令注册表：规范名 → (handler, 是否需要管理员)。"""
        return {
            "帮助": (self.player_handler.help, False),
            "规则": (self.player_handler.show_rule, False),
            "查询": (self.player_handler.query_player, False),
            "排行": (self.player_handler.rank, False),
            "球员": (self.player_handler.list_players, False),
            "期": (self.player_handler.period_status, False),
            "赛程": (self.player_handler.show_fixtures, False),
            "上报": (self.admin_handler.record, True),
            "推进": (self.admin_handler.advance, True),
            "导出": (self.admin_handler.export, True),
            "导入": (self.admin_handler.import_files, True),
            "配置": (self.admin_handler.config, True),
        }

    async def _persist_config(self, key: str, value) -> None:
        """持久化配置变更（优先 AstrBot 托管配置，其次数据库表）。"""
        self.config_cache[key] = value
        if self._config:
            self._config[key] = value
            self._config.save_config()
        else:
            await self.dao.set_config(key, str(value))

    def _maybe_forward(self, event, result: MessageEventResult) -> MessageEventResult:
        """纯文本反馈行数达到阈值时自动转 QQ 合并转发卡片（防刷屏，取配置）。"""
        try:
            line_threshold = int(self.config_cache.get("forward_threshold", 0) or 0)
            node_max = int(self.config_cache.get("forward_node_max_chars", 1500) or 1500)
            max_nodes = int(self.config_cache.get("forward_max_nodes", 50) or 50)
        except (TypeError, ValueError):
            line_threshold, node_max, max_nodes = 0, 1500, 50
        if node_max < 1:
            node_max = 1500
        if max_nodes < 1:
            max_nodes = 50
        return maybe_forward_result(event, result, line_threshold, node_max, max_nodes)

    # ═══════════════════════════════════════════════════════
    # /成长 <子命令>（唯一注册命令，两级分发）
    # ═══════════════════════════════════════════════════════

    @filter.command("成长", alias={"成长帮助"})
    async def cmd_growth(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        parts = event.get_message_str().split()
        if not parts:
            return
        token = parts[0]
        if token != "成长":
            # 旧平铺命令（框架前缀匹配送入）或未知命令 → 迁移/引导提示
            # token 截断展示，避免回显放大用户输入
            shown = token if len(token) <= 20 else token[:20] + "…"
            hint = _LEGACY_HINTS.get(token)
            if hint:
                text = f"命令已改版: 请使用 {hint}\n发送 /成长 查看全部命令"
            else:
                text = f"未知命令: /{shown}\n发送 /成长 查看全部命令"
            yield self._maybe_forward(event, event.plain_result(text))
            return
        sub = _SUBCOMMAND_ALIASES.get(parts[1], parts[1]) if len(parts) > 1 else "帮助"
        entry = self._subs.get(sub)
        if entry is None:
            is_admin = await self.admin_handler._is_admin(event)
            yield self._maybe_forward(
                event, event.plain_result(f"未知子命令: {sub}\n\n{build_help(is_admin)}")
            )
            return
        handler, needs_admin = entry
        if needs_admin and not await self.admin_handler._is_admin(event):
            yield self._maybe_forward(event, event.plain_result(deny()))
            return
        async for r in handler(event, parts[2:]):
            yield self._maybe_forward(event, r)

    # ═══════════════════════════════════════════════════════
    # 群文件捕获（规则_ / 球员_ / 比赛_ 自动识别并预览）
    # ═══════════════════════════════════════════════════════

    @filter.event_message_type(EventMessageType.GROUP_MESSAGE)
    async def on_group_message(self, event: AstrMessageEvent) -> None:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        messages = event.get_messages()
        file_comps = [m for m in messages if isinstance(m, File)]
        if not file_comps:
            return
        qq = event.get_sender_id()
        if not (event.is_admin() or await self.admin_handler._is_admin(event)):
            return
        for comp in file_comps:
            file_name = comp.name or ""
            kind = self.import_service.kind_from_name(file_name)
            if kind is None:
                continue
            try:
                from pathlib import Path as _Path

                ext = _Path(file_name).suffix.lower()
                if ext not in (".json", ".xlsx", ".csv"):
                    continue
                file_path = await comp.get_file()
                if not file_path:
                    continue
                try:
                    if hasattr(event, "track_temporary_local_file"):
                        event.track_temporary_local_file(file_path)
                except Exception:
                    pass
                target = self.import_service.save_uploaded(file_path, file_name)
                preview = await self.import_service.preview(target, kind)
                kind_name = {"rule": "规则", "players": "球员", "matches": "比赛"}[kind]
                await self.dao.insert_pending(kind, file_name, preview, qq)
                await event.send(
                    MessageChain().message(
                        f"📄 收到文件 {file_name}（{kind_name}）\n{preview}\n"
                        f"回复 /成长 导入 确认 {file_name} 执行导入"
                    )
                )
            except (ValueError, FileNotFoundError) as e:
                await event.send(MessageChain().message(str(e)))
            except Exception as e:
                logger.error(f"Growth file import capture error: {e}")
                await event.send(MessageChain().message("文件接收失败，已记录错误"))

    # ═══════════════════════════════════════════════════════
    # Teardown
    # ═══════════════════════════════════════════════════════

    async def terminate(self) -> None:
        if self.revenue_hooks is not None:
            try:
                await self.revenue_hooks.terminate()
            except Exception:
                logger.exception("RevenueHooks terminate error")
        if self.revenue_bridge is not None:
            try:
                await self.revenue_bridge.close()
            except Exception:
                logger.exception("RevenueBridge close error")
        if self.db is not None:
            await self.db.close()
        logger.info("Growth system plugin terminated.")
