"""Instance-owned encrypted coordination control credentials."""
import json
from pathlib import Path
import secrets
import uuid
from monitoring_service.settings import atomic_json, seal, unseal


def load_control_token(directory):
    directory = Path(directory)
    value = json.loads((directory / 'coordination-bootstrap.json').read_text(encoding='utf-8'))
    if value['version'] != '1.0':
        raise ValueError('Unsupported coordination bootstrap')
    return unseal(directory, value['secret'])


def provision(platform_data, directory, port):
    platform_data, directory = Path(platform_data).resolve(), Path(directory).resolve()
    if platform_data == directory or not 1 <= port <= 65535:
        raise ValueError('Independent directory and valid port required')
    platform_data.mkdir(parents=True, exist_ok=True)
    directory.mkdir(parents=True, exist_ok=True)
    bootstrap, connection = directory/'coordination-bootstrap.json', platform_data/'coordination-connection.json'
    url = f'http://127.0.0.1:{port}'
    if bootstrap.exists():
        value = json.loads(bootstrap.read_text(encoding='utf-8'))
        if value['platform_directory'] != str(platform_data) or value['url'] != url:
            raise ValueError('Coordination center belongs to another instance/address')
        token = load_control_token(directory)
    else:
        if connection.exists():
            raise ValueError('Original coordination directory required')
        token = secrets.token_urlsafe(48)
        value = {'version':'1.0','center_id':uuid.uuid4().hex,'platform_directory':str(platform_data),
                 'url':url,'secret':seal(directory,token)}
        atomic_json(bootstrap,value)
    public = {key:value[key] for key in ('version','center_id','url')}
    if connection.exists():
        current = json.loads(connection.read_text(encoding='utf-8'))
        if any(current[key] != val for key,val in public.items()) or unseal(platform_data,current['secret']) != token:
            raise ValueError('Coordination control connection differs')
    else:
        atomic_json(connection,{**public,'secret':seal(platform_data,token)})
    return public
