"""Causal day-level engineering for synthetic wellness data, not medical inference.

Day D values are finalized after all observations for day D. Baselines are built
only from dates strictly preceding D; next-day forecasts use D to predict D+1.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from zoneinfo import ZoneInfo
import math
from .models import DailyBaseline, DailyAssessment, IngestBatch, UserCreate

BASELINE_VERSION = 'median7-prior-v1'
SCORE_VERSION = 'heuristic-experimental-v1'
FEATURE_NAMES = ('hrv_ratio','hrv_robust_z','rhr_delta_bpm','sleep_debt_7d_min','sleep_minutes','stress_mean','stress_spike_fraction','steps','active_kcal','calories_logged','mood','energy','missing_fraction','recovery_score','ali','risk_score')


def bounded(x: float, low: float=0.0, high: float=100.0) -> float:
    return max(low, min(high, float(x)))


def robust_z(current: float, history: list[float]) -> float:
    """Median absolute deviation z; clamped for small-MAD stability."""
    if len(history) < 3:
        return 0.0
    center = median(history)
    mad = median([abs(x-center) for x in history])
    scale = max(1.4826 * mad, 0.08 * max(center, 1), 1.0)
    return bounded((current-center)/scale, -5, 5)

@dataclass(frozen=True)
class FeatureDay:
    local_date: date
    baseline: DailyBaseline
    assessment: DailyAssessment
    values: dict[str, float]
    valid_forecast: bool


def build_features(user: UserCreate, batches: tuple[IngestBatch,...] | list[IngestBatch], lookback: int=7, min_history: int=3) -> list[FeatureDay]:
    """Construct strictly causal baseline and final-day scores from validated batches.

    Sleep belongs to the date it ends in the user's IANA timezone. Interval
    steps and kcal sum (they are NOT cumulative counters). Missing nutrition
    is unknown, not an inferred metabolic deficit; no caloric prescription.
    """
    if not 3 <= lookback <= 90 or not 1 <= min_history <= lookback:
        raise ValueError('invalid baseline configuration')
    tz=ZoneInfo(user.timezone)
    dates: dict[date,dict[str,list[float]]] = defaultdict(lambda: defaultdict(list))
    for batch in batches:
        if batch.user_id != user.user_id:
            raise ValueError('cross-user batch detected')
        for tick in batch.ticks:
            d=tick.observed_at.astimezone(tz).date()
            dates[d][tick.metric.value].append(tick.value)
        for sleep in batch.sleep_sessions:
            d=sleep.ended_at.astimezone(tz).date()
            dates[d]['sleep_minutes'].append(float(sleep.deep_minutes+sleep.rem_minutes+sleep.light_minutes))
        for entry in batch.nutrition_entries:
            d=entry.consumed_at.astimezone(tz).date()
            dates[d]['calories_logged'].append(float(entry.calories_kcal))
        for check in batch.subjective_checkins:
            d=check.observed_at.astimezone(tz).date()
            dates[d]['mood'].append(float(check.mood))
            dates[d]['energy'].append(float(check.energy))
    if not dates:
        return []
    start=min(dates)
    end=max(dates)
    summaries={}
    for i in range((end-start).days+1):
        d=start+timedelta(days=i); entries=dates.get(d,{})
        def avg(k):
            xs=entries.get(k,[])
            return sum(xs)/len(xs) if xs else None
        def total(k):
            xs=entries.get(k,[])
            return sum(xs) if xs else None
        stress=entries.get('stress_score',[])
        summaries[d]={'hrv':avg('hrv_rmssd_ms'),'rhr':avg('resting_hr_bpm'),
          'sleep':total('sleep_minutes'),'stress':avg('stress_score'),
          'spikes':(sum(v>=75 for v in stress)/len(stress) if stress else None),
          'steps':total('steps'),'active':total('active_energy_kcal'),
          'food':total('calories_logged'),'mood':avg('mood'),'energy':avg('energy')}
    results=[]
    for d, row in summaries.items():
        preceding=[summaries[d-timedelta(days=i)] for i in range(lookback,0,-1) if d-timedelta(days=i) in summaries]
        hrv_hist=[r['hrv'] for r in preceding if r['hrv'] is not None]
        rhr_hist=[r['rhr'] for r in preceding if r['rhr'] is not None]
        sleep_hist=[r['sleep'] for r in preceding if r['sleep'] is not None]
        complete_history=min(len(hrv_hist),len(rhr_hist),len(sleep_hist))
        hb=median(hrv_hist) if hrv_hist else None
        rb=median(rhr_hist) if rhr_hist else None
        sb=median(sleep_hist) if sleep_hist else None
        baseline=DailyBaseline(user_id=user.user_id, local_date=d, timezone=user.timezone,
          baseline_version=BASELINE_VERSION, lookback_days=lookback, sample_days=complete_history,
          hrv_rmssd_median_ms=hb, resting_hr_median_bpm=rb, sleep_median_minutes=sb)
        hrv=row['hrv']; rhr=row['rhr']; sleep=row['sleep']; stress=row['stress']
        hrv_ratio=(hrv/hb if hrv is not None and hb else 1.0)
        z=robust_z(hrv,hrv_hist) if hrv is not None else 0.0
        rhr_delta=(rhr-rb if rhr is not None and rb is not None else 0.0)
        # Debt is accumulated over available last seven nights; missing nights
        # are omitted, and data sufficiency is exposed to consumers.
        nights=[summaries[d-timedelta(days=i)]['sleep'] for i in range(7) if d-timedelta(days=i) in summaries]
        debt=sum(max(0,user.daily_sleep_target_minutes-v) for v in nights if v is not None)
        missing=sum(row[k] is None for k in ('hrv','rhr','sleep','stress','steps','active','food','mood','energy'))/9
        hrv_pen=bounded((1-hrv_ratio)*170) if hrv is not None and hb else 0
        rhr_pen=bounded(rhr_delta*6) if rhr is not None and rb else 0
        sleep_pen=bounded(debt/7/1.8)
        stress_pen=bounded(((stress or 40)-35)*1.4) if stress is not None else 0
        spike_pen=bounded((row['spikes'] or 0)*100)
        # Explicitly experimental composite. No claim of clinical ALI validation.
        ali=bounded(.30*hrv_pen+.15*rhr_pen+.30*sleep_pen+.15*stress_pen+.10*spike_pen)
        recovery=bounded(100-(.44*hrv_pen+.18*rhr_pen+.25*sleep_pen+.13*stress_pen))
        risk=bounded(.65*ali+.35*(100-recovery))
        evidence={'synthetic_or_experimental':True,'method':'nonclinical_heuristic',
          'hrv_baseline_ms':hb,'hrv_current_ms':hrv,'hrv_deviation_pct':round((hrv_ratio-1)*100,2) if hb and hrv is not None else None,
          'rhr_baseline_bpm':rb,'rhr_current_bpm':rhr,'sleep_target_minutes':user.daily_sleep_target_minutes,
          'sleep_debt_7d_minutes':debt,'stress_mean':stress,'missing_fraction':round(missing,3),
          'baseline_days':complete_history,'sufficient_history':complete_history>=min_history,
          'limitations':['No total energy expenditure or metabolic deficit inferred','No burnout diagnosis','Not clinically validated']}
        assessment=DailyAssessment(user_id=user.user_id,local_date=d,model_version=SCORE_VERSION,
          baseline_version=BASELINE_VERSION,allostatic_load_index=round(ali,3),
          recovery_score=round(recovery,3),overreach_risk_score=round(risk,3), evidence=evidence)
        values={'hrv_ratio':hrv_ratio,'hrv_robust_z':z,'rhr_delta_bpm':rhr_delta,
          'sleep_debt_7d_min':debt,'sleep_minutes':sleep or 0.,'stress_mean':stress or 0.,
          'stress_spike_fraction':row['spikes'] or 0.,'steps':row['steps'] or 0.,
          'active_kcal':row['active'] or 0.,'calories_logged':row['food'] or 0.,
          'mood':row['mood'] or 0.,'energy':row['energy'] or 0.,
          'missing_fraction':missing,'recovery_score':recovery,'ali':ali,'risk_score':risk}
        # Include records for warm-up & auditing; train only fully observed days
        # with sufficient historical baselines.
        valid=(complete_history>=min_history and missing==0)
        results.append(FeatureDay(d,baseline,assessment,values,valid))
    return results
