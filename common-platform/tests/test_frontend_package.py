"""A host pack must carry its own dependency closure, independent of a source repo."""

from pathlib import Path

import pytest

from test_module_kit import kit, generated


def append(package, statement):
    with (package / "frontend/steps.tsx").open("a", encoding="utf-8") as stream:
        stream.write("\n" + statement + "\n")


def test_check_rejects_a_real_external_example_even_when_full_repo_has_it(tmp_path):
    package = generated(tmp_path, "workflow-node", "kit.portable")
    (package / "examples").mkdir(exist_ok=True)
    (package / "examples/sample.json").write_text('{"synthetic":true}', encoding="utf-8")
    append(package, "import sample from '../examples/sample.json';")
    with pytest.raises(kit.KitError, match="包外"):
        kit.check(package)


def test_check_reports_transitive_helper_json_css_and_assets_but_not_preview(tmp_path):
    package = generated(tmp_path, "workflow-node", "kit.portable")
    front = package / "frontend"
    (front / "samples").mkdir()
    (front / "samples/data.json").write_text('{"synthetic":true}', encoding="utf-8")
    (front / "image.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    (front / "style.css").write_text(".sample{background:url('./image.svg')}", encoding="utf-8")
    (front / "helper.ts").write_text("import data from './samples/data.json'; export default Object.freeze(data);", encoding="utf-8")
    (front / "preview.tsx").write_text("import missing from '../unshipped-preview';", encoding="utf-8")
    append(package, "import helper from './helper'; import './style.css';")
    files = kit.check(package)["frontend_files"]
    assert {"frontend/steps.tsx", "frontend/flow.json", "frontend/helper.ts", "frontend/samples/data.json", "frontend/style.css", "frontend/image.svg"} <= set(files)
    assert "frontend/preview.tsx" not in files


@pytest.mark.parametrize("statement", [
    "import missing from './missing.json';",
    "const load = (name:string) => import(name);",
    "export {default} from '../outside';",
])
def test_unshipped_or_unresolved_dynamic_dependency_is_a_package_error(tmp_path, statement):
    package = generated(tmp_path, "workflow-node", "kit.portable")
    append(package, statement)
    with pytest.raises(kit.KitError):
        kit.check(package)


def test_frontend_only_cli_does_not_import_backend_validation(tmp_path, monkeypatch, capsys):
    package = generated(tmp_path, "workflow-node", "kit.portable")
    monkeypatch.setattr(kit, "_imports", lambda: (_ for _ in ()).throw(AssertionError("backend import")))
    assert kit.main(["check", str(package), "--frontend-only"]) == 0
    output = capsys.readouterr().out
    assert '"frontend_dependencies"' in output
    assert '"installed": false' in output
