"""Render ORM schema, compare it, or initialize an explicitly empty MySQL database.

Never imports a fixed-database HeidiSQL export or drops/updates existing data.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deployment.common import ROOT, load_backend


def metadata():
    load_backend()
    from app.db.session import Base, init_db

    init_db()  # Registers ORM mappings; does not execute DDL.
    return Base.metadata


def render_schema() -> str:
    from sqlalchemy import MetaData
    from sqlalchemy.dialects.mysql import dialect
    from sqlalchemy.schema import CreateIndex, CreateTable

    copied = MetaData()
    for table in metadata().sorted_tables:
        table.to_metadata(copied)
    lines = [
        "-- Complete schema generated from current ORM; empty MySQL 8.0 database only.",
        "-- No accounts, credentials, tasks, or historical user data are included.",
        "-- The user's HeidiSQL export is retained separately for comparison.",
        "SET NAMES utf8mb4;",
    ]
    for table in copied.sorted_tables:
        # SQLAlchemy stores constraints in a set; compile in a stable order.
        ordered = sorted(table.constraints, key=lambda constraint: (
            constraint.__class__.__name__, constraint.name or "",
            tuple(column.name for column in constraint.columns),
        ))
        for order, constraint in enumerate(ordered):
            constraint._creation_order = order
        table.kwargs.update(mysql_engine="InnoDB", mysql_charset="utf8mb4", mysql_collate="utf8mb4_unicode_ci")
        sql = str(CreateTable(table).compile(dialect=dialect())).strip()
        # MySQL table-option iteration order is not stable across Python processes.
        sql = re.sub(r"\)\s*(?:ENGINE|CHARSET|COLLATE).*$", ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci", sql, flags=re.DOTALL)
        lines.append(sql + ";")
        lines.extend(str(CreateIndex(index).compile(dialect=dialect())).strip() + ";"
                     for index in sorted(table.indexes, key=lambda item: item.name or ""))
    return "\n\n".join(lines) + "\n"


def schema_statements(sql: str) -> list[str]:
    # This parser is exclusively for our generated, data-free DDL, not arbitrary dumps.
    return [statement.strip() for statement in re.sub(r"^--.*$", "", sql, flags=re.MULTILINE).split(";") if statement.strip()]


def compare_schema(connection) -> dict:
    from sqlalchemy import inspect

    inspector = inspect(connection)
    from sqlalchemy.dialects.mysql import dialect

    def canonical_type(value):
        compiled = re.split(r"\s+(?:CHARACTER SET|COLLATE)\s+", str(value.compile(dialect=dialect())).upper())[0]
        return "BOOLEAN" if compiled in {"BOOL", "BOOLEAN", "TINYINT(1)"} else compiled

    def canonical_default(value):
        normalized = str(value).lower().split(" on update ")[0].strip("'\"() ")
        return normalized.replace("current_timestamp()", "current_timestamp")

    actual = set(inspector.get_table_names())
    expected = metadata()
    differences = {"missing_tables": sorted(set(expected.tables) - actual), "tables": {}}
    for name in sorted(set(expected.tables) & actual):
        table = expected.tables[name]
        columns = {column["name"]: column for column in inspector.get_columns(name)}
        expected_fks = {(tuple(fk.column_keys), fk.referred_table.name,
                         tuple(element.column.name for element in fk.elements)) for fk in table.foreign_key_constraints}
        actual_fks = {(tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
                      for fk in inspector.get_foreign_keys(name)}
        expected_unique = {tuple(column.name for column in constraint.columns)
                           for constraint in table.constraints if constraint.__class__.__name__ == "UniqueConstraint"}
        actual_unique = {tuple(constraint["column_names"]) for constraint in inspector.get_unique_constraints(name)}
        actual_indexes = {(index["name"], tuple(index["column_names"])) for index in inspector.get_indexes(name)}
        details = {
            "missing_columns": sorted(set(table.columns.keys()) - set(columns)),
            "extra_columns": sorted(set(columns) - set(table.columns.keys())),
            "type_mismatches": [c.name for c in table.columns if c.name in columns
                                and canonical_type(c.type) != canonical_type(columns[c.name]["type"])],
            "server_default_mismatches": [c.name for c in table.columns if c.name in columns and c.server_default is not None
                                          and canonical_default(c.server_default.arg) != canonical_default(columns[c.name].get("default"))],
            "primary_key_mismatch": list(inspector.get_pk_constraint(name)["constrained_columns"]) != [c.name for c in table.primary_key.columns],
            "nullable_mismatches": [c.name for c in table.columns if c.name in columns and c.nullable != columns[c.name]["nullable"]],
            "length_mismatches": [c.name for c in table.columns if c.name in columns and getattr(c.type, "length", None) is not None
                                  and c.type.length != getattr(columns[c.name]["type"], "length", None)],
            "missing_foreign_keys": sorted(map(str, expected_fks - actual_fks)),
            "missing_unique_constraints": sorted(map(str, expected_unique - actual_unique)),
            "missing_indexes": sorted(index.name for index in table.indexes
                                      if (index.name, tuple(c.name for c in index.columns)) not in actual_indexes),
        }
        if any(details.values()):
            differences["tables"][name] = details
    return differences


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["render", "check", "init"])
    parser.add_argument("--output", type=Path, default=ROOT / "deployment/sql/mysql_schema.sql")
    parser.add_argument("--database", help="Explicit target database; required for init")
    parser.add_argument("--create-database", action="store_true", help="Create a new database if missing, never replace it")
    parser.add_argument("--apply", action="store_true", help="Required for initialization; target must have zero tables")
    args = parser.parse_args()
    if args.action == "render":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(render_schema(), encoding="utf-8", newline="\n")
        print(json.dumps({"schema": args.output.name, "table_count": len(metadata().tables)}))
        return
    from sqlalchemy import create_engine, inspect, text
    from sqlalchemy.engine import make_url

    cfg = load_backend()
    database = args.database or cfg.database_name
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,62}", database):
        parser.error("Database must be a simple MySQL identifier")
    if args.action == "init" and (not args.apply or not args.database):
        parser.error("init requires --apply and an explicit --database; existing nonempty databases are refused")
    url = make_url(cfg.database_url).set(database=database)
    if args.action == "init" and args.create_database:
        server = create_engine(url.set(database=""), connect_args={"connect_timeout": 5})
        try:
            with server.begin() as connection:
                databases = set(inspect(connection).get_schema_names())
                if database not in databases:
                    connection.execute(text(f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        finally:
            server.dispose()
    engine = create_engine(url, connect_args={"connect_timeout": 5})
    try:
        with engine.begin() as connection:
            if args.action == "init":
                if inspect(connection).get_table_names():
                    parser.error("Refusing to initialize a database that already contains tables; use check")
                committed = ROOT / "deployment/sql/mysql_schema.sql"
                sql = committed.read_text(encoding="utf-8")
                if sql != render_schema():
                    parser.error("Committed schema differs from ORM; regenerate and review it first")
                for statement in schema_statements(sql):
                    connection.exec_driver_sql(statement)
            result = compare_schema(connection)
        print(json.dumps({"database": database, "differences": result}, ensure_ascii=False, indent=2))
        if result["missing_tables"] or result["tables"]:
            raise SystemExit(1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
