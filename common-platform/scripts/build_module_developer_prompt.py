"""Rebuild the portable module-developer prompt from reviewed prose and public code.

No platform imports, network calls, environment files or runtime data are used.
The digest detects change, not semantic correctness: --write requires --reviewed.
"""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PROMPT_PATH = Path("prompts/模块开发与改造提示词.md")
SNAPSHOT_PATH = Path("prompts/framework-contract.snapshot.json")
SOURCE_PATH = Path("prompts/_source/module-developer.md")
COMPONENT_PROMPT_PATH = Path("prompts/组件开发提示词.md")
QUICK_PROMPT_PATH = Path("prompts/模块开发快速提示词.md")

# Only public, task-relevant source trees. Never scan the project recursively.
# New/deleted files under these roots also invalidate the recorded fingerprint.
SOURCE_TREES = {
    "app": {".py"},
    "web/src": {".ts", ".tsx", ".css"},
    "templates": {".py", ".json", ".md", ".ps1", ".ts", ".tsx", ".mjs"},
    "components": {".json", ".ts", ".tsx", ".css", ".md"},
    "flows": {".json", ".ts", ".tsx", ".css", ".md"},
    "prompts/_source": {".md"},
    "reports/handbook-assets": {".js", ".css"},
    "docs": {".md"},
    "contracts": {".json", ".md"},
    "distribution": {".py", ".json", ".md", ".html", ".js", ".css", ".txt", ".yaml", ".yml"},
}
SOURCE_FILES = (
    "AGENTS.md", "prompts/README.md", "config/review-flow.json", "config/identity-policy.json", "requirements.in", "requirements.lock.txt",
    "web/package.json", "web/package-lock.json", "web/tsconfig.json", "web/vite.config.ts", "web/vitest.config.ts", "scripts/start.ps1",
    "scripts/build_module_developer_prompt.py", "scripts/module_kit.py", "scripts/build_handbooks.py", "scripts/export_framework.py", "config/identity-demo.json", "config/project-public-documents.json", "docs/handbooks.json",
    "scripts/module_kit_integration.py", "scripts/module_kit_services.py",
    "scripts/build_duoflow_repositories.py",
)
EXCLUDED_PARTS = {"data", "logs", "node_modules", "__pycache__", ".venv", ".git", "dist"}


def read(root: Path, relative: str | Path) -> str:
    path = root / relative
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Source must remain inside the project.")
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def tracked_sources(root: Path) -> dict[str, str]:
    paths = {Path(p) for p in SOURCE_FILES if (root / p).is_file()}
    for directory, suffixes in SOURCE_TREES.items():
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if (path.is_file() and path.suffix in suffixes
                    and not EXCLUDED_PARTS.intersection(relative.parts)
                    and not any(part.startswith(".") for part in relative.parts)):
                paths.add(relative)
    # Hash normalized text so checkout line-ending conversion does not cause drift.
    return {p.as_posix(): hashlib.sha256(read(root, p).encode("utf-8")).hexdigest()
            for p in sorted(paths, key=lambda item: item.as_posix())}


def tree(root: Path, path: str) -> ast.Module:
    return ast.parse(read(root, path), filename=path)


def one(values, label):
    values = list(values)
    if len(values) != 1:
        raise ValueError(f"Cannot extract {label}; review the generator for the new contract.")
    return values[0]


def literal(node, names=None):
    """Evaluate constants, containers and numeric arithmetic only; never execute code."""
    names = names or {}
    if isinstance(node, ast.Name) and node.id in names:
        return names[node.id]
    if isinstance(node, ast.IfExp):
        condition = literal(node.test, names)
        if not isinstance(condition, bool):
            raise ValueError("Only an explicitly supplied boolean condition is supported.")
        return literal(node.body if condition else node.orelse, names)
    if isinstance(node, ast.Dict):
        return {literal(k, names): literal(v, names) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values = [literal(v, names) for v in node.elts]
        return set(values) if isinstance(node, ast.Set) else values
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add)):
        left, right = literal(node.left, names), literal(node.right, names)
        if type(left) not in (int, float) or type(right) not in (int, float):
            raise ValueError("Only numeric arithmetic is supported.")
        return left * right if isinstance(node.op, ast.Mult) else left + right
    return ast.literal_eval(node)


def assignment(scope, name, names=None):
    return literal(one((n.value for n in scope.body if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)), name), names)


def named_class(module, name):
    return one((n for n in module.body if isinstance(n, ast.ClassDef) and n.name == name), name)


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def exported_routes(root: Path, path: str) -> list[dict]:
    """Read literal router declarations without importing app code or settings."""
    module = tree(root, path)
    prefixes = [literal(keyword.value) for node in ast.walk(module)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'APIRouter'
                for keyword in node.keywords if keyword.arg == 'prefix']
    prefix = one(prefixes, path + ' router prefix') if prefixes else ''
    return [{'method': decorator.func.attr.upper(), 'path': prefix + literal(decorator.args[0])}
            for node in ast.walk(module) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for decorator in node.decorator_list if isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute) and decorator.func.attr in {'get', 'post', 'patch', 'put', 'delete'}
            and decorator.args and isinstance(decorator.args[0], ast.Constant)]


def public_template_files(root: Path, directory: str) -> dict[str, str]:
    """Export only reviewed public source formats, never env or runtime files."""
    values = {}
    # Path ordering depends on the host OS (Windows folds letter case). Use
    # public POSIX text so generated documents stay identical after a checkout.
    for path in sorted((root / directory).rglob('*'), key=lambda p: p.as_posix()):
        relative = path.relative_to(root)
        if (path.is_file() and path.suffix in {'.json', '.md', '.py', '.mjs', '.ts', '.tsx', '.css', '.ps1'}
                and not EXCLUDED_PARTS.intersection(relative.parts)
                and not any(part.startswith('.') for part in relative.parts)):
            values[relative.as_posix()] = read(root, relative)
    return values


def build_snapshot(root: Path) -> dict:
    main = tree(root, "app/main.py")
    version = one((literal(k.value) for n in ast.walk(main) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Name) and n.func.id == "FastAPI"
                   for k in n.keywords if k.arg == "version"), "platform version")
    monitor = named_class(tree(root, "app/service_monitor.py"), "ServiceMonitor")
    remote = named_class(tree(root, "app/remote_services.py"), "RemoteServiceManager")
    timeout = one((n for n in remote.body if isinstance(n, ast.FunctionDef) and n.name == "_timeout"), "timeout")
    timeout_limit = one((literal(n.args[2]) for n in ast.walk(timeout) if isinstance(n, ast.Call)
                         and isinstance(n.func, ast.Attribute) and n.func.attr == "_number"), "timeout maximum")
    renderer_expression = one((n.comparators[0] for n in ast.walk(remote)
                            if isinstance(n, ast.Compare) and isinstance(n.left, ast.Subscript)
                            and isinstance(n.left.slice, ast.Constant) and n.left.slice.value == "renderer"
                            and len(n.ops) == 1 and isinstance(n.ops[0], ast.NotIn)), "remote renderers")
    remote_renderers = sorted(literal(renderer_expression, {'modern': True}))
    legacy_renderers = sorted(literal(renderer_expression, {'modern': False}))
    max_caps = one((literal(n.comparators[-1]) for n in ast.walk(remote)
                    if isinstance(n, ast.Compare) and len(n.ops) == 2
                    and ast.unparse(n.comparators[0]) == "len(capabilities)"), "capability limit")
    artifact_limit = one((literal(n.comparators[0]) for n in ast.walk(tree(root, "app/contracts.py"))
                          if isinstance(n, ast.Compare) and len(n.ops) == 1
                          and isinstance(n.ops[0], ast.LtE)
                          and ast.unparse(n.left) == "len(content.encode('utf-8'))"), "artifact limit")
    core = tree(root, "templates/remote-service-template/service_core.py")
    protocol = assignment(core, "PROTOCOL")
    local_dir = "templates/module-template/"
    remote_dir = "templates/remote-service-template/"
    local_manifest = json.loads(read(root, local_dir + "manifest.json"))
    # The existing runnable template remains a legacy reference. The portable
    # current example is adapted explicitly and validated against real discovery.
    legacy_manifest = json.loads(read(root, remote_dir + "manifest.json"))
    contracts = {path.name: json.loads(read(root, path.relative_to(root)))
                 for path in sorted((root / 'contracts/v1.2').glob('*.schema.json'), key=lambda p: p.as_posix())}
    expected_contracts = {'invoke-request-1.1.schema.json', 'invoke-response-1.1.schema.json',
                          'workspace.schema.json', 'platform-file.schema.json', 'skill.schema.json'}
    if not expected_contracts <= set(contracts):
        raise ValueError('The v1.2 public contract set is incomplete.')
    for local_cap in local_manifest['capabilities']:
        local_cap['roles'] = ['member', 'admin']
    local_manifest['surfaces']['conversation']['skills'] = [{
        'id': 'echo-flow', 'title': '回显办理流程', 'description': '填写表单并检查回显结果。',
        'triggers': ['回显办理流程'], 'entry_capability': local_manifest['capabilities'][0]['id'],
        'steps': [{'id': 'echo', 'title': '回显消息', 'capability_id': local_manifest['capabilities'][0]['id']}],
    }]
    remote_manifest = deepcopy(legacy_manifest)
    remote_manifest['protocol_version'] = '1.2'
    remote_manifest['module'].update(version='1.2.0', name='统一契约回显示例')
    cap = remote_manifest["capabilities"][0]
    cap['version'] = '1.2.0'
    cap['roles'] = ['member', 'admin']
    cap['presentation'] = {host: {'renderer': 'workspace'} for host in ('page', 'conversation')}
    cap['input_schema']['properties']['attachment'] = {**deepcopy(contracts['platform-file.schema.json']), 'title': '合成附件（可选）'}
    cap['output_schema']['properties']['blocks'] = deepcopy(contracts['workspace.schema.json']['properties']['blocks'])
    cap['output_schema']['required'].append('blocks')
    remote_manifest['surfaces']['conversation']['skills'] = [{
        'id': 'echo-flow', 'title': '回显办理流程', 'description': '输入消息，检查真实返回结果。',
        'triggers': ['回显办理流程'], 'entry_capability': cap['id'],
        'steps': [{'id': 'echo', 'title': '填写并提交消息', 'capability_id': cap['id']}],
    }]
    # This synthetic scenario intentionally belongs to the neutral echo template.
    # If the template changes, the real contract tests require a matching scenario.
    invocation_protocol = contracts['invoke-request-1.1.schema.json']['properties']['protocol_version']['const']
    sample_file = {'filename': 'synthetic.txt', 'media_type': 'text/plain', 'content_base64': 'c3ludGhldGljCg=='}
    request = {"protocol_version": invocation_protocol, "request_id": "0123456789abcdef0123456789abcdef",
               "capability_id": cap["id"], "capability_version": cap["version"],
               "input": {"message": "合成测试消息", 'attachment': sample_file},
               "context": {"workspace_id": "example-workspace", "actor": {
                   "id": "example-user", "role": "member", 'roles': ['member'],
                   'organization_id': 'example-organization', 'team_ids': ['example-team'],
                   'name': '合成使用者', 'authorization_revision': 1}}}
    response = {k: request[k] for k in ("protocol_version", "request_id", "capability_id", "capability_version")}
    response.update(summary="独立服务已返回输入的消息。", data={"text": request["input"]["message"], 'blocks': [
        {'type': 'text', 'title': '消息', 'text': request['input']['message']},
        {'type': 'table', 'title': '合成记录', 'columns': [{'key': 'message', 'label': '消息'}], 'rows': [{'message': request['input']['message']}]},
        {'type': 'metrics', 'items': [{'label': '记录数量', 'value': 1, 'unit': '条'}]},
        {'type': 'chart', 'title': '数量示例', 'series': [{'label': '合成记录', 'value': 1}]},
        {'type': 'map', 'title': '位置示例', 'points': [{'label': '合成点位', 'lat': 0, 'lon': 0, 'count': 1}], 'precision': '合成位置，仅演示'},
        {'type': 'evidence', 'items': [{'quote': request['input']['message'], 'feature': '输入文本', 'value': request['input']['message'], 'status': '原样返回'}]},
        {'type': 'actions', 'items': [{'label': '再次检查', 'capability_id': cap['id'], 'input': {'message': request['input']['message']}}]},
        {'type': 'image', 'media_type': 'image/png', 'alt': '合成的 1 像素图片', 'content_base64': 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Wl6N00AAAAASUVORK5CYII='},
        {'type': 'download', **sample_file},
    ]})
    legacy_cap = legacy_manifest['capabilities'][0]
    legacy_request = {'protocol_version': protocol, 'request_id': '0123456789abcdef0123456789abcdef',
                      'capability_id': legacy_cap['id'], 'capability_version': legacy_cap['version'],
                      'input': {'message': '合成旧协议消息'},
                      'context': {'workspace_id': 'example-workspace', 'actor': {'id': 'example-user', 'role': 'member'}}}
    workspace = tree(root, 'app/workspace_contract.py')
    dependency_lines = read(root, "requirements.lock.txt").splitlines()
    dependencies = [one((line for line in dependency_lines if line.startswith(name + "==")), name)
                    for name in ("fastapi", "uvicorn", "jsonschema")]
    access_contracts = {path.relative_to(root / 'contracts').as_posix(): json.loads(read(root, path.relative_to(root)))
                        for path in sorted((root / 'contracts/access/v1').glob('*.schema.json'), key=lambda p: p.as_posix())}
    if not {'access/v1/definition.schema.json', 'access/v1/binding.schema.json'} <= set(access_contracts):
        raise ValueError('The access v1 contract set is incomplete.')
    bridge = tree(root, 'app/access_bridge.py')
    decision_schema = assignment(bridge, 'DECISION_REQUEST_SCHEMA', {
        'IDENTIFIER': assignment(bridge, 'IDENTIFIER'), 'FACT_VALUE': assignment(bridge, 'FACT_VALUE')})
    access_engine = named_class(tree(root, 'app/access_control.py'), 'CasbinDecisionEngine')
    bridge_class = named_class(bridge, 'AccessBridge')
    bridge_init = one((node for node in bridge_class.body if isinstance(node, ast.FunctionDef) and node.name == '__init__'), 'access bridge initializer')
    bridge_defaults = {arg.arg: literal(value) for arg, value in zip(bridge_init.args.kwonlyargs, bridge_init.args.kw_defaults)}
    access_definition = {'contract_version': '1.0', 'module_id': 'example-access', 'version': '1.0.0', 'title': '合成记录范围权限',
        'scope_keys': ['project_id', 'owner_id'], 'permissions': [{'id': 'example-access.item.read', 'title': '读取记录', 'guards': []}],
        'role_templates': [{'id': 'example-access.reader', 'title': '范围读取者', 'permissions': ['example-access.item.read'], 'required_scope': ['project_id', 'owner_id']}],
        'command_mappings': [{'capability_id': 'example-access.item.read', 'permission': 'example-access.item.read'}],
        'experiences': [], 'workitem_sources': []}
    snapshot = {
        "snapshot_version": 3,
        "facts": {
            "platform_version": version,
            "manifest_protocol": remote_manifest["protocol_version"],
            "invocation_protocol": invocation_protocol,
            "legacy_protocols": {'manifest': legacy_manifest['protocol_version'], 'invocation': protocol},
            "identity_policy": json.loads(read(root, 'config/identity-policy.json')),
            "heartbeat": {name.lower(): assignment(monitor, name) for name in (
                "INTERVAL_SECONDS", "TIMEOUT_SECONDS", "STALE_AFTER_SECONDS", "FAILURE_THRESHOLD", "MAX_CONCURRENCY")},
            "limits": {"request_bytes": assignment(workspace, "MAX_INVOKE_BYTES"),
                       "manifest_bytes": assignment(remote, "_MAX_MANIFEST"),
                       "response_bytes": assignment(workspace, "MAX_WORKSPACE_RESPONSE_BYTES"),
                       "file_bytes": assignment(workspace, "MAX_FILE_BYTES"),
                       "legacy_request_bytes": assignment(remote, "_MAX_REQUEST"),
                       "legacy_response_bytes": assignment(remote, "_MAX_RESPONSE"),
                       "text_artifact_bytes": artifact_limit, "invoke_timeout_seconds": timeout_limit,
                       "remote_capabilities": max_caps},
            "remote_renderers": remote_renderers,
            "legacy_remote_renderers": legacy_renderers,
            "local_renderers": assignment(tree(root, "app/registry.py"), "RENDERERS"),
            "python_reference_dependencies": dependencies,
            "devtools": {"routes": exported_routes(root, "app/devtools_routes.py"),
                         "max_declaration_bytes": assignment(tree(root, "app/devtools.py"), "MAX_DECLARATION_BYTES"),
                         "max_history": assignment(tree(root, "app/devtools.py"), "MAX_HISTORY")},
            "access": {
                "contract_version": access_contracts['access/v1/definition.schema.json']['properties']['contract_version']['const'],
                "engine": assignment(access_engine, 'name'),
                "engine_dependency": one((line for line in dependency_lines if line.startswith('casbin==')), 'Casbin pinned dependency'),
                "decision_header": 'X-Platform-Access',
                "ticket_default_ttl_seconds": bridge_defaults['ttl_seconds'],
                "decision_request_bytes": assignment(bridge, 'MAX_REQUEST_BYTES'),
                "decision_ticket_bytes": assignment(bridge, 'MAX_TICKET_BYTES'),
                "business_routes": exported_routes(root, 'app/business_center.py'),
                "management_routes": exported_routes(root, 'app/access_routes.py'),
                "remote_decision_routes": exported_routes(root, 'app/access_bridge.py'),
            },
        },
        "examples": {
            "local": {"manifest": local_manifest,
                      "schemas": {name: json.loads(read(root, local_dir + f"schemas/{name}.json")) for name in ("input", "output")},
                      "handler": read(root, local_dir + "handlers.py")},
            "remote": {"manifest": remote_manifest,
                       "request_schema": contracts['invoke-request-1.1.schema.json'],
                       "request": request, "response": response,
                       "health": {"status": "ok", "service": remote_manifest["module"]["id"], "protocol_version": invocation_protocol},
                       "embedded_tool": {"id": "standalone-page", "title": "独立页面示例", "description": "服务提供的隔离页面，关联同一能力。",
                                         "kind": "embedded", "path": "/tools/example", "capability_ids": [cap["id"]]}},
            "legacy_remote": {'manifest': legacy_manifest, 'request': legacy_request,
                              'request_schema': assignment(core, 'REQUEST_SCHEMA', {'PROTOCOL': protocol})},
            "access": {
                "definition": access_definition,
                "binding": {'subject_id': 'synthetic-owner', 'module_id': 'example-access', 'role_id': 'example-access.reader',
                            'scope': {'project_id': 'P', 'owner_id': 'synthetic-owner'}, 'expires_at': None, 'expected_policy_revision': 1},
                "decision_request": {'request_id': request['request_id'], 'operation': 'invoke', 'permission': 'example-access.item.read',
                                     'facts': {'project_id': 'P', 'owner_id': 'synthetic-owner'}},
            },
        },
        "contracts": contracts,
        "workflow_contracts": {path.relative_to(root / 'contracts').as_posix(): json.loads(read(root, path.relative_to(root)))
                               for group in ('workflows', 'files', 'jobs', 'state-machines', 'registration') for path in sorted((root / 'contracts' / group).rglob('*.schema.json'), key=lambda p: p.as_posix())},
        "access_contracts": access_contracts,
        "access_decision_request_schema": decision_schema,
        "remote_access_templates": public_template_files(root, 'templates/remote-access-client'),
        "module_kit_files": public_template_files(root, 'templates/module-kit'),
        "sources": tracked_sources(root),
    }
    snapshot["contract_digest"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                            separators=(",", ":")).encode("utf-8")).hexdigest()
    return snapshot


def block(title, value, language="json"):
    body = json_text(value) if language == "json" else value.rstrip() + "\n"
    # README/source examples may contain fenced blocks themselves. Keep them
    # inside this exported block instead of ending the surrounding Markdown.
    fence = '`' * max(3, max((len(run) + 1 for run in re.findall(r'`+', body)), default=3))
    return f"\n### {title}\n\n{fence}{language}\n{body}{fence}\n"


def module_kit_appendix(snapshot):
    text = '\n## 简化模块模板与 SDK（自动导出）\n\n优先使用模板生成器与同批模板包；本附录保留完整公开源码，供离线核对。生成、静态检查、安装、授权和业务验收是不同步骤。已有服务按手册适配，不将独立服务改写成参考示例数据库。\n'
    languages = {'.py': 'python', '.mjs': 'javascript', '.ts': 'typescript', '.tsx': 'tsx', '.css': 'css', '.json': 'json', '.md': 'markdown', '.ps1': 'powershell'}
    for path, body in sorted(snapshot['module_kit_files'].items()):
        language = languages[Path(path).suffix]
        text += block(path, json.loads(body) if language == 'json' else body, language)
    return text


def access_appendix(snapshot, root: Path = ROOT):
    """The same permission contract and server bridge reach both portable prompts."""
    access = snapshot['facts']['access']
    text = f"""\n## 注册权限与继续办理附录（自动导出）

权限包版本 {access['contract_version']}；实际引擎 {access['engine_dependency']}。本附录与 manifest/invoke 分开，不给旧 actor 增加 permissions 字段。安装定义不授予用户权限。

远程接入顺序：发现模块并保持禁用 → 安装需要的 flow/组件 → 注册权限附件 → 启用模块 → 由身份管理者授权。注册定义可以基于已发现但禁用的服务；管理授权 API 要求模块已经启用。启用的新 access 模块没有 grant 时仍拒绝业务访问。

下面的 example-access 权限包与 Node 只读服务的 capability 相配；它没有 workflow，因此 experiences/workitem_sources 为空，不会自动产生复杂办理工作台。开发有状态模块时另交付 flow、步骤组件、对象 adapter 和相应 experience；不得仅复制权限包就声称 Web/对话流程已接入。binding 是单独管理请求的合成说明，不应随模块包创建真实账号或授予实际用户。
"""
    for name, schema in sorted(snapshot['access_contracts'].items()):
        text += block('contracts/' + name, schema)
    text += block('Access v1 执行与兼容边界', read(root, 'contracts/access/v1/README.md'), 'markdown')
    for title, name in [('合成权限附件 access-definition.json', 'definition'), ('合成管理授权请求（不随安装执行）', 'binding')]:
        text += block(title, snapshot['examples']['access'][name])
    text += block('当前管理、业务中心与桥接路由', {key: access[key] for key in ('management_routes', 'business_routes', 'remote_decision_routes')})
    text += block('Python handler 的仓储事实授权函数（完整函数）', '''from app.errors import require

def authorize_record(ctx, record):
    # record is loaded by this module's repository, never accepted as input facts.
    facts = {"project_id": record["project_id"], "owner_id": record["owner_id"]}
    decision = ctx.access.check(ctx.actor, "example-access.item.read", facts)
    require(decision["allowed"], "object_scope", "对象不存在或当前范围不可见。", 404)
    return record
''', 'python')
    text += f"""
Python adapter 工厂可使用 AccessControl(context.sessions) 读取中央授权；业务事务内可用 AccessControl.from_session(session)，它不提交事务也不允许管理写入。has_permission 只供目录，正式对象按 check 与业务守卫判断。ctx.access_stamp 由平台设置，ctx.create_resource 自动记录；不要自行替换用户或快照。

服务端桥接请求头为 {access['decision_header']}，默认票据有效期 {access['ticket_default_ttl_seconds']} 秒；票据最多 {access['decision_ticket_bytes']} 字节，请求正文最多 {access['decision_request_bytes']} 字节。票据由主框架发送，示例 JSON 不包含票据、服务 token 或签名密钥。operation 和 request_id 必须对应正在处理的调用，facts 先从服务自有仓库读取。网络错误默认拒绝，不通过旧角色回退。
"""
    text += block('远程 decision 正文 Schema（从执行代码导出）', snapshot['access_decision_request_schema'])
    text += block('合成 decision 正文，不包含票据', snapshot['examples']['access']['decision_request'])
    for path, body in sorted(snapshot['remote_access_templates'].items()):
        suffix = {'.json': 'json', '.md': 'markdown', '.py': 'python', '.mjs': 'javascript'}[Path(path).suffix]
        text += block(path, json.loads(body) if suffix == 'json' else body, suffix)
    return text


def appendix(snapshot, root: Path = ROOT):
    f, e = snapshot["facts"], snapshot["examples"]
    h, limits = f["heartbeat"], f["limits"]
    text = f"""## 契约附录（自动导出，须与前文一起提供给开发 AI）

### A. 接入速查

| 项目 | local 本地模块 | remote 独立服务 |
|---|---|---|
| 运行方式 | 受信任 Python，随平台加载 | 独立 HTTP 服务，语言不限 |
| manifest | 平铺模块字段，无 protocol_version | module 包裹模块信息；manifest {f['manifest_protocol']} |
| Schema | 包内 JSON 文件或内联对象 | 内联 object Schema |
| 执行模式 | sync / background | background，单次 HTTP 内完成 |
| 页面入口 | form | form / embedded |
| 安装 | extensions/installed.json 添加模块目录后重启 | 管理员保存连接、发现、启用发布 |
| 模型 | ctx.gateway.chat | 没有平台模型代理或密钥继承 |

两端均引用同一 capability ID。presentation 声明结果组件，surfaces 声明页面工具和对话入口；二者不能互相替代。页面工具集合覆盖全部能力，对话列表也覆盖全部能力且不能重复。同一能力可出现在多个页面工具中。

当前远程 renderer：{', '.join(f['remote_renderers'])}。本地已注册 renderer：{', '.join(f['local_renderers'])}；其中管理类组件有特定数据与权限要求，不是通用业务结果渲染器。普通模块优先 text-result/json。

固定平台角色与继承关系由后文 identity-policy 完整导出。已注册业务使用独立 access v1 范围角色模板和实际授权，固定角色字符串仍保留在基础身份/旧协议中；system_admin 不自动获得业务权限。未注册的旧模块继续固定角色路径，不能据此赋予新业务全读。query 是能力语义，不是数据库只读沙箱；confirmation 是交互声明，不是业务审批授权。

### B. 当前数值边界与状态

| 项目 | 平台当前值 |
|---|---:|
| 远程能力数上限 | {limits['remote_capabilities']} |
| 请求 / manifest 最大字节数 | {limits['request_bytes']:,} / {limits['manifest_bytes']:,} |
| 响应最大字节数 | {limits['response_bytes']:,} |
| UTF-8 文本产物最大字节数 | {limits['text_artifact_bytes']:,} |
| platform-file / 下载 / 图片单件最大字节数 | {limits['file_bytes']:,} |
| 远程调用配置超时上限（秒） | {limits['invoke_timeout_seconds']} |
| 后台探测间隔（秒） | {h['interval_seconds']} |
| 单次健康探测超时（秒） | {h['timeout_seconds']} |
| 连续失败达到离线的次数 | {h['failure_threshold']} |
| 距最近完成探测达到过期的秒数 | 超过 {h['stale_after_seconds']} |
| 健康探测最大并发 | {h['max_concurrency']} |

健康状态为 unknown/healthy/degraded/offline/stale；一次成功清零连续失败。unknown/degraded 允许显式调用，offline/stale 阻止新执行；停用和健康相互独立。前端关闭不影响后台探测；运行时监测状态保存在进程中，重启后重新探测。该实现仍是单进程参考平台，不是跨节点高可用注册中心。

修改连接会撤下原能力并要求重新发现/发布。清单变更须重新发布；相同清单发现和失败的发现不自动撤销原发布。任务固定连接修订和能力版本，停用或修订变化后旧任务不会切换新目标。取消本地等待不保证远端停止。

### C. 本地模块最小文件（以下各文件完整）

目录为 extensions/example-echo/，内含 manifest.json、handlers.py、schemas/input.json、schemas/output.json 和 README.md。在现有 extensions/installed.json 的 modules 数组追加 example-echo，保留其他条目，不能覆盖整个安装清单。下面代码在平台环境中运行。
"""
    local = e["local"]
    text += block("C1. manifest.json", local["manifest"])
    for name, schema in local["schemas"].items():
        text += block(f"C2. schemas/{name}.json", schema)
    text += block("C3. handlers.py", local["handler"], "python")
    text += """
handler 返回 CapabilityOutput(summary: str, data: dict, views: list = [], resource: dict | None = None, artifacts: list = [])。
ctx 提供 actor、workspace_id、origin、invocation_id、run_id、session、gateway、access 与 access_stamp。注册业务用 ctx.access.check 检查模块仓储事实；ctx.create_resource(type, title, data) 自动携带该次调用的授权快照。ctx.create_artifact(resource_id, name, content, media_type) 创建当前用户资源的 UTF-8 文本产物，content 可为文本或可解码的字节。平台统一提交事务并检查权限。本节 echo 骨架尚未注册 access，不能仅添加权限附件就声称已有业务对象范围守卫。

若模块需要模型，使用 await ctx.gateway.chat(messages, profile_id=None, tools=None)；messages 为 role/content 对象列表，返回字典中的 content 是文本、model 是实际模型标识。检查空内容与失败，不将错误替换为预制结果。model-profile 字段可供用户选已启用配置，不传模型 Key。

### D. 远程服务最小完整声明

三个端点固定为 GET {base_url}/platform/v1/manifest、GET {base_url}/platform/v1/health、POST {base_url}/platform/v1/invoke。部署到其他服务器使用 HTTPS；HTTP 仅限回环地址。请求由平台后端发送，不需要为了服务接入而开放跨站浏览器权限。平台不跟随重定向，也不使用系统代理。

按模板为三个接口实现服务端 Bearer 鉴权。平台配置支持无 token 连接，但不能据此把用户服务设成公开业务接口；不同部署的认证策略应明确。令牌由管理员在平台连接配置中保存，不进入下列 JSON。

发现时按严格字段校验，不能在 manifest、capability、execution、presentation、result 或 surfaces 中自行添加未定义字段。业务字段放在输入输出 Schema 与 data 中，不扩充协议外壳。

| 发现校验项目 | 当前要求 |
|---|---|
| 模块、能力、resource_type 标识 | 匹配 `[a-z][a-z0-9_.-]{2,120}`，全局标识避免冲突 |
| 模块/能力 version | 非空字符串，最多 40 字符；能力语义变化时显式升级 |
| module.name | 非空，最多 120 字符 |
| 能力/页面 title | 非空，最多 200 字符 |
| description | 可为空，最多 3000 字符 |
| Schema | 内联对象且 type=object；$ref/$dynamicRef/$recursiveRef 只能引用 # 本地片段，不能拉取外部文件或 URL |
| Schema 遍历限制 | 深度最多 40，整个清单校验最多访问 20,000 个 Schema 节点 |
| 页面工具 | 1–200 个；id 匹配 `[a-z][a-z0-9_.-]{0,120}`，同模块内唯一；form 不含 path |
| starter_prompts | 最多 20 条，每条非空且最多 1000 字符 |

元数据字符串不接受控制字符。以上是供实现者复核的发现规则，不能以合法的示例 ID 推断任意名称都可用。
"""
    remote = e["remote"]
    text += block("D1. GET manifest 的完整响应", remote["manifest"])
    text += "\n可选 result.title_from_input 指定顶层输入字符串作为资源标题，无可用值时使用 result.title。text_artifact.field 指向 data 中实际存在的字符串字段，保存 UTF-8 文本附件。另有 workspace.download/image 的受限 base64 传输，两者不能混称；不支持任意 URL 取文件。D1 为本次生成的新协议完整示例，不是原 legacy 模板原文。\n"
    text += block("D2. POST invoke 完整请求（合成值）", remote["request"])
    text += block("D3. HTTP 200 完整成功响应", remote["response"])
    text += "\n以上成功响应只接受所列字段。四个关联字段必须与请求一致，data 必须通过所选能力输出 Schema。request_id 是平台生成的 Invocation ID；不要把浏览器提交的 idempotency_key 直接当作此 ID。\n"
    text += block("D4. 模板请求校验 Schema", remote["request_schema"])
    text += "\n此 Schema 完整来自 contracts/v1.2/invoke-request-1.1.schema.json。服务还需验证能力存在、版本、role 属于 roles，以及 input 对应能力 Schema。未注册 access 的示例还按固定角色交集授权并检查对象范围；注册模块使用独立权限包及本附录的中央 decision 桥接，旧 roles 不能替代范围授权。平台保存首次调用身份快照并在执行/返回时重新授权；同一次重试保留原编号与上下文。Schema default 不自动填值。\n"
    text += block("D5. GET health 的参考响应", remote["health"])
    text += "\n当前平台按明确的 status/ok 语义判断健康（例如 status=ok 或 ok=true），空对象不合格；protocol_version/service 为参考模板信息。失败可返回 status=unhealthy 或对应 HTTP 错误，不调用昂贵模型进行探测。\n"
    text += block("D6. 错误结构示例（HTTP 409）", {"error": {"code": "idempotency_conflict", "message": "同一请求编号已用于不同内容。"}})
    text += """
其他约定状态：400 非法 JSON，401 服务连接认证失败，403 业务操作无权，404 对象或能力不存在/不可见，409 版本/状态/幂等冲突，413 输入过大，422 业务输入不合格，503 暂不可用，500 处理失败。现代 invoke 1.1 分别返回固定 remote_forbidden/remote_not_found/remote_conflict/remote_input_too_large/remote_validation/remote_unavailable；409 仅从有限错误正文识别 revision_conflict、state_conflict、idempotency_conflict 三个代码，分别使用平台固定提示。远端 message/details 不直接暴露给用户或写入平台业务记录，旧 invoke 1.0 与 manifest/health 的错误映射不随之改变。

指纹包含整个请求（含 context），忽略 JSON 属性顺序与无意义空白。相同 request_id、相同内容返回持久保存的原成功响应；内容变化拒绝。并发重复请求必须协调；处理完成与外部副作用之间的故障恢复由服务自行实现。

### E. 有独立网页时追加的声明

将下列对象追加到 D1 的 surfaces.page_tools 数组，保留 form 与 conversation 声明。服务必须确实提供这个页面；不要在服务尚未实现页面时声明已可用。
"""
    text += block("E1. embedded 页面工具", remote["embedded_tool"])
    text += """
实际地址按 base_url + path 拼接：若 base_url 为 https://service.example/api，/tools/example 成为 https://service.example/api/tools/example。path 不接受查询串、片段、百分号编码、反斜杠、// 或点路径。

iframe 仅允许 scripts/forms，使用 credentialless，无 allow-same-origin、SSO 或业务消息桥。静态页不要依赖平台登录或令牌；含敏感数据的旧系统页面需要另行设计合法的访问方式，不能靠公开化来嵌入。独立网页交互不会自动转为平台调用记录；标准表单和对话所调用的关联能力才进入统一执行链。

### F. 独立交付与验收

远程服务交付目录至少包含 manifest.json、服务入口、业务 handler、鉴权/校验/幂等适配层、依赖清单、测试、README 和 .gitignore；运行数据另置 data/ 并排除版本管理。Node.js/Java/Go 按自身栈交付，不能依赖平台 Python 目录。

如采用 Python 参考栈，以下版本来自本次框架锁定依赖，可形成独立 requirements.txt；实现者仍需为自身完整依赖生成可复现锁定清单。
"""
    text += block("F1. Python 参考依赖", "\n".join(f["python_reference_dependencies"]), "text")
    text += """
开发 AI 需要实现 server.py（包含 app）及配置初始化方法，再提供可运行命令，例如 python -m venv .venv、python -m pip install -r requirements.txt、python -m uvicorn server:app --host 127.0.0.1 --port <用户选定端口>。初始化须生成随机服务令牌并存入服务端配置，不打印密钥。启动命令使用所创建虚拟环境内的 Python；交付对应停止方式。部署到其他服务器时另外配置服务管理与 HTTPS，不能把示例回环监听宣称为外网部署完成。

该独立目录需要由开发 AI 创建；只有本附录中的声明并不等于已有运行服务。现存 templates/remote-service-template 是 legacy manifest 1.1 / invoke 1.0 示例，不能直接复制后宣称支持 v0.4。可复用其中恒定时间 Bearer 验证、持久幂等思路，但须替换请求协议、完整身份与多角色校验、输入文件验证及 workspace 返回；不能把新 manifest 放到旧适配器中。原模板启动脚本依赖主工程 Python，独立交付需要自身环境。

逐能力提供验收表，列：原功能/需求、capability ID、页面工具、对话入口、输入、预期输出、资源归属、实际验证方式与结果。至少用合成数据验证成功、非法输入、未授权、重复、冲突、并发、重启、超时与禁用。实际接入后还应验证健康变化、入口撤下、双端运行与保存结果；不要把接口测试等同于界面验收。

本文件的指纹表示这份说明对应的公开源码快照，不是平台提供的在线指纹查询 API，也不是数字签名。其他项目收到的副本不会随框架自动更新；重新接入或升级时应取得新副本，核对版本与接口差异。
"""
    text += "\n### G. v0.4 公共机器契约（完整文件）\n\n以下文件复制到服务自己的受控适配目录；文件名保留，禁止把业务字段硬编码进公共协议。workspace 顶层可保留业务数据，但每种 block 使用严格字段。\n"
    for filename, schema in sorted(snapshot['contracts'].items()):
        text += block('G. contracts/v1.2/' + filename, schema)
    for filename, schema in sorted(snapshot['workflow_contracts'].items()):
        text += block('G2. contracts/' + filename, schema)
    text += block('G2. 管理员开发调试台接口与边界', f['devtools'])
    text += block('G2. Workflow 对象分页与页面组合说明', read(root, 'contracts/workflows/v1/README.md'), 'markdown')
    text += block('G. identity-policy.json（平台角色规则）', f['identity_policy'])
    text += """
### H. 流程、文件与状态的执行边界

本地和新远程模块均可声明 skills，结构见上文 skill Schema。命令 /skill <module_id>/<skill_id> 选择明确流程，triggers 是进入流程的短语；首步引用 entry_capability。平台展示步骤和表单，提交真实能力后可用“继续”推进；当前步骤失败或权限不足时保留可解释状态。skill 不是可执行脚本、任意 shell 或未经用户提交的自动工具循环。

workspace 同一数据在页面与对话复用组件：table 支持当前结果筛选/排序/分页，chart 为条形统计，map 为经纬度分布示意而非行政区 GIS 底图；evidence 保留引文及状态，actions 只打开下一步能力表单。图片仅 PNG/JPEG/WebP 的 base64；download 使用受限文件内容。平台验证格式与权限，服务负责内容、字段密级及下载资格；公开可见不等于允许批量下载。

platform-file 是文件对象，不是本机路径、远程 URL 或 data URL。服务应验证解码后的真实文件、内容类型及业务规则。文件字节不放入持久聊天草稿；调用输入和受权结果可能进入历史，应在业务层设置相应保留政策。大包、断点上传和线下介质交接不是这一有限接口已实现的能力。

平台对话有本人历史、重命名、当前会话与表单草稿恢复、指定消息位置分支。分支保留上下文，不撤销原调用、外部副作用或业务对象；长期项目的对象版本、共享与回溯仍由业务服务实现。会话状态更新使用 expected_revision 并发检查，机构或团队不自动共享私人对话。

### I. 旧版参考（明确 legacy，不能作为新协议请求）

以下旧版声明和请求保留给维护既有示例的开发者。其角色和 renderer 更少，完整身份、skills、platform-file 与 workspace 应使用上述 v0.4 新协议。不要在同一请求混搭不同版本。实际不需要旧版的业务无需增加兼容分支。
"""
    legacy = e['legacy_remote']
    text += block('I1. legacy manifest', legacy['manifest'])
    text += block('I2. legacy invoke request', legacy['request'])
    text += block('I3. legacy request Schema', legacy['request_schema'])
    text += access_appendix(snapshot, root)
    return text


def build_outputs(root: Path) -> dict[Path, str]:
    snapshot = build_snapshot(root)
    source = read(root, SOURCE_PATH)
    replacements = {"PLATFORM_VERSION": snapshot["facts"]["platform_version"],
                    "CONTRACT_DIGEST": snapshot["contract_digest"],
                    "CONTRACT_APPENDIX": appendix(snapshot, root) + module_kit_appendix(snapshot)}
    if set(re.findall(r"\{\{([A-Z_]+)\}\}", source)) != set(replacements):
        raise ValueError("Prompt source placeholders do not match the generator.")
    for name, value in replacements.items():
        source = source.replace("{{" + name + "}}", value)
    if re.search(r"\{\{[A-Z_]+\}\}", source):
        raise ValueError("Unresolved template placeholder.")
    header = "<!-- 自动生成；不要直接编辑。维护 prompts/_source/module-developer.md 后审阅并重建。 -->\n\n"
    component = read(root, 'prompts/_source/component-developer.md')
    component_appendix = '## 组件契约与骨架附录（自动导出）\n'
    component_appendix += block('平台 SDK 类型：@platform/interaction-sdk', read(root, 'web/src/interactions/sdk.ts'), 'typescript')
    component_appendix += block('interaction.json 校验 Schema', json.loads(read(root, 'contracts/interactions/v1/definition.schema.json')))
    component_appendix += block('步骤 SDK 类型：@platform/workflow-sdk', read(root, 'web/src/workflows/sdk.ts'), 'typescript')
    for name, schema in sorted(snapshot['workflow_contracts'].items()):
        component_appendix += block('contracts/' + name, schema)
    component_appendix += block('管理员开发调试台接口与边界', snapshot['facts']['devtools'])
    component_appendix += block('Workflow 对象分页与页面组合说明', read(root, 'contracts/workflows/v1/README.md'), 'markdown')
    for path in sorted((root / 'templates/interaction-module').rglob('*'), key=lambda p: p.as_posix()):
        if path.is_file() and path.suffix in {'.json', '.md', '.tsx', '.py', '.mjs'} and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts):
            suffix = {'.json': 'json', '.md': 'markdown', '.tsx': 'tsx', '.py': 'python', '.mjs': 'javascript'}[path.suffix]
            relative = path.relative_to(root)
            body = json.loads(read(root, relative)) if suffix == 'json' else read(root, relative)
            component_appendix += block(relative.as_posix(), body, suffix)
    for path in sorted((root / 'templates/workflow-module').rglob('*'), key=lambda p: p.as_posix()):
        if path.is_file() and path.suffix in {'.json', '.md', '.tsx', '.py', '.mjs'} and not EXCLUDED_PARTS.intersection(path.relative_to(root).parts):
            suffix = {'.json': 'json', '.md': 'markdown', '.tsx': 'tsx', '.py': 'python', '.mjs': 'javascript'}[path.suffix]
            relative = path.relative_to(root)
            body = json.loads(read(root, relative)) if suffix == 'json' else read(root, relative)
            component_appendix += block(relative.as_posix(), body, suffix)
    component_appendix += access_appendix(snapshot, root) + module_kit_appendix(snapshot)
    for name, value in {'PLATFORM_VERSION': snapshot['facts']['platform_version'], 'CONTRACT_DIGEST': snapshot['contract_digest'], 'COMPONENT_APPENDIX': component_appendix}.items():
        component = component.replace('{{' + name + '}}', value)
    if re.search(r'\{\{[A-Z_]+\}\}', component):
        raise ValueError('Unresolved component prompt placeholder.')
    quick = read(root, 'prompts/_source/module-quickstart.md')
    quick_values = {'PLATFORM_VERSION': snapshot['facts']['platform_version'], 'CONTRACT_DIGEST': snapshot['contract_digest']}
    if set(re.findall(r'\{\{([A-Z_]+)\}\}', quick)) != set(quick_values):
        raise ValueError('Quick prompt placeholders do not match the generator.')
    for name, value in quick_values.items():
        quick = quick.replace('{{' + name + '}}', value)
    return {PROMPT_PATH: header + source.rstrip() + "\n", SNAPSHOT_PATH: json_text(snapshot),
            QUICK_PROMPT_PATH: '<!-- 自动生成；维护 prompts/_source/module-quickstart.md 后审阅并重建。 -->\n\n' + quick.rstrip() + '\n',
            COMPONENT_PROMPT_PATH: '<!-- 自动生成；维护 prompts/_source/component-developer.md 后审阅并重建。 -->\n\n' + component.rstrip() + '\n'}


def check_outputs(root: Path) -> list[str]:
    expected = build_outputs(root)
    stale = [p.as_posix() for p, content in expected.items() if not (root / p).is_file() or read(root, p) != content]
    if stale and (root / SNAPSHOT_PATH).is_file():
        try:
            before = json.loads(read(root, SNAPSHOT_PATH))["sources"]
            after = tracked_sources(root)
            stale += ["source: " + p for p in sorted(before.keys() | after.keys()) if before.get(p) != after.get(p)]
        except (ValueError, KeyError, TypeError):
            stale.append("snapshot is invalid")
    return stale


def write_outputs(root: Path, *, reviewed: bool) -> None:
    if not reviewed:
        raise ValueError("Review prose, contracts and examples first; then use --write --reviewed.")
    for path, content in build_outputs(root).items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n", dir=destination.parent,
                                         prefix=".prompt-build-", suffix=".tmp", delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
        try:
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Check generated artifacts without writing (default).")
    mode.add_argument("--write", action="store_true", help="Rebuild the three prompts and contract snapshot.")
    parser.add_argument("--reviewed", action="store_true", help="Confirm semantic review of prompt source and contract changes.")
    args = parser.parse_args(argv)
    try:
        if args.write:
            write_outputs(ROOT, reviewed=args.reviewed)
            print("Rebuilt portable developer prompt and contract snapshot.")
        differences = check_outputs(ROOT)
        if differences:
            print("Developer prompt is out of date:")
            for path in differences:
                print("  " + path)
            print("Review prompts/_source/module-developer.md and generator, then run --write --reviewed.")
            return 1
        print("Developer prompt matches the current public source contract.")
        return 0
    except (ValueError, OSError, SyntaxError) as error:
        print(f"Prompt contract check failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
