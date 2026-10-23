"""Export the portable framework from an explicit, source-only allowlist.

No Git traversal, runtime configuration, dependency directory or business module
is copied. The export is new-only; never delete or overwrite an existing path.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = ('通用框架架构与代码地图.md', '模块开发与调试说明书.md', '框架迁移与AI交接指南.md')
HANDBOOK_IDS = ('architecture-core', 'module-debug', 'migration')
TREES = {
    'app': {'.py'}, 'contracts': {'.json', '.md'},
    'templates': {'.py', '.json', '.md', '.ps1', '.ts', '.tsx', '.mjs', '.css', '.txt'},
    'web/src': {'.ts', '.tsx', '.css'},
    'extensions/framework-lab': {'.py', '.json', '.md'},
    'flows/packs/framework-lab': {'.json', '.ts', '.tsx', '.css', '.md'},
    'prompts/_source': {'.md'}, 'reports/handbook-assets': {'.js', '.css'},
    'distribution': {'.py', '.json', '.md', '.html', '.js', '.css', '.txt', '.yaml', '.yml'},
}
FILES = (
    '.gitignore', 'AGENTS.md', 'requirements.in', 'requirements.lock.txt', 'pytest.ini',
    'config/review-flow.json', 'config/identity-policy.json', 'prompts/README.md',
    'docs/模块模板使用手册.md', 'docs/integration-contract-v0.4.md', 'IMPLEMENTATION_CONTRACT.md',
    'docs/remote-service-protocol.md', 'docs/module-lifecycle-design.md',
    'web/package.json', 'web/package-lock.json', 'web/index.html',
    'web/tsconfig.json', 'web/vite.config.ts', 'web/vitest.config.ts',
    'scripts/setup.ps1', 'scripts/start.ps1', 'scripts/stop.ps1',
    'scripts/module_kit.py', 'scripts/build_module_developer_prompt.py',
    'scripts/build_handbooks.py', 'scripts/export_framework.py',
    'tests/test_portable_devtools.py', 'tests/test_framework_lab.py',
    'tests/test_developer_prompt_contract.py', 'tests/test_export_framework.py',
    'tests/test_project_documents.py',
    'tests/test_agent_engine.py', 'tests/test_access_control.py', 'tests/test_model_gateway.py',
    'tests/test_access_bridge.py', 'tests/test_interactions.py', 'tests/test_remote_services.py',
    'tests/test_service_monitor.py', 'tests/test_remote_config_cache.py', 'tests/test_module_kit.py',
    'tests/test_portable_isolation.py',
    'tests/test_composition_boundaries.py', 'docs/模块公开与业务组织分析.md',
    'tests/registration_helpers.py', 'tests/test_registration_admission.py',
    'docs/主框架说明.md', 'docs/模块框架说明.md',
    'docs/源码阅读指南.md', 'scripts/build_duoflow_repositories.py',
    'tests/test_duoflow_release.py',
    'distribution/platform/.gitignore', 'distribution/module-kit/.gitignore',
    'distribution/platform/.gitattributes', 'distribution/module-kit/.gitattributes',
)
EXCLUDED_PARTS = frozenset({'node_modules', '__pycache__', '.git', '.venv', 'data', 'dist', 'logs', '.npm-cache', '.pytest_cache'})
# This regression imports an application pack, which the portable profile omits.
EXCLUDED_FILES = frozenset({'web/src/workflows/SubmissionPaging.test.tsx'})


def source_files(root: Path):
    candidates = {root / name for name in FILES}
    candidates.update(root / 'docs' / name for name in DOCS)
    for directory, suffixes in TREES.items():
        base = root / directory
        for path in base.rglob('*'):
            rel = path.relative_to(root)
            if EXCLUDED_PARTS.intersection(rel.parts) or rel.as_posix() in EXCLUDED_FILES:
                continue
            if path.is_file() and path.suffix in suffixes:
                candidates.add(path)
    for path in sorted(candidates):
        if not path.is_file():
            raise ValueError('Required portable source is missing: ' + str(path.relative_to(root)))
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError('Portable sources may not be links: ' + str(path))
        yield path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def export(root: Path, destination: Path):
    root = root.resolve()
    # Validate all inputs and output paths before creating anything.
    sources = list(source_files(root))
    destination = Path(os.path.abspath(destination))
    archive = destination.with_suffix('.zip')
    if destination.resolve() != destination or destination.exists() or archive.exists():
        raise ValueError('Use a new, non-linked output directory and ZIP name; existing files are never replaced.')
    if root.is_relative_to(destination) or destination == root:
        raise ValueError('Export cannot contain its source repository.')
    target = destination / 'common-platform'
    target.mkdir(parents=True)
    for source in sources:
        output = target / source.relative_to(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(source.read_bytes())
    write_json(target / 'extensions/installed.json', {'modules': ['framework-lab']})
    write_json(target / 'flows/installed.json', {'packs': ['framework-lab']})
    write_json(target / 'components/installed.json', {'packs': []})
    policy = json.loads((root / 'config/identity-policy.json').read_text(encoding='utf-8'))
    policy['roles'] = {key: value for key, value in policy['roles'].items() if key in {'admin', 'member', 'system_admin'}}
    write_json(target / 'config/identity-policy.json', policy)
    docs = [{'id': slug, 'title': Path(name).stem, 'source': 'common-platform/docs/' + name, 'audience': '框架维护者、模块开发者与开发 AI'}
            for slug, name in zip(HANDBOOK_IDS, DOCS)]
    write_json(target / 'docs/handbooks.json', {'version': 1, 'documents': docs})
    readme = (root / 'docs/框架迁移与AI交接指南.md').read_text(encoding='utf-8')
    (destination / 'START-HERE.md').write_text(readme, encoding='utf-8')
    (target / 'README.md').write_text(readme, encoding='utf-8')
    for args in (['--write', '--reviewed'], ['--check']):
        result = subprocess.run([sys.executable, str(target / 'scripts/build_module_developer_prompt.py'), *args],
                                cwd=target, capture_output=True, encoding='utf-8', timeout=90)
        if result.returncode:
            raise ValueError('Portable prompt verification failed: ' + result.stderr[-2000:] + result.stdout[-2000:])
    spec = importlib.util.spec_from_file_location('portable_handbooks', root / 'scripts/build_handbooks.py')
    handbooks = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(handbooks)
    parse = handbooks.parse_markdown
    handbooks.parse_markdown = lambda _, inputs: parse(root, inputs)
    if handbooks.run(target, True):
        raise ValueError('Portable handbook build failed; partial directory retained for diagnosis.')
    (target / 'reports/index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>通用框架说明书</title><meta http-equiv="refresh" content="0;url=handbooks/"><a href="handbooks/">打开通用框架说明书</a></html>', encoding='utf-8')
    inventory = {path.relative_to(destination).as_posix(): sha256(path.read_bytes()).hexdigest()
                 for path in sorted(destination.rglob('*')) if path.is_file()}
    write_json(destination / 'EXPORT-MANIFEST.json', {
        'format': 1, 'profile': 'generic-framework-with-framework-lab',
        'files': inventory, 'excluded': ['business modules and project documents', 'all runtime data and secrets',
                                        'model and remote service connections', '.git and dependency directories', 'frontend dist'],
        'verification': 'Source hashes only; run the acceptance steps in START-HERE.md after installing dependencies.',
    })
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(destination.rglob('*')):
            if path.is_file():
                bundle.write(path, path.relative_to(destination).as_posix())
    return {'directory': str(destination), 'archive': str(archive), 'files': len(inventory), 'sha256': sha256(archive.read_bytes()).hexdigest()}


def main():
    parser = argparse.ArgumentParser(description='导出通用框架源码与重新生成的说明书/提示词，不复制运行数据')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export(ROOT, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
