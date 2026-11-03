#!/usr/bin/env python3
"""Back up legacy full-text history, then replace it with metadata (service stopped)."""
import argparse
import gzip
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def sanitize(record):
    # Already-safe records are still allowlisted to prevent unknown text fields.
    stages = []
    for stage in record.get('stages', []):
        stages.append({
            'provider': stage.get('provider'), 'role': stage.get('role'),
            'success': stage.get('success', False),
            'quality_ok': stage.get('quality_ok'),
            'quality_reason': 'legacy_unverified',
            'duration_ms': stage.get('duration_ms'),
            'answer_length': stage.get('answer_length', len(stage.get('answer', ''))),
        })
    return {
        'schema_version': 2, 'migrated_from_legacy': True,
        'route_id': record.get('route_id'), 'timestamp': record.get('timestamp'),
        'success': record.get('success', all(s['success'] for s in stages)),
        'duration_ms': record.get('duration_ms'),
        'prompt_hash': record.get('prompt_hash') or hashlib.sha256(record.get('original_prompt', '').encode()).hexdigest(),
        'answer_length': record.get('answer_length', len(record.get('final_answer', ''))),
        'stages': stages,
    }


def migrate(path, backup_dir):
    raw = path.read_bytes()
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if not any('original_prompt' in r or 'final_answer' in r or any('answer' in s or 'raw_output' in s for s in r.get('stages', [])) for r in records):
        return None, len(records)
    converted = [sanitize(r) for r in records]
    backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = backup_dir / (path.name + '.' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.gz')
    with backup.open('xb') as f:
        os.chmod(backup, 0o600)
        f.write(gzip.compress(raw))
        f.flush()
        os.fsync(f.fileno())
    if gzip.decompress(backup.read_bytes()) != raw:
        raise RuntimeError('backup verification failed')
    # Parse and back up before replacing anything; original bytes remain recoverable.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        for record in converted:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
        f.flush()
        os.fsync(f.fileno())
        temporary = f.name
    os.replace(temporary, path)
    return backup, len(converted)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    backup, count = migrate(root / 'logs/orchestration_history.jsonl', root / 'debug/legacy-log-backups')
    print(json.dumps({'records': count, 'backup': str(backup) if backup else None}))
