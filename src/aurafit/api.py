"""Authenticated Phase 4 HTTP API. All wellness scores are experimental."""
from __future__ import annotations
import hashlib
import os
from datetime import datetime, timedelta, timezone, date
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

import psycopg
from psycopg.errors import UniqueViolation, ForeignKeyViolation, CheckViolation
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, ConfigDict

from .models import IngestBatch, UserCreate, MetricTick, SleepSession, NutritionEntry, SubjectiveCheckin, DailyAssessment, AdaptiveTargets
from .loader import batch_digest
from .features import build_features, FeatureDay
from .agent import ChatRequest, ChatResponse

DISCLAIMER = 'Experimental, nonclinical wellness estimates; not a diagnosis or medical advice.'

class ApiModel(BaseModel):
    model_config=ConfigDict(extra='forbid', allow_inf_nan=False)

class ErrorDetail(ApiModel):
    code: str
    message: str

class ErrorResponse(ApiModel):
    error: ErrorDetail

class IngestResponse(ApiModel):
    batch_id: UUID
    status: str
    observations: int = Field(ge=0)

class AssessmentResponse(ApiModel):
    assessment: DailyAssessment
    sufficient_data: bool
    disclaimer: str = DISCLAIMER

class ForecastResponse(ApiModel):
    forecast_date: date
    predicted_recovery_score: float | None = Field(default=None, ge=0, le=100)
    status: str
    model_version: str | None = None
    disclaimer: str = DISCLAIMER

class TargetsResponse(ApiModel):
    targets: AdaptiveTargets
    calorie_intake_target_kcal: None = None
    disclaimer: str = DISCLAIMER

class ReadyResponse(ApiModel):
    status: str

class Principal(ApiModel):
    user_id: UUID


def _db_url() -> str:
    value = os.environ.get('AURAFIT_DATABASE_URL')
    if not value:
        raise HTTPException(503, detail='Database is not configured')
    return value


def _connection():
    return psycopg.connect(_db_url(), connect_timeout=5)


def principal(authorization: Annotated[str | None, Header()] = None) -> Principal:
    """Authenticate a random bearer token via its SHA256 digest in the database."""
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, detail='Bearer token required', headers={'WWW-Authenticate':'Bearer'})
    key = authorization[7:]
    if not (key.startswith('af_') and len(key) == 67 and all(c in '0123456789abcdef' for c in key[3:])):
        raise HTTPException(401, detail='Invalid bearer token', headers={'WWW-Authenticate':'Bearer'})
    digest=hashlib.sha256(key.encode()).hexdigest()
    try:
        with _connection() as conn:
            row=conn.execute('SELECT user_id FROM aurafit.api_keys WHERE token_sha256=%s AND revoked_at IS NULL', (digest,)).fetchone()
    except psycopg.Error:
        raise HTTPException(503, detail='Database unavailable') from None
    if not row:
        raise HTTPException(401, detail='Invalid bearer token', headers={'WWW-Authenticate':'Bearer'})
    return Principal(user_id=row[0])


def _load_history(conn, user_id: UUID, as_of: date) -> tuple[UserCreate, list[IngestBatch]]:
    """Load 45 days ending at local day's start, never data later than evaluation date."""
    row=conn.execute('SELECT timezone,daily_sleep_target_minutes FROM aurafit.users WHERE user_id=%s', (user_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail='User not found')
    user=UserCreate(user_id=user_id, timezone=row[0], daily_sleep_target_minutes=row[1])
    tz=ZoneInfo(user.timezone)
    start=datetime.combine(as_of-timedelta(days=45),datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    finish=datetime.combine(as_of+timedelta(days=1),datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    ticks=[MetricTick(device_id=r[0],observed_at=r[1],metric=r[2],value=r[3]) for r in conn.execute('SELECT device_id,observed_at,metric,value FROM aurafit.metric_ticks WHERE user_id=%s AND observed_at >= %s AND observed_at < %s ORDER BY observed_at', (user_id,start,finish))]
    sleep=[SleepSession(session_id=r[0],device_id=r[1],started_at=r[2],ended_at=r[3],deep_minutes=r[4],rem_minutes=r[5],light_minutes=r[6],awake_minutes=r[7]) for r in conn.execute('SELECT session_id,device_id,started_at,ended_at,deep_minutes,rem_minutes,light_minutes,awake_minutes FROM aurafit.sleep_sessions WHERE user_id=%s AND ended_at >= %s AND ended_at < %s AND started_at >= %s ORDER BY ended_at', (user_id,start,finish,start-timedelta(days=1)))]
    food=[NutritionEntry(entry_id=r[0],consumed_at=r[1],calories_kcal=r[2],carbs_g=r[3],protein_g=r[4],fat_g=r[5]) for r in conn.execute('SELECT entry_id,consumed_at,calories_kcal,carbs_g,protein_g,fat_g FROM aurafit.nutrition_entries WHERE user_id=%s AND consumed_at >= %s AND consumed_at < %s ORDER BY consumed_at', (user_id,start,finish))]
    checkins=[SubjectiveCheckin(checkin_id=r[0],observed_at=r[1],mood=r[2],energy=r[3]) for r in conn.execute('SELECT checkin_id,observed_at,mood,energy FROM aurafit.subjective_checkins WHERE user_id=%s AND observed_at >= %s AND observed_at < %s ORDER BY observed_at', (user_id,start,finish))]
    if not any((ticks,sleep,food,checkins)):
        return user, []
    from uuid import uuid5, NAMESPACE_URL
    batch=IngestBatch(batch_id=uuid5(NAMESPACE_URL,f'history:{user_id}:{as_of}'), user_id=user_id, source='historical_query',ticks=ticks,sleep_sessions=sleep,nutrition_entries=food,subjective_checkins=checkins)
    return user,[batch]


def _completed_day(conn, user_id: UUID, requested: date | None) -> date:
    row=conn.execute('SELECT timezone FROM aurafit.users WHERE user_id=%s',(user_id,)).fetchone()
    if not row:
        raise HTTPException(404,detail='User not found')
    yesterday=datetime.now(ZoneInfo(row[0])).date()-timedelta(days=1)
    if requested is not None and requested > yesterday:
        raise HTTPException(422, detail='Only completed local days may be assessed')
    if requested is not None and requested < yesterday-timedelta(days=365):
        raise HTTPException(422, detail='Requested date outside supported one-year window')
    return requested or yesterday


def _feature_for(user_id: UUID, target: date) -> FeatureDay:
    try:
        with _connection() as conn:
            user,batches=_load_history(conn,user_id,target)
        if not batches:
            raise HTTPException(404,detail='No observations available')
        days=build_features(user,batches)
        found=next((d for d in days if d.local_date==target),None)
        if found is None:
            raise HTTPException(404,detail='No observations on requested date')
        return found
    except psycopg.Error:
        raise HTTPException(503,detail='Database unavailable') from None


def _insert_batch(batch: IngestBatch) -> IngestResponse:
    """One transaction per batch; serialize same ID, then check digest for replay."""
    digest=batch_digest(batch)
    try:
        with _connection() as conn:
            with conn.transaction():
                conn.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(str(batch.batch_id),))
                exists=conn.execute('SELECT user_id,source,payload_sha256 FROM aurafit.ingestion_batches WHERE batch_id=%s',(batch.batch_id,)).fetchone()
                if exists:
                    if exists!=(batch.user_id,batch.source,digest):
                        raise HTTPException(409,detail='Batch identifier already used for different contents')
                    return IngestResponse(batch_id=batch.batch_id,status='already_processed',observations=0)
                ids={x.device_id for x in batch.ticks}|{x.device_id for x in batch.sleep_sessions}
                for device_id in ids:
                    authorized=conn.execute('SELECT 1 FROM aurafit.devices WHERE device_id=%s AND user_id=%s',(device_id,batch.user_id)).fetchone()
                    if not authorized:
                        raise HTTPException(422,detail='Device not registered to authenticated user')
                conn.execute('INSERT INTO aurafit.ingestion_batches(batch_id,user_id,source,payload_sha256) VALUES (%s,%s,%s,%s)',(batch.batch_id,batch.user_id,batch.source,digest))
                rows=[('metric_ticks','(user_id,device_id,observed_at,metric,value,batch_id)',[(batch.user_id,t.device_id,t.observed_at,t.metric.value,t.value,batch.batch_id) for t in batch.ticks]),
                      ('sleep_sessions','(user_id,device_id,session_id,started_at,ended_at,deep_minutes,rem_minutes,light_minutes,awake_minutes,batch_id)',[(batch.user_id,s.device_id,s.session_id,s.started_at,s.ended_at,s.deep_minutes,s.rem_minutes,s.light_minutes,s.awake_minutes,batch.batch_id) for s in batch.sleep_sessions]),
                      ('nutrition_entries','(user_id,entry_id,consumed_at,calories_kcal,carbs_g,protein_g,fat_g,batch_id)',[(batch.user_id,n.entry_id,n.consumed_at,n.calories_kcal,n.carbs_g,n.protein_g,n.fat_g,batch.batch_id) for n in batch.nutrition_entries]),
                      ('subjective_checkins','(user_id,checkin_id,observed_at,mood,energy,batch_id)',[(batch.user_id,c.checkin_id,c.observed_at,c.mood,c.energy,batch.batch_id) for c in batch.subjective_checkins])]
                with conn.cursor() as cur:
                    for table,columns,values in rows:
                        if values:
                            marks='('+','.join(['%s']*len(values[0]))+')'
                            cur.executemany(f'INSERT INTO aurafit.{table} {columns} VALUES {marks}',values)
        return IngestResponse(batch_id=batch.batch_id,status='inserted',observations=sum(map(len,(batch.ticks,batch.sleep_sessions,batch.nutrition_entries,batch.subjective_checkins))))
    except HTTPException:
        raise
    except (UniqueViolation, ForeignKeyViolation, CheckViolation):
        raise HTTPException(409,detail='Data conflict or constraint violation') from None
    except psycopg.Error:
        raise HTTPException(503,detail='Database unavailable') from None


def conservative_targets(day: FeatureDay, user: UserCreate, typical_steps: int | None=None) -> AdaptiveTargets:
    """Conservative optional movement goals, never a calorie intake prescription."""
    evidence=day.assessment.evidence
    ready=bool(day.valid_forecast)
    risk=day.assessment.overreach_risk_score
    # Never raise someone's target on the basis of this experimental risk score.
    if not ready or typical_steps is None or typical_steps <= 0:
        steps=None
    else:
        steps=round(min(12000,max(1000,typical_steps))*(0.75 if risk>=60 else 0.9 if risk>=35 else 1.0))
    return AdaptiveTargets(user_id=user.user_id,local_date=day.local_date,model_version=day.assessment.model_version,
         step_target=steps,active_energy_target_kcal=None,energy_budget_score=None,
         rationale={'basis':'optional conservative movement reference','sufficient_data':ready,'experimental_risk_score':risk,
          'calorie_intake_target_kcal':None,'safety':'No restrictive calorie goals, metabolic deficits, exercise quotas or medical instructions.'})


def create_app() -> FastAPI:
    app=FastAPI(title='AuraFit Wellness API',version='0.5.0',description=DISCLAIMER)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        message=exc.detail if isinstance(exc.detail,str) else 'Request rejected'
        headers=exc.headers or {}
        return JSONResponse(status_code=exc.status_code,content={'error':{'code':f'HTTP_{exc.status_code}','message':message}},headers=headers)

    @app.get('/health/live',response_model=ReadyResponse,tags=['operations'])
    def liveness():
        return ReadyResponse(status='alive')

    @app.get('/health/ready',response_model=ReadyResponse,tags=['operations'],responses={503:{'model':ErrorResponse}})
    def readiness():
        try:
            with _connection() as conn:
                conn.execute('SELECT 1 FROM aurafit.api_keys LIMIT 1')
            return ReadyResponse(status='ready')
        except (psycopg.Error,HTTPException):
            raise HTTPException(503,detail='Database is not ready') from None

    @app.post('/data/ingest',response_model=IngestResponse,tags=['ingestion'],responses={409:{'model':ErrorResponse},422:{'model':ErrorResponse}})
    def ingest(batch:IngestBatch, actor:Annotated[Principal,Depends(principal)]):
        if batch.user_id != actor.user_id:
            raise HTTPException(403,detail='User scope mismatch')
        return _insert_batch(batch)

    @app.get('/metrics/allostatic-load',response_model=AssessmentResponse,tags=['assessment'])
    def metrics(actor:Annotated[Principal,Depends(principal)],local_date:date|None=None):
        with _connection() as conn:
            target=_completed_day(conn,actor.user_id,local_date)
        day=_feature_for(actor.user_id,target)
        if not day.valid_forecast:
            raise HTTPException(409,detail='Insufficient complete data or historical baseline; no score reported')
        return AssessmentResponse(assessment=day.assessment,sufficient_data=True)

    @app.get('/predictions/next-day',response_model=ForecastResponse,tags=['predictions'])
    def prediction(actor:Annotated[Principal,Depends(principal)],local_date:date|None=None):
        with _connection() as conn:
            target=_completed_day(conn,actor.user_id,local_date)
        day=_feature_for(actor.user_id,target)
        # Only a locally controlled, explicitly enabled model may be deserialized.
        # Model evaluation is not a clinical validation; API labels remain experimental.
        artifact=os.environ.get('AURAFIT_TRUSTED_MODEL_DIR')
        if artifact and day.valid_forecast:
            from pathlib import Path
            from .predictive import forecast
            try:
                result=forecast([day],Path(artifact))
            except (OSError,ValueError,KeyError) as exc:
                raise HTTPException(503,detail='Configured forecasting model is unavailable') from exc
            return ForecastResponse(forecast_date=result.forecast_date,predicted_recovery_score=result.recovery,
                status='experimental_opt_in',model_version=result.model_version)
        return ForecastResponse(forecast_date=target+timedelta(days=1),predicted_recovery_score=None,
            status='model_not_deployed_insufficient_validation',model_version=None)

    @app.get('/targets/adaptive',response_model=TargetsResponse,tags=['targets'])
    def targets(actor:Annotated[Principal,Depends(principal)],local_date:date|None=None):
        with _connection() as conn:
            target=_completed_day(conn,actor.user_id,local_date)
            user,_=_load_history(conn,actor.user_id,target)
        day=_feature_for(actor.user_id,target)
        # Steps are never guessed from one observation; configured goal absent => abstain.
        result=conservative_targets(day,user)
        return TargetsResponse(targets=result)
    @app.post('/agent/chat',response_model=ChatResponse,tags=['agent'])
    def agent_chat(body: 'ChatRequest', actor:Annotated[Principal,Depends(principal)]):
        """Scoped wellness chat. Only completed days; no arbitrary SQL or direct model tool access."""
        from .agent import PostgresEvidenceStore, OptionalLangChainWriter, intent, respond
        category=intent(body.message)
        if category in {'urgent','medical','injection','other'}:
            return respond(body.message,[])
        try:
            with _connection() as conn:
                as_of=_completed_day(conn,actor.user_id,body.as_of)
            evidence=PostgresEvidenceStore(_connection).fetch(actor.user_id,as_of,body.lookback_days)
        except psycopg.Error:
            raise HTTPException(503,detail='Evidence database unavailable') from None
        return respond(body.message,evidence,OptionalLangChainWriter())

    @app.post('/agent/chat/stream',tags=['agent'])
    def agent_stream(body: 'ChatRequest', actor:Annotated[Principal,Depends(principal)]):
        """SSE with prevalidated response. Streaming starts only after evidence validation."""
        import json
        response=agent_chat(body,actor)
        payload=response.model_dump(mode='json')
        def events():
            yield 'event: metadata\ndata: '+json.dumps({'status':payload['status'],'citations':payload['citations']})+'\n\n'
            answer=payload['answer']
            for start in range(0,len(answer),180):
                yield 'event: text\ndata: '+json.dumps({'text':answer[start:start+180]})+'\n\n'
            yield 'event: final\ndata: '+json.dumps(payload)+'\n\n'
        return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-store','X-Accel-Buffering':'no'})

    return app

app=create_app()
