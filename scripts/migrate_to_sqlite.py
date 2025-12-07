import os
import argparse
from typing import List, Dict, Any
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError

# Import application models and Base (metadata)
from app.database import Base, DATABASE_URL  # uses env and normalizes driver


def make_sqlite_engine(sqlite_path: str) -> Engine:
    os.makedirs(os.path.dirname(sqlite_path), exist_ok=True)
    url = f"sqlite:///{sqlite_path}"
    return create_engine(url, connect_args={"check_same_thread": False})


def copy_table(src_engine: Engine, dst_engine: Engine, table_name: str, batch_size: int = 1000) -> int:
    table = Base.metadata.tables[table_name]
    total = 0

    # ensure destination table exists
    Base.metadata.create_all(bind=dst_engine, tables=[table])

    with src_engine.connect() as src_conn, dst_engine.begin() as dst_conn:
        # stream results to avoid loading entire table into memory
        result = src_conn.execution_options(stream_results=True).execute(select(table))
        batch: List[Dict[str, Any]] = []
        for row in result:
            batch.append(dict(row._mapping))
            if len(batch) >= batch_size:
                dst_conn.execute(table.insert(), batch)
                total += len(batch)
                batch.clear()
        if batch:
            dst_conn.execute(table.insert(), batch)
            total += len(batch)
    return total


def main():
    parser = argparse.ArgumentParser(description="Migrate Postgres data to SQLite using SQLAlchemy metadata.")
    parser.add_argument("--dest", required=True, help="Destination SQLite file path inside container, e.g. /app/data/app.db")
    parser.add_argument("--batch", type=int, default=1000, help="Batch size for inserts")
    args = parser.parse_args()

    # Source engine (Postgres) from app config
    src_engine = create_engine(DATABASE_URL, pool_pre_ping=True)
    # Destination engine (SQLite)
    dst_engine = make_sqlite_engine(args.dest)

    # Create all target tables
    Base.metadata.create_all(bind=dst_engine)

    # Disable FKs during load for speed and ordering tolerance
    with dst_engine.begin() as conn:
        try:
            conn.exec_driver_sql("PRAGMA foreign_keys = OFF")
        except Exception:
            pass

    # Copy in dependency order
    tables = list(Base.metadata.sorted_tables)

    summary = []
    for t in tables:
        try:
            count = copy_table(src_engine, dst_engine, t.name, batch_size=args.batch)
            summary.append((t.name, count, None))
        except SQLAlchemyError as e:
            summary.append((t.name, 0, str(e)))

    # Re-enable FKs
    with dst_engine.begin() as conn:
        try:
            conn.exec_driver_sql("PRAGMA foreign_keys = ON")
        except Exception:
            pass

    # Report
    ok = True
    for name, count, err in summary:
        if err:
            ok = False
            print(f"ERROR copying {name}: {err}")
        else:
            print(f"Copied {name}: {count} rows")

    if not ok:
        raise SystemExit(2)

    print("Migration complete.")


if __name__ == "__main__":
    main()
