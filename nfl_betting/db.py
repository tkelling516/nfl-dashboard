"""Shared DuckDB connection helper."""

import duckdb

from config import DB_PATH, SCHEMA_PATH


def get_connection() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(str(DB_PATH))
    apply_schema(con)
    return con


def apply_schema(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA_PATH.read_text())
