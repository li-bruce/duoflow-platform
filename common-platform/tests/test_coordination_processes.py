"""Five actual HTTP processes and independent databases, no installed services."""
import importlib.util
import os
from pathlib import Path


def test_parallel_modules_publication_fanout_and_restart_over_real_http(tmp_path):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("process_acceptance", root / "scripts/coordination_acceptance.py")
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    report_path = Path(os.environ.get("DUOFLOW_PROCESS_ACCEPTANCE_REPORT", tmp_path / "process-acceptance.json"))
    report = acceptance.run_acceptance(tmp_path / "processes", report_path)
    assert report["status"] == "passed"
    assert report["counts"]["checks"] >= 15
    assert report["counts"]["passed"] == report["counts"]["checks"]
    assert report["counts"]["all_processes_exited"] is True
    assert report["existing_business_modules_adapted"] is False
    assert len(report["final_module_snapshots"]) == 4
