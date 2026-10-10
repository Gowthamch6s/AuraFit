"""Strict JSON contracts for AuraFit ingestion and computed wellness outputs.

Numerical bounds are input-quality safeguards, not medical thresholds.
Metric ticks are interval deltas for steps and active energy (not cumulative counters).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, field_validator, model_validator


class StrictModel(BaseModel):
    """Reject unknown fields and non-finite floating-point values."""
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)


class Metric(str, Enum):
    HRV_RMSSD_MS = "hrv_rmssd_ms"
    RESTING_HR_BPM = "resting_hr_bpm"
    STRESS_SCORE = "stress_score"
    STEPS = "steps"
    ACTIVE_ENERGY_KCAL = "active_energy_kcal"


class UserCreate(StrictModel):
    user_id: UUID
    timezone: str = Field(min_length=3, max_length=64)
    daily_sleep_target_minutes: int = Field(default=480, ge=360, le=600)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value


class DeviceCreate(StrictModel):
    device_id: UUID
    user_id: UUID
    provider: str = Field(min_length=1, max_length=64)
    model: str | None = Field(default=None, max_length=128)


class MetricTick(StrictModel):
    device_id: UUID
    observed_at: AwareDatetime
    metric: Metric
    value: float = Field(ge=0)

    @model_validator(mode="after")
    def check_metric_range(self) -> "MetricTick":
        bounds = {
            Metric.HRV_RMSSD_MS: (1, 300),
            Metric.RESTING_HR_BPM: (25, 220),
            Metric.STRESS_SCORE: (0, 100),
            Metric.STEPS: (0, 100000),
            Metric.ACTIVE_ENERGY_KCAL: (0, 10000),
        }
        lower, upper = bounds[self.metric]
        if not lower <= self.value <= upper:
            raise ValueError(f"{self.metric.value} must be between {lower} and {upper}")
        if self.metric is Metric.STEPS and not self.value.is_integer():
            raise ValueError("steps must be a whole number")
        return self


class SleepSession(StrictModel):
    session_id: UUID
    device_id: UUID
    started_at: AwareDatetime
    ended_at: AwareDatetime
    deep_minutes: int = Field(ge=0, le=1440)
    rem_minutes: int = Field(ge=0, le=1440)
    light_minutes: int = Field(ge=0, le=1440)
    awake_minutes: int = Field(ge=0, le=1440)

    @model_validator(mode="after")
    def duration_consistency(self) -> "SleepSession":
        elapsed = (self.ended_at - self.started_at).total_seconds() / 60
        stages = self.deep_minutes + self.rem_minutes + self.light_minutes + self.awake_minutes
        if not 0 < elapsed <= 1440:
            raise ValueError("sleep interval must be positive and at most 24 hours")
        if abs(elapsed - stages) > 2:
            raise ValueError("sleep stage durations must match elapsed minutes within 2 minutes")
        return self


class NutritionEntry(StrictModel):
    entry_id: UUID
    consumed_at: AwareDatetime
    calories_kcal: Decimal = Field(ge=0, le=10000, max_digits=9, decimal_places=2)
    carbs_g: Decimal = Field(ge=0, le=1500, max_digits=9, decimal_places=2)
    protein_g: Decimal = Field(ge=0, le=1000, max_digits=9, decimal_places=2)
    fat_g: Decimal = Field(ge=0, le=1000, max_digits=9, decimal_places=2)
    # Calories and macro energy are intentionally not required to match:
    # fiber, alcohol, label rounding, and incomplete logging can cause divergence.


class SubjectiveCheckin(StrictModel):
    checkin_id: UUID
    observed_at: AwareDatetime
    mood: int = Field(ge=1, le=10)
    energy: int = Field(ge=1, le=10)


class IngestBatch(StrictModel):
    batch_id: UUID
    user_id: UUID
    source: str = Field(min_length=1, max_length=64)
    ticks: list[MetricTick] = Field(default_factory=list, max_length=5000)
    sleep_sessions: list[SleepSession] = Field(default_factory=list, max_length=100)
    nutrition_entries: list[NutritionEntry] = Field(default_factory=list, max_length=500)
    subjective_checkins: list[SubjectiveCheckin] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def check_batch(self) -> "IngestBatch":
        if not any((self.ticks, self.sleep_sessions, self.nutrition_entries, self.subjective_checkins)):
            raise ValueError("ingestion batch must contain at least one observation")
        keys = [(t.device_id, t.metric, t.observed_at) for t in self.ticks]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate metric tick in batch")
        for collection, key in ((self.sleep_sessions, lambda x: (x.session_id, x.started_at)),
                                (self.nutrition_entries, lambda x: (x.entry_id, x.consumed_at)),
                                (self.subjective_checkins, lambda x: (x.checkin_id, x.observed_at))):
            identifiers = [key(item) for item in collection]
            if len(identifiers) != len(set(identifiers)):
                raise ValueError("duplicate record in batch")
        return self


Score = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


class DailyBaseline(StrictModel):
    user_id: UUID
    local_date: date
    timezone: str
    baseline_version: str = Field(min_length=1, max_length=64)
    lookback_days: int = Field(ge=3, le=90)
    sample_days: int = Field(ge=0)
    hrv_rmssd_median_ms: float | None = Field(default=None, ge=1, le=300)
    resting_hr_median_bpm: float | None = Field(default=None, ge=25, le=220)
    sleep_median_minutes: float | None = Field(default=None, ge=0, le=1440)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return UserCreate.valid_timezone(value)

    @model_validator(mode="after")
    def check_sample_days(self) -> "DailyBaseline":
        if self.sample_days > self.lookback_days:
            raise ValueError("sample_days cannot exceed lookback_days")
        return self


class DailyAssessment(StrictModel):
    user_id: UUID
    local_date: date
    model_version: str = Field(min_length=1, max_length=64)
    baseline_version: str = Field(min_length=1, max_length=64)
    allostatic_load_index: Score
    recovery_score: Score
    overreach_risk_score: Score
    predicted_next_day_recovery: Score | None = None
    evidence: dict[str, object] = Field(default_factory=dict)


class AdaptiveTargets(StrictModel):
    user_id: UUID
    local_date: date
    model_version: str = Field(min_length=1, max_length=64)
    step_target: int | None = Field(default=None, ge=0, le=50000)
    active_energy_target_kcal: Decimal | None = Field(default=None, ge=0, le=10000, max_digits=9, decimal_places=2)
    energy_budget_score: Score | None = None
    bedtime_start: AwareDatetime | None = None
    bedtime_end: AwareDatetime | None = None
    rationale: dict[str, object] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bedtime_pair(self) -> "AdaptiveTargets":
        if (self.bedtime_start is None) != (self.bedtime_end is None):
            raise ValueError("bedtime_start and bedtime_end must both be provided")
        if self.bedtime_start is not None and self.bedtime_end <= self.bedtime_start:
            raise ValueError("bedtime_end must be after bedtime_start")
        return self
