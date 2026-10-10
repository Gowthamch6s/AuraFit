import json
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from aurafit.models import IngestBatch, MetricTick, SleepSession, DailyBaseline, AdaptiveTargets, UserCreate

ROOT = Path(__file__).resolve().parents[1]

def test_valid_batch():
    batch = IngestBatch.model_validate_json((ROOT / "examples/ingest_batch.json").read_text())
    assert len(batch.ticks) == 5
    assert len(batch.sleep_sessions) == 1

def test_invalid_metric():
    with pytest.raises(ValidationError):
        MetricTick(device_id=uuid4(), observed_at="2026-10-08T12:00:00Z", metric="stress_score", value=110)

def test_reject_naive_timestamp():
    with pytest.raises(ValidationError):
        MetricTick(device_id=uuid4(), observed_at="2026-10-08T12:00:00", metric="steps", value=10)

def test_sleep_stage_mismatch():
    obj = json.loads((ROOT / "examples/ingest_batch.json").read_text())["sleep_sessions"][0]
    obj["deep_minutes"] = 5
    with pytest.raises(ValidationError):
        SleepSession.model_validate(obj)

def test_duplicate_ticks():
    obj = json.loads((ROOT / "examples/ingest_batch.json").read_text())
    obj["ticks"].append(obj["ticks"][0])
    with pytest.raises(ValidationError):
        IngestBatch.model_validate(obj)

def test_invalid_timezone():
    with pytest.raises(ValidationError):
        UserCreate(user_id=uuid4(), timezone="Mars/Phobos")

def test_baseline_samples():
    with pytest.raises(ValidationError):
        DailyBaseline(user_id=uuid4(), local_date="2026-10-08", timezone="UTC", baseline_version="v1", lookback_days=7, sample_days=8)

def test_target_bedtime_pair():
    with pytest.raises(ValidationError):
        AdaptiveTargets(user_id=uuid4(), local_date="2026-10-08", model_version="v1", bedtime_start="2026-10-09T03:00:00Z")
