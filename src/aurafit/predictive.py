"""Next-day experimental recovery forecast; chronological holdout and safe artifacts."""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.dummy import DummyRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.inspection import permutation_importance
import joblib
from .features import FEATURE_NAMES, FeatureDay, bounded

MODEL_VERSION='hgb-nextday-v1'

@dataclass(frozen=True)
class ForecastResult:
    forecast_date: date
    recovery: float
    model_version: str
    uncertainty: str


def supervised_rows(days: list[FeatureDay]):
    """Pair D features only with exactly D+1 recovery labels."""
    ordered=sorted(days,key=lambda x:x.local_date)
    if len({d.local_date for d in ordered})!=len(ordered):
        raise ValueError('duplicate dates')
    X=[]; y=[]; dates=[]
    for today, tomorrow in zip(ordered,ordered[1:]):
        if tomorrow.local_date != today.local_date+timedelta(days=1):
            continue
        if not today.valid_forecast or not tomorrow.valid_forecast:
            continue
        X.append([today.values[k] for k in FEATURE_NAMES]); y.append(tomorrow.assessment.recovery_score); dates.append(today.local_date)
    return np.asarray(X,dtype=float).reshape((-1,len(FEATURE_NAMES))), np.asarray(y,dtype=float), dates


def _metrics(y, pred):
    return {'mae':round(float(mean_absolute_error(y,pred)),4),
            'rmse':round(float(np.sqrt(mean_squared_error(y,pred))),4),
            'n':int(len(y))}


def train(days: list[FeatureDay], output: Path, holdout_fraction: float=.25, min_rows: int=12) -> dict:
    """Train and evaluate on a chronologically later untouched test segment.

    Synthetic example only. Model selection does not use holdout outcomes.
    A constant-median baseline is reported for comparison. Uses fixed algorithm
    settings; test-period permutation importance is descriptive, not selection.
    """
    if not .1 <= holdout_fraction <= .4:
        raise ValueError('holdout_fraction must be in [0.1,0.4]')
    X,y,dates=supervised_rows(days)
    if len(y)<min_rows or min_rows<8:
        raise ValueError(f'need >= {max(min_rows,8)} valid consecutive day pairs, got {len(y)}')
    n_test=max(3,int(np.ceil(len(y)*holdout_fraction)))
    Xtr,Xte=X[:-n_test],X[-n_test:]
    ytr,yte=y[:-n_test],y[-n_test:]
    if len(ytr)<5:
        raise ValueError('too few train observations')
    model=HistGradientBoostingRegressor(max_iter=90, max_leaf_nodes=5, min_samples_leaf=3,
                                        learning_rate=.06,l2_regularization=5,random_state=42)
    model.fit(Xtr,ytr)
    baseline=DummyRegressor(strategy='median').fit(Xtr,ytr)
    pred=np.clip(model.predict(Xte),0,100)
    base_pred=np.clip(baseline.predict(Xte),0,100)
    importance=permutation_importance(model,Xte,yte,n_repeats=5,random_state=42,scoring='neg_mean_absolute_error')
    explanations=sorted([{'feature':f,'holdout_mae_increase':round(float(s),5)} for f,s in zip(FEATURE_NAMES,importance.importances_mean)],key=lambda r:r['holdout_mae_increase'],reverse=True)
    report={'model_version':MODEL_VERSION,'dataset':'synthetic_or_unverified',
        'target':'next_day_recovery_score_experimental','train_rows':len(ytr),'test_rows':len(yte),
        'train_start':dates[0].isoformat(),'train_end':dates[-n_test-1].isoformat(),
        'test_start':dates[-n_test].isoformat(),'test_end':dates[-1].isoformat(),
        'model':_metrics(yte,pred),'naive_median':_metrics(yte,base_pred),
        'permutation_importance_on_holdout':explanations,
        'caveat':'Synthetic metrics and heuristic targets; not clinically validated. Importance on holdout is descriptive and not for model selection.'}
    output.mkdir(parents=True,exist_ok=True)
    # joblib artifacts are executable pickle; only load trusted local artifacts.
    target=output/'model.joblib'; joblib.dump(model,target)
    digest=hashlib.sha256(target.read_bytes()).hexdigest()
    report['artifact_sha256']=digest
    (output/'manifest.json').write_text(json.dumps({'features':list(FEATURE_NAMES),**report},indent=2,sort_keys=True)+'\n',encoding='utf-8')
    return report


def forecast(days: list[FeatureDay], artifact_dir: Path) -> ForecastResult:
    """Verify artifact digest + contract; refuse incomplete latest-day features."""
    manifest=json.loads((artifact_dir/'manifest.json').read_text(encoding='utf-8'))
    if manifest['model_version']!=MODEL_VERSION or manifest['features']!=list(FEATURE_NAMES):
        raise ValueError('model contract/version mismatch')
    path=artifact_dir/'model.joblib'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=manifest['artifact_sha256']:
        raise ValueError('model artifact checksum mismatch')
    if not days:
        raise ValueError('no feature days')
    latest=max(days,key=lambda d:d.local_date)
    if not latest.valid_forecast:
        raise ValueError('latest day lacks sufficient baseline or observations')
    model=joblib.load(path)  # ONLY trusted artifacts; hash is integrity, not authentication.
    x=np.asarray([[latest.values[k] for k in FEATURE_NAMES]],dtype=float)
    score=bounded(float(model.predict(x)[0]))
    return ForecastResult(latest.local_date+timedelta(days=1),round(score,2),MODEL_VERSION,'unvalidated synthetic estimate')
