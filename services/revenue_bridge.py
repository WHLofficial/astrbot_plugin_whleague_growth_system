"""只读主场营收系统数据库桥（赛程/赛季状态）。

主场库不存在或字段不符时静默降级：is_available() 返回 False，
上层（WebUI 赛程页签、/成长 赛程 命令）回退为无联动模式。
仿照主场插件自身的 NegotiationBridge：连接按需打开、mode=ro。
"""

import os

import aiosqlite

from astrbot.api import logger

_DEFAULT_SUBDIR = "astrbot_plugin_whleague_revenue_system"
_DEFAULT_FILENAME = "revenue_system.db"


def _default_revenue_db_path() -> str | None:
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

        p = os.path.join(get_astrbot_plugin_data_path(), _DEFAULT_SUBDIR, _DEFAULT_FILENAME)
        if os.path.exists(p):
            return p
    except Exception:
        pass
    try:
        from astrbot.core.utils.astrbot_path import get_astrbot_data_path

        p = os.path.join(get_astrbot_data_path(), _DEFAULT_SUBDIR, _DEFAULT_FILENAME)
        if os.path.exists(p):
            return p
    except Exception:
        pass
    return None


class RevenueBridge:
    """只读访问主场营收系统数据库。"""

    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._conn: aiosqlite.Connection | None = None
        self._path: str | None = self._resolve_path()

    def _resolve_path(self) -> str | None:
        configured = str(self._cfg.get("revenue_db_path", "") or "").strip()
        if configured:
            return configured if os.path.exists(configured) else None
        return _default_revenue_db_path()

    @property
    def path(self) -> str | None:
        return self._path

    async def connect(self) -> None:
        if self._conn is not None:
            return
        if not self._path:
            return
        try:
            uri = f"file:{self._path}?mode=ro"
            conn = await aiosqlite.connect(uri, uri=True)
            conn.row_factory = aiosqlite.Row
            self._conn = conn
        except Exception as e:
            logger.warning(f"Revenue bridge connect failed: {e}")
            self._conn = None

    async def close(self) -> None:
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def is_available(self) -> bool:
        if not self._path:
            return False
        if self._conn is None:
            await self.connect()
        return self._conn is not None

    async def _query(self, sql: str, params=()) -> list[dict] | None:
        """执行只读查询；库不可用或查询失败返回 None（调用方据此降级）。"""
        if not await self.is_available():
            return None
        try:
            async with self._conn.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]
        except Exception as e:
            logger.warning(f"Revenue bridge query failed: {e}")
            return None

    async def get_league_state(self) -> dict | None:
        """当前赛季/窗口状态 + 赛季名。"""
        rows = await self._query(
            "SELECT season_number, window_seq, current_round FROM league_state WHERE id = 1"
        )
        if rows is None or not rows:
            return {} if rows is not None else None
        state = dict(rows[0])
        name_rows = await self._query(
            "SELECT name FROM season_names WHERE season_number = ?",
            (state.get("season_number"),),
        )
        if name_rows and name_rows[0].get("name"):
            state["season_name"] = name_rows[0]["name"]
        else:
            state["season_name"] = ""
        return state

    async def list_rounds(self) -> list[dict] | None:
        """按轮次聚合：场次总数与已录比分数。保留文字前缀轮次原样返回。"""
        return await self._query(
            """
            SELECT round_no,
                   COUNT(*) AS total,
                   SUM(CASE WHEN result IS NOT NULL AND result != '' THEN 1 ELSE 0 END)
                       AS played
            FROM matches
            GROUP BY round_no
            ORDER BY round_no
            """
        )

    async def list_fixtures(
        self, round_no: int | None = None, played: bool | None = None
    ) -> list[dict] | None:
        """列出对阵（可选按轮次/是否已赛过滤），按轮次与主队排序。"""
        sql = (
            "SELECT id, season_number, window_seq, round_no, competition, "
            "home_team, away_team, weather, result, score "
            "FROM matches"
        )
        conds, params = [], []
        if round_no is not None:
            conds.append("round_no = ?")
            params.append(round_no)
        if played is True:
            conds.append("result IS NOT NULL AND result != ''")
        elif played is False:
            conds.append("(result IS NULL OR result = '')")
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " ORDER BY round_no, id"
        return await self._query(sql, params)

    async def get_fixture(self, fixture_key: str) -> dict | None:
        """取单场对阵详情；找不到返回 {}，库不可用返回 None。"""
        if fixture_key == "":
            return {}
        try:
            key_int = int(fixture_key)
        except (TypeError, ValueError):
            return {}
        rows = await self._query(
            "SELECT id, season_number, window_seq, round_no, competition, "
            "home_team, away_team, weather, result, score "
            "FROM matches WHERE id = ?",
            (key_int,),
        )
        if rows is None:
            return None
        return rows[0] if rows else {}

    async def refresh_path(self) -> bool:
        """重新解析数据库路径（配置热更后调用），并重置连接。"""
        new_path = self._resolve_path()
        changed = new_path != self._path
        self._path = new_path
        await self.close()
        return changed or await self.is_available()
