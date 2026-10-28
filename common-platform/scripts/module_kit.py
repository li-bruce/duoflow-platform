"""Generate portable module reference packages; check metadata without executing code.

This CLI deliberately does not install, start, enable or grant a module. Run with
the platform Python environment so checks use its current contract validators.
"""

from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import shutil
import sys
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
KIT = ROOT / "templates" / "module-kit"
KIT_VERSION = "1.1.0"
TEMPLATES = {"tool-python": ("tool", "python"), "workflow-node": ("workflow", "node")}
ADAPTATIONS = {"workflow-python-remote": ("workflow", "python-remote")}
TOKENS = ("__MODULE_ID__", "__TITLE__", "__PACK_NAME__")
CONTRACTS = {
    "access": "1.0",
    "interaction": "1.0",
    "workflow": "1.0",
    "remote_manifest": "1.2",
    "remote_invoke": "1.1",
    "registration": "1.1",
    "module_graph": "1.0",
    "flow_exports": "1.0",
    "runtime_observation": "1.0",
}
LEGACY_CONTRACTS = {
    key: value
    for key, value in CONTRACTS.items()
    if key not in {"module_graph", "flow_exports", "runtime_observation"}
}
LEGACY_CONTRACTS["registration"] = "1.0"
RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}
IGNORED = {"__pycache__", "node_modules", ".git", ".pytest_cache", "data", ".runtime"}


class KitError(ValueError):
    pass


def need(condition, message):
    if not condition:
        raise KitError(message)


def unredirected(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        need(
            not part.is_symlink() and not getattr(part, "is_junction", lambda: False)(),
            "路径不可经过符号链接或目录联接。",
        )
    return path


def file_in(root, relative):
    need(isinstance(relative, str) and not Path(relative).is_absolute(), "包内路径必须为相对路径。")
    value = unredirected(root / relative)
    need(value.resolve().is_relative_to(root.resolve()), "包内路径不能越界。")
    need(
        value.is_file() and value.stat().st_size <= 2 * 1024 * 1024,
        f"缺少文件或文件过大：{relative}",
    )
    return value


def pairs_unique(pairs):
    value = {}
    for key, child in pairs:
        need(key not in value, "JSON 字段不能重复。")
        value[key] = child
    return value


def read_json(root, relative):
    try:
        return json.loads(
            file_in(root, relative).read_text(encoding="utf-8-sig"),
            object_pairs_hook=pairs_unique,
            parse_constant=lambda _: (_ for _ in ()).throw(KitError("JSON 必须为有限数值。")),
        )
    except (ValueError, UnicodeError) as exc:
        raise KitError(f"无效 JSON：{relative}") from exc


def validate_names(module_id, title, pack_name):
    need(
        isinstance(module_id, str)
        and re.fullmatch(r"[a-z][a-z0-9.-]{1,59}", module_id)
        and not any(not part or part in RESERVED for part in module_id.split("."))
        and module_id != "core"
        and not module_id.startswith("core."),
        "模块 ID 应为 2—60 位小写字母、数字、点或短横线，不能使用 core 或系统保留名。",
    )
    need(
        isinstance(pack_name, str)
        and re.fullmatch(r"[a-z][a-z0-9-]{0,60}", pack_name)
        and pack_name not in RESERVED,
        "安装目录名应为小写字母、数字、短横线，最多 61 位。",
    )
    need(
        isinstance(title, str)
        and title.strip() == title
        and 1 <= len(title) <= 80
        and not any(ord(c) < 32 or c in "\\<>\"'`{}" for c in title),
        "标题应为 1—80 位普通文字，不含引号、括号代码或换行。",
    )


def scaffold(template, module_id, title, output, pack_name=None):
    """先生成到临时目录并完成静态检查，再改名为新模块目录。"""
    need(template in TEMPLATES, "未知模板。")
    pack_name = pack_name or module_id.replace(".", "-")
    validate_names(module_id, title, pack_name)
    destination = unredirected(Path(output))
    need(not destination.exists(), "目标目录已经存在；请使用新目录，不会覆盖已有项目。")
    need(destination.parent.is_dir(), "目标父目录必须已存在。")
    source = KIT / "blueprints" / template
    need(source.is_dir(), "模板源码缺失。")
    staging = Path(tempfile.mkdtemp(prefix=".mk-", dir=destination.parent)).resolve()
    try:
        replacements = dict(zip(TOKENS, (module_id, title, pack_name)))
        for base, prefix in (
            (source, Path(".")),
            (KIT / "sdk" / TEMPLATES[template][1], Path("sdk") / TEMPLATES[template][1]),
            (KIT / "sdk/frontend", Path("sdk/frontend")),
            (KIT / "examples/ai-services", Path("examples/ai-services")),
        ):
            need(base.is_dir(), "模板 SDK 缺失。")
            for path in sorted(base.rglob("*")):
                rel = path.relative_to(base)
                if prefix == Path("examples/ai-services") and (
                    path.name == "standalone-node.mjs" and template != "workflow-node"
                    or path.name == "standalone-python.py" and template != "tool-python"
                ):
                    continue
                if (
                    any(part in IGNORED for part in rel.parts)
                    or prefix.parts[0:1] == ("sdk",)
                    and "tests" in rel.parts
                ):
                    continue
                unredirected(path)
                if not path.is_file():
                    continue
                need(
                    path.suffix in {".json", ".py", ".mjs", ".tsx", ".ts", ".md", ".css", ".txt"}
                    or path.name == ".gitignore",
                    "模板包含非源码文件。",
                )
                text = path.read_text(encoding="utf-8-sig")
                for token, value in replacements.items():
                    text = text.replace(token, value)
                target = staging / prefix / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8", newline="\n")
        if template == "tool-python":
            shutil.copyfile(
                staging / "sdk/python/module_kit.py", staging / "backend/_module_kit.py"
            )
        kind, runtime = TEMPLATES[template]
        metadata = {
            "kit_version": KIT_VERSION,
            "template": template,
            "module_id": module_id,
            "title": title,
            "pack_name": pack_name,
            "kind": kind,
            "runtime": runtime,
            "contracts": CONTRACTS,
        }
        (staging / "module-kit.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (staging / ".gitignore").write_text(
            "data/\n.runtime/\nnode_modules/\n__pycache__/\n.pytest_cache/\n*.pyc\n.env\n.env.*\n"
            "!.env.example\n!.env.*.example\n*.log\n.stategraph/collector.json\n"
            "bo_stategraph/config/views/local.json\n.stategraph/upgrades/\n",
            encoding="utf-8",
        )
        check(staging)
        # rename only our fresh, validated staging directory; never replace a target.
        need(not destination.exists(), "目标目录在生成期间已创建；不会覆盖。")
        staging.rename(destination)
        return {
            "status": "generated",
            "path": str(destination),
            "module_id": module_id,
            "template": template,
            "installed": False,
            "grants_created": False,
        }
    finally:
        if staging.exists():
            need(
                staging.parent == destination.parent.resolve()
                and staging.name.startswith(".mk-")
                and not staging.is_symlink(),
                "拒绝清理非本次临时目录。",
            )
            shutil.rmtree(staging)


def _imports():
    # Import platform validators, never app.main and never supplier handlers.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from jsonschema import Draft202012Validator
    from app.access_control import validate_definition
    from app.module_contract import normalize_surfaces
    from app.registry import Registry
    from app.remote_services import RemoteServiceManager

    return (
        Draft202012Validator,
        validate_definition,
        normalize_surfaces,
        Registry,
        RemoteServiceManager,
    )


def frontend_exports(path, *, dependencies_only=False):
    """Parse, never import or evaluate, supplier TSX with the host TypeScript AST.

    Workflow references require a statically declared literal stepComponents map.
    Computed keys, spreads and dynamic registration belong in a separate framework
    extension rather than silently pretending this static checker understood them.
    """
    node = os.environ.get("NODE_EXE") or shutil.which("node")
    # The standalone kit installs its own parser; platform checkouts keep theirs
    # under web/. Both locations are inside the current repository.
    typescript = next(
        (
            path
            for path in (ROOT / "node_modules/typescript", ROOT / "web/node_modules/typescript")
            if path.is_dir()
        ),
        None,
    )
    need(node and typescript is not None, "前端静态检查需要 Node 与本仓库已安装的 TypeScript。")
    parser = r"""
const fs=require('node:fs'), ts=require(process.argv[1]);
const source=ts.createSourceFile(process.argv[2],fs.readFileSync(process.argv[2],'utf8'),ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
if(source.parseDiagnostics.length) throw new Error('Invalid TSX');
let hasDefault=false, names=null;const declarations=new Set(), imports=[];let dynamic=false;
function dependencies(node){
  if((ts.isImportDeclaration(node)||ts.isExportDeclaration(node))&&node.moduleSpecifier&&ts.isStringLiteral(node.moduleSpecifier))imports.push(node.moduleSpecifier.text);
  if(ts.isCallExpression(node)&&node.expression.kind===ts.SyntaxKind.ImportKeyword){
    if(node.arguments.length===1&&ts.isStringLiteral(node.arguments[0]))imports.push(node.arguments[0].text);else dynamic=true;
  }
  ts.forEachChild(node,dependencies);
}
dependencies(source);
if(process.argv[3]==='dependencies'){process.stdout.write(JSON.stringify({imports,dynamic}));process.exit(0);}
for(const statement of source.statements){
  if((ts.isFunctionDeclaration(statement)&&statement.body||ts.isClassDeclaration(statement))&&statement.name&&ts.isIdentifier(statement.name))declarations.add(statement.name.text);
  if(ts.isVariableStatement(statement))for(const d of statement.declarationList.declarations)if(ts.isIdentifier(d.name)&&d.initializer)declarations.add(d.name.text);
  if(ts.isImportDeclaration(statement) && statement.importClause && !statement.importClause.isTypeOnly){
    const clause=statement.importClause;if(clause.name)declarations.add(clause.name.text);
    if(clause.namedBindings){if(ts.isNamespaceImport(clause.namedBindings))declarations.add(clause.namedBindings.name.text);
    else for(const binding of clause.namedBindings.elements)if(!binding.isTypeOnly)declarations.add(binding.name.text);}
  }
}
for(const statement of source.statements){
  const modifiers=ts.canHaveModifiers(statement)?ts.getModifiers(statement)||[]:[];
  if(modifiers.some(m=>m.kind===ts.SyntaxKind.DefaultKeyword)&&((ts.isFunctionDeclaration(statement)&&statement.body)||ts.isClassDeclaration(statement)))hasDefault=true;
  if(ts.isExportAssignment(statement)&&!statement.isExportEquals){
    let value=statement.expression;while(ts.isParenthesizedExpression(value))value=value.expression;
    if(ts.isIdentifier(value)&&declarations.has(value.text)||ts.isArrowFunction(value)||ts.isFunctionExpression(value)||ts.isClassExpression(value))hasDefault=true;
    else throw new Error('Declared default value required');
  }
  if(!ts.isVariableStatement(statement)||!modifiers.some(m=>m.kind===ts.SyntaxKind.ExportKeyword))continue;
  for(const d of statement.declarationList.declarations){
    if(!ts.isIdentifier(d.name)||d.name.text!=='stepComponents')continue;
    if(names!==null||!d.initializer||!ts.isObjectLiteralExpression(d.initializer))throw new Error('Static object required');
    names=[];
    for(const p of d.initializer.properties){
      if(!ts.isShorthandPropertyAssignment(p)&&!ts.isPropertyAssignment(p))throw new Error('Static property required');
      if(!ts.isIdentifier(p.name)&&!ts.isStringLiteral(p.name))throw new Error('Static name required');
      const value=ts.isShorthandPropertyAssignment(p)?p.name:p.initializer;
      if(!ts.isIdentifier(value)||!declarations.has(value.text)||names.includes(p.name.text))throw new Error('Declared component required');
      names.push(p.name.text);
    }
  }
}
process.stdout.write(JSON.stringify({default:hasDefault,components:names,imports,dynamic}));
"""
    completed = subprocess.run(
        [node, "-e", parser, str(typescript), str(path), "dependencies" if dependencies_only else "exports"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    need(
        completed.returncode == 0,
        "共享前端语法或静态导出无效；流程须显式导出 stepComponents 对象字面量。",
    )
    return json.loads(completed.stdout)


def frontend_package_files(package):
    """Resolve the shipped host entrypoints' local imports without executing code.

    Assets and helper sources must stay in frontend/. Standalone previews are
    not dependencies of the production entries and are not installed implicitly.
    Host typechecking and production/browser acceptance remain separate checks.
    """
    root = unredirected(Path(package)).resolve()
    front = root / "frontend"
    pending = [front / name for name in ("steps.tsx", "Component.tsx", "flow.json", "interaction.json")
               if (front / name).is_file()]
    seen = set()
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".svg", ".png", ".jpg", ".webp")
    while pending:
        path = unredirected(pending.pop()).resolve()
        need(path.is_relative_to(front), "共享前端依赖必须随 frontend 包携带，不能引用包外文件。")
        file_in(root, path.relative_to(root).as_posix())
        if path in seen:
            continue
        seen.add(path)
        references = []
        if path.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            parsed = frontend_exports(path, dependencies_only=True)
            need(not parsed["dynamic"], "共享前端动态 import 必须使用可静态定位的包内字符串路径。")
            references = parsed["imports"]
        elif path.suffix == ".css":
            text = path.read_text(encoding="utf-8-sig")
            references = [next(part for part in match if part) for match in re.findall(
                r'''@import\s+["']([^"']+)["']|url\(\s*["']?([^\s"')]+)["']?\s*\)''', text
            )]
        for reference in references:
            if reference.startswith(("http:", "https:", "data:", "#")):
                continue
            if not reference.startswith(".") and path.suffix != ".css":
                continue
            need("?" not in reference and "#" not in reference, "包内资源引用暂不支持查询参数或片段。")
            value = unredirected(path.parent / reference)
            need(value.resolve().is_relative_to(front), "共享前端依赖必须随 frontend 包携带，不能引用包外文件。")
            candidates = [value, *(Path(str(value) + suffix) for suffix in suffixes),
                          *(value / ("index" + suffix) for suffix in suffixes)]
            found = next((candidate for candidate in candidates if candidate.is_file()), None)
            need(found is not None, "共享前端引用的包内依赖文件不存在。")
            pending.append(found)
    return sorted(path.relative_to(root).as_posix() for path in seen)


def check(package):
    """核对模块协议、权限、共享组件和引用，全程不导入供应商业务函数。"""
    root = unredirected(Path(package)).resolve()
    need(root.is_dir(), "模块包目录不存在。")
    packaged_frontend = frontend_package_files(root)
    meta = read_json(root, "module-kit.json")
    need(
        isinstance(meta, dict)
        and set(meta)
        == {
            "kit_version",
            "template",
            "module_id",
            "title",
            "pack_name",
            "kind",
            "runtime",
            "contracts",
        },
        "module-kit.json 字段不完整或含未知字段。",
    )
    supported = {**TEMPLATES, **ADAPTATIONS}
    need(
        meta["kit_version"] in {KIT_VERSION, "1.0.0"} and meta["template"] in supported,
        "模板版本或类型不受支持。",
    )
    need(
        (meta["kind"], meta["runtime"]) == supported[meta["template"]]
        and meta["contracts"]
        == (CONTRACTS if meta["kit_version"] == KIT_VERSION else LEGACY_CONTRACTS),
        "模板运行方式或协议版本不匹配。",
    )
    validate_names(meta["module_id"], meta["title"], meta["pack_name"])
    validator, validate_access, surfaces, registry_type, remote_type = _imports()
    manifest = read_json(root, "backend/manifest.json")
    access = read_json(root, "backend/access-definition.json")
    module_id = meta["module_id"]
    if meta["runtime"] in {"node", "python-remote"}:
        remote_type._validate_manifest(manifest)
        need(manifest.get("protocol_version") == "1.2", "远程模板必须使用 manifest 1.2。")
        module = manifest["module"]
        files = (
            (
                "backend/server.mjs",
                "backend/business.mjs",
                "sdk/node/runtime.mjs",
                "sdk/node/access-client.mjs",
                "sdk/node/store.mjs",
                "tests/workflow.test.mjs",
            )
            if meta["runtime"] == "node"
            else (
                "backend/server.py",
                "backend/workflow.py",
                "sdk/python/remote_access.py",
                "tests/test_adapter.py",
            )
        )
        for path in files:
            file_in(root, path)
        capabilities = manifest["capabilities"]
        from app.registration_contract import validate_registration

        registration = read_json(root, "backend/registration.json")
        try:
            validate_registration(registration, manifest)
        except Exception as exc:
            raise KitError("注册声明不符合当前接入标准。") from exc
        # Optional AI declarations belong to registration, never to a credential
        # file or capability input. Runtime binding is checked by each SDK.
        if registration.get("ai_services"):
            file_in(root, "sdk/node/ai-service.mjs" if meta["runtime"] == "node"
                    else "sdk/python/ai_service.py")
        need(
            registration["permissions"] == {"mode": "platform_access", "definition": access},
            "注册声明必须包含同一份权限定义。",
        )
        file_in(
            root,
            (
                "sdk/node/registration.mjs"
                if meta["runtime"] == "node"
                else "sdk/python/registration.py"
            ),
        )
    else:
        module = manifest
        need(
            set(manifest) == {"id", "version", "name", "description", "capabilities", "surfaces"},
            "本模板本地 manifest 顶层字段不完整或包含未支持字段。",
        )
        registry = registry_type(root)
        capabilities = []
        for raw in manifest["capabilities"]:
            spec = deepcopy(raw)
            need(
                isinstance(spec.get("handler"), str)
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*:[A-Za-z_][A-Za-z0-9_]*", spec["handler"]),
                "本地处理器应为包内文件名:函数名。",
            )
            filename, symbol = spec.pop("handler").split(":")
            source = file_in(root, f"backend/{filename}.py")
            tree = ast.parse(source.read_text(encoding="utf-8-sig"))
            need(
                any(
                    isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == symbol
                    for node in tree.body
                ),
                "本地处理器函数未声明。",
            )
            for key in ("input_schema", "output_schema"):
                if isinstance(spec.get(key), str):
                    spec[key] = read_json(root, "backend/" + spec[key])
            registry.register(module, spec, lambda *_: None)
            capabilities.append(spec)
        surfaces(module_id, capabilities, manifest.get("surfaces"), allow_skills=True)
        for path in (
            "backend/business.py",
            "sdk/python/module_kit.py",
            "backend/_module_kit.py",
            "tests/self_test.py",
        ):
            file_in(root, path)
        need(
            (root / "sdk/python/module_kit.py").read_bytes()
            == (root / "backend/_module_kit.py").read_bytes(),
            "Python SDK 源与可安装副本不一致。",
        )
    need(
        module["id"] == module_id and access.get("module_id") == module_id,
        "模块 ID 与权限附件或元数据不一致。",
    )
    validate_access(access)
    caps = {cap["id"]: cap for cap in capabilities}
    need(
        len(caps) == len(capabilities) and all(value.startswith(module_id + ".") for value in caps),
        "能力必须唯一且属于本模块命名空间。",
    )
    mappings = access["command_mappings"]
    need(all(mapping["capability_id"] in caps for mapping in mappings), "权限映射引用未知能力。")
    need(
        set(caps) == {mapping["capability_id"] for mapping in mappings},
        "每个能力必须有明确权限映射；不能退回旧固定角色。",
    )
    # Concrete template capabilities use structured inputs, never string commands.
    need(
        all("command" not in mapping for mapping in mappings),
        "模板每个具体能力应独立映射权限，不接受字符串子命令混用。",
    )
    need(len(mappings) == len(caps), "每个模板能力必须且只能映射一个权限。")
    permissions = {entry["id"] for entry in access["permissions"]}
    bound = {mapping["capability_id"]: mapping["permission"] for mapping in mappings}

    def capability(identifier, version=None):
        need(identifier in caps and identifier in bound, "前端引用未定义或未映射权限的能力。")
        spec = caps[identifier]
        need(version is None or version == spec["version"], "共享组件绑定的能力版本不匹配。")
        return spec

    def check_interaction():
        definition = read_json(root, "frontend/interaction.json")
        validator(read_json(ROOT, "contracts/interactions/v1/definition.schema.json")).validate(
            definition
        )
        need(definition["id"].startswith(module_id + "."), "共享组件必须使用本模块命名空间。")
        component = file_in(root, "frontend/Component.tsx").read_text(encoding="utf-8")
        need(frontend_exports(root / "frontend/Component.tsx")["default"], "共享组件需要默认导出。")
        need("@platform/interaction-sdk" in component, "共享组件须使用宿主 interaction SDK。")
        for key in ("input_schema", "state_schema"):
            validator.check_schema(definition[key])
            registry_type._check_refs(definition[key])
            need(definition[key].get("type") == "object", "共享组件输入和状态必须为对象。")
        validator(definition["state_schema"]).validate(definition["initial_state"])
        need(
            len({a["id"] for a in definition["actions"]}) == len(definition["actions"]),
            "组件动作编号重复。",
        )
        for action in definition["actions"]:
            spec = capability(action["capability_id"], action["capability_version"])
            properties = spec["input_schema"].get("properties", {})
            need(
                set(spec["input_schema"].get("required", []))
                <= set(action["bindings"])
                <= set(properties),
                "组件动作输入绑定缺失或引用未知字段。",
            )
            for target, binding in action["bindings"].items():
                source_schema = (
                    definition["state_schema"]
                    if binding["source"] == "state"
                    else definition["input_schema"]
                )
                need(
                    binding["key"] in source_schema.get("properties", {}),
                    "组件绑定引用未声明的输入或草稿字段。",
                )
        return [definition["id"]]

    if meta["kind"] == "tool":
        frontend_ids = check_interaction()
        need(
            not access["experiences"] and not access["workitem_sources"],
            "简单工具不能虚构 workflow 办理入口或待办源。",
        )
    else:
        document = read_json(root, "frontend/flow.json")
        validator(read_json(ROOT, "contracts/workflows/v1/definition.schema.json")).validate(
            document
        )
        component = file_in(root, "frontend/steps.tsx").read_text(encoding="utf-8")
        exports = frontend_exports(root / "frontend/steps.tsx")["components"]
        need(
            "@platform/workflow-sdk" in component and exports,
            "流程组件需要宿主 SDK 和 stepComponents 导出。",
        )
        flows = {flow["id"]: flow for flow in document["flows"]}
        need(
            len(flows) == len(document["flows"])
            and all(fid.startswith(module_id + ".") for fid in flows),
            "流程编号必须唯一且属于本模块。",
        )
        for flow in flows.values():
            if "state_machine" in flow:
                from app.state_machines import validate_state_machine

                validate_state_machine(flow["state_machine"], flow)
            provider = flow["provider"]
            need(
                provider["object_type"].startswith(module_id + "."),
                "模板业务对象类型须属于本模块。",
            )
            for field in ("read_capability", "upload_capability", "jobs_capability"):
                if field in provider:
                    spec = capability(provider[field])
                    if field == "read_capability":
                        need(spec["kind"] == "query", "流程读取能力必须为 query。")
            steps = {step["id"]: step for step in flow["steps"]}
            need(
                len(steps) == len(flow["steps"]) and flow["initial_step"] in steps,
                "流程步骤重复或起点未声明。",
            )
            groups = flow.get("page_groups", [])
            need(
                len({group["id"] for group in groups}) == len(groups)
                and all(step in steps for group in groups for step in group["steps"]),
                "页面组合引用未知步骤或重复分组。",
            )
            for step in steps.values():
                need(step["component"] in exports, "流程引用未在 stepComponents 中导出的共享组件。")
                need(
                    len({a["id"] for a in step["actions"]}) == len(step["actions"]),
                    "步骤内动作重复。",
                )
                for action in step["actions"]:
                    spec = capability(action["capability_id"], action["capability_version"])
                    need(
                        not action.get("target_step") or action["target_step"] in steps,
                        "动作目标步骤不存在。",
                    )
                    validator.check_schema(action["command_schema"])
                    registry_type._check_refs(action["command_schema"])
                    need(
                        action["command_schema"].get("type") == "object",
                        "步骤命令 Schema 必须为对象。",
                    )
                    payload = spec["input_schema"]
                    need(
                        payload.get("type") == "object"
                        and payload.get("properties", {}).get("command")
                        == action["command_schema"],
                        "步骤 command_schema 必须与绑定能力的 command Schema 一致。",
                    )
                    expected = (
                        {"command"}
                        if action.get("creates_object") or action.get("mode") == "draft_query"
                        else {"command", "object_id", "expected_revision"}
                    )
                    need(
                        set(payload.get("required", [])) == expected
                        and set(payload.get("properties", {})) == expected,
                        "步骤能力的事务信封字段不匹配。",
                    )
                    if action.get("mode") == "draft_query":
                        need(
                            spec["kind"] == "query"
                            and spec["execution"]["mode"] == "sync"
                            and not action.get("creates_object"),
                            "个人草案查询必须为同步 query。",
                        )
        need(access["experiences"], "流程模板需要至少一个实际办理视图。")
        for experience in access["experiences"]:
            need(experience["flow_id"] in flows, "办理视图引用未知流程。")
            declared = {step["id"] for step in flows[experience["flow_id"]]["steps"]}
            need(set(experience.get("step_ids", declared)) <= declared, "办理视图引用未知步骤。")
        experiences = {entry["id"]: entry for entry in access["experiences"]}
        for source in access["workitem_sources"]:
            spec = capability(source["capability_id"])
            need(
                source["experience_id"] in experiences and spec["kind"] == "query",
                "待办元数据应引用已声明视图和查询能力。",
            )
        frontend_ids = list(flows)
        if (root / "frontend/interaction.json").exists() or (
            root / "frontend/Component.tsx"
        ).exists():
            frontend_ids.extend(check_interaction())
    # Graph-aware packages must cover every actual host, including attachments.
    # Older packages retain their original contract and cannot claim this check.
    graph_checked = False
    if meta["kit_version"] == KIT_VERSION or (root / "backend/module-graph.json").exists():
        from app.module_graph import validate_graph_hosts

        graph = read_json(root, "backend/module-graph.json")
        if meta["kind"] == "workflow":
            need(
                registration["registration_version"] == "1.1",
                "新模块须确认原子、公开业务流与运行同步协议。",
            )
            need(
                registration["module_graph"] == graph
                and registration["flow_exports"] == read_json(root, "backend/flow-exports.json"),
                "注册附件与实际原子和业务流文件不一致。",
            )
        interaction_definitions = (
            [read_json(root, "frontend/interaction.json")]
            if (root / "frontend/interaction.json").exists()
            else []
        )
        workflow_definitions = (
            read_json(root, "frontend/flow.json")["flows"]
            if (root / "frontend/flow.json").exists()
            else []
        )
        if meta["kind"] == "workflow":
            expected_hosts = {
                "workflows": sorted(
                    (value["id"], value["version"]) for value in workflow_definitions
                ),
                "interactions": sorted(
                    (value["id"], value["version"]) for value in interaction_definitions
                ),
            }
            need(
                all(
                    sorted(
                        (value["id"], value["version"])
                        for value in registration["host_requirements"][kind]
                    )
                    == expected
                    for kind, expected in expected_hosts.items()
                ),
                "注册附件必须完整声明实际共享组件和流程。",
            )
        try:
            validate_graph_hosts(
                graph,
                manifest,
                interactions=interaction_definitions,
                workflows=workflow_definitions,
            )
        except Exception as exc:
            raise KitError("原子登记、业务节点或实际宿主映射不一致。") from exc
        for atom in graph["atoms"]:
            file_in(root, atom["source"]["path"])
        from app.flow_exports import validate_flow_exports

        flow_registration = (
            registration
            if meta["kind"] == "workflow"
            else {
                "permissions": {"mode": "platform_access", "definition": access},
                "data_objects": [],
            }
        )
        try:
            validate_flow_exports(
                read_json(root, "backend/flow-exports.json"),
                manifest,
                flow_registration,
                graph,
                workflows=workflow_definitions,
            )
        except Exception as exc:
            raise KitError("公开业务流的入口、数据或权限声明不一致。") from exc
        graph_checked = True
    for path in ("README.md",):
        file_in(root, path)
    for directory in ("backend", "frontend", "sdk"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and not any(part in IGNORED for part in path.relative_to(root).parts):
                file_in(root, path.relative_to(root).as_posix())
                if path.suffix in {".json", ".py", ".mjs", ".tsx", ".md"}:
                    need(
                        not any(token in path.read_text(encoding="utf-8-sig") for token in TOKENS),
                        "包内仍有未替换的模板占位符。",
                    )
    return {
        "status": "valid",
        "module_id": module_id,
        "template": meta["template"],
        "capabilities": len(caps),
        "permissions": len(permissions),
        "frontend_ids": frontend_ids,
        "frontend_files": packaged_frontend,
        "module_graph_checked": graph_checked,
        "checks": [
            "manifest",
            "access",
            "capability_permissions",
            "shared_frontend",
            "references",
            "install_files",
            "registration",
            *(["module_graph", "atomic_hosts", "flow_exports"] if graph_checked else []),
        ],
        "executed_supplier_code": False,
        "installed": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("scaffold", help="生成新模块包，不安装或覆盖")
    generate.add_argument("--template", choices=TEMPLATES, required=True)
    generate.add_argument("--module-id", required=True)
    generate.add_argument("--title", required=True)
    generate.add_argument("--pack-name")
    generate.add_argument("--output", type=Path, required=True)
    validate = commands.add_parser("check", help="只读检查生成包或按相同布局接入的现有服务")
    validate.add_argument("package", type=Path)
    validate.add_argument("--frontend-only", action="store_true", help="只核对共享前端生产依赖闭包，不导入后端契约或业务代码")
    args = parser.parse_args(argv)
    try:
        result = (
            scaffold(args.template, args.module_id, args.title, args.output, args.pack_name)
            if args.command == "scaffold"
            else ({"status": "valid", "checks": ["frontend_dependencies"],
                   "frontend_files": frontend_package_files(args.package),
                   "executed_supplier_code": False, "installed": False}
                  if args.frontend_only else check(args.package))
        )
    except Exception as exc:
        # Validators may contain supplier values in their verbose repr. Only emit
        # our controlled errors or a stable exception class, never raw documents.
        message = (
            str(exc) if isinstance(exc, KitError) else f"契约检查未通过（{type(exc).__name__}）。"
        )
        print(
            json.dumps({"status": "invalid", "message": message}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
