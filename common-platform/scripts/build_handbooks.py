"""Build a bounded Markdown handbook collection with marked and a safe HTML shell.

Only SOURCE_ALLOWLIST may be read as Markdown. No deployment data, credentials,
linked attachments, remote resources, or arbitrary configuration paths are read.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ALLOWLIST = frozenset({
    "common-platform/docs/通用框架架构与代码地图.md",
    "common-platform/docs/模块开发与调试说明书.md",
    "common-platform/docs/框架迁移与AI交接指南.md",
    "common-platform/docs/框架功能说明书.md",
    "common-platform/docs/数据库知识化与模块业务串联.md",
    "common-platform/docs/模块模板使用手册.md",
    "common-platform/docs/微服务管理使用说明.md",
    "demos/01-ingestion/docs/模块使用说明.md",
    "demos/04-discovery/docs/模块使用说明.md",
    "demos/07-knowledge/docs/模块使用说明.md",
    "demos/07-knowledge/docs/开源选型与架构.md",
})
ASSETS = ("handbook.css", "handbook.js")
APP_PAGES = frozenset({"devtools", "business", "access", "flows", "components", "intro", "workbench", "chat", "records", "models", "services", "modules", "contracts", "identity"})
MARKED_BRIDGE = r"""
import fs from 'node:fs';
const {marked, Renderer} = await import(process.argv[1]);
const renderer = new Renderer();
renderer.html = html => html.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const inputs = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(inputs.map(source => marked.parse(source, {renderer, gfm:true, breaks:false}))));
"""


class HandbookError(ValueError):
    pass


def contained(path: Path, root: Path) -> Path:
    resolved, boundary = path.resolve(), root.resolve()
    if not resolved.is_relative_to(boundary):
        raise HandbookError("路径超出受控工作区")
    return resolved


def source_path(root: Path, source: str) -> Path:
    nominal = root.parent.resolve() / source
    resolved = contained(nominal, root.parent)
    if resolved != nominal:
        raise HandbookError("说明书来源不能通过符号链接转向其他文件")
    return resolved


def load_documents(root: Path) -> list[dict]:
    config = contained(root / "docs/handbooks.json", root)
    data = json.loads(config.read_text(encoding="utf-8"))
    if set(data) != {"version", "documents"} or data["version"] != 1:
        raise HandbookError("说明书配置版本或字段不受支持")
    if not isinstance(data["documents"], list) or not data["documents"]:
        raise HandbookError("说明书清单不能为空")
    seen, sources = set(), set()
    for doc in data["documents"]:
        if set(doc) != {"id", "title", "source", "audience"}:
            raise HandbookError("说明书条目字段不受支持")
        if not all(isinstance(doc[x], str) and doc[x] for x in doc):
            raise HandbookError("说明书字段必须为非空字符串")
        slug = doc["id"]
        if not slug.isascii() or not all(c.islower() or c.isdigit() or c == "-" for c in slug) or slug.startswith("-") or len(slug) > 64 or slug in {"index", "build-status"}:
            raise HandbookError("说明书 ID 必须为小写英文、数字和短横线")
        if slug in seen or doc["source"] in sources:
            raise HandbookError("说明书 ID 或来源重复")
        if doc["source"] not in SOURCE_ALLOWLIST:
            raise HandbookError("说明书来源不在源码白名单中")
        source_path(root, doc["source"])
        seen.add(slug)
        sources.add(doc["source"])
    return data["documents"]


def node_executable() -> str:
    explicit = os.environ.get("HANDBOOK_NODE_EXE") or os.environ.get("NODE_EXE")
    if explicit:
        return explicit
    bundled = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    if bundled.is_file():
        return str(bundled)
    executable = shutil.which("node")
    if not executable:
        raise HandbookError("缺少 Node.js：请设置 HANDBOOK_NODE_EXE 或安装项目运行环境")
    return executable


def parse_markdown(root: Path, inputs: list[str]) -> list[str]:
    parser = contained(root / "web/node_modules/marked/lib/marked.esm.js", root)
    if not parser.is_file():
        raise HandbookError("缺少 marked 解析器：请在 web 目录安装锁定的前端依赖；不会回退到简化解析")
    result = subprocess.run(
        [node_executable(), "--input-type=module", "-e", MARKED_BRIDGE, parser.as_uri()],
        input=json.dumps(inputs, ensure_ascii=False), encoding="utf-8", capture_output=True, timeout=40,
    )
    if result.returncode:
        raise HandbookError("marked 解析失败，请检查 Node.js 与项目依赖")
    parsed = json.loads(result.stdout)
    if not isinstance(parsed, list) or len(parsed) != len(inputs) or not all(isinstance(item, str) for item in parsed):
        raise HandbookError("Markdown 解析器返回格式错误")
    return parsed


def safe_link(value: str, source: str, mapping: dict[str, str], root: Path) -> str | None:
    """Allow only web URLs, anchors, mapped handbooks, and bounded report/app links."""
    if any(ord(c) < 32 or ord(c) == 127 for c in value) or "\\" in value:
        return None
    decoded = unquote(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in decoded) or "\\" in decoded:
        return None
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme:
        if parts.scheme.lower() not in {"https", "http"} or not parts.hostname or parts.username or parts.password:
            return None
        return value
    if parts.netloc or value.startswith("//") or parts.query:
        return None
    if value.startswith("#"):
        return value
    if parts.path in {"/", "../../", "../.."} and parts.fragment in APP_PAGES:
        return "/#" + parts.fragment
    if parts.path in {"index.html", *(x + ".html" for x in mapping.values())}:
        return value
    if parts.path.startswith("/reports/") and parts.path.endswith(".html"):
        try:
            target = contained(root / unquote(parts.path).lstrip("/"), root / "reports")
        except HandbookError:
            return None
        if target.is_file() or parts.path.startswith("/reports/handbooks/") and Path(parts.path).name in {"index.html", *(x + ".html" for x in mapping.values())}:
            return value
        return None
    if parts.path.startswith("/") or not parts.path:
        return None
    try:
        resolved = contained(root.parent / source, root.parent).parent / unquote(parts.path)
        relative = contained(resolved, root.parent).relative_to(root.parent).as_posix()
    except (HandbookError, ValueError):
        return None
    if relative in mapping:
        return mapping[relative] + ".html" + ("#" + parts.fragment if parts.fragment else "")
    if relative.startswith("common-platform/reports/") and resolved.suffix == ".html" and resolved.is_file():
        contained(resolved, root / "reports")
        return "/reports/" + resolved.resolve().relative_to(root / "reports").as_posix() + ("#" + parts.fragment if parts.fragment else "")
    return None


class SafeMarkdownHTML(HTMLParser):
    TAGS = frozenset({"p", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "strong", "em", "del", "blockquote", "ul", "ol", "li", "pre", "code", "a", "table", "thead", "tbody", "tr", "th", "td", "input"})
    VOID = frozenset({"br", "hr", "input"})

    def __init__(self, source: str, mapping: dict[str, str], root: Path):
        super().__init__(convert_charrefs=True)
        self.source, self.mapping, self.root = source, mapping, root
        self.parts: list[str] = []
        self.headings: list[dict] = []
        self.heading: dict | None = None
        self.ids: set[str] = set()
        self.blocked_links = 0

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "img":
            self.parts.append('<span class="omitted-image">[图片：' + escape(attr.get("alt") or "未提供文字说明") + "；静态说明书不自动载入图片]</span>")
            return
        if tag not in self.TAGS:
            return
        allowed = ""
        if tag == "a":
            target = safe_link(attr.get("href") or "", self.source, self.mapping, self.root)
            if target:
                allowed = ' href="' + escape(target, quote=True) + '"'
                if urlsplit(target).scheme:
                    allowed += ' rel="noopener noreferrer"'
            else:
                self.blocked_links += 1
                allowed = ' class="blocked-link" title="此链接未列入说明书发布范围"'
        elif tag == "code" and (attr.get("class") or "").startswith("language-"):
            allowed = ' class="' + escape(attr["class"], quote=True) + '"'
        elif tag == "input":
            if attr.get("type") != "checkbox":
                return
            allowed = ' type="checkbox" disabled' + (" checked" if "checked" in attr else "")
        elif tag == "ol" and (attr.get("start") or "").isdigit():
            allowed = ' start="' + attr["start"] + '"'
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self.heading = {"level": int(tag[1]), "text": "", "position": len(self.parts), "tag": tag}
        self.parts.append("<" + tag + allowed + ">")

    def handle_endtag(self, tag):
        if tag not in self.TAGS or tag in self.VOID:
            return
        if self.heading and tag == self.heading["tag"]:
            heading = self.heading
            slug = "".join(c.lower() if c.isalnum() else "-" for c in heading["text"]).strip("-") or "section"
            while "--" in slug:
                slug = slug.replace("--", "-")
            base, n = slug, 2
            while slug in self.ids:
                slug, n = f"{base}-{n}", n + 1
            self.ids.add(slug)
            self.parts[heading["position"]] = '<' + tag + ' id="' + escape(slug, quote=True) + '">'
            self.headings.append({"id": slug, "level": heading["level"], "text": heading["text"]})
            self.heading = None
        self.parts.append("</" + tag + ">")

    def handle_data(self, data):
        if self.heading:
            self.heading["text"] += data
        self.parts.append(escape(data))

    def result(self):
        return "".join(self.parts)


def shell(title: str, content: str, *, sidebar: str = "", extra: str = "") -> str:
    return f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{escape(title)} · 平台使用说明</title>
<link rel="stylesheet" href="../handbook-assets/handbook.css"><script src="../handbook-assets/handbook.js" defer></script></head>
<body><header class="topbar"><a href="index.html" class="brand">平台使用说明</a><nav aria-label="常用入口"><a href="/#business">我的业务</a><a href="/#chat">对话工作台</a><a href="/reports/">其他报告</a></nav></header>
<div class="layout"><aside class="sidebar">{sidebar}</aside><main id="main-content">{extra}{content}</main></div>
<footer>正文由受控 Markdown 源文件生成。页面刷新不会执行任何业务动作。</footer></body></html>
'''


def collection_links(documents: list[dict], active: str = "") -> str:
    return '<nav aria-label="说明书列表"><p class="nav-label">说明书</p>' + "".join(
        '<a' + (' aria-current="page"' if doc["id"] == active else "") + ' href="' + doc["id"] + '.html">' + escape(doc["title"]) + '</a>' for doc in documents
    ) + "</nav>"


def build_outputs(root: Path = ROOT) -> tuple[dict[str, str], list[dict]]:
    documents = load_documents(root)
    mapping = {doc["source"]: doc["id"] for doc in documents}
    sources = {}
    for doc in documents:
        path = source_path(root, doc["source"])
        if path.is_file():
            if path.stat().st_size > 2_000_000:
                raise HandbookError("单份说明书超过 2 MB 发布限额")
            sources[doc["id"]] = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
    parsed = iter(parse_markdown(root, list(sources.values()))) if sources else iter(())
    output, status = {}, []
    for doc in documents:
        slug = doc["id"]
        sidebar = collection_links(documents, slug)
        if slug not in sources:
            status.append({"id": slug, "source": doc["source"], "status": "missing"})
            output[slug + ".html"] = shell(doc["title"], '<article class="missing"><p class="eyebrow">待交付文档</p><h1>' + escape(doc["title"]) + '</h1><p>该说明书的 Markdown 源文件尚未交付，本页面不代表功能已经实现或测试通过。</p><p>预期来源：<code>' + escape(doc["source"]) + '</code></p></article>', sidebar=sidebar)
            continue
        cleaner = SafeMarkdownHTML(doc["source"], mapping, root)
        cleaner.feed(next(parsed))
        cleaner.close()
        toc = '<nav class="toc" aria-label="本页目录"><p class="nav-label">本页目录</p>' + "".join('<a class="level-' + str(item["level"]) + '" href="#' + escape(item["id"], quote=True) + '">' + escape(item["text"]) + '</a>' for item in cleaner.headings if item["level"] <= 3) + '</nav>'
        source = sources[slug]
        status.append({"id": slug, "source": doc["source"], "status": "ready", "sha256": sha256(source.encode()).hexdigest(), "blocked_links": cleaner.blocked_links})
        tools = '<div class="document-meta"><span>' + escape(doc["audience"]) + '</span><a href="' + slug + '.md" download>下载 Markdown 原文</a></div><div class="tools" hidden><label for="doc-search">查找本页内容</label><input id="doc-search" type="search" placeholder="例如：登录、对话、测试" autocomplete="off"><button type="button" id="clear-search">清空</button><button type="button" id="expand-all">展开全部</button><button type="button" id="collapse-all">折叠章节</button><span id="search-status" role="status" aria-live="polite"></span></div><noscript><p>已展示完整正文；启用 JavaScript 后可搜索和折叠章节。</p></noscript>'
        output[slug + ".html"] = shell(doc["title"], '<article id="doc-content">' + cleaner.result() + '</article>', sidebar=sidebar + toc, extra=tools)
        output[slug + ".md"] = source
    cards = "".join('<a class="doc-card" href="' + doc["id"] + '.html"><span class="eyebrow">' + escape(doc["audience"]) + '</span><h2>' + escape(doc["title"]) + '</h2><p>' + ('打开说明书 →' if doc["id"] in sources else '待交付 · Markdown 源文件缺失') + '</p></a>' for doc in documents)
    output["index.html"] = shell("说明书首页", '<article><p class="eyebrow">从使用到开发</p><h1>找到你需要的说明</h1><p>先了解如何使用，再按需展开接入与维护细节。每份说明书都以 Markdown 为唯一正文来源。</p><div class="doc-cards">' + cards + '</div></article>', sidebar=collection_links(documents))
    output["build-status.json"] = json.dumps({"version": 1, "documents": status}, ensure_ascii=False, indent=2) + "\n"
    return output, status


def run(root: Path, write: bool) -> int:
    outputs, status = build_outputs(root)
    destination = contained(root / "reports/handbooks", root / "reports")
    for asset in ASSETS:
        path = contained(root / "reports/handbook-assets" / asset, root / "reports/handbook-assets")
        if not path.is_file():
            raise HandbookError("缺少同源说明书静态资源：" + asset)
    if write:
        destination.mkdir(parents=True, exist_ok=True)
    changed = []
    for name, content in outputs.items():
        target = contained(destination / name, destination)
        if not target.is_file() or target.read_text(encoding="utf-8") != content:
            changed.append(name)
            if write:
                target.write_text(content, encoding="utf-8", newline="\n")
    # Only remove our own known Markdown copy if its authoritative source vanished.
    # Unknown files are reported, never recursively deleted or silently published.
    owned = {doc["id"] + ".md" for doc in load_documents(root)}
    unexpected = []
    if destination.is_dir():
        for path in destination.iterdir():
            contained(path, destination)
            if path.name not in outputs:
                if write and path.name in owned and path.is_file():
                    path.unlink()
                else:
                    unexpected.append(path.name)
    missing = [row["id"] for row in status if row["status"] == "missing"]
    print(json.dumps({"mode": "write" if write else "check", "documents": len(status), "missing": missing, "changed": changed, "unexpected_output": unexpected}, ensure_ascii=False))
    if missing or unexpected:
        return 2
    return 0 if write or not changed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="从受控 Markdown 生成交互式静态说明书")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        return run(ROOT, args.write)
    except (HandbookError, OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("说明书构建失败：" + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
