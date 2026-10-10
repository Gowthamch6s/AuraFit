"""Command-line schema initialization and JSON validation."""
import argparse
import os
from pathlib import Path

import psycopg
from pydantic import ValidationError

from .models import IngestBatch


def migrate(url: str, migration_dir: Path) -> None:
    """Apply numbered SQL files once, in order, with transactional rollback."""
    if not migration_dir.is_dir():
        raise FileNotFoundError(f"SQL directory not found: {migration_dir}")
    with psycopg.connect(url, autocommit=True) as conn:
        with conn.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(913770001)")
            try:
                cursor.execute("CREATE SCHEMA IF NOT EXISTS aurafit")
                cursor.execute("CREATE TABLE IF NOT EXISTS aurafit.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())")
                for path in sorted(migration_dir.glob("[0-9]*.sql")):
                    cursor.execute("SELECT 1 FROM aurafit.schema_migrations WHERE version=%s", (path.stem,))
                    if cursor.fetchone():
                        print(f"SKIP {path.name}")
                        continue
                    # Each DDL migration explicitly wraps changes in BEGIN/COMMIT.
                    cursor.execute(path.read_text(encoding="utf-8"))
                    cursor.execute("SELECT 1 FROM aurafit.schema_migrations WHERE version=%s", (path.stem,))
                    if not cursor.fetchone():
                        raise RuntimeError(f"Migration {path.name} did not register itself")
                    print(f"APPLIED {path.name}")
            finally:
                cursor.execute("SELECT pg_advisory_unlock(913770001)")


def main() -> None:
    parser = argparse.ArgumentParser(description="AuraFit Phase 1 tooling")
    subs = parser.add_subparsers(dest="command", required=True)
    valid = subs.add_parser("validate", help="Validate ingest JSON without writing to DB")
    valid.add_argument("json_file", type=Path)
    init = subs.add_parser("migrate", help="Initialize TimescaleDB schema")
    init.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    init.add_argument("--sql-dir", type=Path, default=Path(__file__).resolve().parents[2] / "sql")
    args = parser.parse_args()
    if args.command == "validate":
        try:
            batch = IngestBatch.model_validate_json(args.json_file.read_text(encoding="utf-8"))
        except ValidationError as exc:
            parser.error(str(exc))
        print(f"VALID batch={batch.batch_id}; ticks={len(batch.ticks)}; sleep={len(batch.sleep_sessions)}; nutrition={len(batch.nutrition_entries)}; checkins={len(batch.subjective_checkins)}")
    else:
        if not args.database_url:
            parser.error("Provide --database-url or DATABASE_URL")
        migrate(args.database_url, args.sql_dir)


if __name__ == "__main__":
    main()
