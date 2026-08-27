"""插件 WebUI 后端 API：注册到 AstrBot Dashboard 的插件扩展端点。

前端页面位于 pages/console/，经 bridge-sdk 调用此处端点；路由统一带插件名
前缀，由 Dashboard 登录态保护。入参校验复用聊天命令侧同一批工具函数
（parse_date / validate_and_cast / sanitize_* 等），保证聊天与 WebUI 行为一致。
"""

from __future__ import annotations

import mimetypes
from datetime import date

from astrbot.api import logger
from astrbot.api.web import error_response, file_response, json_response, request

from .config.defaults import validate_and_cast
from .handlers.admin import _CONFIG_GROUPS, _KIND_NAME, _as_bool, _format_config_value
from .utils.security import normalize_name, parse_num, sanitize_filename, sanitize_text

PLUGIN_NAME = "astrbot_plugin_whleague_growth_system"


class WebApi:
    """把插件服务层能力暴露为 REST 端点，供 pages/console 前端调用。"""

    def __init__(self, plugin) -> None:
        self._plugin = plugin
        self._register_routes()

    # ─── 注册框架 ──────────────────────────────────────────

    def _register(self, route: str, handler, methods: list[str], desc: str) -> None:
        async def _wrapped(**kwargs):
            try:
                return await handler(**kwargs)
            except (ValueError, TypeError) as e:
                return error_response(str(e), status_code=400)
            except FileNotFoundError as e:
                return error_response(str(e), status_code=404)
            except LookupError:
                return error_response("目标不存在", status_code=404)
            except Exception as e:  # 统一兜底，避免向 Dashboard 返回裸栈
                logger.exception("WebAPI %s %s 执行失败", methods, route)
                return error_response(f"服务器内部错误：{e}", status_code=500)

        self._plugin.context.register_web_api(
            f"/{PLUGIN_NAME}{route}", _wrapped, methods, desc
        )

    def _register_routes(self) -> None:
        r = self._register
        r("/ping", self.ping, ["GET"], "连通性检查")
        r("/stats/overview", self.overview, ["GET"], "总览统计")
        r("/players", self.list_players, ["GET"], "球员列表")
        r("/players/<player_uid>", self.player_detail, ["GET"], "球员详情")
        r("/rank", self.rank, ["GET"], "排行榜")
        r("/periods", self.periods, ["GET"], "成长期列表与汇总")
        r("/periods/advance", self.advance_period, ["POST"], "推进成长期")
        r("/periods/<period_no>/result", self.period_result, ["GET"], "单期结算表")
        r("/rule", self.get_rule, ["GET"], "当前成长规则")
        r("/config", self.get_config, ["GET"], "配置分组查看")
        r("/config/<key>", self.put_config, ["PUT"], "修改单项配置")
        r("/imports/pending", self.pending_imports, ["GET"], "待确认导入列表")
        r("/imports/pending/<pending_id>", self.reject_pending, ["DELETE"], "驳回待确认导入")
        r("/imports/confirm", self.confirm_import, ["POST"], "确认执行导入")
        r("/uploads/<kind>", self.upload_file, ["POST"], "上传导入文件并预览")
        r("/matches/record", self.record_match, ["POST"], "录入单场比赛")
        r("/fixtures/rounds", self.fixtures_rounds, ["GET"], "赛程轮次总览")
        r("/fixtures", self.fixtures_list, ["GET"], "赛程对阵列表")
        r("/fixtures/detail/<key>", self.fixture_detail, ["GET"], "单场对阵详情")
        r("/fixtures/appearance", self.save_fixture_appearance, ["POST"], "按对阵保存球员数据")
        r("/exports", self.download_export, ["GET"], "下载结算表文件")

    # ─── 基础 ──────────────────────────────────────────────

    async def ping(self):
        return json_response({"pong": True, "plugin": PLUGIN_NAME})

    async def overview(self):
        p = self._plugin
        stats = await p.dao.count_overview()
        current = await p.dao.get_current_period()
        pending_rows = await p.dao.list_pending(limit=50)
        recent = await p.dao.recent_matches(limit=8)
        pending = sum(1 for row in pending_rows if row["status"] == "pending")
        return json_response(
            {
                "period": dict(current) if current else None,
                "player_count": stats["player_count"],
                "match_count": stats["match_count"],
                "appearance_count": stats["appearance_count"],
                "current_xp": float(stats["current_xp"]),
                "career_xp": float(stats["career_xp"]),
                "pending_imports": pending,
                "recent_matches": [dict(row) for row in recent],
            }
        )

    # ─── 球员与排行 ────────────────────────────────────────

    async def list_players(self):
        p = self._plugin
        page = max(1, request.query.get("page", 1, int))
        keyword = sanitize_text(request.query.get("q", "") or "")
        sort = request.query.get("sort", "uid") or "uid"
        page_size = p.growth_service._page_size()
        if keyword:
            rows, total = await p.dao.search_players(keyword, page, page_size)
        else:
            method = {
                "xp": p.dao.list_players_by_xp,
                "career": p.dao.list_players_by_total,
            }.get(sort, p.dao.list_players)
            rows = await method(page, page_size)
            total = await p.dao.count_players()
        return json_response(
            {
                "rows": [dict(row) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": max(1, -(-total // page_size)),
                "sort": sort,
                "q": keyword,
            }
        )

    async def player_detail(self, player_uid: str):
        profile = await self._plugin.growth_service.get_profile(player_uid)
        if profile is None:
            return error_response("球员不存在或已注销", status_code=404)
        return json_response(profile)

    async def rank(self):
        mode = request.query.get("mode", "xp") or "xp"
        if mode not in ("xp", "career"):
            raise ValueError(f"未知排行口径: {mode}（支持 xp / career）")
        page = max(1, request.query.get("page", 1, int))
        result = await self._plugin.growth_service.rank(mode=mode, page=page)
        result["mode"] = mode
        return json_response(result)

    # ─── 成长期 ────────────────────────────────────────────

    async def periods(self):
        status = await self._plugin.growth_service.period_status()
        status.pop("rule", None)  # 规则体积大且另有端点，总览不重复携带
        return json_response(status)

    async def period_result(self, period_no: str):
        if not period_no.isdigit():
            raise ValueError(f"无效的期号: {period_no}")
        result = await self._plugin.growth_service.period_result(int(period_no))
        if result is None:
            raise ValueError(f"第 {period_no} 期没有结算数据")
        return json_response(result)

    async def advance_period(self):
        body = await request.json(default={})
        name = sanitize_text(str(body.get("name", "")).strip())
        if not name:
            raise ValueError("新成长期名称不能为空，例如：2026春季联赛")
        carryover = _as_bool(body.get("carryover"), True)
        result = await self._plugin.growth_service.advance_period(name, carryover)
        return json_response(result)

    # ─── 规则与配置 ────────────────────────────────────────

    async def get_rule(self):
        rule = await self._plugin.growth_service.get_rule()
        if rule is None:
            return error_response("尚未导入成长规则", status_code=404)
        return json_response(rule)

    async def get_config(self):
        cache = self._plugin.config_cache
        grouped_keys = {k for _, keys in _CONFIG_GROUPS for k in keys}
        groups = [
            {
                "title": title,
                "items": [
                    {"key": k, "value": cache.get(k), "formatted": _format_config_value(k, cache.get(k))}
                    for k in keys
                ],
            }
            for title, keys in _CONFIG_GROUPS
        ]
        others = sorted(set(cache) - grouped_keys - {"PLUGIN_VERSION"})
        if others:
            groups.append(
                {
                    "title": "其他",
                    "items": [
                        {"key": k, "value": cache.get(k), "formatted": str(cache.get(k))}
                        for k in others
                    ],
                }
            )
        return json_response({"groups": groups})

    async def put_config(self, key: str):
        raw = await request.json(default={})
        value = validate_and_cast(key, str(raw.get("value")))
        await self._plugin._persist_config(key, value)
        logger.info("WebUI 配置变更 %s = %r（by %s）", key, value, request.username)
        return json_response(
            {"key": key, "value": value, "formatted": _format_config_value(key, value)}
        )

    # ─── 比赛录入 ──────────────────────────────────────────

    async def record_match(self):
        body = await request.json(default={})
        player_uid = str(body.get("player_uid", "")).strip()
        if not player_uid:
            raise ValueError("缺少球员 UID")
        match_date = str(body.get("match_date", "")).strip()
        if not match_date:
            raise ValueError("缺少比赛日期")
        opponent = sanitize_text(str(body.get("opponent", "")).strip())
        raw_stats = body.get("stats")
        if not isinstance(raw_stats, dict) or not raw_stats:
            raise ValueError("缺少数据项数值，请至少填写一项")
        stats = {}
        for key, val in raw_stats.items():
            try:
                num = parse_num(str(val))
            except ValueError:
                raise ValueError(f"数据项「{key}」的值“{val}”不是合法数字") from None
            if num > 0:
                stats[str(key)] = num
        if not stats:
            raise ValueError("所有数据项均为 0，无需录入")
        created_by = f"webui:{request.username}" if request.username else "webui"
        result = await self._plugin.growth_service.record_match(
            player_uid, match_date, opponent, stats, created_by
        )
        return json_response(result)

    # ─── 主场赛程联动 ──────────────────────────────────────

    def _team_matches(self, player_team: str, team_name: str) -> bool:
        a = normalize_name(player_team)
        b = normalize_name(team_name)
        return bool(a) and a == b

    async def fixtures_rounds(self):
        p = self._plugin
        bridge = p.revenue_bridge
        if not await bridge.is_available():
            return json_response({"available": False, "state": None, "rounds": [], "recorded": {}})
        rounds = await bridge.list_rounds()
        if rounds is None:
            return json_response({"available": False, "state": None, "rounds": [], "recorded": {}})
        state = await bridge.get_league_state()
        recorded = await p.dao.fixture_record_counts()
        return json_response(
            {
                "available": True,
                "state": state,
                "rounds": [dict(row) for row in rounds],
                "recorded": recorded,
            }
        )

    async def fixtures_list(self):
        p = self._plugin
        bridge = p.revenue_bridge
        round_raw = (request.query.get("round", "") or "").strip()
        round_no: int | None = None
        if round_raw:
            try:
                round_no = int(round_raw)
            except ValueError:
                raise ValueError(f"无效的轮次号: {round_raw}") from None
        played_raw = (request.query.get("played", "") or "").strip()
        played: bool | None = True if played_raw == "1" else False if played_raw == "0" else None
        competition = (request.query.get("competition", "") or "").strip()
        if not await bridge.is_available():
            return json_response({"available": False, "fixtures": []})
        rows = await bridge.list_fixtures(
            round_no=round_no, played=played, competition=competition or None
        )
        if rows is None:
            return json_response({"available": False, "fixtures": []})
        return json_response(
            {
                "available": True,
                "fixtures": [
                    {**row, "fixture_key": str(row.pop("id"))} for row in rows
                ],
            }
        )

    async def fixture_detail(self, key: str):
        p = self._plugin
        fx = await p.revenue_bridge.get_fixture(key)
        if fx is None:
            raise ValueError("主场插件数据库不可用，无法读取赛程")
        if not fx:
            return error_response("主场库中不存在该对阵（可能已重新导入赛程）", status_code=404)
        players = await p.dao.list_all_active_players()
        rosters = {"home": [], "away": [], "unmatched": []}
        for pl in players:
            team = str(pl["team"] or "")
            bucket = (
                "home"
                if self._team_matches(team, fx["home_team"])
                else "away"
                if self._team_matches(team, fx["away_team"])
                else "unmatched"
            )
            rosters[bucket].append(
                {
                    "player_uid": pl["player_uid"],
                    "name": pl["name"],
                    "team": team,
                    "level": int(pl["level"]),
                    "xp": float(pl["xp"]),
                }
            )
        appearances: dict[str, dict] = {}
        for row in await p.dao.list_fixture_appearances(key):
            entry = appearances.setdefault(
                row["player_uid"],
                {
                    "player_uid": row["player_uid"],
                    "name": row["player_name"],
                    "team": row["player_team"],
                    "period_no": int(row["period_no"]),
                    "stat_xp": float(row["stat_xp"]),
                    "bonus_xp": float(row["bonus_xp"]),
                    "total_xp": float(row["total_xp"]),
                    "created_at": row["created_at"],
                    "stats": {},
                },
            )
            if row["stat_key"] is not None:
                entry["stats"][row["stat_key"]] = float(row["value"])
        current = await p.dao.get_current_period()
        rule = await p.growth_service.get_rule()
        fixture = dict(fx)
        fixture["fixture_key"] = str(fixture.pop("id"))
        return json_response(
            {
                "available": True,
                "fixture": fixture,
                "rosters": rosters,
                "appearances": appearances,
                "current_period_no": int(current["period_no"]) if current else 1,
                "has_rule": rule is not None,
            }
        )

    async def save_fixture_appearance(self):
        body = await request.json(default={})
        fixture_key = str(body.get("rev_fixture_key", "")).strip()
        side = str(body.get("rev_side", "")).strip().lower()
        player_uid = str(body.get("player_uid", "")).strip()
        if not fixture_key or side not in ("home", "away") or not player_uid:
            raise ValueError("缺少对阵编号 / 球队视角 / 球员 UID")
        fx = await self._plugin.revenue_bridge.get_fixture(fixture_key)
        if fx is None:
            raise ValueError("主场插件数据库不可用，无法读取赛程")
        if not fx:
            return error_response("主场库中不存在该对阵（可能已重新导入赛程）", status_code=404)

        # 历史期锁定：已有出场记录且归属非当前成长期时禁止修改
        current = await self._plugin.dao.get_current_period()
        current_no = int(current["period_no"]) if current else 1
        for row in await self._plugin.dao.list_fixture_appearances(fixture_key):
            if row["player_uid"] == player_uid and int(row["period_no"]) != current_no:
                raise ValueError(
                    f"该比赛数据属于已结束的成长期第 {row['period_no']} 期，已锁定不能修改"
                )

        raw_stats = body.get("stats")
        if not isinstance(raw_stats, dict) or not raw_stats:
            raise ValueError("缺少数据项数值，请至少填写一项")
        stats = {}
        for k, val in raw_stats.items():
            try:
                num = parse_num(str(val))
            except ValueError:
                raise ValueError(f"数据项「{k}」的值“{val}”不是合法数字") from None
            if num > 0:
                stats[str(k)] = num
        if not stats:
            raise ValueError("所有数据项均为 0，无需录入")

        match_date = str(body.get("match_date", "")).strip()
        if not match_date:
            match_date = date.today().strftime("%Y-%m-%d")
        opponent = fx["away_team"] if side == "home" else fx["home_team"]
        created_by = f"webui:{request.username}" if request.username else "webui"
        result = await self._plugin.growth_service.record_match(
            player_uid, match_date, sanitize_text(opponent), stats, created_by,
            rev_fixture_key=fixture_key, rev_side=side,
        )
        logger.info(
            "WebUI 赛程录数 %s/%s %s（by %s）", fixture_key, side, player_uid, request.username
        )
        return json_response(result)

    # ─── 导入 ──────────────────────────────────────────────

    async def pending_imports(self):
        rows = await self._plugin.dao.list_pending(limit=50)
        files = self._plugin.import_service.list_files()
        return json_response(
            {
                "pending": [dict(row) for row in rows],
                "files": [{"name": f.name, "size": f.stat().st_size} for f in files],
            }
        )

    async def reject_pending(self, pending_id: str):
        if not pending_id.isdigit():
            raise ValueError(f"无效的待确认导入编号: {pending_id}")
        pid = int(pending_id)
        dao = self._plugin.dao
        row = await dao.get_pending(pid)
        if row is None:
            raise ValueError("待确认导入不存在")
        if row["status"] != "pending":
            raise ValueError(f"该导入已处理（状态: {row['status']}）")
        await dao.update_pending_status(pid, "rejected")
        return json_response({"id": pid, "status": "rejected"})

    async def confirm_import(self):
        body = await request.json(default={})
        file_name = sanitize_filename(str(body.get("file_name", "")))
        kind = str(body.get("kind", "")).strip() or None
        service = self._plugin.import_service
        if kind is None:
            kind = service.kind_from_name(file_name)
            if kind is None:
                raise ValueError(
                    "无法识别文件类型：文件名需以「规则_/球员_/比赛_」开头，或在请求中指定 kind"
                )
        if kind not in _KIND_NAME:
            raise ValueError(f"未知导入类型: {kind}")
        created_by = f"webui:{request.username}" if request.username else "webui"
        import_call = {
            "rule": service.confirm_rule_import,
            "players": service.confirm_players_import,
            "matches": service.confirm_matches_import,
        }[kind]
        result = dict(await import_call(file_name, kind, created_by))
        pending_row = await self._plugin.dao.get_pending_by_filename(file_name)
        if pending_row is not None and pending_row["status"] == "pending":
            await self._plugin.dao.update_pending_status(pending_row["id"], "done")
        result["kind"] = kind
        result["file_name"] = file_name
        return json_response(result)

    async def upload_file(self, kind: str):
        if kind not in _KIND_NAME:
            raise ValueError(f"未知导入类型: {kind}（支持 rule / players / matches）")
        files = await request.files()
        upload = files.get("file")
        if upload is None or not upload.filename:
            raise ValueError("请求缺少文件字段 file")
        safe_name = sanitize_filename(upload.filename)
        ext = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if ext not in ("json", "xlsx", "csv"):
            raise ValueError("仅支持 .json / .xlsx / .csv 文件")
        service = self._plugin.import_service
        target = service.imports_dir / safe_name
        size_mb = self._plugin.config_cache.get("import_max_file_size_mb", 50)
        if upload.content_length is not None and upload.content_length > int(size_mb) * 1024 * 1024:
            raise ValueError(f"文件超过大小上限（{size_mb} MB）")
        await upload.save(target)
        preview = await service.preview(target, kind)
        pending_id = await self._plugin.dao.insert_pending(kind, safe_name, preview, "webui")
        return json_response(
            {
                "pending_id": pending_id,
                "file_name": safe_name,
                "kind": kind,
                "preview": preview,
            }
        )

    # ─── 导出下载 ──────────────────────────────────────────

    async def download_export(self):
        raw_no = (request.query.get("period_no") or "").strip()
        if raw_no and not raw_no.isdigit():
            raise ValueError(f"无效的期号: {raw_no}")
        period_no = int(raw_no) if raw_no else None
        built = await self._plugin.export_service.build_export(period_no)
        path = built["path"]
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return file_response(path, filename=path.name, content_type=content_type)
