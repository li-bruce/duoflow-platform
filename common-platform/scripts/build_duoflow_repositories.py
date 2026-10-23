"""Build two source-only DuoFlow repositories from reviewed local sources.

The platform retains common-platform/ so handbook references remain valid. The
kit carries exact, AST-selected pure platform validators, with source hashes for
review. No service, credential, database or installed dependency is copied.
"""

from __future__ import annotations

import argparse
import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import textwrap

ROOT = Path(__file__).resolve().parents[1]
OVERLAYS = ROOT / "distribution"
VERSION = "v0.01"
EXCLUDED = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "data",
    "dist",
    ".runtime",
}
DESCRIPTIONS = {
    "platform": "DuoFlow 双行主框架：统一模块接入、身份与范围授权，支持页面和对话共用业务组件、状态恢复与开发调试。",
    "module-kit": "DuoFlow 模块开发套件：提供接入协议、Python/Node SDK、模块模板、校验工具与 duoflow-module Skill，衔接 business-observer 业务建模和持续验证。",
}


def write(path: Path, text: str):
    """Write deterministic UTF-8 source and create only its required parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def copy_tree(source: Path, target: Path):
    """Copy reviewed source trees while rejecting links and runtime folders."""
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if EXCLUDED.intersection(relative.parts):
            continue
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)():
            raise ValueError("Source links cannot be published")
        if path.is_file():
            (target / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target / relative)


def select(source: str, name: str, methods=None):
    """Select actual validator definitions, including their decorators, via AST."""
    text = (ROOT / source).read_text(encoding="utf-8-sig")
    node = next(node for node in ast.parse(text).body if getattr(node, "name", None) == name)
    lines = text.splitlines()
    if methods is None:
        start = min([node.lineno] + [item.lineno for item in getattr(node, "decorator_list", [])])
        return "\n".join(lines[start - 1 : node.end_lineno])
    selected = []
    for child in node.body:
        if getattr(child, "name", None) in methods:
            start = min([child.lineno] + [item.lineno for item in child.decorator_list])
            selected.append(textwrap.dedent("\n".join(lines[start - 1 : child.end_lineno])))
    if len(selected) != len(methods):
        raise ValueError("Validator API changed; review the release extractor")
    return f"class {name}:\n" + textwrap.indent("\n\n".join(selected), "    ")


def constants(source: str, names):
    text = (ROOT / source).read_text(encoding="utf-8-sig")
    tree = ast.parse(text)
    return "\n".join(
        ast.get_source_segment(text, node)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)
    )


def validators(target: Path):
    """Ship platform checks without importing databases, servers or business code."""
    base = target / "duoflow_validation"
    write(
        base / "__init__.py",
        '"""Pure development-time checks extracted from DuoFlow Platform."""\n',
    )
    full = (
        "errors",
        "module_contract",
        "workspace_contract",
        "registration_contract",
        "state_machines",
    )
    for name in full:
        write(base / (name + ".py"), (ROOT / f"app/{name}.py").read_text(encoding="utf-8-sig"))
    source = "app/registry.py"
    write(
        base / "registry.py",
        '"""Static capability checks; supplier code is never imported."""\n'
        "from pathlib import Path\nimport re\nfrom jsonschema import Draft202012Validator\n"
        "from .errors import PlatformError, require\nfrom .workspace_contract import ROLE_IDS\n"
        + constants(source, {"RENDERERS"})
        + "\n\n"
        + select(source, "Registry", {"__init__", "register", "_check_refs", "validate"})
        + "\n",
    )
    write(
        base / "access_control.py",
        '"""Access-definition validation, without the authorization database."""\n'
        "import json\nfrom pathlib import Path\nfrom jsonschema import Draft202012Validator\nfrom .errors import require\n"
        "DEFINITION_SCHEMA=json.loads((Path(__file__).resolve().parents[1]/'contracts/access/v1/definition.schema.json').read_text(encoding='utf-8'))\n\n"
        + select("app/access_control.py", "_validate")
        + "\n\n"
        + select("app/access_control.py", "validate_definition")
        + "\n",
    )
    write(
        base / "remote_services.py",
        '"""Manifest validation only; no network or credential storage."""\n'
        "import copy\nimport re\nfrom jsonschema import Draft202012Validator, SchemaError\n"
        "from .module_contract import normalize_surfaces\nfrom .workspace_contract import ROLE_IDS\n"
        + select("app/remote_services.py", "RemoteServiceError")
        + "\n\n"
        + select("app/remote_services.py", "RemoteServiceManager", {"_validate_manifest"})
        + "\n",
    )


def build_kit(target: Path):
    """Produce a standalone developer kit with local contracts and TypeScript."""
    target.mkdir(parents=True)
    for tree in (
        "contracts",
        "templates/module-kit",
        "templates/workflow-module",
        "templates/remote-access-client",
    ):
        copy_tree(ROOT / tree, target / tree)
    write(
        target / "scripts/module_kit.py",
        (ROOT / "scripts/module_kit.py")
        .read_text(encoding="utf-8-sig")
        .replace("from app.", "from duoflow_validation."),
    )
    validators(target)
    copy_tree(OVERLAYS / "module-kit", target)
    for name in ("site.css", "demo.js", "demo.html"):
        write(target / "docs" / name, (OVERLAYS / "site" / name).read_text(encoding="utf-8"))
    write(target / "docs/index.html", (OVERLAYS / "site/kit.html").read_text(encoding="utf-8"))
    write(
        target / "docs/模块模板使用手册.md",
        (ROOT / "docs/模块模板使用手册.md").read_text(encoding="utf-8"),
    )
    write(
        target / "docs/模块框架说明.md", (ROOT / "docs/模块框架说明.md").read_text(encoding="utf-8")
    )
    write(target / "docs/主框架说明.md", (ROOT / "docs/主框架说明.md").read_text(encoding="utf-8"))
    for name in ('通用框架架构与代码地图.md', '模块开发与调试说明书.md', '框架迁移与AI交接指南.md'):
        write(target / 'docs' / name, (ROOT / 'docs' / name).read_text(encoding='utf-8'))
    sources = (
        "registry",
        "access_control",
        "remote_services",
        *(
            "errors",
            "module_contract",
            "workspace_contract",
            "registration_contract",
            "state_machines",
        ),
    )
    write(
        target / "VALIDATOR-SOURCES.json",
        json.dumps(
            {
                "source_repository": "https://github.com/li-bruce/duoflow-platform",
                "release": VERSION,
                "sources": {
                    f"common-platform/app/{name}.py": sha256(
                        (ROOT / f"app/{name}.py")
                        .read_text(encoding="utf-8-sig")
                        .replace("\r\n", "\n")
                        .encode()
                    ).hexdigest()
                    for name in sources
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )


def build_platform(target: Path):
    """Use the existing reviewed export profile, then add the public release files."""
    spec = importlib.util.spec_from_file_location(
        "duoflow_portable", ROOT / "scripts/export_framework.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.export(ROOT, target)
    copy_tree(OVERLAYS / "platform", target)
    for name in ("site.css", "demo.js", "demo.html"):
        write(target / "docs" / name, (OVERLAYS / "site" / name).read_text(encoding="utf-8"))
    write(target / "docs/index.html", (OVERLAYS / "site/platform.html").read_text(encoding="utf-8"))
    # Source review is useful to platform maintainers; the original import
    # manifest remains the immutable import baseline, copied under provenance/.
    for source in ("docs/源码阅读指南.md",):
        write(target / "common-platform" / source, (ROOT / source).read_text(encoding="utf-8"))
    original = ROOT.parent / "EXPORT-MANIFEST.json"
    if original.exists():
        (target / "provenance").mkdir(exist_ok=True)
        shutil.copyfile(original, target / "provenance/IMPORT-MANIFEST.json")


def build(profile, target):
    target = target.absolute()
    if target.exists() or target.resolve() != target:
        raise ValueError(
            "Use a new directory without links; existing repositories are never overwritten"
        )
    (build_platform if profile == "platform" else build_kit)(target)
    demo = target / "docs/demo.html"
    write(
        demo,
        demo.read_text(encoding="utf-8").replace(
            'data-product="PRODUCT"',
            'data-product="kit"' if profile == "module-kit" else 'data-product="platform"',
        ),
    )
    write(target / "VERSION", VERSION + "\n")
    write(target / "DESCRIPTION.txt", DESCRIPTIONS[profile] + "\n")
    # Keep source inventories and Git checkouts in the same UTF-8/LF form.
    for path in target.rglob("*"):
        if path.is_file():
            try:
                value = path.read_text(encoding="utf-8-sig")
            except UnicodeError:
                continue
            write(path, value)
    files = {
        p.relative_to(target).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in sorted(target.rglob("*"))
        if p.is_file() and ".git" not in p.parts
    }
    write(
        target / "RELEASE-MANIFEST.json",
        json.dumps(
            {
                "release": VERSION,
                "profile": profile,
                "files": files,
                "scope": "Source inventory; runtime validation is reported separately.",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return {"profile": profile, "directory": str(target), "release": VERSION, "files": len(files)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", choices=("platform", "module-kit"))
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.profile, args.output), ensure_ascii=False, indent=2))
