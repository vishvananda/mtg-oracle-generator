"""Use mtgish's own Scryfall preprocessing; never overwrite generated text with Arena reference text."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from paths import ARTIFACTS

ROOT = ARTIFACTS / 'mtgish-standard-workflow'


def preprocess(cards):
    with tempfile.TemporaryDirectory(prefix='mtgish-input-') as tmp:
        path = Path(tmp)/'cards.json'
        path.write_text(json.dumps(cards, ensure_ascii=False))
        run = subprocess.run([sys.executable, str(ROOT/'preprocess_scryfall'), str(path)],
                             text=True, capture_output=True, timeout=15, check=True)
        return json.loads(run.stdout)


def input_for(draft):
    card = copy.deepcopy(draft)
    card.setdefault('name', 'Draft Card')
    card.setdefault('layout', 'normal')
    card.setdefault('set', '')
    card.setdefault('type_line', ' // '.join(f.get('type_line','') for f in card.get('card_faces',[])))
    entries = preprocess([card])
    if not entries:
        return {'preprocessing_status':'out_of_scope', 'name':card['name']}
    if len(entries) != 1:
        raise ValueError('Expected one complete card from mtgish preprocessing')
    return {'entry':entries[0]}
