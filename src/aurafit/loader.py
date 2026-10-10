"""Atomic and idempotent loading of validated synthetic batches into Phase 1 tables."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable

from .models import UserCreate, DeviceCreate, IngestBatch


def batch_digest(batch: IngestBatch) -> str:
    """Hash a canonical representation of the validated payload."""
    canonical = json.dumps(batch.model_dump(mode='json'), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(canonical.encode('utf-8')).hexdigest()


def load_directory(database_url: str, directory: Path) -> dict[str, int]:
    """Verify the manifest and load each batch transactionally.

    Existing identical batch IDs are skipped; an ID reused with different
    content is rejected. Rollbacks occur at the individual batch boundary.
    """
    user = UserCreate.model_validate_json((directory / 'user.json').read_text(encoding='utf-8'))
    device = DeviceCreate.model_validate_json((directory / 'device.json').read_text(encoding='utf-8'))
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    files = sorted((directory / 'batches').glob('batch_*.json'))
    if len(files) != manifest['days']:
        raise ValueError('number of batch files disagrees with manifest')
    if str(user.user_id) != manifest['user_id'] or str(device.device_id) != manifest['device_id'] or device.user_id != user.user_id:
        raise ValueError('user/device identity disagrees with manifest')
    counts = {'inserted': 0, 'skipped': 0}
    import psycopg
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            row = conn.execute('SELECT timezone, daily_sleep_target_minutes FROM aurafit.users WHERE user_id=%s', (user.user_id,)).fetchone()
            if row is not None and row != (user.timezone, user.daily_sleep_target_minutes):
                raise ValueError('existing user configuration differs from input')
            conn.execute('INSERT INTO aurafit.users (user_id, timezone, daily_sleep_target_minutes) VALUES (%s,%s,%s) ON CONFLICT(user_id) DO NOTHING', (user.user_id, user.timezone, user.daily_sleep_target_minutes))
            row = conn.execute('SELECT user_id, provider, model FROM aurafit.devices WHERE device_id=%s', (device.device_id,)).fetchone()
            if row is not None and row != (device.user_id, device.provider, device.model):
                raise ValueError('existing device configuration differs from input')
            conn.execute('INSERT INTO aurafit.devices (device_id,user_id,provider,model) VALUES (%s,%s,%s,%s) ON CONFLICT(device_id) DO NOTHING', (device.device_id, device.user_id, device.provider, device.model))
        for path in files:
            batch = IngestBatch.model_validate_json(path.read_text(encoding='utf-8'))
            if batch.user_id != user.user_id or any(t.device_id != device.device_id for t in batch.ticks) or any(s.device_id != device.device_id for s in batch.sleep_sessions):
                raise ValueError(f'foreign user/device reference in {path}')
            digest = batch_digest(batch)
            with conn.transaction():
                # Serialize concurrent imports of this specific batch ID.
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (str(batch.batch_id),))
                existing = conn.execute('SELECT user_id, source, payload_sha256 FROM aurafit.ingestion_batches WHERE batch_id=%s', (batch.batch_id,)).fetchone()
                if existing is not None:
                    if existing != (batch.user_id, batch.source, digest):
                        raise ValueError(f'batch_id reused with different payload: {batch.batch_id}')
                    counts['skipped'] += 1
                    continue
                conn.execute('INSERT INTO aurafit.ingestion_batches(batch_id,user_id,source,payload_sha256) VALUES(%s,%s,%s,%s)', (batch.batch_id,batch.user_id,batch.source,digest))
                with conn.cursor() as cur:
                    cur.executemany('INSERT INTO aurafit.metric_ticks(user_id,device_id,observed_at,metric,value,batch_id) VALUES (%s,%s,%s,%s,%s,%s)',
                        [(batch.user_id,t.device_id,t.observed_at,t.metric.value,t.value,batch.batch_id) for t in batch.ticks])
                    cur.executemany('INSERT INTO aurafit.sleep_sessions(user_id,device_id,session_id,started_at,ended_at,deep_minutes,rem_minutes,light_minutes,awake_minutes,batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                        [(batch.user_id,s.device_id,s.session_id,s.started_at,s.ended_at,s.deep_minutes,s.rem_minutes,s.light_minutes,s.awake_minutes,batch.batch_id) for s in batch.sleep_sessions])
                    cur.executemany('INSERT INTO aurafit.nutrition_entries(user_id,entry_id,consumed_at,calories_kcal,carbs_g,protein_g,fat_g,batch_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)',
                        [(batch.user_id,n.entry_id,n.consumed_at,n.calories_kcal,n.carbs_g,n.protein_g,n.fat_g,batch.batch_id) for n in batch.nutrition_entries])
                    cur.executemany('INSERT INTO aurafit.subjective_checkins(user_id,checkin_id,observed_at,mood,energy,batch_id) VALUES (%s,%s,%s,%s,%s,%s)',
                        [(batch.user_id,c.checkin_id,c.observed_at,c.mood,c.energy,batch.batch_id) for c in batch.subjective_checkins])
                counts['inserted'] += 1
    return counts
