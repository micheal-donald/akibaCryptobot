"""Offline removal of ephemeral research/diagnostic events from runtime SQLite.

Stop the bot first. All typed tables and all other event types are copied and
checked before the old file is removed. This intentionally drops only event
history that is reconstructable or already represented by in-memory counters.
"""

import argparse
import os
from pathlib import Path
import sqlite3
from datetime import datetime, timezone


EPHEMERAL_EVENT_TYPES = (
    "market.tick",
    "market.kline",
    "market.spread",
    "execution.balance_update",
    "microstructure.decision",
    "signal.suppressed",
    "timer.housekeeping",
    "market.kline_closed",
)


def compact_database(path: Path) -> dict:
    """Caller must hold the deployment lock and stop every database writer."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    archive = path.with_name(f"{path.stem}.archive-{stamp}{path.suffix}")
    temporary = path.with_name(f"{path.name}.compact-{stamp}")
    stat = path.stat()
    with sqlite3.connect(path) as source:
        busy, _, _ = source.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise RuntimeError("Database still has active WAL readers/writers")
        schema = source.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' "
            "ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
        ).fetchall()
        with sqlite3.connect(temporary) as target:
            target.execute("ATTACH DATABASE ? AS original", (str(path),))
            counts = {}
            for kind, name, _table, sql in schema:
                if kind != "table":
                    continue
                target.execute(sql)
                quoted = '"' + name.replace('"', '""') + '"'
                if name == "runtime_events":
                    placeholders = ",".join("?" for _ in EPHEMERAL_EVENT_TYPES)
                    target.execute(
                        f"INSERT INTO {quoted} SELECT * FROM original.{quoted} "
                        f"WHERE event_type NOT IN ({placeholders})",
                        EPHEMERAL_EVENT_TYPES,
                    )
                    before = source.execute(
                        f"SELECT COUNT(*) FROM {quoted}"
                    ).fetchone()[0]
                    after = target.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[
                        0
                    ]
                    counts[name] = after
                    counts["ephemeral_events_removed"] = before - after
                else:
                    target.execute(
                        f"INSERT INTO {quoted} SELECT * FROM original.{quoted}"
                    )
                    before = source.execute(
                        f"SELECT COUNT(*) FROM {quoted}"
                    ).fetchone()[0]
                    after = target.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[
                        0
                    ]
                    if before != after:
                        raise RuntimeError(f"Row-count mismatch for {name}")
                    counts[name] = after
            for kind, _name, _table, sql in schema:
                if kind == "index":
                    target.execute(sql)
            target.commit()
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("Compacted database failed integrity check")
    # All connections are now closed. On POSIX, preserve owner and file mode.
    os.chmod(temporary, stat.st_mode & 0o777)
    if hasattr(os, "chown"):
        os.chown(temporary, stat.st_uid, stat.st_gid)
    os.replace(path, archive)
    try:
        os.replace(temporary, path)
    except BaseException:
        os.replace(archive, path)
        raise
    active_bytes = path.stat().st_size
    original_bytes = archive.stat().st_size
    # Drop the inactive research history only after the compact DB is live.
    archive.unlink()
    return {
        "removed_research_bytes": max(0, original_bytes - active_bytes),
        "active_bytes": path.stat().st_size,
        "preserved_rows": counts,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument(
        "--apply", action="store_true", help="Requires all writers stopped"
    )
    args = parser.parse_args()
    if not args.apply:
        print(
            {
                "database": str(args.database),
                "bytes": args.database.stat().st_size,
                "action": "Stop writers and use --apply to remove ephemeral research/diagnostic events",
            }
        )
    else:
        import json

        print(json.dumps(compact_database(args.database), indent=2))
