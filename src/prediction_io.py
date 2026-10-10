"""Resume a fixed generation request without rerunning or dropping failed cases."""
import json
from pathlib import Path
from data_utils import digest


class Predictions:
    def __init__(self, output, identity, identifiers, resume=False):
        self.output=Path(output)
        self.request=self.output.with_suffix('.request.json')
        self.manifest=self.output.with_suffix('.manifest.json')
        if len(set(identifiers))!=len(identifiers): raise ValueError('Duplicate scheduled ID')
        self.identifiers=set(identifiers)
        self.identity={**identity,'scheduled_cases':len(identifiers),'scheduled_ids_sha256':digest(identifiers)}
        self.done=set()
        if self.output.exists() or self.request.exists() or self.manifest.exists():
            if not resume: raise ValueError('Output exists; explicitly resume the identical request')
            if not self.request.is_file() or json.loads(self.request.read_text())!=self.identity:
                raise ValueError('Generation request changed; use a fresh output')
            if self.manifest.exists():
                receipt=json.loads(self.manifest.read_text())
                if receipt['predictions_sha256']!=digest(self.output.read_bytes()):
                    raise ValueError('Completed predictions were modified')
            if self.output.exists():
                raw=self.output.read_bytes()
                if raw and not raw.endswith(b'\n'):
                    end=raw.rfind(b'\n')+1
                    # A killed write is not a completed case. Keep its bytes for audit.
                    self.output.with_suffix('.interrupted-'+digest(raw[end:])[:12]+'.txt').write_bytes(raw[end:])
                    self.output.write_bytes(raw[:end])
                for line in self.output.read_text().splitlines():
                    row=json.loads(line);key=row['example_id']
                    if key not in self.identifiers or key in self.done: raise ValueError('Unknown/duplicate saved prediction')
                    self.done.add(key)
        else:
            self.output.parent.mkdir(parents=True,exist_ok=True)
            self.request.write_text(json.dumps(self.identity,indent=2)+'\n')
        self.stream=self.output.open('a')

    def append(self, row):
        key=row['example_id']
        if key not in self.identifiers or key in self.done: raise ValueError('Unknown/duplicate prediction')
        self.stream.write(json.dumps(row,ensure_ascii=False)+'\n');self.stream.flush()
        self.done.add(key)

    def close(self): self.stream.close()

    def finish(self):
        self.close()
        if self.done!=self.identifiers: raise ValueError('Generation is incomplete; missing cases remain scheduled')
        result={**self.identity,'cases':len(self.done),'complete':True,
                'predictions_sha256':digest(self.output.read_bytes())}
        self.manifest.write_text(json.dumps(result,indent=2)+'\n')
        return result
