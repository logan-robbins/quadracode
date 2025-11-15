"""
This module provides a lightweight, thread-safe access layer for the SQLite 
database that underpins the Quadracode Agent Registry.

It encapsulates all SQL operations, offering a clean, high-level API for the 
rest of the application. The `Database` class manages connections, schema 
initialization, and all CRUD (Create, Read, Update, Delete) operations for agent 
records. By centralizing data access, this module ensures consistency and makes 
the application easier to maintain and test.
"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterable, List, Optional, Tuple


class Database:
    """
    Manages all interactions with the SQLite database for the agent registry.

    This class provides a set of methods for initializing the database schema 
    and performing CRUD operations on agent records. It uses a context manager 
    for connection handling to ensure that connections are properly managed and 
    thread-safe.

    Attributes:
        path: The file path to the SQLite database.
    """

    def __init__(self, path: str):
        """
        Initializes the Database instance with the path to the SQLite file.

        Args:
            path: The file path for the SQLite database.
        """
        self.path = path

    @contextmanager
    def connect(self):
        """
        Provides a thread-safe connection to the SQLite database.

        This context manager handles the opening and closing of the database 
        connection, as well as transaction management (commit/rollback). It 
        also configures the connection to return rows as `sqlite3.Row` objects, 
        which allows for dictionary-like access to columns.
        """
        con = sqlite3.connect(self.path, check_same_thread=False)
        try:
            con.row_factory = sqlite3.Row
            yield con
            con.commit()
        finally:
            con.close()

    def init_schema(self) -> None:
        """
        Initializes the database schema if it doesn't already exist.

        This method creates the `agents` table with all the necessary columns. 
        It is designed to be idempotent, so it can be safely called every time 
        the application starts. It also includes a non-destructive schema 
        migration to add the `hotpath` column if it's missing.
        """
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
        """
        Inserts a new agent or updates an existing one.

        This method uses an "upsert" operation (`INSERT ... ON CONFLICT`) to 
        either create a new agent record or update the details of an existing 
        one. This is a robust way to handle agent registrations, as it avoids 
        race conditions and simplifies the application logic.

        Args:
            agent_id: The unique identifier for the agent.
            host: The hostname or IP address of the agent.
            port: The port number on which the agent is listening.
            now: The current timestamp, used for `registered_at` and 
                 `last_heartbeat`.
            hotpath: A boolean indicating if the agent should be on the hotpath.
        """
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
        """
        Updates the heartbeat timestamp for a given agent.

        This method is called when an agent sends a heartbeat to the registry. 
        It updates the `last_heartbeat` and `status` fields for the specified 
        agent.

        Args:
            agent_id: The ID of the agent to update.
            status: The new status of the agent (e.g., 'healthy').
            at: The timestamp of the heartbeat.

        Returns:
            True if the update was successful, False otherwise.
        """
        with self.connect() as con:
            cur = con.execute(
                "UPDATE agents SET status = ?, last_heartbeat = ? WHERE agent_id = ?",
                (status, at.isoformat(), agent_id),
            )
            return cur.rowcount > 0

    def delete_agent(self, *, agent_id: str) -> bool:
        """
        Deletes an agent from the registry.

        Args:
            agent_id: The ID of the agent to delete.

        Returns:
            True if the deletion was successful, False otherwise.
        """
        with self.connect() as con:
            cur = con.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
            return cur.rowcount > 0

    def fetch_agent(self, *, agent_id: str) -> Optional[sqlite3.Row]:
        """
        Fetches a single agent from the database.

        Args:
            agent_id: The ID of the agent to fetch.

        Returns:
            A `sqlite3.Row` object representing the agent, or `None` if not 
            found.
        """
        with self.connect() as con:
            cur = con.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,))
            row = cur.fetchone()
            return row

    def fetch_agents(self, *, hotpath_only: bool = False) -> List[sqlite3.Row]:
        """
        Fetches a list of all agents, with an option to filter for hotpath agents.

        Args:
            hotpath_only: If True, only returns agents on the hotpath.

        Returns:
            A list of `sqlite3.Row` objects, each representing an agent.
        """
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
        """
        Sets the hotpath status for a given agent.

        Args:
            agent_id: The ID of the agent to modify.
            hotpath: The new hotpath status (True or False).

        Returns:
            True if the update was successful, False otherwise.
        """
        with self.connect() as con:
            cur = con.execute(
                "UPDATE agents SET hotpath = ? WHERE agent_id = ?",
                (1 if hotpath else 0, agent_id),
            )
            return cur.rowcount > 0
