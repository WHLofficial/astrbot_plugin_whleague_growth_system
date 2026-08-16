"""球员成长系统 for WHL：按规则将比赛数据换算为成长经验，支持成长期里程碑、等级与推进。

- 规则 / 球员库 / 比赛数据 均支持群内发文件导入（规则_/球员_/比赛_ 前缀自动识别）
- 规则格式: JSON / CSV / Excel；球员库与比赛数据: CSV / Excel
- 命令全部为 /成长 系列（玩家只读，管理命令需管理员权限）
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
        self.player_handler = None
        self.admin_handler = None

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
        logger.info("Growth system plugin initialized (v%s).", PLUGIN_VERSION)

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
    # 玩家命令（只读）
    # ═══════════════════════════════════════════════════════

    @filter.command("成长", alias={"成长帮助"})
    async def cmd_help(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        is_admin = await self.admin_handler._is_admin(event)
        async for r in self.player_handler.help(event, is_admin):
            yield self._maybe_forward(event, r)

    @filter.command("成长规则")
    async def cmd_show_rule(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.show_rule(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长查询")
    async def cmd_query(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.query_player(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长排行")
    async def cmd_rank(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.rank(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长球员")
    async def cmd_list_players(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.list_players(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长期状态")
    async def cmd_period_status(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.period_status(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长预览")
    async def cmd_preview(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.player_handler.preview(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长导入列表")
    async def cmd_import_list(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        if not await self.admin_handler._is_admin(event):
            async for r in self.admin_handler._deny(event):
                yield self._maybe_forward(event, r)
            return
        async for r in self.admin_handler.import_list(event):
            yield self._maybe_forward(event, r)

    # ═══════════════════════════════════════════════════════
    # 管理命令
    # ═══════════════════════════════════════════════════════

    @filter.command("成长上报")
    async def cmd_record(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.record(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长推进")
    async def cmd_advance(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.advance(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长导出")
    async def cmd_export(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.export(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长导入文件")
    async def cmd_import_file(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.import_file(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长确认导入")
    async def cmd_confirm_import(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.confirm_import(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长设置")
    async def cmd_set_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.set_config(event):
            yield self._maybe_forward(event, r)

    @filter.command("成长查看配置")
    async def cmd_view_config(self, event: AstrMessageEvent) -> AsyncGenerator[MessageEventResult, None]:
        if not _is_group_allowed(self.config_cache, event.get_group_id()):
            return
        async for r in self.admin_handler.view_config(event):
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
                        f"回复 /成长确认导入 {file_name} 执行导入"
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
        if self.db is not None:
            await self.db.close()
        logger.info("Growth system plugin terminated.")
