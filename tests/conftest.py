"""
Test isolation.

The router builds its DatabaseRepository from CACHE_DB_PATH at import
time, and several router tests call database.clear() / delete() as
part of their fixtures. Without this file those tests operate on the
real data/cache.db and wipe it every time pytest runs.

Pointing CACHE_DB_PATH at a throwaway file here — before any test
module imports the router — makes the whole suite hermetic.
"""

import atexit
import os
import shutil
import tempfile


_TEST_DB_DIR = tempfile.mkdtemp(prefix="dcs-tests-")

os.environ["CACHE_DB_PATH"] = os.path.join(_TEST_DB_DIR, "cache.db")

atexit.register(shutil.rmtree, _TEST_DB_DIR, ignore_errors=True)
