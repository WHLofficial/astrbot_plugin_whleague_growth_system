"""pytest 单元测试：规则解析、经验计算、里程碑幂等、成长期推进、命令参数解析。"""

import asyncio
import json
import os
import tempfile

import pytest

from astrbot_plugin_whleague_growth_system.config.defaults import DEFAULT_CONFIG, validate_and_cast
from astrbot_plugin_whleague_growth_system.db.connection import DatabaseManager
from astrbot_plugin_whleague_growth_system.db.dao import GrowthDAO
from astrbot_plugin_whleague_growth_system.db.schema import init_schema
from astrbot_plugin_whleague_growth_system.services.growth_service import GrowthService
from astrbot_plugin_whleague_growth_system.services.import_service import GrowthImportService, kind_from_name
from astrbot_plugin_whleague_growth_system.services.rule_parser import (
    RuleError,
    normalize_rule,
    parse_rule_json,
    parse_rule_table,
)
from astrbot_plugin_whleague_growth_system.utils.security import (
    parse_date,
    parse_num,
    sanitize_filename,
    sanitize_uid,
)


# ─── 规则解析 ─────────────────────────────────────────────

def _rule_json():
    return {
        "stats": {
            "goal": {"name": "进球", "xp": 10},
            "assist": {"name": "助攻", "xp": 5},
            "appearance": {"name": "出场", "xp": 2},
        },
        "milestones": [
            {"stat": "goal", "period": "period", "threshold": 10, "xp": 50},
            {"stat": "goal", "period": "career", "threshold": 100, "xp": 1000},
        ],
        "level_xp": 100,
    }


def test_parse_rule_json_ok():
    rule = parse_rule_json(json.dumps(_rule_json()), 100)
    assert rule["stats"]["goal"]["xp"] == 10
    assert rule["milestones"][0]["period"] == "period"
    assert rule["level_xp"] == 100


def test_parse_rule_json_default_level_xp():
    data = _rule_json()
    del data["level_xp"]
    rule = parse_rule_json(json.dumps(data), 50)
    assert rule["level_xp"] == 50


def test_parse_rule_json_stat_shorthand():
    data = {"stats": {"goal": 10, "assist": {"name": "助攻", "xp": 5}}, "milestones": []}
    rule = normalize_rule(data, 100)
    assert rule["stats"]["goal"] == {"name": "goal", "xp": 10}


def test_parse_rule_json_invalid():
    with pytest.raises(RuleError):
        parse_rule_json("not json", 100)
    with pytest.raises(RuleError):
        parse_rule_json(json.dumps({"stats": {}}), 100)
    bad = _rule_json()
    bad["milestones"] = [{"stat": "nope", "period": "period", "threshold": 1, "xp": 1}]
    with pytest.raises(RuleError):
        parse_rule_json(json.dumps(bad), 100)
    bad2 = _rule_json()
    bad2["milestones"] = [{"stat": "goal", "period": "yearly", "threshold": 1, "xp": 1}]
    with pytest.raises(RuleError):
        parse_rule_json(json.dumps(bad2), 100)


def test_parse_rule_table_csv():
    rows = [
        ["type", "stat", "name", "xp", "period", "threshold"],
        ["stat", "goal", "进球", "10", "", ""],
        ["stat", "assist", "助攻", "5", "", ""],
        ["milestone", "goal", "", "50", "period", "10"],
        ["level", "", "", "100", "", ""],
    ]
    rule = parse_rule_table(rows, DEFAULT_CONFIG, 100)
    assert rule["stats"]["goal"]["name"] == "进球"
    assert rule["stats"]["goal"]["xp"] == 10
    assert rule["milestones"] == [
        {"stat": "goal", "period": "period", "threshold": 10.0, "xp": 50}
    ]
    assert rule["level_xp"] == 100


def test_parse_rule_table_type_inference():
    # 无类型列布局: [stat, name, xp, period, threshold]
    rows = [
        ["goal", "进球", 10],
        ["assist", "助攻", 5],
        ["goal", "", 50, "period", 10],
    ]
    rule = parse_rule_table(rows, DEFAULT_CONFIG, 100)
    assert "goal" in rule["stats"]
    assert rule["stats"]["goal"]["xp"] == 10
    assert rule["milestones"][0] == {
        "stat": "goal", "period": "period", "threshold": 10.0, "xp": 50
    }


# ─── 文件类型嗅探 ─────────────────────────────────────────

def test_kind_from_name():
    assert kind_from_name("规则_进球.json") == "rule"
    assert kind_from_name("球员_名单.csv") == "players"
    assert kind_from_name("比赛_2026.csv") == "matches"
    assert kind_from_name("report.csv") is None


# ─── 工具函数 ─────────────────────────────────────────────

def test_parse_date_variants():
    assert parse_date("2026-08-14") == "2026-08-14"
    assert parse_date("2026/8/14") == "2026-08-14"
    assert parse_date("20260814") == "2026-08-14"
    with pytest.raises(ValueError):
        parse_date("14-08-2026")


def test_parse_date_invalid():
    # 非法日期必须拒绝（修复3 回归）
    for bad in ("2026-13-40", "2026-02-30", "2026-00-01", "2026-13-01", "2026-00-00"):
        with pytest.raises(ValueError):
            parse_date(bad)


def test_parse_num():
    assert parse_num("2") == 2.0
    assert parse_num("1.5") == 1.5
    with pytest.raises(ValueError):
        parse_num("-1")
    with pytest.raises(ValueError):
        parse_num("abc")


def test_sanitize():
    assert sanitize_uid("  p01\n") == "p01"
    assert sanitize_filename("../恶意/文件.csv") == ".._恶意_文件.csv"


def test_validate_and_cast():
    assert validate_and_cast("default_level_xp", "150") == 150
    with pytest.raises(ValueError):
        validate_and_cast("default_level_xp", "0")
    assert validate_and_cast("advance_default_carryover", "false") is False
    assert validate_and_cast("group_whitelist", "111,222") == ["111", "222"]


# ─── 集成：经验 / 里程碑 / 推进（内存 SQLite）──────────────

def _make_env():
    """构造临时环境，返回 (service, dao, imp, tmp_dir)。"""
    tmp = tempfile.mkdtemp()
    db = DatabaseManager(os.path.join(tmp, "test.db"))
    # 环境初始化在调用方的事件循环内完成
    asyncio.run(db.init())
    asyncio.run(init_schema(db))
    dao = GrowthDAO(db)
    service = GrowthService(db, dao, DEFAULT_CONFIG.get)
    imp = GrowthImportService(db, dao, DEFAULT_CONFIG.get, service)
    env_holder = {"db": db}
    return service, dao, imp, tmp, env_holder


def _run_async(coro):
    return asyncio.run(coro)


async def _setup(service, dao):
    rule = parse_rule_json(json.dumps(_rule_json()), 100)
    await service.save_rule(rule, "test", "admin")

    async def _tx(conn):
        await dao.upsert_player(conn, "p01", "球员一", "A队", "admin")
        await dao.upsert_player(conn, "p02", "球员二", "B队", "admin")

    await service._db.execute_transaction(_tx)


def test_record_match_and_xp():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            r = await service.record_match("p01", "2026-08-01", "强队", {"goal": 2, "assist": 1}, "admin")
            assert r["stat_xp"] == 25  # 2*10 + 1*5
            assert r["total_xp"] == 25
            assert r["xp"] == 25
            assert r["xp_total"] == 25
            p = await dao.get_player("p01")
            assert p["xp"] == 25 and p["xp_total"] == 25
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_record_match_overwrite_same_day():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            await service.record_match("p01", "2026-08-01", "", {"goal": 1}, "admin")
            r = await service.record_match("p01", "2026-08-01", "", {"goal": 3}, "admin")
            # 覆盖：同日期同球员替换整条
            assert r["stat_xp"] == 30
            p = await dao.get_player("p01")
            assert p["xp"] == 30
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_milestone_award_and_idempotency():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            # 两场各 5 球，累计 10 达成成长期里程碑（奖励 50）
            r1 = await service.record_match("p01", "2026-08-01", "", {"goal": 5}, "admin")
            assert r1["bonus_xp"] == 0
            r2 = await service.record_match("p01", "2026-08-08", "", {"goal": 5}, "admin")
            assert r2["bonus_xp"] == 50
            assert len(r2["awarded"]) == 1
            p = await dao.get_player("p01")
            assert p["xp"] == 100 + 50  # 10*10 数据 + 50 奖励
            # 再录一场：累计超阈值但已颁发，不重复奖励
            r3 = await service.record_match("p01", "2026-08-15", "", {"goal": 1}, "admin")
            assert r3["bonus_xp"] == 0
            p = await dao.get_player("p01")
            assert p["xp"] == 100 + 50 + 10
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_career_milestone_uses_total():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            # 生涯阈值 100：分两成长期累计（推进后数据仍计入生涯）
            await service.record_match("p01", "2026-08-01", "", {"goal": 60}, "admin")
            await service.advance_period("成长期2", True)
            r = await service.record_match("p01", "2026-09-01", "", {"goal": 50}, "admin")
            # 生涯里程碑 +1000；新成长期累计 50 球也触发 period 里程碑 +50
            assert r["bonus_xp"] == 1050
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_advance_carryover_and_clear():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            await service.record_match("p01", "2026-08-01", "", {"goal": 25}, "admin")
            # 25 球 → 数据经验 250 + 成长期里程碑(10球)奖励 50 → xp=300
            # 每级 100 → 升 3 级，溢出 0
            r = await service.advance_period("成长期2", True)
            assert r["opened_no"] == 2
            p = await dao.get_player("p01")
            assert p["level"] == 4
            assert p["xp"] == 0  # 溢出 0
            assert p["xp_total"] == 300  # 生涯经验不动
            # 清零分支
            await service.record_match("p01", "2026-09-01", "", {"goal": 5}, "admin")
            p = await dao.get_player("p01")
            assert p["xp"] == 50
            r2 = await service.advance_period("成长期3", False)
            assert r2["opened_no"] == 3
            p = await dao.get_player("p01")
            assert p["level"] == 4  # 50//100=0 不升级
            assert p["xp"] == 0  # 清零
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_advance_no_rule_uses_default():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            async def _tx(conn):
                await dao.upsert_player(conn, "p01", "球员一", "", "admin")

            await service._db.execute_transaction(_tx)
            # 无规则时按 default_level_xp=100
            await service._db.execute(
                "UPDATE players SET xp=250 WHERE player_uid='p01'"
            )
            r = await service.advance_period("成长期2", True)
            assert r["level_xp"] == 100
            p = await dao.get_player("p01")
            assert p["level"] == 3 and p["xp"] == 50
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_record_unknown_player_and_stat():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            with pytest.raises(ValueError):
                await service.record_match("nobody", "2026-08-01", "", {"goal": 1}, "admin")
            with pytest.raises(ValueError):
                await service.record_match("p01", "2026-08-01", "", {"unknown": 1}, "admin")
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_rule_import_roundtrip():
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            rule = parse_rule_json(json.dumps(_rule_json()), 100)
            await service.save_rule(rule, "x.json", "admin")
            loaded = await service.get_rule()
            assert loaded["stats"]["goal"]["xp"] == 10
            assert loaded["milestones"][1]["threshold"] == 100
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_overwrite_keeps_period():
    """跨成长期覆盖旧比赛必须保留原成长期，防止篡改历史统计（修复1 回归）。"""
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            await _setup(service, dao)
            await service.record_match("p01", "2026-07-01", "", {"goal": 1}, "admin")
            row = await dao._db.fetchone(
                "SELECT period_no FROM appearances a JOIN matches m ON m.id=a.match_id "
                "WHERE m.match_date='2026-07-01' AND a.player_uid='p01'"
            )
            assert row["period_no"] == 1
            await service.advance_period("成长期2", True)
            # 推进后覆盖成长期1的同一场比赛
            await service.record_match("p01", "2026-07-01", "", {"goal": 2}, "admin")
            row = await dao._db.fetchone(
                "SELECT period_no FROM appearances a JOIN matches m ON m.id=a.match_id "
                "WHERE m.match_date='2026-07-01' AND a.player_uid='p01'"
            )
            assert row["period_no"] == 1, "覆盖不得改写原成长期"
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_advance_concurrent_race():
    """并发推进必须只有一个当前成长期、球员只结算一次（修复2 回归）。"""
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            async def _tx(conn):
                await dao.upsert_player(conn, "p01", "P1", "", "admin")
                await dao.upsert_player(conn, "p02", "P2", "", "admin")
            await service._db.execute_transaction(_tx)
            await service._db.execute(
                "UPDATE players SET xp=150 WHERE player_uid IN ('p01','p02')"
            )
            results = await asyncio.gather(
                service.advance_period("期A", True),
                service.advance_period("期B", True),
                return_exceptions=True,
            )
            ok = [r for r in results if not isinstance(r, Exception)]
            errs = [r for r in results if isinstance(r, Exception)]
            assert len(ok) == 1, f"应恰好一次推进成功, 实际 {len(ok)}"
            assert errs, "并发第二个推进应失败"
            cur = await dao._db.fetchone(
                "SELECT COUNT(*) AS c FROM growth_periods WHERE is_current=1"
            )
            assert cur["c"] == 1, "只允许一个当前成长期"
            p = await dao.get_player("p01")
            assert p["level"] == 2 and p["xp"] == 50, "球员只应结算一次"
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_matches_empty_stats_rejected():
    """比赛文件无表头/无匹配列时不得生成 0 经验记录（修复4 回归）。"""
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            rule = parse_rule_json(json.dumps(_rule_json()), 100)
            file_path = os.path.join(tmp, "比赛_空表.csv")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("2026-08-01,p01,3\n2026-08-02,p02,1\n")
            entries, errors, skipped = imp.parse_matches_file(file_path, rule)
            assert entries == [], "无有效数据列时不得生成记录"
            assert len(errors) == 2, "两行都应记为错误"
            # 带表头的正常文件
            file2 = os.path.join(tmp, "比赛_正常.csv")
            with open(file2, "w", encoding="utf-8") as f:
                f.write("日期,球员ID,进球,助攻\n2026-08-01,p01,2,1\n")
            entries2, errors2, skipped2 = imp.parse_matches_file(file2, rule)
            assert len(entries2) == 1 and entries2[0]["stats"] == {"goal": 2.0, "assist": 1.0}
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())


def test_config_bool_false_parsed():
    """advance_default_carryover 为字符串 'false' 时必须走清零分支（修复6 回归）。"""
    service, dao, imp, tmp, env = _make_env()
    try:
        async def _run():
            from astrbot_plugin_whleague_growth_system.handlers.admin import _as_bool
            assert _as_bool(False, True) is False
            assert _as_bool("false", True) is False
            assert _as_bool("0", True) is False
            assert _as_bool("no", True) is False
            assert _as_bool("true", False) is True
            assert _as_bool("garbage", True) is True  # 非法值回退默认
            assert _as_bool(None, True) is True
        _run_async(_run())
    finally:
        asyncio.run(env["db"].close())
