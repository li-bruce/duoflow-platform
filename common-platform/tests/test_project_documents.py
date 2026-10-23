import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.project_documents import create_project_documents_router


def test_portable_profile_has_no_project_routes(tmp_path):
    assert create_project_documents_router(tmp_path).routes == []


def test_only_exact_declared_public_document_is_served(tmp_path):
    root = tmp_path / 'common-platform'
    (root / 'config').mkdir(parents=True)
    (tmp_path / 'public.md').write_text('public', encoding='utf-8')
    (tmp_path / 'private.md').write_text('synthetic-private', encoding='utf-8')
    (root / 'config/project-public-documents.json').write_text(json.dumps({'version': 1,
        'documents': [{'url': '/project/readme.md', 'source': 'public.md'}]}), encoding='utf-8')
    app = FastAPI(); app.include_router(create_project_documents_router(root))
    with TestClient(app) as client:
        assert client.get('/project/readme.md').text == 'public'
        assert client.get('/project/private.md').status_code == 404
        assert client.get('/private.md').status_code == 404


@pytest.mark.parametrize('item', [
    {'url': '/api/v1/secret', 'source': 'file.md'},
    {'url': '/{path:path}', 'source': 'file.md'},
    {'url': '/readme', 'source': '../outside.md'},
    {'url': '/readme', 'source': 'private.env'},
])
def test_profile_cannot_escape_workspace_or_override_api(tmp_path, item):
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/project-public-documents.json').write_text(json.dumps({'version':1, 'documents':[item]}))
    with pytest.raises(ValueError):
        create_project_documents_router(tmp_path)
