"""Build/check monitoring schemas from the actual strict HTTP models."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.monitor_contract import Snapshot
from monitoring_service.protocol import Report, Targets


def outputs():
    return {name: json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2)+"\n"
            for name, model in [("report.schema.json", Report), ("targets.schema.json", Targets),
                                ("snapshot.schema.json", Snapshot)]}


def run(write=False):
    directory = ROOT/"contracts/monitoring/v1"
    if write: directory.mkdir(parents=True, exist_ok=True)
    changed=[]
    for name,value in outputs().items():
        path=directory/name
        if not path.is_file() or path.read_text(encoding="utf-8")!=value:
            changed.append(name)
            if write:path.write_text(value,encoding="utf-8",newline="\n")
    print(json.dumps({"written":write,"changed":changed}))
    return 0 if write or not changed else 1


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--check',action='store_true')
    args=parser.parse_args()
    if args.write==args.check:parser.error('Choose --write or --check')
    raise SystemExit(run(args.write))
