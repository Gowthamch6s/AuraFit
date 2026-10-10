"""Contract and route tests with a mocked DB boundary; not PostgreSQL integration tests."""
import importlib
import sys
import types
from uuid import uuid4
from datetime import date

# CI environments should install psycopg; lightweight offline test image lacks it.
if importlib.util.find_spec('psycopg') is None:
    dummy=types.ModuleType('psycopg')
    class Error(Exception): pass
    dummy.Error=Error
    dummy.connect=lambda *a, **k: (_ for _ in ()).throw(Error('not connected'))
    errors=types.ModuleType('psycopg.errors')
    for n in ('UniqueViolation','ForeignKeyViolation','CheckViolation'):
        setattr(errors,n,type(n,(Error,),{}))
    sys.modules['psycopg']=dummy
    sys.modules['psycopg.errors']=errors

from fastapi.testclient import TestClient
from fastapi import HTTPException
from aurafit import api
from aurafit.models import IngestBatch, UserCreate

client=TestClient(api.app)
USER=uuid4()

def auth_override():
    return api.Principal(user_id=USER)

def test_openapi_contains_required_routes():
    paths=client.get('/openapi.json').json()['paths']
    assert {'/data/ingest','/metrics/allostatic-load','/predictions/next-day','/targets/adaptive','/health/live','/health/ready'} <= paths.keys()

def test_liveness():
    assert client.get('/health/live').json()=={'status':'alive'}

def test_requires_authentication():
    assert client.get('/targets/adaptive').status_code==401
    assert client.get('/metrics/allostatic-load').json()['error']['code']=='HTTP_401'

def test_bad_batch_returns_422():
    api.app.dependency_overrides[api.principal]=auth_override
    try:
        response=client.post('/data/ingest',json={'user_id':str(USER),'batch_id':str(uuid4()),'source':'test','ticks':[]})
        assert response.status_code==422
    finally:
        api.app.dependency_overrides.clear()

def test_cross_user_ingest_rejected():
    api.app.dependency_overrides[api.principal]=auth_override
    try:
        payload={'user_id':str(uuid4()),'batch_id':str(uuid4()),'source':'test','subjective_checkins':[{'checkin_id':str(uuid4()),'observed_at':'2026-09-03T14:00:00Z','mood':5,'energy':6}]}
        response=client.post('/data/ingest',json=payload)
        assert response.status_code==403
    finally:
        api.app.dependency_overrides.clear()

def test_model_schema_disallows_extra():
    from pydantic import ValidationError
    try:
        api.ForecastResponse(forecast_date=date(2026,9,2),status='abstain', surprise='yes')
    except ValidationError: pass
    else: raise AssertionError('extra field not rejected')

def test_no_calorie_or_activity_prescription_without_baseline():
    from aurafit.models import DailyBaseline,DailyAssessment
    from aurafit.features import FeatureDay
    user=UserCreate(user_id=USER,timezone='America/New_York')
    d=date(2026,9,10)
    baseline=DailyBaseline(user_id=USER,local_date=d,timezone=user.timezone,baseline_version='median7-prior-v1',lookback_days=7,sample_days=0)
    assessment=DailyAssessment(user_id=USER,local_date=d,model_version='heuristic-experimental-v1',baseline_version='median7-prior-v1',allostatic_load_index=30,recovery_score=70,overreach_risk_score=40)
    target=api.conservative_targets(FeatureDay(d,baseline,assessment,{},False),user)
    assert target.step_target is None
    assert target.active_energy_target_kcal is None
    assert target.energy_budget_score is None
    assert api.TargetsResponse(targets=target).calorie_intake_target_kcal is None

def test_insufficient_prediction_does_not_deploy_model(monkeypatch):
    api.app.dependency_overrides[api.principal]=auth_override
    class Conn:
        def __enter__(self): return self
        def __exit__(self,*args): return None
    monkeypatch.setattr(api,'_connection',lambda:Conn())
    monkeypatch.setattr(api,'_completed_day',lambda *_:date(2026,9,12))
    monkeypatch.setattr(api,'_feature_for',lambda *_:None)
    try:
        response=client.get('/predictions/next-day')
        assert response.status_code==200
        assert response.json()['predicted_recovery_score'] is None
    finally:
        api.app.dependency_overrides.clear()

def test_agent_routes_are_documented_and_secured():
    paths=client.get('/openapi.json').json()['paths']
    assert {'/agent/chat','/agent/chat/stream'} <= paths.keys()
    assert client.post('/agent/chat',json={'message':'HRV today?'}).status_code==401
    assert client.post('/agent/chat/stream',json={'message':'HRV today?'}).status_code==401

def test_agent_safety_redirect_does_not_read_database(monkeypatch):
    api.app.dependency_overrides[api.principal]=auth_override
    monkeypatch.setattr(api,'_connection',lambda: (_ for _ in ()).throw(AssertionError('should not access db')))
    try:
        result=client.post('/agent/chat',json={'message':'How many calories should I eat?'})
        assert result.status_code==200
        assert result.json()['status']=='safety_redirect'
        assert result.json()['evidence']==[]
    finally:
        api.app.dependency_overrides.clear()

def test_agent_sse_is_framed_and_contains_final_payload(monkeypatch):
    api.app.dependency_overrides[api.principal]=auth_override
    monkeypatch.setattr(api,'_connection',lambda: (_ for _ in ()).throw(AssertionError('should not access db')))
    try:
        with client.stream('POST','/agent/chat/stream',json={'message':'I have chest pain'}) as stream:
            body=''.join(stream.iter_text())
            assert stream.status_code==200
            assert stream.headers['content-type'].startswith('text/event-stream')
            assert 'event: metadata' in body and 'event: text' in body and 'event: final' in body
    finally:
        api.app.dependency_overrides.clear()
