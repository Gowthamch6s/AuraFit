"""Contract, reproducibility, safety and scenario tests."""
from datetime import date, timezone
from pathlib import Path
from statistics import mean
import hashlib
import pytest
from pydantic import ValidationError
from aurafit.synthetic import GeneratorConfig, generate
from aurafit.models import IngestBatch, UserCreate, DeviceCreate
from aurafit.loader import batch_digest


def test_reproducible_and_different_seeds():
    cfg = GeneratorConfig()
    first, second = generate(cfg), generate(cfg)
    assert [batch_digest(b) for b in first.batches] == [batch_digest(b) for b in second.batches]
    assert [batch_digest(b) for b in first.batches] != [batch_digest(b) for b in generate(GeneratorConfig(seed=43)).batches]


def test_full_30_day_contract_and_counts():
    ds = generate(GeneratorConfig(missing_rate=0))
    assert len(ds.batches) == 30
    assert all(len(b.ticks) == 74 for b in ds.batches)
    assert all(len(b.sleep_sessions) == 1 and len(b.nutrition_entries) == 3 and len(b.subjective_checkins) == 1 for b in ds.batches)
    assert ds.manifest['observation_counts']['ticks'] == 2220
    for batch in ds.batches:
        assert IngestBatch.model_validate_json(batch.model_dump_json()) == batch


def test_serialized_file_roundtrip(tmp_path: Path):
    ds = generate(GeneratorConfig(days=4, seed=33))
    ds.write(tmp_path)
    assert UserCreate.model_validate_json((tmp_path/'user.json').read_text()) == ds.user
    assert DeviceCreate.model_validate_json((tmp_path/'device.json').read_text()) == ds.device
    assert [IngestBatch.model_validate_json(p.read_text()) for p in sorted((tmp_path/'batches').glob('*.json'))] == list(ds.batches)


def test_scenario_relationships():
    over = generate(GeneratorConfig(days=30, scenario='overreach', missing_rate=0))
    rec = generate(GeneratorConfig(days=30, scenario='recovery', missing_rate=0))
    def avg(batches, metric):
        return mean(t.value for b in batches for t in b.ticks if t.metric.value == metric)
    assert avg(over.batches[-7:], 'hrv_rmssd_ms') < avg(over.batches[:7], 'hrv_rmssd_ms')
    assert avg(over.batches[-7:], 'resting_hr_bpm') > avg(over.batches[:7], 'resting_hr_bpm')
    assert avg(rec.batches[-7:], 'hrv_rmssd_ms') > avg(rec.batches[:7], 'hrv_rmssd_ms')
    assert avg(rec.batches[-7:], 'stress_score') < avg(rec.batches[:7], 'stress_score')


def test_all_timestamps_timezone_aware_and_stage_sums():
    ds = generate(GeneratorConfig(days=4, start_date=date(2026, 10, 31), missing_rate=0))
    for b in ds.batches:
        for s in b.sleep_sessions:
            assert s.started_at.tzinfo and s.ended_at.tzinfo
            assert abs((s.ended_at-s.started_at).total_seconds()/60 - sum((s.deep_minutes,s.rem_minutes,s.light_minutes,s.awake_minutes))) <= 2
        for t in b.ticks:
            assert t.observed_at.utcoffset() is not None
    # Autumn clock change yields 25 actual hourly intervals on Nov 1.
    assert len(ds.batches[1].ticks) == 77


@pytest.mark.parametrize('kwargs', [{'days':0}, {'days':366}, {'scenario':'unrealistic'}, {'missing_rate':-1}, {'missing_rate':.5}, {'timezone':'Fake/Timezone'}])
def test_bad_config(kwargs):
    with pytest.raises(ValueError):
        GeneratorConfig(**kwargs)


def test_missingness_reduces_ticks_but_not_other_records():
    complete = generate(GeneratorConfig(missing_rate=0))
    partial = generate(GeneratorConfig(missing_rate=0.25))
    assert partial.manifest['observation_counts']['ticks'] < complete.manifest['observation_counts']['ticks']
    assert partial.manifest['observation_counts']['sleep_sessions'] == 30


def test_idempotency_digest_stable():
    batch = generate(GeneratorConfig(days=1)).batches[0]
    assert batch_digest(batch) == batch_digest(IngestBatch.model_validate_json(batch.model_dump_json()))
