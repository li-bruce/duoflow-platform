"""Verify actual validators run without the platform, and pages remain separate."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "duoflow_release", ROOT / "scripts/build_duoflow_repositories.py"
)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


def test_kit_runs_pure_validators_without_the_platform(tmp_path):
    target = tmp_path / "kit"
    release.build("module-kit", target)
    code = """
import copy,json,sys
from pathlib import Path
from duoflow_validation.remote_services import RemoteServiceManager
from duoflow_validation.registration_contract import validate_registration
base=Path('templates/module-kit/blueprints/workflow-node/backend')
manifest=json.loads((base/'manifest.json').read_text(encoding='utf-8').replace('__MODULE_ID__','example.test').replace('__TITLE__','Synthetic').replace('__PACK_NAME__','example-test'))
registration=json.loads((base/'registration.json').read_text(encoding='utf-8').replace('__MODULE_ID__','example.test').replace('__TITLE__','Synthetic').replace('__PACK_NAME__','example-test'))
RemoteServiceManager._validate_manifest(manifest)
validate_registration(registration,manifest)
bad=copy.deepcopy(registration)
bad['business_chains'][0]['steps'].append('other.missing')
try: validate_registration(bad,manifest)
except ValueError: pass
else: raise AssertionError('Unknown function passed')
assert not {'app','sqlalchemy','fastapi','uvicorn'} & set(sys.modules)
"""
    subprocess.run([sys.executable, "-c", code], cwd=target, check=True)


def test_release_does_not_overwrite_and_records_sources(tmp_path):
    target = tmp_path / "kit"
    result = release.build("module-kit", target)
    assert result["release"] == "v0.01"
    assert (target / "VERSION").read_text().strip() == "v0.01"
    assert (target / "skills/duoflow-module/SKILL.md").is_file()
    assert 'data-product="kit"' in (target / "docs/demo.html").read_text(encoding="utf-8")
    assert not (target / "app").exists()
    with pytest.raises(ValueError):
        release.build("module-kit", target)
    assert len(json.loads((target / "VALIDATOR-SOURCES.json").read_text())["sources"]) >= 8


def test_product_pages_keep_demo_separate_and_copy_clear():
    for name in ("kit", "platform"):
        text = (ROOT / f"distribution/site/{name}.html").read_text(encoding="utf-8")
        assert 'href="demo.html"' in text and "<script" not in text
        assert all(
            word not in text for word in ("不是", "不能", "不要", "不需要", "AI约束", "技术方案")
        )
