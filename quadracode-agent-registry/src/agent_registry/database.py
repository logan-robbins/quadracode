"""Thread-safe SQLite data-access layer for the Agent Registry.

Provides connection management, schema initialisation with idempotent migrations,
and parameterised CRUD operations.  A per-operation threading lock serialises
writes while allowing safe concurrent reads on separate connections.

For in-memory databases (mock mode), a shared-cache URI keeps all connections
pointed at the same in-memory instance.
"""

import logging
import sqlite3
import threading
from contextlib import contextmanager
from collections.abc import Generator
from datetime import datetime

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS agents (
    agent_id       TEXT PRIMARY KEY,
    host           TEXT NOT NULL,
    port           INTEGER NOT NULL,
    status         TEXT NOT NULL,
    registered_at  TEXT NOT NULL,
    last_heartbeat TEXT,
    hotpath        INTEGER NOT NULL DEFAULT 0,
    metrics        TEXT
)
"""

_MIGRATIONS: list[str] = [
    "ALTER TABLE agents ADD COLUMN hotpath INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE agents ADD COLUMN metrics TEXT",
]


class Database:
    """Manages all interactions with the SQLite database for agent records.

    Attributes:
        path: Filesystem path to the SQLite file, or ``":memory:"``.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._is_memory = path == ":memory:"
        self._lock = threading.Lock()

        if self._is_memory:
            self._shared_uri = "file:quadracode_registry?mode=memory&cache=shared"
            self._keeper = sqlite3.connect(
                self._shared_uri, uri=True, check_same_thread=False
            )
            self._keeper.row_factory = sqlite3.Row
        else:
            self._shared_uri = None
            self._keeper = None

        logger.info(
            "Database initialised: path=%s, in_memory=%s", self.path, self._is_memory
        )

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a thread-safe SQLite connection with auto-commit/rollback.

        The lock is held only during the execute+commit window so that
        connection creation does not serialise unnecessarily.
        """
        if self._is_memory:
            con = sqlite3.connect(
                self._shared_uri, uri=True, check_same_thread=False  # type: ignore[arg-type]
            )
        else:
            con = sqlite3.connect(self.path, check_same_thread=False)
        con.row_factory = sqlite3.Row
        try:
            with self._lock:
                yield con
                con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # Keep the public name `connect` as an alias for backwards compat (tests).
    connect = _connect

    def init_schema(self) -> None:
        """Create the ``agents`` table and apply idempotent column migrations."""
        with self._connect() as con:
            con.execute(_SCHEMA_SQL)
            for migration in _MIGRATIONS:
                try:
                    con.execute(migration)
                except sqlite3.OperationalError:
                    pass  # column already exists
        logger.info("Database schema initialised")

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def upsert_agent(
        self,
        *,
        agent_id: str,
        host: str,
        port: int,
        now: datetime,
        hotpath: bool = False,
    ) -> None:
        """Insert or update an agent record.

        On conflict the host/port/status/timestamps are refreshed.  The
        ``hotpath`` flag is only upgraded (once set, re-registration does not
        clear it).

        Args:
            agent_id: Unique identifier for the agent.
            host: Hostname or IP address of the agent.
            port: Service port the agent listens on.
            now: Current UTC timestamp for registration and heartbeat.
            hotpath: Whether the agent should be pinned to the hotpath.
        """
        with self._connect() as con:
            con.execute(
                """
                INSERT INTO agents
                    (agent_id, host, port, status, registered_at, last_heartbeat, hotpath)
                VALUES (?, ?, ?, 'healthy', ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    host           = excluded.host,
                    port           = excluded.port,
                    status         = 'healthy',
                    registered_at  = excluded.registered_at,
                    last_heartbeat = excluded.last_heartbeat,
                    hotpath        = CASE WHEN agents.hotpath = 1 THEN 1
                                         ELSE excluded.hotpath END
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
        logger.debug("Upserted agent %s at %s:%d", agent_id, host, port)

    def update_heartbeat(
        self,
        *,
        agent_id: str,
        status: str,
        at: datetime,
        metrics: str | None = None,
    ) -> bool:
        """Update heartbeat timestamp and status for *agent_id*.

        Args:
            agent_id: The agent to update.
            status: New status value (e.g. ``"healthy"``).
            at: Timestamp of the heartbeat.
            metrics: JSON-encoded metrics payload, or ``None``.

        Returns:
            ``True`` if the agent was found and updated.
        """
        with self._connect() as con:
            cur = con.execute(
                "UPDATE agents SET status = ?, last_heartbeat = ?, metrics = ? "
                "WHERE agent_id = ?",
                (status, at.isoformat(), metrics or "{}", agent_id),
            )
            updated = cur.rowcount > 0
        if not updated:
            logger.warning("Heartbeat for unknown agent %s", agent_id)
        return updated

    def delete_agent(self, *, agent_id: str) -> bool:
        """Delete an agent record.

        Returns:
            ``True`` if a row was actually deleted.
        """
        with self._connect() as con:
            cur = con.execute(
                "DELETE FROM agents WHERE agent_id = ?", (agent_id,)
            )
            deleted = cur.rowcount > 0
        if deleted:
            logger.info("Deleted agent %s", agent_id)
        return deleted

    def fetch_agent(self, *, agent_id: str) -> sqlite3.Row | None:
        """Fetch a single agent by ID.

        Returns:
            A ``sqlite3.Row`` or ``None`` if not found.
        """
        with self._connect() as con:
            cur = con.execute(
                "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
            )
            return cur.fetchone()

    def fetch_agents(self, *, hotpath_only: bool = False) -> list[sqlite3.Row]:
        """Fetch all agents, optionally filtered to hotpath-only.

        Returns:
            List of ``sqlite3.Row`` objects ordered by ``registered_at`` descending.
        """
        with self._connect() as con:
            if hotpath_only:
                cur = con.execute(
                    "SELECT * FROM agents WHERE hotpath = 1 "
                    "ORDER BY registered_at DESC"
                )
            else:
                cur = con.execute(
                    "SELECT * FROM agents ORDER BY registered_at DESC"
                )
            return list(cur.fetchall())

    def set_hotpath(self, *, agent_id: str, hotpath: bool) -> bool:
        """Set the hotpath flag for an agent.

        Returns:
            ``True`` if the agent was found and updated.
        """
        with self._connect() as con:
            cur = con.execute(
                "UPDATE agents SET hotpath = ? WHERE agent_id = ?",
                (1 if hotpath else 0, agent_id),
            )
            return cur.rowcount > 0
