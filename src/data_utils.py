"""Shared deterministic dataset utilities; no editor dependencies."""
import hashlib
import json


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def write_jsonl(path, rows):
    data = ''.join(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n' for row in rows).encode()
    path.write_bytes(data)
    return {'rows': len(rows), 'bytes': len(data), 'sha256': digest(data)}


def without_reminder(text):
    # Balanced removal preserves all non-reminder clauses, including multi-line text.
    out=[]; depth=0
    for ch in text:
        if ch=='(': depth+=1
        elif ch==')':
            if not depth: raise ValueError('Unbalanced reminder text')
            depth-=1
        elif depth==0: out.append(ch)
    if depth: raise ValueError('Unbalanced reminder text')
    return '\n'.join(line.rstrip() for line in ''.join(out).splitlines()).strip()
