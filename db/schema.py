from astrbot.api import logger

SCHEMA_VERSION = 4

SQL_CREATE_TABLES = r"""

CREATE TABLE IF NOT EXISTS growth_periods (
    period_no INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    is_current INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS players (
    player_uid TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    level INTEGER NOT NULL DEFAULT 1,
    xp REAL NOT NULL DEFAULT 0,
    xp_total REAL NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_players_active ON players(active);

CREATE TABLE IF NOT EXISTS matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_date TEXT NOT NULL,
    opponent TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_matches_date_opponent ON matches(match_date, opponent);

CREATE TABLE IF NOT EXISTS appearances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    player_uid TEXT NOT NULL REFERENCES players(player_uid),
    period_no INTEGER NOT NULL REFERENCES growth_periods(period_no),
    stat_xp REAL NOT NULL DEFAULT 0,
    bonus_xp REAL NOT NULL DEFAULT 0,
    total_xp REAL NOT NULL DEFAULT 0,
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(match_id, player_uid)
);

CREATE INDEX IF NOT EXISTS idx_appearances_period ON appearances(period_no);
CREATE INDEX IF NOT EXISTS idx_appearances_player ON appearances(player_uid);

CREATE TABLE IF NOT EXISTS match_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appearance_id INTEGER NOT NULL REFERENCES appearances(id),
    stat_key TEXT NOT NULL,
    value REAL NOT NULL DEFAULT 0,
    UNIQUE(appearance_id, stat_key)
);

CREATE TABLE IF NOT EXISTS milestone_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uid TEXT NOT NULL REFERENCES players(player_uid),
    period_no INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL,
    stat_key TEXT NOT NULL,
    threshold REAL NOT NULL,
    xp REAL NOT NULL DEFAULT 0,
    match_id INTEGER NOT NULL REFERENCES matches(id),
    awarded_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(player_uid, period, stat_key, threshold, period_no)
);

CREATE INDEX IF NOT EXISTS idx_awards_player ON milestone_awards(player_uid);

CREATE TABLE IF NOT EXISTS repeat_awards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_uid TEXT NOT NULL REFERENCES players(player_uid),
    period_no INTEGER NOT NULL DEFAULT 0,
    period TEXT NOT NULL,
    stat_key TEXT NOT NULL,
    step REAL NOT NULL,
    xp REAL NOT NULL DEFAULT 0,
    awarded_count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(player_uid, period, stat_key, step, period_no)
);

CREATE INDEX IF NOT EXISTS idx_repeat_awards_player ON repeat_awards(player_uid);

CREATE TABLE IF NOT EXISTS period_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    period_no INTEGER NOT NULL REFERENCES growth_periods(period_no),
    player_uid TEXT NOT NULL REFERENCES players(player_uid),
    level_end INTEGER NOT NULL DEFAULT 1,
    level_gained INTEGER NOT NULL DEFAULT 0,
    xp_period REAL NOT NULL DEFAULT 0,
    xp_carryover REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(period_no, player_uid)
);

CREATE INDEX IF NOT EXISTS idx_snapshots_period ON period_snapshots(period_no);

CREATE TABLE IF NOT EXISTS growth_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    imported_by TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS pending_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    file_name TEXT NOT NULL,
    preview TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_imports(status);

CREATE TABLE IF NOT EXISTS plugin_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

"""


async def init_schema(db_manager):
    db = db_manager.conn
    async with db_manager.lock:
        await db.executescript(SQL_CREATE_TABLES)
        await db.commit()

    cur = await db.execute("SELECT value FROM plugin_config WHERE key='schema_version'")
    row = await cur.fetchone()
    await cur.close()
    if row is None:
        await db.execute(
            "INSERT INTO plugin_config (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        await db.execute(
            "INSERT OR IGNORE INTO growth_periods (period_no, name, is_current) "
            "SELECT 1, '成长期1', 1 WHERE NOT EXISTS (SELECT 1 FROM growth_periods)"
        )
        await db.commit()
        logger.info("Database schema initialized (version %d).", SCHEMA_VERSION)
    else:
        try:
            current = int(row["value"])
        except (ValueError, TypeError):
            logger.warning(
                "Invalid schema_version value %r, rewriting to %d.",
                row["value"],
                SCHEMA_VERSION,
            )
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            current = SCHEMA_VERSION
        if current < SCHEMA_VERSION:
            await _migrate(db, current)
            await db.execute(
                "UPDATE plugin_config SET value=?, updated_at=datetime('now','localtime') WHERE key='schema_version'",
                (str(SCHEMA_VERSION),),
            )
            await db.commit()
            logger.info("Database schema migrated %d -> %d.", current, SCHEMA_VERSION)


async def _migrate(db, current_version: int):
    """增量迁移：仅在目标结构缺失时执行，保证可重复运行。"""
    if current_version < 4:
        # v3→v4：period_snapshots 表由 executescript 的 CREATE TABLE IF NOT EXISTS 自动补建，
        # 此处仅确认版本号推进。
        await db.execute("SELECT 1")
        await db.commit()

    if current_version < 3:
        # v2→v3：经验列支持 1 位小数。SQLite 类型亲和（INTEGER 列可存非整 REAL），
        # 无需重建表即可写入小数经验；此处仅确认版本号推进。
        await db.execute("SELECT 1")
        await db.commit()

    if current_version < 1:
        await db.execute(
            "INSERT OR IGNORE INTO growth_periods (period_no, name, is_current) "
            "SELECT 1, '成长期1', 1 WHERE NOT EXISTS (SELECT 1 FROM growth_periods)"
        )
        await db.commit()
