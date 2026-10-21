import sqlite3
from pathlib import Path

from .exceptions import DatabaseUnavailableError


class _ConnectionMixin:

    def _get_connection(self):

        connection = sqlite3.connect(
            self.db_path,
            timeout=self.timeout,
            check_same_thread=False
        )

        connection.row_factory = sqlite3.Row

        return connection

    def _initialize_database(self):

        connection = self._get_connection()

        try:

            # WAL lets the router's per-request reads proceed while a
            # background thread (health monitor / warm-up / search
            # write-through) writes. The default rollback journal
            # takes an exclusive lock for every commit, which under
            # that concurrency surfaces as "database is locked" on
            # Windows. `timeout` in connect() already bounds how long
            # a genuinely contended write waits (busy_timeout).
            connection.execute("PRAGMA journal_mode=WAL")

            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_data (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            connection.commit()

        except sqlite3.Error as error:
            connection.close()
            raise DatabaseUnavailableError(
                f"database unavailable during initialization: {error}"
            )
        finally:
            connection.close()
