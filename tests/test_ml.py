from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path
import json
import pytest
from aurafit.synthetic import GeneratorConfig, generate
from aurafit.features import build_features, robust_z, FEATURE_NAMES
from aurafit.predictive import supervised_rows, train, forecast
from aurafit.models import DailyAssessment

@pytest.fixture(scope='module')
def days():
    ds=generate(GeneratorConfig(days=30,missing_rate=0,seed=42))
    return build_features(ds.user,ds.batches)

def test_count_and_contract(days):
    assert len(days)==30
    assert all(set(d.values)==set(FEATURE_NAMES) for d in days)
    assert all(0<=d.assessment.allostatic_load_index<=100 for d in days)
    assert all(0<=d.assessment.recovery_score<=100 for d in days)
    assert all(0<=d.assessment.overreach_risk_score<=100 for d in days)

def test_prior_only_baseline(days):
    assert days[0].baseline.hrv_rmssd_median_ms is None
    assert days[1].baseline.sample_days==1
    assert days[7].baseline.sample_days==7
    assert days[3].valid_forecast

def test_future_edits_cannot_change_past():
    ds=generate(GeneratorConfig(days=30,seed=33,missing_rate=0))
    first=build_features(ds.user,ds.batches)
    second=build_features(ds.user,ds.batches[:14])
    assert [d.assessment for d in first[:14]]==[d.assessment for d in second]
    assert [d.baseline for d in first[:14]]==[d.baseline for d in second]

def test_reproducible(days):
    ds=generate(GeneratorConfig(days=30,missing_rate=0,seed=42))
    assert days==build_features(ds.user,ds.batches)

def test_consecutive_pairing(days):
    X,y,dates=supervised_rows(days)
    assert X.shape[1]==len(FEATURE_NAMES)
    assert dates[0]==days[3].local_date
    assert y[0]==days[4].assessment.recovery_score

def test_missingness_rejects_train_pairs():
    ds=generate(GeneratorConfig(days=30,missing_rate=.3))
    days=build_features(ds.user,ds.batches)
    X,y,_=supervised_rows(days)
    assert len(y)<20

def test_robust_z_edge():
    assert robust_z(40,[])==0
    assert robust_z(40,[50,50,50])<0

def test_train_evaluate_predict(tmp_path,days):
    report=train(days,tmp_path)
    assert report['train_end'] < report['test_start']
    assert report['test_rows']>=3
    assert report['model']['mae']>=0
    assert report['naive_median']['mae']>=0
    assert len(report['permutation_importance_on_holdout'])==len(FEATURE_NAMES)
    prediction=forecast(days,tmp_path)
    assert prediction.forecast_date==days[-1].local_date+timedelta(days=1)
    assert 0<=prediction.recovery<=100

def test_manifest_integrity(tmp_path,days):
    train(days,tmp_path)
    path=tmp_path/'model.joblib'
    path.write_bytes(path.read_bytes()+b'corrupt')
    with pytest.raises(ValueError,match='checksum'):
        forecast(days,tmp_path)

def test_insufficient_history(tmp_path,days):
    with pytest.raises(ValueError,match='need'):
        train(days[:10],tmp_path)

def test_missing_latest_rejects(tmp_path,days):
    train(days,tmp_path)
    changed=replace(days[-1],valid_forecast=False)
    with pytest.raises(ValueError,match='latest day'):
        forecast(days[:-1]+[changed],tmp_path)

def test_overreach_has_worse_end_than_recovery():
    over=generate(GeneratorConfig(days=30,scenario='overreach',seed=42,missing_rate=0))
    recover=generate(GeneratorConfig(days=30,scenario='recovery',seed=42,missing_rate=0))
    a=build_features(over.user,over.batches); b=build_features(recover.user,recover.batches)
    assert sum(x.assessment.recovery_score for x in a[-5:])/5 < sum(x.assessment.recovery_score for x in b[-5:])/5
