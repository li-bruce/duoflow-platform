"""Generate portable JSON contracts from the actual coordinator models."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from coordination_service import protocol
MODELS={'event':protocol.Event,'configuration':protocol.Config,'configuration-response':protocol.ControlConfiguration,'subscription':protocol.Subscription,
        'decision':protocol.Decision,'ack':protocol.Ack,'summary':protocol.Summary}

def run(write=False):
    for name,model in MODELS.items():
        path=ROOT/'contracts/coordination/v1'/f'{name}.schema.json'
        content=json.dumps({'$schema':'https://json-schema.org/draft/2020-12/schema',**model.model_json_schema()},ensure_ascii=False,sort_keys=True,indent=2)+'\n'
        if write:path.write_text(content,encoding='utf-8',newline='\n')
        elif not path.is_file() or path.read_text(encoding='utf-8')!=content:
            raise ValueError('Coordination contract requires reviewed regeneration: '+name)
    print('Coordination JSON contracts match runtime models.')
if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--write',action='store_true');p.add_argument('--check',action='store_true')
    run(p.parse_args().write)
