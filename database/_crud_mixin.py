import json
import sqlite3

from typing import Any, Optional

from .exceptions import DatabaseUnavailableError


class _CrudMixin:

    READ_ONLY_STATEMENTS = ("SELECT", "WITH", "PRAGMA", "EXPLAIN")

    def query(self, sql: str, params=(), max_rows: int = 50) -> dict:
        """
        Run one read-only statement and return the result as
        {"headers": [...], "rows": [[...]], "truncated": bool}.

        Only single SELECT / WITH / PRAGMA / EXPLAIN statements are
        accepted, so this can back an interactive SQL console without
        any way to modify the source of truth. Raises ValueError for
        rejected input and DatabaseUnavailableError for storage
        failures.
        """

        text = sql.strip().rstrip(";").strip()

        if not text:
            raise ValueError("empty statement")

        first_word = text.split(None, 1)[0].upper()

        if (first_word not in self.READ_ONLY_STATEMENTS
                or ";" in text):
            raise ValueError(
                "only single read-only statements are allowed "
                "(SELECT / WITH / PRAGMA / EXPLAIN)"
            )

        connection = self._get_connection()

        try:

            cursor = connection.execute(text, params)

            headers = [column[0] for column in cursor.description or []]
            rows = cursor.fetchmany(max_rows + 1)

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during query: {error}"
            )
        finally:
            connection.close()

        return {
            "headers": headers,
            "rows": [list(row) for row in rows[:max_rows]],
            "truncated": len(rows) > max_rows,
        }

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

    def count(self) -> int:

        connection = self._get_connection()

        try:

            cursor = connection.execute("SELECT COUNT(*) FROM cache_data")

            return int(cursor.fetchone()[0])

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during count: {error}"
            )
        finally:
            connection.close()

    def keys(self, limit: Optional[int] = None) -> list:

        connection = self._get_connection()

        try:

            if limit is None:
                cursor = connection.execute(
                    "SELECT key FROM cache_data ORDER BY key"
                )
            else:
                cursor = connection.execute(
                    "SELECT key FROM cache_data ORDER BY key LIMIT ?",
                    (limit,)
                )

            return [row["key"] for row in cursor.fetchall()]

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during keys: {error}"
            )
        finally:
            connection.close()

    def iter_keys(self, batch_size: int = 500):
        """
        Layer 7.3 - stream every key without loading them all.

        Snapshot sync and re-replication walk the whole keyspace;
        a generator keeps memory flat regardless of table size.
        Yields keys in ORDER BY key order.
        """

        connection = self._get_connection()

        try:

            cursor = connection.execute(
                "SELECT key FROM cache_data ORDER BY key"
            )

            while True:

                rows = cursor.fetchmany(batch_size)

                if not rows:
                    return

                for row in rows:
                    yield row["key"]

        except sqlite3.Error as error:
            raise DatabaseUnavailableError(
                f"database unavailable during iter_keys: {error}"
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
