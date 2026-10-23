from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets

from handlers import handle
from service_core import create_app

ROOT = Path(__file__).resolve().parent


def initialize(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    configuration = data_dir / "service-config.json"
    try:
        with configuration.open("x", encoding="utf-8") as file:
            json.dump({"service_token": secrets.token_urlsafe(48)}, file, indent=2)
            file.write("\n")
    except FileExistsError:
        pass
    print("Service configuration is ready. The token was not printed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--init-data", type=Path, required=True)
    initialize(parser.parse_args().init_data.resolve())
else:
    app = create_app(ROOT, handle)
