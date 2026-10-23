"""Optional, exact public document routes supplied by the project profile.

The portable core ships no project profile. Never mount the parent workspace.
"""

import json
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

from .errors import require


def create_project_documents_router(root: Path):
    router = APIRouter()
    config = root / "config/project-public-documents.json"
    if not config.is_file():
        return router
    data = json.loads(config.read_text(encoding="utf-8"))
    if set(data) != {"version", "documents"} or data["version"] != 1:
        raise ValueError("Unsupported public document profile")
    boundary = root.parent.resolve()
    seen = set()
    for item in data["documents"]:
        if set(item) != {"url", "source"}:
            raise ValueError("Invalid public document declaration")
        url, source = item["url"], item["source"]
        if (
            not url.startswith("/")
            or url.startswith(("/api/", "//"))
            or any(c in url for c in "{}?#\\")
            or url in seen
        ):
            raise ValueError("Invalid or repeated public document URL")
        nominal = boundary / source
        path = nominal.resolve()
        if (
            Path(source).is_absolute()
            or ".." in Path(source).parts
            or path != nominal
            or not path.is_relative_to(boundary)
            or path.suffix not in {".md", ".json"}
        ):
            raise ValueError("Public document path escapes its declared boundary")
        seen.add(url)

        def endpoint_for(document):
            def document_response():
                require(
                    document.resolve() == document and document.is_file(),
                    "not_found",
                    "公开文档不存在。",
                    404,
                )
                return FileResponse(document)

            return document_response

        router.add_api_route(url, endpoint_for(path), methods=["GET"], include_in_schema=False)
    return router
