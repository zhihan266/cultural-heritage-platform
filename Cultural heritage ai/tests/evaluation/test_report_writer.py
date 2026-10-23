import json

import pytest

from evaluation.reports.report_writer import save_evaluation_report


def test_reports_preserve_existing_baseline_and_get_unique_names(tmp_path):
    baseline = tmp_path / "dense_v1.json"
    baseline.write_text('{"historical": true}', encoding="utf-8")
    first = save_evaluation_report(category="rag", variant="dense", report={"summary": {}}, report_dir=tmp_path)
    second = save_evaluation_report(category="rag", variant="dense", report={"summary": {}}, report_dir=tmp_path)
    assert first != second and first.name.startswith("001_") and second.name.startswith("002_")
    assert baseline.read_text(encoding="utf-8") == '{"historical": true}'
    assert json.loads(first.read_text(encoding="utf-8"))["report_meta"]["variant"] == "dense"


def test_report_variant_cannot_escape_directory(tmp_path):
    with pytest.raises(ValueError):
        save_evaluation_report(category="rag", variant="../private", report={}, report_dir=tmp_path)
