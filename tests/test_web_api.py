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
