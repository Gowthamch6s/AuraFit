"""Persist exact Phase 1 baseline/assessment contracts transactionally."""
from __future__ import annotations
import json
from collections.abc import Sequence
from .features import FeatureDay


def persist_feature_days(database_url: str, days: Sequence[FeatureDay]) -> int:
    """Upsert baseline before assessment, replacing same-version recomputations."""
    import psycopg
    with psycopg.connect(database_url) as conn:
        with conn.transaction():
            for item in days:
                b=item.baseline;a=item.assessment
                if b.user_id!=a.user_id or b.local_date!=a.local_date or b.baseline_version!=a.baseline_version:
                    raise ValueError('baseline and assessment keys differ')
                conn.execute('''INSERT INTO aurafit.daily_baselines
                 (user_id,local_date,timezone,baseline_version,lookback_days,sample_days,hrv_rmssd_median_ms,resting_hr_median_bpm,sleep_median_minutes)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                 ON CONFLICT (user_id,local_date,baseline_version) DO UPDATE SET
                 timezone=EXCLUDED.timezone,lookback_days=EXCLUDED.lookback_days,sample_days=EXCLUDED.sample_days,
                 hrv_rmssd_median_ms=EXCLUDED.hrv_rmssd_median_ms,resting_hr_median_bpm=EXCLUDED.resting_hr_median_bpm,
                 sleep_median_minutes=EXCLUDED.sleep_median_minutes,computed_at=now()''',
                 (b.user_id,b.local_date,b.timezone,b.baseline_version,b.lookback_days,b.sample_days,b.hrv_rmssd_median_ms,b.resting_hr_median_bpm,b.sleep_median_minutes))
                conn.execute('''INSERT INTO aurafit.daily_assessments
                 (user_id,local_date,model_version,baseline_version,allostatic_load_index,recovery_score,overreach_risk_score,predicted_next_day_recovery,evidence)
                 VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                 ON CONFLICT (user_id,local_date,model_version) DO UPDATE SET
                 baseline_version=EXCLUDED.baseline_version,allostatic_load_index=EXCLUDED.allostatic_load_index,
                 recovery_score=EXCLUDED.recovery_score,overreach_risk_score=EXCLUDED.overreach_risk_score,
                 predicted_next_day_recovery=EXCLUDED.predicted_next_day_recovery,evidence=EXCLUDED.evidence,computed_at=now()''',
                 (a.user_id,a.local_date,a.model_version,a.baseline_version,a.allostatic_load_index,a.recovery_score,
                  a.overreach_risk_score,a.predicted_next_day_recovery,json.dumps(a.evidence,allow_nan=False)))
    return len(days)
