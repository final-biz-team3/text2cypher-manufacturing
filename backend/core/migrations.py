"""Small checksum-verified migration runner for trusted application DDL."""

from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from core.postgres import postgres_conninfo

_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
_LOCK_ID = 737_113_481


async def apply_migrations() -> None:
    async with await psycopg.AsyncConnection.connect(postgres_conninfo()) as conn:
        await conn.execute("SELECT pg_advisory_lock(%s)", (_LOCK_ID,))
        try:
            await conn.execute("CREATE SCHEMA IF NOT EXISTS app")
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS app.schema_migrations ("
                "filename TEXT PRIMARY KEY, sha256 CHAR(64) NOT NULL, "
                "applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
            await conn.commit()
            for path in sorted(_MIGRATIONS.glob("*.sql")):
                sql = path.read_text(encoding="utf-8")
                checksum = hashlib.sha256(sql.encode()).hexdigest()
                row = await (
                    await conn.execute(
                        "SELECT sha256 FROM app.schema_migrations WHERE filename=%s",
                        (path.name,),
                    )
                ).fetchone()
                if row:
                    if row[0] != checksum:
                        raise RuntimeError(f"Migration checksum mismatch: {path.name}")
                    continue
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO app.schema_migrations(filename, sha256) VALUES (%s, %s)",
                        (path.name, checksum),
                    )
        finally:
            await conn.execute("SELECT pg_advisory_unlock(%s)", (_LOCK_ID,))
            await conn.commit()
