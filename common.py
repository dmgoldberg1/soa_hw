import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


def connect(url=None):
    return psycopg.connect(url or os.environ["DATABASE_URL"], row_factory=dict_row,
                           connect_timeout=3)


def migrate(service):
    with connect() as db:
        db.execute("SELECT pg_advisory_xact_lock(42)")
        db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (name text PRIMARY KEY)")
        for path in sorted((Path(__file__).parent / service / "migrations").glob("*.sql")):
            if not db.execute("SELECT 1 FROM schema_migrations WHERE name = %s", (path.name,)).fetchone():
                db.execute(path.read_text())
                db.execute("INSERT INTO schema_migrations VALUES (%s)", (path.name,))
