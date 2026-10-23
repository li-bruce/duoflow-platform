import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('framework_export', ROOT / 'scripts/export_framework.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_allowlist_has_runtime_core_and_only_neutral_installed_module():
    paths = {p.relative_to(ROOT).as_posix() for p in exporter.source_files(ROOT)}
    assert {'app/main.py', 'app/devtools.py', 'scripts/export_framework.py',
            'extensions/framework-lab/handlers.py', 'flows/packs/framework-lab/flow.json',
            'web/src/SiteRoot.tsx', 'web/src/guide/FrameworkGuide.tsx',
            'web/src/guide/guideState.ts', 'web/src/guide/guide.css',
            'web/src/guide/FrameworkGuide.test.tsx',
            'web/src/developer-guide/DeveloperGuide.tsx', 'web/src/developer-guide/reference.ts',
            'web/src/developer-guide/developer-guide.css',
            'web/src/developer-guide/DeveloperGuide.test.tsx'} <= paths
    assert {'web/src/developer-guide/CompositionAnalysis.tsx', 'web/src/developer-guide/composition.ts',
            'tests/test_composition_boundaries.py', 'docs/模块公开与业务组织分析.md'} <= paths
    assert all(not p.startswith('extensions/') or p.startswith('extensions/framework-lab/') for p in paths)
    assert all(not p.startswith('flows/') or p.startswith('flows/packs/framework-lab/') for p in paths)
    assert not {'config/identity-demo.json', 'config/project-public-documents.json'} & paths
    assert not any(set(Path(p).parts) & exporter.EXCLUDED_PARTS for p in paths)
    for name in ('remote-service-protocol.md', 'module-lifecycle-design.md'):
        assert 'docs/' + name in paths
        assert (ROOT / 'templates/remote-service-template' / '../../docs' / name).is_file()


def test_private_runtime_files_and_unlisted_text_are_not_exported(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, 'FILES', ())
    monkeypatch.setattr(exporter, 'DOCS', ())
    for path in ['app/safe.py', 'app/.env', 'app/private.pem', 'templates/example/data/token.json',
                 'templates/example/node_modules/private/package.json', 'data/remote-services.json', 'web/dist/index.html']:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('synthetic-private-canary', encoding='utf-8')
    assert [p.relative_to(tmp_path).as_posix() for p in exporter.source_files(tmp_path)] == ['app/safe.py']


def test_framework_explanation_sources_travel_with_export():
    reference = ROOT / 'web/src/developer-guide/reference.ts'
    exported = set(exporter.source_files(ROOT))
    imports = re.findall(r"from '([^']+)\?raw'", reference.read_text(encoding='utf-8'))
    assert len(imports) == 1
    for relative in imports:
        source = (reference.parent / relative).resolve()
        assert source.is_file()
        assert source in {path.resolve() for path in exported}
    text = reference.read_text(encoding='utf-8') + (reference.parent / 'composition.ts').read_text(encoding='utf-8')
    references = set(re.findall(r"'((?:app|web/src|extensions|flows|contracts|docs|tests)/[^']+)'", text))
    assert len(references) >= 35
    for relative in references:
        assert (ROOT / relative).is_file(), relative
        assert ROOT / relative in exported, relative


def test_never_overwrites_existing_destination_or_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, 'source_files', lambda _: [])
    root = tmp_path / 'source'; root.mkdir()
    destination = tmp_path / 'already-exists'; destination.mkdir()
    marker = destination / 'keep.txt'; marker.write_text('keep')
    with pytest.raises(ValueError, match='never replaced'):
        exporter.export(root, destination)
    assert marker.read_text() == 'keep'
    archive = tmp_path / 'occupied.zip'; archive.write_text('keep zip')
    with pytest.raises(ValueError, match='never replaced'):
        exporter.export(root, tmp_path / 'occupied')
    assert archive.read_text() == 'keep zip'


def test_destination_cannot_contain_source(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, 'source_files', lambda _: [])
    with pytest.raises(ValueError):
        exporter.export(tmp_path, tmp_path)
