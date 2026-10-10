"""Deterministic synthetic wellness records; simulation, not clinical observations."""
from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid5, NAMESPACE_URL
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import (DeviceCreate, IngestBatch, MetricTick, NutritionEntry,
                     SleepSession, SubjectiveCheckin, UserCreate)

SCENARIOS = ('balanced', 'overreach', 'recovery')


def identifier(*parts: object) -> UUID:
    """Create stable namespace-scoped identifiers for replay-safe imports."""
    return uuid5(NAMESPACE_URL, 'aurafit-synthetic-v2:' + ':'.join(map(str, parts)))


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def q(value: float) -> Decimal:
    return Decimal(str(round(value, 2))).quantize(Decimal('0.01'))


@dataclass(frozen=True)
class GeneratorConfig:
    seed: int = 42
    days: int = 30
    start_date: date = date(2026, 9, 1)
    timezone: str = 'America/New_York'
    scenario: str = 'balanced'
    missing_rate: float = 0.03
    user_id: UUID | None = None
    device_id: UUID | None = None

    def __post_init__(self) -> None:
        if not (1 <= self.days <= 365):
            raise ValueError('days must be between 1 and 365')
        if self.scenario not in SCENARIOS:
            raise ValueError(f'scenario must be one of {SCENARIOS}')
        if not (0 <= self.missing_rate < 0.5) or not math.isfinite(self.missing_rate):
            raise ValueError('missing_rate must be finite and in [0, 0.5)')
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc


@dataclass(frozen=True)
class SyntheticDataset:
    user: UserCreate
    device: DeviceCreate
    batches: tuple[IngestBatch, ...]
    manifest: dict[str, object]

    def write(self, directory: Path) -> None:
        """Write contract-compliant JSON and metadata; deterministic bytes."""
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'user.json').write_text(self.user.model_dump_json(indent=2) + '\n', encoding='utf-8')
        (directory / 'device.json').write_text(self.device.model_dump_json(indent=2) + '\n', encoding='utf-8')
        (directory / 'manifest.json').write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        batch_dir = directory / 'batches'
        batch_dir.mkdir(exist_ok=True)
        for index, batch in enumerate(self.batches, 1):
            (batch_dir / f'batch_{index:03d}.json').write_text(batch.model_dump_json(indent=2) + '\n', encoding='utf-8')


def generate(config: GeneratorConfig) -> SyntheticDataset:
    """Generate correlated sleep, strain, mood, metabolic and wearable observations.

    A latent fatigue process couples recent activity/stress with sleep, HRV,
    resting HR, and subjective energy; parameters are illustrative, not validated.
    Sleep is attributed to the local date it ends; interval ticks are hourly deltas.
    """
    rng = random.Random(config.seed)
    tz = ZoneInfo(config.timezone)
    uid = config.user_id or identifier(config.seed, config.start_date, config.timezone, config.scenario, 'user')
    did = config.device_id or identifier(uid, 'wearable')
    user = UserCreate(user_id=uid, timezone=config.timezone, daily_sleep_target_minutes=480)
    device = DeviceCreate(device_id=did, user_id=uid, provider='aurafit_synthetic', model='sim-v2')
    batches: list[IngestBatch] = []
    fatigue = 0.25
    prior_sleep_debt = 0.0
    for day in range(config.days):
        local_day = config.start_date + timedelta(days=day)
        progress = day / max(1, config.days - 1)
        if config.scenario == 'overreach':
            workload = 0.4 + 0.55 * progress
            stress_base = 35 + 33 * progress
            sleep_pressure = 0.6 + 0.9 * progress
        elif config.scenario == 'recovery':
            workload = 0.85 - 0.55 * progress
            stress_base = 63 - 34 * progress
            sleep_pressure = 1.1 - 0.85 * progress
        else:
            workload = 0.52 + 0.12 * math.sin(day * 0.65)
            stress_base = 36 + 9 * math.sin(day * 0.4)
            sleep_pressure = 0.55
        fatigue = clamp(0.73 * fatigue + 0.30 * workload + 0.004 * (stress_base - 35)
                        + 0.09 * prior_sleep_debt + rng.gauss(0, 0.035) - 0.12, 0, 1.5)
        asleep_minutes = int(clamp(rng.gauss(505 - sleep_pressure * 45 - fatigue * 68, 20), 300, 590))
        awake = round(clamp(rng.gauss(25 + fatigue * 12, 7), 5, 70))
        deep = round(clamp((asleep_minutes - awake) * (0.19 - 0.035 * min(fatigue, 1)), 0, 150))
        rem = round(clamp((asleep_minutes - awake) * (0.23 - 0.022 * min(fatigue, 1)), 0, 170))
        light = asleep_minutes - awake - deep - rem
        # Local sleep end at 07:00 and start computed as actual elapsed duration.
        end = datetime.combine(local_day, time(7, 0), tzinfo=tz)
        end = end.astimezone(timezone.utc)
        start = end - timedelta(minutes=asleep_minutes)
        sleep = SleepSession(session_id=identifier(uid, local_day, 'sleep'), device_id=did,
                             started_at=start, ended_at=end, deep_minutes=deep,
                             rem_minutes=rem, light_minutes=light, awake_minutes=awake)
        prior_sleep_debt = clamp((480 - (asleep_minutes - awake)) / 480, -0.3, 0.6)
        hrv = round(clamp(rng.gauss(62 - fatigue * 25 - max(0, stress_base - 35) * 0.17, 3), 10, 180), 2)
        resting_hr = round(clamp(rng.gauss(58 + fatigue * 13 + max(0, stress_base - 35) * 0.085, 2), 36, 125), 2)
        ticks: list[MetricTick] = []
        def append_tick(ts: datetime, metric: str, value: float) -> None:
            if rng.random() >= config.missing_rate:
                ticks.append(MetricTick(device_id=did, observed_at=ts, metric=metric, value=value))
        anchor = datetime.combine(local_day, time(7, 15), tzinfo=tz)
        append_tick(anchor, 'hrv_rmssd_ms', hrv)
        append_tick(anchor, 'resting_hr_bpm', resting_hr)
        # Local hourly wall-clock values are timezone aware; UTC representation
        # disambiguates duplicate wall times during autumn DST changes.
        start_utc = datetime.combine(local_day, time.min, tzinfo=tz).astimezone(timezone.utc)
        end_utc = datetime.combine(local_day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
        elapsed_hours = int((end_utc - start_utc).total_seconds() // 3600)
        for hour_index in range(elapsed_hours):
            at = start_utc + timedelta(hours=hour_index)
            local_hour = at.astimezone(tz).hour
            awake_hour = 7 <= local_hour <= 22
            movement = (1 if awake_hour else 0.025) * (0.45 + workload)
            movement *= 1.25 if local_hour in (8, 12, 17, 18) else 0.7
            steps = int(clamp(rng.gauss(310 * movement, 100 * movement), 0, 2700))
            energy = round(clamp(steps * rng.uniform(0.028, 0.057) + (7 * workload if awake_hour else 0), 0, 210), 2)
            stress = round(clamp(rng.gauss(stress_base + fatigue * 11 + (6 if 9 <= local_hour <= 18 else -12), 8), 0, 100), 2)
            ts = at + timedelta(minutes=30)
            append_tick(ts, 'steps', float(steps))
            append_tick(ts, 'active_energy_kcal', energy)
            append_tick(ts, 'stress_score', stress)
        # Three meals: calories track dietary intake, not inferred energy deficit.
        daily_food = clamp(rng.gauss(2300 - 90 * fatigue + 140 * workload, 130), 1300, 3600)
        nutrition: list[NutritionEntry] = []
        for meal, hour, fraction in (('breakfast', 8, 0.26), ('lunch', 13, 0.36), ('dinner', 19, 0.38)):
            calories = daily_food * fraction
            # 48/24/28 percent macro energy (4/4/9 kcal per gram).
            nutrition.append(NutritionEntry(entry_id=identifier(uid, local_day, meal),
                consumed_at=datetime.combine(local_day, time(hour, 0), tzinfo=tz),
                calories_kcal=q(calories), carbs_g=q(calories * .48 / 4),
                protein_g=q(calories * .24 / 4), fat_g=q(calories * .28 / 9)))
        mood = round(clamp(rng.gauss(8.2 - 3.4 * fatigue - max(0, stress_base - 35) * .025, .8), 1, 10))
        subjective_energy = round(clamp(rng.gauss(8.5 - 4.2 * fatigue - prior_sleep_debt * 1.5, .7), 1, 10))
        checkin = SubjectiveCheckin(checkin_id=identifier(uid, local_day, 'checkin'),
            observed_at=datetime.combine(local_day, time(20), tzinfo=tz), mood=mood, energy=subjective_energy)
        batch = IngestBatch(batch_id=identifier(uid, local_day, 'batch'), user_id=uid,
            source='aurafit_synthetic', ticks=ticks, sleep_sessions=[sleep],
            nutrition_entries=nutrition, subjective_checkins=[checkin])
        batches.append(batch)
    counts = {'ticks': sum(len(b.ticks) for b in batches),
              'sleep_sessions': sum(len(b.sleep_sessions) for b in batches),
              'nutrition_entries': sum(len(b.nutrition_entries) for b in batches),
              'subjective_checkins': sum(len(b.subjective_checkins) for b in batches)}
    manifest: dict[str, object] = {'generator_version': '2.0.0', 'seed': config.seed,
        'days': config.days, 'start_date': config.start_date.isoformat(),
        'timezone': config.timezone, 'scenario': config.scenario,
        'missing_rate': config.missing_rate, 'user_id': str(uid), 'device_id': str(did),
        'observation_counts': counts, 'synthetic': True,
        'notice': 'Illustrative correlated simulation; not medical measurements or validated predictions.'}
    return SyntheticDataset(user=user, device=device, batches=tuple(batches), manifest=manifest)
