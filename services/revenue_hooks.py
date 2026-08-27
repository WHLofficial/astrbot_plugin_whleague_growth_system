"""主场插件联赛状态联动：监听推进事件，在原群实时提醒开启新成长期。

经 context.get_registered_star 取主场插件实例并注册状态监听器（需主场
v2.6+）。对方未安装或未升级时静默待机；未找到插件实例时以 10 分钟惰性
重试应对插件加载顺序。提醒会话取自主场插件在推进命令里暂存的
last_advance_session，因此只在广播发生的那一个群发送。
"""

import asyncio

from astrbot.api import logger
from astrbot.api.event import MessageChain

_STAR_NAME = "astrbot_plugin_whleague_revenue_system"
_RETRY_INTERVAL = 600


class RevenueHooks:
    """挂接主场插件的窗口/赛季推进广播。"""

    def __init__(self, plugin) -> None:
        self._plugin = plugin
        self._registered = False
        self._revenue_star = None
        self._retry_task: asyncio.Task | None = None

    async def start(self) -> None:
        if self.try_register():
            return
        if not bool(self._plugin.config_cache.get("notify_on_league_advance", True)):
            return
        # 主场插件尚未加载（启动顺序在后）时后台惰性重试，直到注册成功
        self._retry_task = asyncio.create_task(self._retry_loop(), name="revenue-hooks-retry")

    def try_register(self) -> bool:
        """幂等注册监听器；返回是否已完成（成功或确认无需再试）。"""
        if self._registered:
            return True
        try:
            star = self._plugin.context.get_registered_star(_STAR_NAME)
        except Exception as e:
            logger.warning(f"查询主场插件实例失败: {e}")
            return False
        if star is None or not star.activated or star.star_cls is None:
            return False
        register = getattr(star.star_cls, "register_state_listener", None)
        if register is None or not hasattr(star.star_cls, "last_advance_session"):
            # 已安装但版本过旧：重载/升级前不会再变化，不再重试
            logger.info("主场插件未提供推进广播（需 v2.6+），赛程联动提醒保持停用。")
            self._registered = True
            self._revenue_star = star
            return True
        registered = bool(register(self.on_league_advance))
        if registered:
            self._revenue_star = star
            logger.info("已注册联赛推进监听器（%s）。", _STAR_NAME)
        self._registered = True
        return True

    async def _retry_loop(self) -> None:
        while True:
            await asyncio.sleep(_RETRY_INTERVAL)
            try:
                if self.try_register():
                    return
            except Exception:
                logger.exception("联赛推进监听器惰性注册失败")

    async def terminate(self) -> None:
        task = self._retry_task
        self._retry_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._registered = False

    async def on_league_advance(self, state_event: dict) -> None:
        """主场 fixture_service 广播的推进事件入口（同步异常已被对方隔离）。"""
        try:
            await self._handle(state_event)
        except Exception:
            logger.exception("处理联赛推进提醒失败")

    async def _handle(self, ev: dict) -> None:
        cfg = self._plugin.config_cache
        if not bool(cfg.get("notify_on_league_advance", True)):
            return
        kind = ev.get("event")
        if kind == "window_advanced":
            title = f"🏟️ 联赛已进入 第{ev.get('season_number')}赛季 第{ev.get('window_seq')}窗口"
        elif kind == "season_advanced":
            name = str(ev.get("name") or "").strip()
            suffix = f"「{name}」" if name else ""
            title = f"🏆 新赛季{suffix}开启（第{ev.get('season_number')}赛季 第{ev.get('window_seq')}窗口）"
        else:
            return
        # 会话暂存在主场插件实例（star_cls）上，StarMetadata 本身无此属性
        star_cls = getattr(self._revenue_star, "star_cls", None)
        session = getattr(star_cls, "last_advance_session", None)
        if not session:
            logger.info("收到联赛推进事件但未捕获到会话，跳过群内提醒。")
            return
        text = (
            f"{title}\n"
            "若需开启新的成长期，请管理员发送：/成长 推进 <名称> [保留|清零]"
        )
        try:
            sent = await self._plugin.context.send_message(session, MessageChain().message(text))
            if sent is False:
                logger.info("联赛推进提醒未送达（平台未匹配会话）。")
        except Exception as e:
            logger.warning(f"联赛推进提醒发送失败: {e}")
