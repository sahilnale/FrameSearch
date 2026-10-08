"""Wait for a disposable processor-test database and its shared schema."""

import time

from framesearch_processor.database import Database, DatabaseError
from framesearch_processor.settings import DatabaseSettings

database = Database(DatabaseSettings.from_env())
deadline = time.monotonic() + 30
while time.monotonic() < deadline:
    try:
        database.check_schema()
        print("Disposable PostgreSQL schema is ready", flush=True)
        break
    except DatabaseError:
        time.sleep(1)
else:
    raise RuntimeError("Disposable PostgreSQL schema startup timed out")
