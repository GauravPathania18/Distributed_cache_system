import json
import sqlite3

from typing import Any, Optional

from .exceptions import DatabaseUnavailableError


class _CrudMixin:

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

    def set_many(self, items) -> int:
        """
        Batch upsert.

        `items` is an iterable of (key, value) pairs. All rows are
        written in a single transaction, which is dramatically
        faster than calling set() once per row (dataset imports).
        Returns the number of rows written.
        """

        connection = self._get_connection()

        try:

            rows = [
                (key, json.dumps(value))
                for key, value in items
            ]

            connection.executemany(
                """
                INSERT INTO cache_data (key, value)
                VALUES (?, ?)

                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                rows
            )

            connection.commit()

            return len(rows)

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during set_many: {error}"
            )
        finally:
            connection.close()

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
