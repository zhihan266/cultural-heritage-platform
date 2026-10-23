"""验证现行 17 表建库入口、历史导出保留和旧表生产依赖清理。"""

from pathlib import Path
import re

from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects.mysql import dialect
from sqlalchemy.schema import CreateTable

from app.db import session


ROOT = Path(__file__).resolve().parents[1]
RETIRED = {"crowd_task_participants", "task_knowledge_ingestions"}


def table_blocks(sql):
    return {
        match.group(1): match.group(0)
        for match in re.finditer(r"CREATE TABLE IF NOT EXISTS `([^`]+)` \([\s\S]*?;", sql)
    }


def test_current_schema_preserves_all_active_definitions_and_resolves_foreign_keys_in_order():
    sql = (ROOT / "sql/mysql_schema.sql").read_text(encoding="utf-8")
    archived = table_blocks((ROOT / "sql/createtable.sql").read_text(encoding="utf-8-sig"))
    current = table_blocks(sql)
    session.init_db()
    assert len(archived) == 19 and RETIRED <= archived.keys()
    assert set(current) == set(session.Base.metadata.tables) == archived.keys() - RETIRED
    assert len(current) == 17
    created: set[str] = set()
    for name, block in current.items():
        assert block == archived[name], name
        assert set(re.findall(r"REFERENCES `([^`]+)`", block)) - {name} <= created, name
        assert block.count("(") == block.count(")"), name
        assert "ENGINE=InnoDB" in block and "CHARSET=utf8mb4" in block
        created.add(name)
    # 仅允许当前入口包含 SET NAMES 和完整建表块，不夹带数据、迁移或自动清理。
    remainder = re.sub(r"^--.*$", "", sql, flags=re.M)
    for block in current.values():
        remainder = remainder.replace(block, "", 1)
    assert remainder.strip() == "SET NAMES utf8mb4;"


def test_all_active_mappings_compile_for_mysql_and_create_in_isolated_sqlite():
    session.init_db()
    assert len(session.Base.metadata.tables) == 17
    assert not RETIRED & session.Base.metadata.tables.keys()
    for table in session.Base.metadata.sorted_tables:
        assert str(CreateTable(table).compile(dialect=dialect()))
        assert all(fk.column.table.name not in RETIRED for fk in table.foreign_keys)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        session.Base.metadata.create_all(engine)
        assert set(inspect(engine).get_table_names()) == set(session.Base.metadata.tables)
    finally:
        engine.dispose()


def test_production_python_has_no_retired_models_tables_or_registration():
    forbidden = re.compile(
        r"\bcrowd_task_participants\b|\btask_knowledge_ingestions\b|"
        r"\bCrowdTaskParticipantTable\b|\bTaskKnowledgeIngestionTable\b|"
        r"\blegacy_knowledge_curation\b|\bCrowdParticipantStatus\b|\bKnowledgeIngestionStatus\b"
    )
    for directory in ("app", "scripts", "evaluation"):
        for path in (ROOT / directory).rglob("*.py"):
            assert not forbidden.search(path.read_text(encoding="utf-8-sig")), path
