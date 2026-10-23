"""Real HTTP bootstrap and cookies for an independent loopback deployment."""
import importlib
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import User
from app.security import COOKIE


def test_new_instance_keeps_legacy_cookie_and_owns_identity_and_database(tmp_path, monkeypatch):
    isolated = tmp_path / 'isolated-data'
    monkeypatch.setenv('PLATFORM_DATA_DIR', str(isolated))
    main = importlib.import_module('app.main')
    app = main.create_app(isolated, start_worker=False)
    assert Path(app.state.engine.url.database).parent.resolve() == isolated.resolve()
    assert COOKIE == 'common_framework_session'
    with TestClient(app) as client:
        client.cookies.set('common_platform_session', 'legacy-cookie-must-remain')
        response = client.post('/api/v1/auth/login', json={
            'username': 'admin', 'password': 'demo-admin-2026'})
        assert response.status_code == 200
        assert response.headers['set-cookie'].startswith(COOKIE + '=')
        assert 'HttpOnly' in response.headers['set-cookie']
        assert client.cookies['common_platform_session'] == 'legacy-cookie-must-remain'
        assert client.get('/api/v1/auth/session').json()['user']['username'] == 'admin'
        assert [row['id'] for row in client.get('/api/v1/flows/catalog').json()['items']] == ['framework-lab.workflow']
        with app.state.sessions() as db:
            assert {row.username for row in db.query(User)} == {'admin', 'member'}
        client.cookies.delete(COOKIE, domain='testserver.local', path='/')
        assert client.get('/api/v1/auth/session').status_code == 401
    main.app.state.engine.dispose()
