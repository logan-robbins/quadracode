import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, List, Optional, Tuple


class Database:
    """Lightweight SQLite access layer for the agent registry."""

    def __init__(self, path: str):
        self.path = path

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, check_same_thread=False)
        try:
            con.row_factory = sqlite3.Row
            yield con
            con.commit()
        finally:
            con.close()

    def init_schema(self) -> None:
        with self.connect() as con:
            con.execute(
                """
                CREATE TABLE IF NOT EXISTS agents (
                    agent_id TEXT PRIMARY KEY,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    registered_at TEXT NOT NULL,
                    last_heartbeat TEXT,
                    hotpath INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            try:
                con.execute("ALTER TABLE agents ADD COLUMN hotpath INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:
                # Column already exists; ignore
                pass

    def upsert_agent(
        self,
        *,
        agent_id: str,
        host: str,
        port: int,
        now: datetime,
        hotpath: bool = False,
    ) -> None:
        with self.connect() as con:
            # Try insert; if exists, update host/port and registered_at
            con.execute(
                """
                INSERT INTO agents (agent_id, host, port, status, registered_at, last_heartbeat, hotpath)
                VALUES (?, ?, ?, 'healthy', ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    host=excluded.host,
                    port=excluded.port,
                    status='healthy',
                    registered_at=excluded.registered_at,
                    last_heartbeat=excluded.last_heartbeat,
                    hotpath=CASE WHEN agents.hotpath=1 THEN 1 ELSE excluded.hotpath END
                """,
                (
                    agent_id,
                    host,
                    port,
                    now.isoformat(),
                    now.isoformat(),
                    1 if hotpath else 0,
                ),
            )

    def update_heartbeat(self, *, agent_id: str, status: str, at: datetime) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "UPDATE agents SET status = ?, last_heartbeat = ? WHERE agent_id = ?",
                (status, at.isoformat(), agent_id),
            )
            return cur.rowcount > 0

    def delete_agent(self, *, agent_id: str) -> bool:
        with self.connect() as con:
            cur = con.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            return cur.rowcount > 0

    def fetch_agent(self, *, agent_id: str) -> Optional[sqlite3.Row]:
        with self.connect() as con:
            cur = con.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
            row = cur.fetchone()
            return row

    def fetch_agents(self, *, hotpath_only: bool = False) -> List[sqlite3.Row]:
        with self.connect() as con:
            if hotpath_only:
                cur = con.execute(
                    "SELECT * FROM agents WHERE hotpath = 1 ORDER BY registered_at DESC"
                )
            else:
                cur = con.execute("SELECT * FROM agents ORDER BY registered_at DESC")
            rows = cur.fetchall()
            return list(rows)

    def set_hotpath(self, *, agent_id: str, hotpath: bool) -> bool:
        with self.connect() as con:
            cur = con.execute(
                "UPDATE agents SET hotpath = ? WHERE agent_id = ?",
                (1 if hotpath else 0, agent_id),
            )
            return cur.rowcount > 0
