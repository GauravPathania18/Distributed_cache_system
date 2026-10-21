class DatabaseUnavailableError(Exception):
    """Raised when the database cannot be reached or fails mid-operation.

    This separates a *storage failure* (database down / corrupted / too
    slow) from a *cache miss* (key simply does not exist). The cache and
    HTTP layers use it to decide between 503 and 404.
    """


class DatabaseQueryError(Exception):
    """Raised when a *statement* is rejected by SQLite itself.

    A malformed query (syntax error, unknown column/table, ambiguous
    column) is a bug in the SQL that was built - NOT a storage outage.
    Reporting it as DatabaseUnavailableError would send a misleading
    "database unavailable, try again later" (503) for something that
    will fail identically every time. It maps to 422 so the caller
    sees the real SQLite message. A storage failure during a valid
    statement is still DatabaseUnavailableError.
    """