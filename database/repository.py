from pathlib import Path

from ._connection_mixin import _ConnectionMixin
from ._crud_mixin import _CrudMixin


class DatabaseRepository(_ConnectionMixin, _CrudMixin):
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
