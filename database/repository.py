import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from database.exceptions import DatabaseUnavailableError


class DatabaseRepository:
    """
    SQLite-backed persistent store.

    This is the system's source of truth. It hides all SQL,
    connection handling and JSON serialization behind a small
    key/value interface:

        get(key)
        set(key, value)
        delete(key)
        exists(key)
        clear()

    The rest of the application does not know that SQLite is used,
    so the backend could later be swapped for PostgreSQL/MySQL
    without changing the cache layers.

    Every public method guards against storage failures: any
    sqlite3.Error is translated into DatabaseUnavailableError so
    callers can distinguish "database is down" from "key not found".
    """

    def __init__(self, db_path: str = "data/cache.db", timeout: int = 5):

        self.db_path = db_path
        self.timeout = timeout

        # Make sure the parent directory exists.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._initialize_database()

    # --------------------------------------------------
    # CONNECTION / SETUP
    # --------------------------------------------------

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

    # --------------------------------------------------
    # GET
    # --------------------------------------------------

    def get(self, key: str) -> Optional[Any]:

        connection = self._get_connection()

        try:

            cursor = connection.execute(
                """
                SELECT value
                FROM cache_data
                WHERE key = ?
                """,
                (key,)
            )

            row = cursor.fetchone()

            if row is None:
                return None

            return json.loads(row["value"])

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during get({key!r}): {error}"
            )
        finally:
            connection.close()

    # --------------------------------------------------
    # SET (upsert)
    # --------------------------------------------------

    def set(self, key: str, value: Any) -> None:

        connection = self._get_connection()

        try:

            serialized_value = json.dumps(value)

            connection.execute(
                """
                INSERT INTO cache_data (key, value)
                VALUES (?, ?)

                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (key, serialized_value)
            )

            connection.commit()

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during set({key!r}): {error}"
            )
        finally:
            connection.close()

    # --------------------------------------------------
    # DELETE
    # --------------------------------------------------

    def delete(self, key: str) -> bool:

        connection = self._get_connection()

        try:

            cursor = connection.execute(
                """
                DELETE FROM cache_data
                WHERE key = ?
                """,
                (key,)
            )

            connection.commit()

            return cursor.rowcount > 0

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during delete({key!r}): {error}"
            )
        finally:
            connection.close()

    # --------------------------------------------------
    # EXISTS
    # --------------------------------------------------

    def exists(self, key: str) -> bool:

        connection = self._get_connection()

        try:

            cursor = connection.execute(
                """
                SELECT 1
                FROM cache_data
                WHERE key = ?
                LIMIT 1
                """,
                (key,)
            )

            return cursor.fetchone() is not None

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during exists({key!r}): {error}"
            )
        finally:
            connection.close()

    # --------------------------------------------------
    # CLEAR
    # --------------------------------------------------

    def clear(self) -> None:

        connection = self._get_connection()

        try:

            connection.execute("DELETE FROM cache_data")

            connection.commit()

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during clear: {error}"
            )
        finally:
            connection.close()