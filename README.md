# AuraFit — Phase 4 (FastAPI service)

Extends the **unchanged Phase 1 schema/contracts**, Phase 2 generator, and Phase 3 experimental ML pipeline. The only additive database migration is `002_api_keys.sql`.

**Not a clinical device.** Scores are experimental wellness heuristics. This code has not been penetration-tested, independently validated with real-world data, or run end-to-end against TimescaleDB in the supplied environment. Do not deploy publicly without security review, TLS termination, secret management, connection pooling, rate limiting, observability, backups and retention/privacy controls.

## Structure

- `src/aurafit/api.py`: FastAPI app, Pydantic output contracts, authenticated endpoints, transactional idempotent ingestion, history reconstruction, adaptive target safety
- `src/aurafit/key_cli.py`: mint one opaque 256-bit random bearer key, store SHA-256 digest
- `sql/001_initial.sql`: original schema unchanged
- `sql/002_api_keys.sql`: additive auth table and migration record
- `tests/test_api.py`: contract tests with DB boundary mocked, plus all original tests
- `Dockerfile`, `docker-compose.yml`: local deployment

## Prerequisites

Python >= 3.12, Docker and Docker Compose, PostgreSQL 17/TimescaleDB, network access to install project dependencies.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\\Scripts\\Activate.ps1
python -m pip install -e '.[dev]'
docker compose up -d db
aurafit migrate --database-url 'postgresql://aurafit:local_development_only@localhost:5433/aurafit'
```

Create demo user and device (the original Phase 1 examples):

```bash
python - <<'PY'
import json, psycopg
user=json.load(open('examples/user.json'))
device=json.load(open('examples/device.json'))
with psycopg.connect('postgresql://aurafit:local_development_only@localhost:5433/aurafit') as conn:
    conn.execute('INSERT INTO aurafit.users(user_id,timezone,daily_sleep_target_minutes) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO NOTHING', (user['user_id'],user['timezone'],user.get('daily_sleep_target_minutes',480)))
    conn.execute('INSERT INTO aurafit.devices(device_id,user_id,provider,model) VALUES(%s,%s,%s,%s) ON CONFLICT(device_id) DO NOTHING', (device['device_id'],device['user_id'],device['provider'],device.get('model')))
PY
```

Provision token (copy user ID from `examples/user.json`):

```bash
aurafit-key --database-url 'postgresql://aurafit:local_development_only@localhost:5433/aurafit' --user-id YOUR_USER_UUID
```

Save resulting token in `AURAFIT_TOKEN` *in your shell*, not in the repository. Then start:

```bash
export AURAFIT_DATABASE_URL='postgresql://aurafit:local_development_only@localhost:5433/aurafit'
uvicorn aurafit.api:app --host 127.0.0.1 --port 8000
```

For Windows PowerShell, use `$env:AURAFIT_DATABASE_URL = '...'` and `$env:AURAFIT_TOKEN = '...'`.

Alternatively `docker compose up --build -d api` after migrating and provisioning locally. The sample compose file binds API and database to `127.0.0.1` for local-only access. Its hardcoded development credential is **not suitable** for shared or production systems. Use a managed secret store and TLS in deployment.

## Endpoints

| Route | Auth | Semantics |
|---|---|---|
| `GET /health/live` | none | process liveness |
| `GET /health/ready` | none | database/auth-schema readiness |
| `POST /data/ingest` | bearer | validated Phase 1 `IngestBatch`; atomic inserts; same ID+body -> `already_processed`; conflicting ID -> 409 |
| `GET /metrics/allostatic-load?local_date=YYYY-MM-DD` | bearer | score for a **completed local day** with sufficient baseline and data; 409 if insufficient |
| `GET /predictions/next-day?local_date=...` | bearer | explicit abstention unless trusted model directory configured |
| `GET /targets/adaptive?local_date=...` | bearer | optional conservative targets; absent baseline => no step target; never calorie-intake prescription |

OpenAPI: <http://127.0.0.1:8000/docs> and <http://127.0.0.1:8000/openapi.json>.

Example authenticated ingestion:

```bash
curl -X POST 'http://127.0.0.1:8000/data/ingest' \
  -H "Authorization: Bearer ${AURAFIT_TOKEN}" \
  -H 'Content-Type: application/json' \
  --data-binary @examples/ingest_batch.json
```

```bash
curl -H "Authorization: Bearer ${AURAFIT_TOKEN}" \
  'http://127.0.0.1:8000/metrics/allostatic-load?local_date=2026-09-15'
```

Only the most recent 45 days of raw records relative to the requested date are fetched for scoring. Readiness scores are derived on demand and are **not automatically persisted** on GET. Phase 3 `aurafit-ml` and `persist_feature_days` remain available for offline materialization.

For explicit **nonclinical developer testing only**, set `AURAFIT_TRUSTED_MODEL_DIR` to a trusted, locally generated Phase 3 model directory. The `joblib` artifact uses pickle deserialization; never mount untrusted artifacts. SHA-256 integrity checking does not authenticate the model producer. By default the API abstains because the Phase 3 synthetic model underperformed the naive baseline.

## Tests

```bash
pytest -q
```

Local test run: 41 Python tests passed, including 8 API contract tests. Database integration, concurrency/load testing, security hardening, and Docker startup are **not tested** in this environment. DB-dependent API tests use a fake dependency boundary; do not mistake this for an integration pass.

## Safety boundaries

- User scope authenticated via high-entropy opaque bearer keys, digest-only storage; key rotation/revocation via database `revoked_at` update.
- Scores refer to completed local days, not live clinical allostatic load.
- Optional step targets are not exercise prescriptions. No targets are inferred without a configured typical-step reference; no calorie-intake or weight-loss targets are issued.
- Unknown/missing data is not assumed normal. Incomplete daily assessments return 409 rather than a false reassuring readiness score.
- Privacy/compliance obligations (including HIPAA where applicable) require organizational controls beyond this reference implementation.

## Phase 5 extension: grounded conversational agent

See [`PHASE5.md`](PHASE5.md) for the read-only evidence retrieval, optional LangChain wording adapter, authenticated `/agent/chat` and `/agent/chat/stream` APIs, example cURL requests, SSE semantics, safety caveats, and operational requirements. These additions do not alter the earlier schema or endpoint contracts.

## Phase 6 — React Dashboard

A React/TypeScript dashboard is available in [`web/`](web/README.md). Run `cd web && npm install && npm run dev`. It starts in clearly labeled demo mode and can connect to this FastAPI backend via a transient bearer token. See the web README for operational and security constraints.
