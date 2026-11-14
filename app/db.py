"""SQLite persistence. Every claim and completion is a short transaction."""
from contextlib import contextmanager
from pathlib import Path
import json
import sqlite3
import time

SCHEMA = '''
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS auth (id INTEGER PRIMARY KEY CHECK(id=1), password_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS login_attempts (address TEXT PRIMARY KEY, attempts INTEGER NOT NULL, reset_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS authors (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, source_id TEXT NOT NULL UNIQUE,
 provider TEXT NOT NULL DEFAULT 'self_hosted', enabled INTEGER NOT NULL DEFAULT 1,
 interval_seconds INTEGER NOT NULL DEFAULT 600, backfill INTEGER NOT NULL DEFAULT 0,
 created_at REAL NOT NULL, initialized INTEGER NOT NULL DEFAULT 0, baseline_at REAL NOT NULL DEFAULT 0,
 last_polled REAL, next_run REAL NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS videos (
 id INTEGER PRIMARY KEY, video_id TEXT NOT NULL UNIQUE, author_id INTEGER NOT NULL REFERENCES authors(id),
 author_name TEXT NOT NULL, published_at REAL NOT NULL, title TEXT NOT NULL, url TEXT NOT NULL, media_url TEXT NOT NULL,
 transcript TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '', model_name TEXT NOT NULL DEFAULT '',
 prompt_used TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_run REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS video_order ON videos(status, published_at DESC, id DESC);
CREATE TABLE IF NOT EXISTS subscribers (
 id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE, enabled INTEGER NOT NULL DEFAULT 1,
 author_ids TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS models (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, base_url TEXT NOT NULL, model_id TEXT NOT NULL,
 api_key TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 0, timeout_seconds INTEGER NOT NULL DEFAULT 120
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_model ON models(enabled) WHERE enabled=1;
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY, video_id INTEGER NOT NULL REFERENCES videos(id),
 subscriber_id INTEGER NOT NULL REFERENCES subscribers(id) ON DELETE CASCADE, email TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
 next_run REAL NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', lease_owner TEXT, lease_until REAL NOT NULL DEFAULT 0,
 UNIQUE(video_id, subscriber_id)
);
CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, value REAL NOT NULL);
CREATE TABLE IF NOT EXISTS market_cache (
 key TEXT PRIMARY KEY, payload TEXT NOT NULL, fetched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS market_refresh (
 key TEXT PRIMARY KEY, last_attempt REAL NOT NULL, failed INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS market_lease (
 id INTEGER PRIMARY KEY CHECK(id=1), owner TEXT, lease_until REAL NOT NULL, next_run REAL NOT NULL
);
'''


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def all(self, sql, params=()):
        with self.connect() as db:
            return [dict(row) for row in db.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        with self.connect() as db:
            row = db.execute(sql, params).fetchone()
            return dict(row) if row else None

    def claim_market(self, owner, now, lease_seconds):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO market_lease VALUES(1,NULL,0,0)')
            return db.execute(
                'UPDATE market_lease SET owner=?,lease_until=? WHERE id=1 AND lease_until<=? AND next_run<=?',
                (owner, now+lease_seconds, now, now),
            ).rowcount == 1

    def release_market(self, owner):
        with self.connect() as db:
            db.execute('UPDATE market_lease SET owner=NULL,lease_until=0 WHERE id=1 AND owner=?', (owner,))

    def finish_market(self, owner, results, *, now, next_run):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            now = time.time() if now is None else now
            acquired = db.execute(
                'UPDATE market_lease SET owner=NULL,lease_until=0,next_run=? WHERE id=1 AND owner=? AND lease_until>?',
                (next_run, owner, now),
            )
            if not acquired.rowcount:
                return False
            for key, payload in results.items():
                if payload is not None:
                    cached = db.execute('SELECT payload FROM market_cache WHERE key=?', (key,)).fetchone()
                    if cached:
                        old = json.loads(cached['payload'])
                        if (payload.get('trade_date', ''), payload.get('as_of', '')) < (
                                old.get('trade_date', ''), old.get('as_of', '')):
                            payload = None
                    if payload is not None:
                        db.execute(
                            'INSERT INTO market_cache VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET '
                            'payload=excluded.payload,fetched_at=excluded.fetched_at',
                            (key, json.dumps(payload, ensure_ascii=False, allow_nan=False), now),
                        )
                db.execute(
                    'INSERT INTO market_refresh VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET '
                    'last_attempt=excluded.last_attempt,failed=excluded.failed',
                    (key, now, payload is None),
                )
            return True

    def market_rows(self):
        return self.all(
            'SELECT r.key,r.last_attempt,r.failed,c.payload,c.fetched_at FROM market_refresh r '
            'LEFT JOIN market_cache c ON r.key=c.key'
        )

    def claim(self, table, owner, now=None, lease_seconds=300):
        if table not in ('authors', 'videos', 'deliveries'):
            raise ValueError('Invalid task type')
        now = time.time() if now is None else now
        condition = 'enabled=1' if table == 'authors' else "status IN ('pending','retry','processing') AND attempts<5"
        if table == 'videos':
            condition += ' AND EXISTS (SELECT 1 FROM authors WHERE authors.id=videos.author_id AND enabled=1)'
        if table == 'deliveries':
            condition += ' AND EXISTS (SELECT 1 FROM subscribers WHERE subscribers.id=deliveries.subscriber_id AND enabled=1)'
        status = '' if table == 'authors' else ",status='processing'"
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # A crash during the final attempt must become visibly failed, not hang forever.
            if table != 'authors':
                db.execute(f"UPDATE {table} SET status='failed',error='任务中断，已达到重试上限',lease_owner=NULL,lease_until=0 WHERE status='processing' AND attempts>=5 AND lease_until<?", (now,))
            row = db.execute(f'SELECT id FROM {table} WHERE {condition} AND next_run<=? AND lease_until<=? ORDER BY next_run,id LIMIT 1', (now, now)).fetchone()
            if not row:
                return None
            row = db.execute(f'UPDATE {table} SET lease_owner=?,lease_until=?,attempts=attempts+1{status} WHERE id=? RETURNING *',
                             (owner, now + lease_seconds, row['id'])).fetchone()
            return dict(row)

    def heartbeat(self, owner):
        now = time.time()
        with self.connect() as db:
            db.execute("INSERT INTO runtime(key,value) VALUES('worker_heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
            for table in ('authors', 'videos', 'deliveries'):
                db.execute(f'UPDATE {table} SET lease_until=? WHERE lease_owner=?', (now + 300, owner))

    def fail(self, table, task, error):
        if table not in ('authors', 'videos', 'deliveries'):
            raise ValueError('Invalid task type')
        attempts = task['attempts']
        delay = min(3600, 30 * 2 ** min(attempts, 7))
        status = '' if table == 'authors' else ",status='" + ('failed' if attempts >= 5 else 'retry') + "'"
        with self.connect() as db:
            db.execute(f'UPDATE {table} SET error=?,next_run=?,lease_until=0,lease_owner=NULL{status} WHERE id=? AND lease_owner=?',
                       (error[:500], time.time() + delay, task['id'], task['lease_owner']))


def assert_owner(db, table, task):
    if task.get('lease_owner'):
        row = db.execute(f'SELECT lease_owner FROM {table} WHERE id=?', (task['id'],)).fetchone()
        if not row or row['lease_owner'] != task['lease_owner']:
            raise RuntimeError('任务租约已由其他进程接管')
