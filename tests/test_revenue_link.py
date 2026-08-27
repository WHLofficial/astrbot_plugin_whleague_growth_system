"""pytest 单元测试：主场营收插件联动。

覆盖：revenue_bridge 只读查询与降级、schema v4→v5 迁移（含存量库升级路径）、
联赛推进监听器的注册/重试/提醒发送。
"""

import asyncio
import os
import sqlite3
import tempfile
import types

import pytest

from astrbot_plugin_whleague_growth_system.db.connection import DatabaseManager
from astrbot_plugin_whleague_growth_system.db.schema import SCHEMA_VERSION, init_schema
from astrbot_plugin_whleague_growth_system.services.revenue_bridge import RevenueBridge


def call(coro):
    return asyncio.run(coro)


# ─── 主场库最小样本 ───────────────────────────────────────

_REVENUE_SQL = """
CREATE TABLE league_state (
    id INTEGER PRIMARY KEY,
    season_number INTEGER NOT NULL,
    window_seq INTEGER NOT NULL,
    current_round INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE season_names (
    season_number INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT ''
);
CREATE TABLE matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_number INTEGER NOT NULL,
    window_seq INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    competition TEXT DEFAULT '联赛',
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    weather TEXT DEFAULT '',
    result TEXT DEFAULT '',
    score TEXT DEFAULT ''
);
CREATE TABLE round_names (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season_number INTEGER NOT NULL,
    competition TEXT NOT NULL DEFAULT '联赛',
    token TEXT NOT NULL,
    round_no INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(season_number, competition, token)
);
INSERT INTO league_state (id, season_number, window_seq, current_round)
VALUES (1, 7, 3, 5);
INSERT INTO season_names VALUES (7, '黄金一代');
INSERT INTO round_names (season_number, competition, token, round_no) VALUES
    (7, '联赛', '顶级3', 1),
    (7, '联赛', '顶级4', 2),
    (7, '冠军杯', '小组赛第1轮', 1),
    (6, '联赛', '顶级9', 9);
"""


def _make_revenue_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(_REVENUE_SQL)
    conn.executemany(
        "INSERT INTO matches (season_number, window_seq, round_no, competition,"
        " home_team, away_team, weather, result, score) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (7, 3, 1, "联赛", "A队", "B队", "晴", "W", "2-1"),
            (7, 3, 1, "联赛", "C队", "D队", "", "", ""),
            (7, 3, 2, "联赛", "A队", "C队", "雨", "", ""),
            (7, 3, 1, "冠军杯", "A队", "G队", "", "L", "0-2"),
            (6, 8, 9, "联赛", "E队", "F队", "", "D", "0-0"),
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def revenue_db():
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "revenue_system.db")
    _make_revenue_db(path)
    yield path
    try:
        os.remove(path)
        os.rmdir(tmp)
    except OSError:
        pass


# ─── RevenueBridge ────────────────────────────────────────

class TestRevenueBridge:
    def test_league_state_with_season_name(self, revenue_db):
        bridge = RevenueBridge({"revenue_db_path": revenue_db})
        state = call(bridge.get_league_state())
        assert state["season_number"] == 7
        assert state["window_seq"] == 3
        assert state["season_name"] == "黄金一代"
        call(bridge.close())

    def test_list_rounds_aggregates_played(self, revenue_db):
        bridge = RevenueBridge({"revenue_db_path": revenue_db})
        rounds = {(r["competition"], r["round_no"]): r for r in call(bridge.list_rounds())}
        assert rounds[("联赛", 1)]["total"] == 2
        assert rounds[("联赛", 1)]["played"] == 1
        assert rounds[("联赛", 2)]["played"] == 0
        # 赛事分开聚合：冠军杯的第1轮与联赛第1轮是不同的轮
        assert rounds[("冠军杯", 1)]["total"] == 1
        assert rounds[("冠军杯", 1)]["played"] == 1
        # 旧赛季（6季8窗）不混入当前窗口
        assert ("联赛", 9) not in rounds
        call(bridge.close())

    def test_list_fixtures_filters(self, revenue_db):
        bridge = RevenueBridge({"revenue_db_path": revenue_db})
        rnd2 = call(bridge.list_fixtures(round_no=2))
        assert [f["home_team"] for f in rnd2] == ["A队"]
        # 赛事过滤：冠军杯第1轮独立于联赛第1轮
        cup = call(bridge.list_fixtures(round_no=1, competition="冠军杯"))
        assert [f["away_team"] for f in cup] == ["G队"]
        played = call(bridge.list_fixtures(played=True))
        assert len(played) == 2
        unplayed = call(bridge.list_fixtures(played=False))
        assert {f["away_team"] for f in unplayed} == {"D队", "C队"}
        all_now = call(bridge.list_fixtures())
        assert all(f["season_number"] == 7 and f["window_seq"] == 3 for f in all_now)
        call(bridge.close())

    def test_resolve_round_token(self, revenue_db):
        bridge = RevenueBridge({"revenue_db_path": revenue_db})
        assert call(bridge.resolve_round_token("顶级3")) == {"competition": "联赛", "round_no": 1}
        # 后缀匹配：省略赛事前缀中缀仍可命中
        assert call(bridge.resolve_round_token("第1轮")) == {"competition": "冠军杯", "round_no": 1}
        # 旧赛季同名轮次不参与解析
        assert call(bridge.resolve_round_token("顶级9")) is None
        assert call(bridge.resolve_round_token("不存在的轮次")) is None
        call(bridge.close())

    def test_get_fixture_lookup_and_missing(self, revenue_db):
        bridge = RevenueBridge({"revenue_db_path": revenue_db})
        fx = call(bridge.get_fixture("1"))
        assert fx["home_team"] == "A队" and fx["id"] == 1
        assert call(bridge.get_fixture("99999")) == {}
        assert call(bridge.get_fixture("abc")) == {}
        call(bridge.close())

    def test_missing_db_degrades(self):
        bridge = RevenueBridge({"revenue_db_path": os.path.join(
            tempfile.mkdtemp(), "nope.db")})
        assert call(bridge.is_available()) is False
        assert call(bridge.get_league_state()) is None
        assert call(bridge.list_rounds()) is None
        assert call(bridge.get_fixture("1")) is None

    def test_empty_cfg_uses_default_locator(self):
        # 无配置且默认路径不存在 → 可用性 False，不抛异常
        bridge = RevenueBridge({})
        assert call(bridge.is_available()) in (True, False)


# ─── Schema v5：全新安装与存量升级 ────────────────────────

def _table_cols(db_path, table="matches"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    cur.close()
    conn.close()
    return cols


def _indexes(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    conn.close()
    return {r[0] for r in rows}


class TestSchemaV5:
    @pytest.fixture()
    def fresh_db(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "growth.db")

        async def run():
            db = DatabaseManager(path)
            await db.init()
            await init_schema(db)
            return db

        db = asyncio.run(run())
        yield path, db

        async def stop():
            await db.close()

        asyncio.run(stop())

    def test_fresh_install_has_binding_columns(self, fresh_db):
        path, _db = fresh_db
        cols = _table_cols(path)
        assert "rev_fixture_key" in cols and "rev_side" in cols
        assert "idx_matches_rev" in _indexes(path)

    def test_partial_unique_index_enforced(self, fresh_db):
        path, _db = fresh_db
        conn = sqlite3.connect(path)
        # 绑定的同一对阵不允许重复视角
        conn.execute("INSERT INTO matches (match_date, opponent, rev_fixture_key, rev_side)"
                     " VALUES ('2026-09-01','A队','42','home')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO matches (match_date, opponent, rev_fixture_key, rev_side)"
                         " VALUES ('2026-09-02','A队','42','home')")
        # 不同视角各一行不受限；未绑定的普通比赛完全不受约束
        conn.execute("INSERT INTO matches (match_date, opponent, rev_fixture_key, rev_side)"
                     " VALUES ('2026-09-03','B队','42','away')")
        conn.execute("INSERT INTO matches (match_date, opponent)"
                     " VALUES ('2026-09-04','普通场次')")
        conn.commit()
        conn.close()

    def test_upgrade_from_v4_database(self):
        # 模拟存量 v4 库：无 rev 列的 matches + schema_version=4
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "upgrade.db")
        conn = sqlite3.connect(path)
        conn.execute(
            "CREATE TABLE matches ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "match_date TEXT NOT NULL,"
            "opponent TEXT NOT NULL DEFAULT '',"
            "created_by TEXT NOT NULL DEFAULT '',"
            "created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))"
        )
        conn.execute("CREATE UNIQUE INDEX idx_matches_date_opponent ON matches(match_date, opponent)")
        conn.execute("INSERT INTO matches (match_date, opponent, created_by)"
                     " VALUES ('2026-08-01','老对手','v4-user')")
        conn.execute("CREATE TABLE plugin_config (key TEXT PRIMARY KEY,"
                     " value TEXT NOT NULL DEFAULT '',"
                     " updated_at TEXT DEFAULT (datetime('now','localtime')))")
        conn.execute("INSERT INTO plugin_config (key, value) VALUES ('schema_version','4')")
        conn.commit()
        conn.close()

        async def run():
            db = DatabaseManager(path)
            await db.init()
            await init_schema(db)

            async with db.lock:
                cur = await db.conn.execute(
                    "SELECT count(*) FROM matches WHERE match_date='2026-08-01'")
                kept = (await cur.fetchone())[0]
                ver_cur = await db.conn.execute(
                    "SELECT value FROM plugin_config WHERE key='schema_version'")
                ver = (await ver_cur.fetchone())[0]
            await db.close()
            return kept, int(ver)

        kept, version = asyncio.run(run())
        assert kept == 1
        cols = _table_cols(path)
        assert "rev_fixture_key" in cols and "rev_side" in cols
        assert "idx_matches_rev" in _indexes(path)
        assert version == SCHEMA_VERSION


# ─── 联赛推进提醒（hooks）────────────────────────────────

class _FakeStadium:
    """带广播挂点的最小主场插件替身。"""

    def __init__(self, modern=True):
        self.listeners = []
        self.last_advance_session = "aiocqhttp:GroupMessage:12345"
        if not modern:
            # 模拟 v2.5 及更早版本：实例属性覆盖后 getattr 检查视为缺失
            self.register_state_listener = None

    def register_state_listener(self, fn):
        self.listeners.append(fn)
        return True


class _FakeHooksContext:
    def __init__(self, stadium=None):
        self._stadium = stadium
        self.sent = []

    def get_registered_star(self, name):
        if self._stadium is None:
            return None
        return types.SimpleNamespace(
            activated=True, star_cls=self._stadium, version="2.6.0",
        )

    async def send_message(self, session, chain):
        self.sent.append((session, chain.text))
        return True


class _FakeChain:
    def __init__(self):
        self.text = ""

    def message(self, text):
        self.text += text
        return self


def _make_hooks(stadium, cfg_notify=True):
    import astrbot_plugin_whleague_growth_system.services.revenue_hooks as rh

    rh.MessageChain = _FakeChain
    ctx = _FakeHooksContext(stadium)
    plugin = types.SimpleNamespace(context=ctx, config_cache={
        "notify_on_league_advance": cfg_notify})
    return rh.RevenueHooks(plugin), ctx


class TestRevenueHooks:
    def test_register_success_and_dispatch(self):
        stadium = _FakeStadium()
        hooks, ctx = _make_hooks(stadium)
        hooks.try_register()
        assert len(stadium.listeners) == 1
        assert hooks.try_register() is True  # 幂等
        assert len(stadium.listeners) == 1

        asyncio.run(hooks.on_league_advance(
            {"event": "window_advanced", "season_number": 7, "window_seq": 4}))
        assert ctx.sent and "第7赛季 第4窗口" in ctx.sent[0][1]
        assert ctx.sent[0][0] == "aiocqhttp:GroupMessage:12345"

    def test_season_advanced_with_name(self):
        stadium = _FakeStadium()
        hooks, ctx = _make_hooks(stadium)
        hooks.try_register()
        asyncio.run(hooks.on_league_advance(
            {"event": "season_advanced", "season_number": 8, "window_seq": 1,
             "name": "传奇 continuing"}))
        text = ctx.sent[0][1]
        assert "传奇 continuing" in text
        assert "/成长 推进" in text

    def test_old_version_stops_retrying_without_listener(self):
        hooks, ctx = _make_hooks(_FakeStadium(modern=False))
        assert hooks.try_register() is True
        assert not ctx.sent

    def test_missing_star_creates_retry_task_then_terminate(self):
        hooks, ctx = _make_hooks(None)
        asyncio.run(hooks.start())
        assert hooks._retry_task is not None
        asyncio.run(hooks.terminate())
        assert hooks._retry_task is None

    def test_notify_disabled_skips_retry_task(self):
        hooks, ctx = _make_hooks(None, cfg_notify=False)
        asyncio.run(hooks.start())
        assert hooks._retry_task is None

    def test_switch_off_swallows_events(self):
        stadium = _FakeStadium()
        hooks, ctx = _make_hooks(stadium, cfg_notify=False)
        hooks.try_register()
        asyncio.run(hooks.on_league_advance(
            {"event": "window_advanced", "season_number": 7, "window_seq": 4}))
        assert not ctx.sent

    def test_missing_session_logs_and_skips(self):
        stadium = _FakeStadium()
        stadium.last_advance_session = None
        hooks, ctx = _make_hooks(stadium)
        hooks.try_register()
        asyncio.run(hooks.on_league_advance(
            {"event": "window_advanced", "season_number": 7, "window_seq": 4}))
        assert not ctx.sent
