class DatabaseUnavailableError(Exception):
    """Raised when the database cannot be reached or fails mid-operation.

    This separates a *storage failure* (database down / corrupted / too
    slow) from a *cache miss* (key simply does not exist). The cache and
    HTTP layers use it to decide between 503 and 404.
    """