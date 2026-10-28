"""Private, instance-scoped monitoring credentials and atomic configuration."""
import json
import os
from pathlib import Path
import secrets
import tempfile
import uuid

from app.model_gateway import ModelGateway


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".monitor-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def seal(directory, value):
    return ModelGateway(Path(directory) / "secrets")._encrypt(value)


def unseal(directory, value):
    return ModelGateway(Path(directory) / "secrets")._decrypt(value)


def provision(platform_data, monitor_data, port):
    """New credentials only; a different owner/address is never silently reused."""
    platform_data, monitor_data = Path(platform_data).resolve(), Path(monitor_data).resolve()
    if not 1 <= port <= 65535 or platform_data == monitor_data:
        raise ValueError("Independent monitor directory and valid port required")
    platform_data.mkdir(parents=True, exist_ok=True)
    monitor_data.mkdir(parents=True, exist_ok=True)
    connection = platform_data / "monitor-connection.json"
    bootstrap = monitor_data / "monitor-bootstrap.json"
    url = f"http://127.0.0.1:{port}"
    if bootstrap.exists():
        settings = json.loads(bootstrap.read_text(encoding="utf-8"))
        if settings["platform_directory"] != str(platform_data) or settings["url"] != url:
            raise ValueError("Monitor belongs to another instance/address")
        token = unseal(monitor_data, settings["secret"])
    else:
        if connection.exists():
            raise ValueError("Existing platform monitor connection requires its original monitor directory")
        token = secrets.token_urlsafe(48)
        settings = {"version": "1.0", "owner_id": uuid.uuid4().hex,
                    "collector_id": uuid.uuid4().hex, "url": url,
                    "platform_directory": str(platform_data), "secret": seal(monitor_data, token)}
        atomic_json(bootstrap, settings)
    public = {key: settings[key] for key in ("version", "owner_id", "collector_id", "url")}
    if connection.exists():
        current = json.loads(connection.read_text(encoding="utf-8"))
        if any(current[key] != value for key, value in public.items()) or unseal(platform_data, current["secret"]) != token:
            raise ValueError("Platform monitor connection does not match")
    else:
        atomic_json(connection, {**public, "secret": seal(platform_data, token)})
    return public
