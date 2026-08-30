from datetime import datetime


class GrowthDAO:
    def __init__(self, db_manager):
        self._db = db_manager

    def _now_str(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ─── growth periods ────────────────────────────────────

    async def get_current_period(self):
        return await self._db.fetchone(
            "SELECT * FROM growth_periods WHERE is_current=1 LIMIT 1"
        )

    async def get_current_period_conn(self, conn):
        async with conn.execute(
            "SELECT * FROM growth_periods WHERE is_current=1 LIMIT 1"
        ) as cur:
            return await cur.fetchone()

    async def create_period(self, period_no: int, name: str) -> None:
        await self._db.execute(
            "INSERT INTO growth_periods (period_no, name, is_current) VALUES (?, ?, 1)",
            (period_no, name),
        )

    async def close_period(self, period_no: int) -> None:
        await self._db.execute(
            "UPDATE growth_periods SET is_current=0, "
            "ended_at=datetime('now','localtime') WHERE period_no=?",
            (period_no,),
        )

    async def close_period_conn(self, conn, period_no: int) -> None:
        await conn.execute(
            "UPDATE growth_periods SET is_current=0, "
            "ended_at=datetime('now','localtime') WHERE period_no=?",
            (period_no,),
        )

    async def max_period_no(self) -> int:
        row = await self._db.fetchone(
            "SELECT COALESCE(MAX(period_no), 0) AS m FROM growth_periods"
        )
        return int(row["m"]) if row else 0

    async def max_period_no_conn(self, conn) -> int:
        async with conn.execute(
            "SELECT COALESCE(MAX(period_no), 0) AS m FROM growth_periods"
        ) as cur:
            row = await cur.fetchone()
        return int(row["m"]) if row else 0

    async def create_period_conn(self, conn, period_no: int, name: str) -> None:
        await conn.execute(
            "INSERT INTO growth_periods (period_no, name, is_current) VALUES (?, ?, 1)",
            (period_no, name),
        )

    async def list_periods(self) -> list:
        return await self._db.fetchall(
            "SELECT * FROM growth_periods ORDER BY period_no DESC"
        )

    # ─── period snapshots（期末快照）───────────────────────

    async def insert_period_snapshot(
        self,
        conn,
        period_no: int,
        player_uid: str,
        level_end: int,
        level_gained: int,
        xp_period: float,
        xp_carryover: float,
    ) -> None:
        await conn.execute(
            "INSERT INTO period_snapshots (period_no, player_uid, level_end, "
            "level_gained, xp_period, xp_carryover) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(period_no, player_uid) DO UPDATE SET "
            "level_end=excluded.level_end, level_gained=excluded.level_gained, "
            "xp_period=excluded.xp_period, xp_carryover=excluded.xp_carryover, "
            "created_at=datetime('now','localtime')",
            (period_no, player_uid, level_end, level_gained, xp_period, xp_carryover),
        )

    async def list_period_snapshots(self, period_no: int) -> list:
        return await self._db.fetchall(
            "SELECT s.*, p.name AS player_name, p.team AS player_team "
            "FROM period_snapshots s "
            "JOIN players p ON p.player_uid=s.player_uid "
            "WHERE s.period_no=? ORDER BY s.xp_period DESC, s.player_uid",
            (period_no,),
        )

    async def get_snapshots_carryover(self, period_no: int) -> dict:
        """返回 period_no 期快照记录的溢出结转（即 period_no+1 期的期初账面经验；
        取 period_no 期期初请传 period_no-1，唯一调用方 export_service._start_xp_map 即此用法）。"""
        rows = await self._db.fetchall(
            "SELECT player_uid, xp_carryover FROM period_snapshots WHERE period_no=?",
            (period_no,),
        )
        return {r["player_uid"]: round(float(r["xp_carryover"]), 1) for r in rows}

    async def list_all_active_players(self) -> list:
        return await self._db.fetchall(
            "SELECT * FROM players WHERE active=1 ORDER BY player_uid"
        )

    async def summarize_period(self, period_no: int):
        return await self._db.fetchone(
            "SELECT COUNT(*) AS player_count, "
            "COALESCE(SUM(CASE WHEN level_gained>0 THEN 1 ELSE 0 END), 0) AS upgraded_count, "
            "COALESCE(SUM(xp_period), 0) AS xp_total "
            "FROM period_snapshots WHERE period_no=?",
            (period_no,),
        )

    async def sum_current_xp(self) -> float:
        row = await self._db.fetchone(
            "SELECT COALESCE(SUM(xp), 0) AS v FROM players WHERE active=1"
        )
        return float(row["v"]) if row else 0.0

    # ─── players ───────────────────────────────────────────

    async def upsert_player(
        self, conn, player_uid: str, name: str, team: str, created_by: str
    ) -> None:
        await conn.execute(
            "INSERT INTO players (player_uid, name, team, created_by) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(player_uid) DO UPDATE SET "
            "name=excluded.name, team=excluded.team, active=1, "
            "created_by=excluded.created_by, "
            "updated_at=datetime('now','localtime')",
            (player_uid, name, team, created_by),
        )

    async def get_player(self, player_uid: str):
        return await self._db.fetchone(
            "SELECT * FROM players WHERE player_uid=?", (player_uid,)
        )

    async def get_player_conn(self, conn, player_uid: str):
        async with conn.execute(
            "SELECT * FROM players WHERE player_uid=?", (player_uid,)
        ) as cur:
            return await cur.fetchone()

    async def update_player_progress(
        self, conn, player_uid: str, level: int, xp: float, xp_total: float
    ) -> None:
        await conn.execute(
            "UPDATE players SET level=?, xp=?, xp_total=?, "
            "updated_at=datetime('now','localtime') WHERE player_uid=?",
            (level, xp, xp_total, player_uid),
        )

    async def count_players(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS c FROM players WHERE active=1"
        )
        return int(row["c"]) if row else 0

    async def list_players(self, page: int, page_size: int) -> list:
        return await self._db.fetchall(
            "SELECT * FROM players WHERE active=1 ORDER BY player_uid LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        )

    async def list_players_by_xp(self, page: int, page_size: int) -> list:
        return await self._db.fetchall(
            "SELECT * FROM players WHERE active=1 ORDER BY xp DESC, xp_total DESC, player_uid "
            "LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        )

    async def list_players_by_total(self, page: int, page_size: int) -> list:
        return await self._db.fetchall(
            "SELECT * FROM players WHERE active=1 ORDER BY xp_total DESC, xp DESC, player_uid "
            "LIMIT ? OFFSET ?",
            (page_size, (page - 1) * page_size),
        )

    # ─── WebUI 专用查询 ────────────────────────────────────

    async def search_players(self, keyword: str, page: int, page_size: int) -> tuple:
        """按 UID / 名字 / 球队模糊搜索在册球员，返回 (行列表, 总数)。"""
        like = f"%{keyword}%"
        cond = "active=1 AND (player_uid LIKE ? OR name LIKE ? OR team LIKE ?)"
        total = await self._db.fetchone(
            f"SELECT COUNT(*) AS c FROM players WHERE {cond}", (like, like, like)
        )
        rows = await self._db.fetchall(
            f"SELECT * FROM players WHERE {cond} ORDER BY player_uid LIMIT ? OFFSET ?",
            (like, like, like, page_size, (page - 1) * page_size),
        )
        return rows, int(total["c"])

    async def recent_matches(self, limit: int = 20) -> list:
        """近期比赛（含出场人数与经验合计），供比赛页与总览使用。"""
        return await self._db.fetchall(
            """
            SELECT m.id, m.match_date, m.opponent,
                   COUNT(a.id) AS player_count,
                   COALESCE(SUM(a.total_xp), 0) AS xp_total
            FROM matches m
            LEFT JOIN appearances a ON a.match_id = m.id
            GROUP BY m.id
            ORDER BY m.match_date DESC, m.id DESC
            LIMIT ?
            """,
            (limit,),
        )

    async def count_overview(self) -> dict:
        """总览聚合：球员数、比赛数、出场数、当前期经验池、生涯经验总和。"""
        row = await self._db.fetchone(
            """
            SELECT
              (SELECT COUNT(*) FROM players WHERE active=1) AS player_count,
              (SELECT COUNT(*) FROM matches) AS match_count,
              (SELECT COUNT(*) FROM appearances) AS appearance_count,
              (SELECT COALESCE(SUM(xp), 0) FROM players WHERE active=1) AS current_xp,
              (SELECT COALESCE(SUM(xp_total), 0) FROM players WHERE active=1) AS career_xp
            """
        )
        return dict(row)

    async def iter_active_players(self, conn) -> list:
        async with conn.execute(
            "SELECT player_uid, level, xp, xp_total FROM players WHERE active=1"
        ) as cur:
            return await cur.fetchall()

    async def list_all_active_players_conn(self, conn) -> list:
        async with conn.execute(
            "SELECT player_uid, name, team FROM players WHERE active=1 ORDER BY player_uid"
        ) as cur:
            return await cur.fetchall()

    # ─── matches / appearances / stats ─────────────────────

    async def get_match(self, conn, match_date: str, opponent: str):
        async with conn.execute(
            "SELECT * FROM matches WHERE match_date=? AND opponent=?", (match_date, opponent)
        ) as cur:
            return await cur.fetchone()

    async def create_match(
        self,
        conn,
        match_date: str,
        opponent: str,
        created_by: str,
        rev_fixture_key: str | None = None,
        rev_side: str | None = None,
    ) -> int:
        cur = await conn.execute(
            "INSERT INTO matches (match_date, opponent, created_by, rev_fixture_key, rev_side) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                match_date,
                opponent,
                created_by,
                str(rev_fixture_key) if rev_fixture_key else None,
                rev_side,
            ),
        )
        return cur.lastrowid

    # ─── 主场赛程绑定（rev_fixture_key = 主场库 matches.id）───

    async def get_match_by_fixture(self, conn, rev_fixture_key: str, rev_side: str):
        async with conn.execute(
            "SELECT * FROM matches WHERE rev_fixture_key=? AND rev_side=?",
            (str(rev_fixture_key), rev_side),
        ) as cur:
            return await cur.fetchone()

    async def bind_match_fixture(
        self, conn, match_id: int, rev_fixture_key: str, rev_side: str
    ) -> None:
        await conn.execute(
            "UPDATE matches SET rev_fixture_key=?, rev_side=? WHERE id=?",
            (str(rev_fixture_key), rev_side, match_id),
        )

    async def list_fixture_appearances(self, rev_fixture_key: str) -> list:
        """该真实对阵下全部出场记录（含主客视角行与明细数据项）。"""
        return await self._db.fetchall(
            """
            SELECT a.id AS appearance_id, a.player_uid, a.period_no,
                   a.stat_xp, a.bonus_xp, a.total_xp, a.created_at,
                   m.rev_side, m.match_date, p.name AS player_name, p.team AS player_team,
                   s.stat_key, s.value
            FROM appearances a
            JOIN matches m ON m.id = a.match_id
            JOIN players p ON p.player_uid = a.player_uid
            LEFT JOIN match_stats s ON s.appearance_id = a.id
            WHERE m.rev_fixture_key = ?
            ORDER BY a.player_uid, s.stat_key
            """,
            (str(rev_fixture_key),),
        )

    async def fixture_record_counts(self) -> dict:
        """已录数据的赛程汇总：{rev_fixture_key: {player_count, xp_total}}。"""
        rows = await self._db.fetchall(
            """
            SELECT m.rev_fixture_key AS fid,
                   COUNT(DISTINCT a.player_uid) AS player_count,
                   COALESCE(SUM(a.total_xp), 0) AS xp_total
            FROM matches m
            JOIN appearances a ON a.match_id = m.id
            WHERE m.rev_fixture_key IS NOT NULL
            GROUP BY m.rev_fixture_key
            """
        )
        return {
            r["fid"]: {"player_count": int(r["player_count"]), "xp_total": float(r["xp_total"])}
            for r in rows
        }

    async def get_appearance(self, conn, match_id: int, player_uid: str):
        async with conn.execute(
            "SELECT * FROM appearances WHERE match_id=? AND player_uid=?",
            (match_id, player_uid),
        ) as cur:
            return await cur.fetchone()

    async def upsert_appearance(
        self,
        conn,
        match_id: int,
        player_uid: str,
        period_no: int,
        stat_xp: int,
        bonus_xp: int,
        total_xp: int,
        created_by: str,
    ) -> int:
        await conn.execute(
            "INSERT INTO appearances (match_id, player_uid, period_no, stat_xp, "
            "bonus_xp, total_xp, created_by) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(match_id, player_uid) DO UPDATE SET "
            # 覆盖已有记录时保留原 period_no，防止跨成长期覆盖旧比赛篡改历史统计
            "period_no=appearances.period_no, stat_xp=excluded.stat_xp, "
            "bonus_xp=excluded.bonus_xp, total_xp=excluded.total_xp, "
            "created_by=excluded.created_by, "
            "created_at=datetime('now','localtime')",
            (match_id, player_uid, period_no, stat_xp, bonus_xp, total_xp, created_by),
        )
        async with conn.execute(
            "SELECT id FROM appearances WHERE match_id=? AND player_uid=?",
            (match_id, player_uid),
        ) as cur:
            row = await cur.fetchone()
        return int(row["id"])

    async def delete_appearance_stats(self, conn, appearance_id: int) -> None:
        await conn.execute("DELETE FROM match_stats WHERE appearance_id=?", (appearance_id,))

    async def insert_match_stat(
        self, conn, appearance_id: int, stat_key: str, value: float
    ) -> None:
        await conn.execute(
            "INSERT INTO match_stats (appearance_id, stat_key, value) VALUES (?, ?, ?)",
            (appearance_id, stat_key, value),
        )

    async def list_player_appearances(self, player_uid: str, limit: int = 20) -> list:
        return await self._db.fetchall(
            "SELECT a.*, m.match_date, m.opponent FROM appearances a "
            "JOIN matches m ON m.id=a.match_id "
            "WHERE a.player_uid=? ORDER BY m.match_date DESC, a.id DESC LIMIT ?",
            (player_uid, limit),
        )

    async def list_appearance_stats(self, conn, appearance_id: int) -> list:
        async with conn.execute(
            "SELECT * FROM match_stats WHERE appearance_id=?", (appearance_id,)
        ) as cur:
            return await cur.fetchall()

    # ─── milestone awards ──────────────────────────────────

    async def get_award(
        self, conn, player_uid: str, period: str, stat_key: str, threshold: float, period_no: int
    ):
        async with conn.execute(
            "SELECT * FROM milestone_awards WHERE player_uid=? AND period=? AND "
            "stat_key=? AND threshold=? AND period_no=?",
            (player_uid, period, stat_key, threshold, period_no),
        ) as cur:
            return await cur.fetchone()

    async def insert_award(
        self,
        conn,
        player_uid: str,
        period_no: int,
        period: str,
        stat_key: str,
        threshold: float,
        xp: int,
        match_id: int,
    ) -> None:
        await conn.execute(
            "INSERT INTO milestone_awards (player_uid, period_no, period, stat_key, "
            "threshold, xp, match_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (player_uid, period_no, period, stat_key, threshold, xp, match_id),
        )

    async def sum_stat_value(self, conn, player_uid: str, stat_key: str, period_no: int | None) -> float:
        """数据值累计：period_no 为 None 表示生涯累计，否则限定成长期。"""
        if period_no is None:
            sql = (
                "SELECT COALESCE(SUM(s.value), 0) AS v FROM match_stats s "
                "JOIN appearances a ON a.id=s.appearance_id WHERE a.player_uid=? AND s.stat_key=?"
            )
            params = (player_uid, stat_key)
        else:
            sql = (
                "SELECT COALESCE(SUM(s.value), 0) AS v FROM match_stats s "
                "JOIN appearances a ON a.id=s.appearance_id "
                "WHERE a.player_uid=? AND a.period_no=? AND s.stat_key=?"
            )
            params = (player_uid, period_no, stat_key)
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return float(row["v"]) if row else 0.0

    async def sum_stat_values(
        self, conn, player_uid: str, stat_keys: list, period_no: int | None
    ) -> float:
        """多数据项值累计求和（总和里程碑）：period_no 为 None 表示生涯累计。"""
        if not stat_keys:
            return 0.0
        placeholders = ",".join("?" for _ in stat_keys)
        if period_no is None:
            sql = (
                "SELECT COALESCE(SUM(s.value), 0) AS v FROM match_stats s "
                "JOIN appearances a ON a.id=s.appearance_id "
                f"WHERE a.player_uid=? AND s.stat_key IN ({placeholders})"
            )
            params = (player_uid, *stat_keys)
        else:
            sql = (
                "SELECT COALESCE(SUM(s.value), 0) AS v FROM match_stats s "
                "JOIN appearances a ON a.id=s.appearance_id "
                f"WHERE a.player_uid=? AND a.period_no=? AND s.stat_key IN ({placeholders})"
            )
            params = (player_uid, period_no, *stat_keys)
        async with conn.execute(sql, params) as cur:
            row = await cur.fetchone()
        return float(row["v"]) if row else 0.0

    async def list_awards(self, player_uid: str) -> list:
        return await self._db.fetchall(
            "SELECT * FROM milestone_awards WHERE player_uid=? ORDER BY awarded_at DESC, id DESC",
            (player_uid,),
        )

    async def list_repeat_awards(self, player_uid: str) -> list:
        return await self._db.fetchall(
            "SELECT * FROM repeat_awards WHERE player_uid=? ORDER BY updated_at DESC, id DESC",
            (player_uid,),
        )

    # ─── repeat awards（每累计 step 次奖励，可重复）─────────

    async def get_repeat_award(
        self, conn, player_uid: str, period: str, stat_key: str, step: float, period_no: int
    ):
        async with conn.execute(
            "SELECT * FROM repeat_awards WHERE player_uid=? AND period=? AND "
            "stat_key=? AND step=? AND period_no=?",
            (player_uid, period, stat_key, step, period_no),
        ) as cur:
            return await cur.fetchone()

    async def upsert_repeat_award(
        self,
        conn,
        player_uid: str,
        period_no: int,
        period: str,
        stat_key: str,
        step: float,
        xp: int,
        awarded_count: int,
    ) -> None:
        await conn.execute(
            "INSERT INTO repeat_awards (player_uid, period_no, period, stat_key, "
            "step, xp, awarded_count) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player_uid, period, stat_key, step, period_no) DO UPDATE SET "
            "awarded_count=excluded.awarded_count, xp=excluded.xp, "
            "updated_at=datetime('now','localtime')",
            (player_uid, period_no, period, stat_key, step, xp, awarded_count),
        )

    # ─── growth rules ──────────────────────────────────────

    async def save_rule(self, label: str, payload_json: str, imported_by: str) -> None:
        await self._db.execute(
            "INSERT INTO growth_rules (label, payload_json, imported_by) VALUES (?, ?, ?)",
            (label, payload_json, imported_by),
        )

    async def get_latest_rule(self):
        return await self._db.fetchone(
            "SELECT * FROM growth_rules ORDER BY id DESC LIMIT 1"
        )

    async def list_rules(self, limit: int = 10) -> list:
        return await self._db.fetchall(
            "SELECT * FROM growth_rules ORDER BY id DESC LIMIT ?", (limit,)
        )

    # ─── pending imports ───────────────────────────────────

    async def insert_pending(self, kind: str, file_name: str, preview: str, created_by: str) -> int:
        cur = await self._db.execute(
            "INSERT INTO pending_imports (kind, file_name, preview, created_by) "
            "VALUES (?, ?, ?, ?)",
            (kind, file_name, preview, created_by),
        )
        try:
            return cur.lastrowid
        finally:
            await cur.close()

    async def get_pending(self, pending_id: int):
        return await self._db.fetchone(
            "SELECT * FROM pending_imports WHERE id=?", (pending_id,)
        )

    async def get_pending_by_filename(self, file_name: str):
        return await self._db.fetchone(
            "SELECT * FROM pending_imports WHERE file_name=? AND status='pending' "
            "ORDER BY id DESC LIMIT 1",
            (file_name,),
        )

    async def get_latest_import_by_filename(self, file_name: str):
        """按文件名取最新登记（不过滤状态，供确认前检查是否已处理）。"""
        return await self._db.fetchone(
            "SELECT * FROM pending_imports WHERE file_name=? ORDER BY id DESC LIMIT 1",
            (file_name,),
        )

    async def list_pending(self, limit: int = 20) -> list:
        return await self._db.fetchall(
            "SELECT * FROM pending_imports WHERE status='pending' ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    async def update_pending_status(self, pending_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE pending_imports SET status=? WHERE id=?", (status, pending_id)
        )

    # ─── plugin config ─────────────────────────────────────

    async def get_config(self, key: str) -> str | None:
        row = await self._db.fetchone(
            "SELECT value FROM plugin_config WHERE key=?", (key,)
        )
        return row["value"] if row else None

    async def set_config(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO plugin_config (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=datetime('now','localtime')",
            (key, value),
        )
