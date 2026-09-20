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
