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


def test_kit_carries_ai_declaration_validation_sdks_and_usage(tmp_path):
    """An exported kit must validate actual AI declarations without platform imports."""
    target = tmp_path / "ai-kit"
    release.build("module-kit", target)
    for relative in (
        "contracts/ai/v1/declaration.schema.json",
        "contracts/ai/v1/README.md",
        "templates/module-kit/sdk/node/ai-service.mjs",
        "templates/module-kit/sdk/python/ai_service.py",
        "docs/模块AI服务与配置.md",
    ):
        assert (target / relative).is_file(), relative
    assert "cryptography==50.0.1" in (target / "requirements.lock.txt").read_text()
    code = '''
import copy,json,sys
from pathlib import Path
from jsonschema.exceptions import ValidationError
from duoflow_validation.registration_contract import validate_registration
base=Path('templates/module-kit/blueprints/workflow-node/backend')
def load(name):
    text=(base/name).read_text(encoding='utf-8')
    for a,b in [('__MODULE_ID__','example.test'),('__TITLE__','Synthetic'),('__PACK_NAME__','example-test')]:
        text=text.replace(a,b)
    return json.loads(text)
manifest=load('manifest.json')
registration=load('registration.json')
registration['ai_services']=[{'id':'example.test.describe-ai','title':'Synthetic AI','description':'Literal extraction',
    'capability_ids':[manifest['capabilities'][0]['id']],'supported_sources':['platform','module']}]
validate_registration(registration,manifest)
for mutation in ['unknown-capability','secret-in-declaration','duplicate-id']:
    bad=copy.deepcopy(registration)
    if mutation=='unknown-capability': bad['ai_services'][0]['capability_ids']=['example.other.missing']
    elif mutation=='secret-in-declaration': bad['ai_services'][0]['api_key']='synthetic-canary'
    else: bad['ai_services'].append(copy.deepcopy(bad['ai_services'][0]))
    try: validate_registration(bad,manifest)
    except (ValueError, ValidationError): pass
    else: raise AssertionError(mutation)
assert not {'app','sqlalchemy','fastapi','uvicorn'} & set(sys.modules)
'''
    subprocess.run([sys.executable, "-c", code], cwd=target, check=True)


def test_release_does_not_overwrite_and_records_sources(tmp_path):
    target = tmp_path / "kit"
    result = release.build("module-kit", target)
    assert result["release"] == "v0.02"
    assert (target / "VERSION").read_text().strip() == "v0.02"
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
