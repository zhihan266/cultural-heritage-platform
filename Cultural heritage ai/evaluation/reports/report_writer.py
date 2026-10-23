from __future__ import annotations
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = PROJECT_ROOT / "evaluation" / "reports"
_REPORT_INDEX_PATTERN = re.compile("^(\\d{3,})_")
_NAME_PATTERN = re.compile("^[a-z0-9_]+$")


def _normalize_name(value: str, *, field_name: str) -> str:
    """统一报告方式名字"""
    value = value.strip().lower()
    if not value:
        raise ValueError(f"{field_name} 不能为空")
    if not _NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} 只能包含小写字母、数字和下划线：{value!r}")
    return value


def _find_next_report_index(report_dir: Path) -> int:
    """寻找最大的编号，并且输出最大编号的下一位"""
    if not report_dir.exists():
        return 1
    max_index = 0
    for path in report_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() != ".json":
            continue
        match = _REPORT_INDEX_PATTERN.match(path.name)
        if match is None:
            continue
        index = int(match.group(1))
        max_index = max(max_index, index)
    return max_index + 1


def build_evaluation_report_path(
    *, category: str, variant: str, report_dir: Path | None = None
) -> Path:
    category = _normalize_name(category, field_name="category")
    variant = _normalize_name(variant, field_name="variant")
    target_dir = report_dir if report_dir is not None else DEFAULT_REPORT_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    report_index = _find_next_report_index(target_dir)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{report_index:03d}_{category}_{variant}_{timestamp}.json"
    report_path = target_dir / filename
    if report_path.exists():
        raise FileExistsError(f"Evaluation report already exists: {report_path}")
    return report_path


"\n暴露给外部的调用方法\ncategory为大目录rag、tool等\nvariant为细分方法 dense,hybrid_rerank,selection\nreport为具体内容\n"


def save_evaluation_report(
    *, category: str, variant: str, report: dict[str, Any], report_dir: Path | None = None
) -> Path:
    category = _normalize_name(category, field_name="category")
    variant = _normalize_name(variant, field_name="variant")
    report_path = build_evaluation_report_path(
        category=category, variant=variant, report_dir=report_dir
    )
    payload = {
        "report_meta": {
            "category": category,
            "variant": variant,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "schema_version": "1.0",
            "report_file": report_path.name,
        },
        **report,
    }
    with report_path.open("x", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.write("\n")
    return report_path
