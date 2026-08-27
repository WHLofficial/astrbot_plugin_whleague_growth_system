"""pytest 单元测试：插件 WebUI 后端（web_api.py）。

覆盖：路由注册、比赛录入校验、配置强转、待确认导入驳回、
上传文件名清洗（通过 upload_file 的前置校验逻辑）、总览聚合。

web 桩来自 tests/stubs.py（astrbot.api.web）；request 代理在测试内替换。
"""

import asyncio
import json
import os
import tempfile

import pytest

from astrbot_plugin_whleague_growth_system import web_api as webapi_mod
from astrbot_plugin_whleague_growth_system.config.defaults import DEFAULT_CONFIG, validate_and_cast
from astrbot_plugin_whleague_growth_system.db.connection import DatabaseManager
from astrbot_plugin_whleague_growth_system.db.dao import GrowthDAO
from astrbot_plugin_whleague_growth_system.db.schema import init_schema
from astrbot_plugin_whleague_growth_system.services.growth_service import GrowthService
from astrbot_plugin_whleague_growth_system.services.import_service import GrowthImportService
from astrbot_plugin_whleague_growth_system.services.rule_parser import parse_rule_json


def _rule_json():
    return {
        "stats": {
            "goal": {"name": "进球", "xp": 10},
            "assist": {"name": "助攻", "xp": 5},
        },
        "milestones": [
            {"stat": "goal", "period": "period", "threshold": 5, "xp": 50},
        ],
        "level_xp": 100,
    }


class _FakeRegistry:
    def __init__(self):
        self.routes = {}

    def register_web_api(self, route, handler, methods, desc):
        self.routes[(route, tuple(methods))] = handler


class _FakePlugin:
    """最小插件替身：只暴露 WebApi 依赖的属性。"""

    def __init__(self, tmp_dir):
        self._tmp = tmp_dir
        self.context = _FakeRegistry()
        self.config_cache = dict(DEFAULT_CONFIG)
        self.persisted = {}

    async def _persist_config(self, key, value):
        # 与 main.py 同语义：仅更新内存缓存（测试不落 AstrBot 配置）
        self.config_cache[key] = value
        self.persisted[key] = value

    async def _bootstrap(self):
        db = DatabaseManager(os.path.join(self._tmp, "test.db"))
        await db.init()
        await init_schema(db)
        self.db = db
        self.dao = GrowthDAO(db)
        self.growth_service = GrowthService(db, self.dao, self.config_cache.get)
        self.import_service = GrowthImportService(
            db, self.dao, self.config_cache.get, self.growth_service
        )
        from astrbot_plugin_whleague_growth_system.services.export_service import ExportService

        self.export_service = ExportService(db, self.dao)
        rule = parse_rule_json(json.dumps(_rule_json()), 100)
        await self.growth_service.save_rule(rule, "test-rule", "tester")

        async def _tx(conn):
            await self.dao.upsert_player(conn, "p01", "球员一", "A队", "tester")

        await db.execute_transaction(_tx)


class _Query:
    def __init__(self, params=None):
        self._params = dict(params or {})

    def get(self, key, default=None, type=None):
        raw = self._params.get(key)
        if raw is None:
            return default
        if type is not None:
            try:
                return type(raw)
            except (TypeError, ValueError):
                return default
        return raw

    def getlist(self, key):
        v = self._params.get(key)
        return [v] if v is not None else []


class _Files:
    def __init__(self, mapping=None):
        self._mapping = dict(mapping or {})

    def get(self, key):
        return self._mapping.get(key)


def set_request(query=None, body=None, username="tester", files=None):
    """配置 astrbot.api.web 桩上共享的 request 替身状态。"""
    from astrbot.api import web as web_stub
    from types import SimpleNamespace

    req = web_stub.request
    req.query = _Query(query) if query is not None else SimpleNamespace(get=lambda k, d=None, t=None: d)
    req.username = username

    async def _json(default=None):
        if body is not None:
            return body
        return default if default is not None else {}

    req.json = _json
    req.files = lambda: _async_files(files or {})


@pytest.fixture()
def api():
    tmp = tempfile.mkdtemp()
    plugin = _FakePlugin(tmp)
    asyncio.run(plugin._bootstrap())
    inst = webapi_mod.WebApi(plugin)
    yield inst
    try:
        for f in os.listdir(tmp):
            os.remove(os.path.join(tmp, f))
        os.rmdir(tmp)
    except OSError:
        pass


def call(coro):
    return asyncio.run(coro)


async def _async_files(files):
    return _Files(files)


# ─── 注册与连通 ───────────────────────────────────────────

def test_routes_registered(api):
    routes = {route for route, _ in api._plugin.context.routes}
    assert any(r.endswith("/ping") for r in routes)
    assert any(r.endswith("/matches/record") for r in routes)
    assert any("/config/" in r for r in routes)
    assert all(route.startswith(f"/{webapi_mod.PLUGIN_NAME}") for route, _ in api._plugin.context.routes)


def test_ping(api):
    res = call(api.ping())
    assert res["data"]["pong"] is True


# ─── 总览 ─────────────────────────────────────────────────

def test_overview_aggregates(api):
    set_request(username="t")
    res = call(api.overview())
    data = res["data"]
    assert data["player_count"] == 1
    assert data["match_count"] == 0
    assert data["period"]["is_current"] == 1 or data["period"] is not None


# ─── 球员 / 排行 ──────────────────────────────────────────

def test_players_list_and_search(api):
    set_request(query={"page": "1"}, username="t")
    res = call(api.list_players())
    assert res["data"]["total"] == 1

    set_request(query={"q": "球员"}, username="t")
    res2 = call(api.list_players())
    assert res2["data"]["total"] == 1

    set_request(query={"q": "不存在"}, username="t")
    res3 = call(api.list_players())
    assert res3["data"]["total"] == 0


def test_players_detail_404():
    # 通过 _wrapped 路径验证 404 分支
    tmp = tempfile.mkdtemp()
    plugin = _FakePlugin(tmp)

    async def run():
        await plugin._bootstrap()
        webapi_mod.WebApi(plugin)
        handler = plugin.context.routes[(f"/{webapi_mod.PLUGIN_NAME}/players/<player_uid>", ("GET",))]
        set_request(username="t")
        return await handler(player_uid="ghost")

    res = asyncio.run(run())
    assert res.status_code == 404


def test_rank_invalid_mode_rejected(api):
    set_request(query={"mode": "bogus"}, username="t")
    with pytest.raises(ValueError):
        call(api.rank())


# ─── 比赛录入校验 ────────────────────────────────────────

def test_record_match_requires_uid(api):
    set_request(body={"match_date": "2026-09-01", "stats": {"goal": 1}}, username="t")
    with pytest.raises(ValueError) as e:
        call(api.record_match())
    assert "UID" in str(e.value)


def test_record_match_requires_date(api):
    set_request(body={"player_uid": "p01", "stats": {"goal": 1}}, username="t")
    with pytest.raises(ValueError):
        call(api.record_match())


def test_record_match_requires_stats(api):
    set_request(body={"player_uid": "p01", "match_date": "2026-09-01"}, username="t")
    with pytest.raises(ValueError) as e:
        call(api.record_match())
    assert "数据项" in str(e.value)


def test_record_match_rejects_bad_number(api):
    set_request(
        body={"player_uid": "p01", "match_date": "2026-09-01", "stats": {"goal": "abc"}},
        username="t",
    )
    with pytest.raises(ValueError) as e:
        call(api.record_match())
    assert "不是合法数字" in str(e.value)


def test_record_match_success_and_milestone(api):
    set_request(
        body={
            "player_uid": "p01",
            "match_date": "2026-09-01",
            "opponent": "B队",
            "stats": {"goal": 6},
        },
        username="webtester",
    )
    res = call(api.record_match())
    d = res["data"]
    assert d["stat_xp"] == 60          # 6 * 10
    assert d["bonus_xp"] == 50         # 里程碑 goal>=5 period +50
    assert d["total_xp"] == 110


def test_record_match_zero_stats_only_rejected(api):
    set_request(
        body={"player_uid": "p01", "match_date": "2026-09-01", "stats": {"goal": "0"}},
        username="t",
    )
    with pytest.raises(ValueError):
        call(api.record_match())


# ─── 配置 ─────────────────────────────────────────────────

def test_config_groups_shape(api):
    set_request(username="t")
    res = call(api.get_config())
    titles = [g["title"] for g in res["data"]["groups"]]
    assert "基础" in titles
    assert all("items" in g for g in res["data"]["groups"])


def test_put_config_casts_int(api):
    set_request(body={"value": "20"}, username="t")
    res = call(api.put_config("rank_page_size"))
    assert res["data"]["value"] == 20
    assert api._plugin.config_cache["rank_page_size"] == 20


def test_put_config_rejects_out_of_bounds(api):
    set_request(body={"value": "99999"}, username="t")   # 上限 100
    with pytest.raises(ValueError):
        call(api.put_config("rank_page_size"))


def test_put_config_unknown_key(api):
    set_request(body={"value": "x"}, username="t")
    with pytest.raises(ValueError):
        call(api.put_config("no_such_key"))


# ─── 待确认导入 ──────────────────────────────────────────

def test_pending_flow_insert_list_reject(api):
    call(_insert_pending(api))
    set_request(username="t")
    listed = call(api.pending_imports())
    row = next(r for r in listed["data"]["pending"] if r["kind"] == "rule")
    assert row["status"] == "pending"

    res = call(api.reject_pending(str(row["id"])))
    assert res["data"]["status"] == "rejected"
    again = call(api.pending_imports())
    assert not [r for r in again["data"]["pending"] if r["kind"] == "rule" and r["status"] == "pending"]


async def _insert_pending(api):
    from astrbot_plugin_whleague_growth_system.utils.security import sanitize_text

    return await api._plugin.dao.insert_pending("rule", "规则_t.json", "预览", sanitize_text("tester"))


def test_reject_nonexistent(api):
    set_request(username="t")
    with pytest.raises(ValueError):
        call(api.reject_pending("9999"))


def test_reject_invalid_id(api):
    set_request(username="t")
    with pytest.raises(ValueError):
        call(api.reject_pending("abc"))


# ─── 上传文件名校验 ──────────────────────────────────────

class _FakeUpload:
    def __init__(self, filename, size=100):
        self.filename = filename
        self.content_length = size


def test_upload_unknown_kind(api):
    set_request(files={"file": _FakeUpload("x.csv")}, username="t")
    with pytest.raises(ValueError):
        call(api.upload_file("bogus"))


def test_upload_missing_file_field(api):
    set_request(files={}, username="t")
    with pytest.raises(ValueError) as e:
        call(api.upload_file("rule"))
    assert "file" in str(e.value)


def test_upload_bad_extension_cleaned_via_path_logic(api):
    # 扩展名白名单在前置分支（upload_file 内）触发
    set_request(files={"file": _FakeUpload("恶意.exe")}, username="t")
    with pytest.raises(ValueError) as e:
        call(api.upload_file("rule"))
    assert ".json" in str(e.value)


def test_upload_sanitizes_filename_and_creates_pending(api):
    content = json.dumps(_rule_json()).encode("utf-8")

    class _Upload:
        filename = "..\\..\\evil 规则_a.json"
        content_length = len(content)

        async def save(self, dest):
            with open(dest, "wb") as fh:
                fh.write(content)

    set_request(files={"file": _Upload()}, username="t")
    res = call(api.upload_file("rule"))
    d = res["data"]
    assert d["pending_id"] > 0
    assert "\\" not in d["file_name"] and "/" not in d["file_name"]
    saved = api._plugin.import_service.imports_dir / d["file_name"]
    assert saved.is_file() and saved.parent.resolve() == api._plugin.import_service.imports_dir.resolve()
    assert "成长规则" in d["preview"] or d["preview"]  # 预览非空


# ─── 导出下载 ─────────────────────────────────────────────

def test_export_download_current(api):
    set_request(query={}, username="t")
    res = call(api.download_export())
    assert isinstance(res, str)  # file_response 桩返回路径字符串


# ─── 主场赛程联动 ─────────────────────────────────────────

def _make_revenue_db(path):
    import sqlite3

    conn = sqlite3.connect(path)
    conn.executescript(
        """
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
        INSERT INTO matches (season_number, window_seq, round_no, home_team, away_team, weather, result, score) VALUES
            (7, 2, 1, 'A队', 'B队', '晴', 'home', '2-1'),
            (7, 2, 1, 'C队', 'D队', '', '', ''),
            (7, 2, 2, 'A队', 'E队', '', '', '');
        CREATE TABLE league_state (
            id INTEGER PRIMARY KEY CHECK (id=1),
            season_number INTEGER, window_seq INTEGER, current_round INTEGER
        );
        INSERT INTO league_state VALUES (1, 7, 2, 1);
        CREATE TABLE season_names (season_number INTEGER PRIMARY KEY, name TEXT);
        INSERT INTO season_names VALUES (7, '秋赛');
        """
    )
    conn.commit()
    conn.close()


@pytest.fixture()
def rev_api(api):
    """在 api 的临时目录里放一个最小 revenue 库并接上只读桥。"""
    from astrbot_plugin_whleague_growth_system.services.revenue_bridge import RevenueBridge

    db_path = os.path.join(api._plugin._tmp, "revenue_system.db")
    _make_revenue_db(db_path)
    bridge = RevenueBridge({"revenue_db_path": db_path})
    api._plugin.revenue_bridge = bridge
    yield api
    try:
        asyncio.run(bridge.close())
    except Exception:
        pass


def test_fixture_routes_registered(api):
    routes = {route for route, _ in api._plugin.context.routes}
    assert any(r.endswith("/fixtures/rounds") for r in routes)
    assert any(r.endswith("/fixtures") and not r.rstrip("/").endswith("rounds") for r in routes)
    assert any("/fixtures/detail/" in r for r in routes)
    assert any(r.endswith("/fixtures/appearance") for r in routes)


def test_fixtures_unavailable_when_db_missing(api):
    from astrbot_plugin_whleague_growth_system.services.revenue_bridge import RevenueBridge

    missing = os.path.join(api._plugin._tmp, "nope.db")
    api._plugin.revenue_bridge = RevenueBridge({"revenue_db_path": missing})
    res = call(api.fixtures_rounds())
    assert res["data"]["available"] is False


def test_fixtures_rounds_and_list(rev_api):
    api = rev_api
    set_request(username="t")
    data = call(api.fixtures_rounds())["data"]
    assert data["available"] is True
    assert data["state"]["season_number"] == 7
    assert data["state"]["season_name"] == "秋赛"
    assert [r["round_no"] for r in data["rounds"]] == [1, 2]
    assert data["rounds"][0]["total"] == 2 and data["rounds"][0]["played"] == 1

    set_request(query={"round": "1"}, username="t")
    res = call(api.fixtures_list())["data"]
    assert {f["fixture_key"] for f in res["fixtures"]} == {"1", "2"}
    assert all(f["season_number"] == 7 for f in res["fixtures"])

    set_request(query={"played": "1"}, username="t")
    only_played = call(api.fixtures_list())["data"]["fixtures"]
    assert {f["fixture_key"] for f in only_played} == {"1"}

    set_request(query={"round": "x"}, username="t")
    with pytest.raises(ValueError):
        call(api.fixtures_list())


async def _match_rows(plugin, where: str):
    def _query(conn):
        return conn.execute(f"SELECT match_date, opponent, rev_fixture_key, rev_side FROM matches WHERE {where}")

    # DatabaseManager.execute_transaction 需要异步函数，这里包一层
    async def tx(conn):
        cur = await _query(conn)
        return [dict(zip(("match_date", "opponent", "rev_fixture_key", "rev_side"), row)) for row in await cur.fetchall()]

    return await plugin.db.execute_transaction(tx)


def test_save_appearance_reuses_legacy_row(rev_api):
    """普通通道先建的比赛（无绑定），绑定保存时应被采纳而不是另开一行。"""
    api = rev_api
    set_request(
        body={"player_uid": "p01", "match_date": "2026-09-01", "opponent": "B队",
              "stats": {"assist": "1"}},
        username="t",
    )
    call(api.record_match())
    legacy = asyncio.run(_match_rows(api._plugin, "opponent='B队'"))
    assert len(legacy) == 1 and legacy[0]["rev_fixture_key"] is None

    set_request(
        body={"rev_fixture_key": "1", "rev_side": "home", "player_uid": "p01",
              "stats": {"goal": "2"}, "match_date": "2026-09-01"},
        username="t",
    )
    call(api.save_fixture_appearance())

    rows = asyncio.run(_match_rows(api._plugin, "opponent='B队'"))
    assert len(rows) == 1
    assert rows[0]["rev_fixture_key"] == "1" and rows[0]["rev_side"] == "home"


def test_save_appearance_and_detail_roundtrip(rev_api):
    api = rev_api
    set_request(
        body={"rev_fixture_key": "1", "rev_side": "home", "player_uid": "p01",
              "stats": {"goal": "3"}},
        username="admin",
    )
    saved = call(api.save_fixture_appearance())["data"]
    assert saved["stat_xp"] == 30 and saved["bonus_xp"] == 0

    detail = call(api.fixture_detail("1"))["data"]
    fx = detail["fixture"]
    assert fx["fixture_key"] == "1" and fx["home_team"] == "A队" and fx["weather"] == "晴"
    rosters = detail["rosters"]
    assert [p["player_uid"] for p in rosters["home"]] == ["p01"]
    assert rosters["unmatched"] == []
    entry = detail["appearances"].get("p01")
    assert entry is not None and entry["stats"] == {"goal": 3.0}
    assert entry["period_no"] >= 1
    assert detail["has_rule"] is True


def test_fixture_detail_unknown_key_404(rev_api):
    api = rev_api
    set_request(username="t")
    res = call(api.fixture_detail("999"))
    assert res.status_code == 404


def test_save_appearance_locked_for_past_period(rev_api):
    api = rev_api
    body = {"rev_fixture_key": "1", "rev_side": "away", "player_uid": "p01", "stats": {"goal": "1"}}
    set_request(body=body, username="t")
    call(api.save_fixture_appearance())

    async def tx(conn):
        # 外键要求 period_no 必须真实存在，先造一个已结束的旧期再改归属
        await conn.execute(
            "INSERT OR IGNORE INTO growth_periods (period_no, name, is_current) VALUES (9, '旧期', 0)"
        )
        await conn.execute("UPDATE appearances SET period_no = 9")

    asyncio.run(api._plugin.db.execute_transaction(tx))

    with pytest.raises(ValueError) as ei:
        call(api.save_fixture_appearance())
    assert "锁定" in str(ei.value)


def test_save_appearance_validation(rev_api):
    api = rev_api
    cases = [
        {"rev_fixture_key": "", "rev_side": "home", "player_uid": "p01", "stats": {"goal": "1"}},
        {"rev_fixture_key": "1", "rev_side": "midfield", "player_uid": "p01", "stats": {"goal": "1"}},
        {"rev_fixture_key": "1", "rev_side": "home", "player_uid": "", "stats": {"goal": "1"}},
        {"rev_fixture_key": "1", "rev_side": "home", "player_uid": "p01", "stats": {}},
        {"rev_fixture_key": "1", "rev_side": "home", "player_uid": "p01", "stats": {"goal": "0"}},
        {"rev_fixture_key": "1", "rev_side": "home", "player_uid": "p01", "stats": {"goal": "abc"}},
    ]
    for case in cases:
        set_request(body=case, username="t")
        with pytest.raises(ValueError):
            call(api.save_fixture_appearance())

    # 对阵不存在：返回 404 响应而非异常
    set_request(body={"rev_fixture_key": "999", "rev_side": "home",
                      "player_uid": "p01", "stats": {"goal": "1"}}, username="t")
    res = call(api.save_fixture_appearance())
    assert res.status_code == 404
