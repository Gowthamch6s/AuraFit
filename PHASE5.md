# AuraFit Phase 5 — read-only grounded wellness assistant

Phase 5 **adds** `src/aurafit/agent.py`, chat endpoints in `api.py`, tests, and optional LangChain dependencies. Phase 1 SQL DDL and Phase 1–4 Pydantic/contracts remain unchanged.

## Endpoints

- `POST /agent/chat`: authenticated, structured JSON response with exact metric evidence IDs and curated reference metadata.
- `POST /agent/chat/stream`: authenticated SSE with `metadata`, `text`, `final` events. The answer is **fully generated and validated before streaming**; streaming chunks are delivery only, not live inference tokens.

```json
{"message":"Why is my HRV different this week?", "as_of":"2026-09-30", "lookback_days":7}
```

Call with your Phase 4 bearer token and `/docs` for interactive OpenAPI:

```bash
curl -sS -X POST http://127.0.0.1:8000/agent/chat \
 -H "Authorization: Bearer ${AURAFIT_TOKEN}" \
 -H 'Content-Type: application/json' \
 -d '{"message":"How is my HRV trending?","as_of":"2026-09-30","lookback_days":7}'

curl -N -X POST http://127.0.0.1:8000/agent/chat/stream \
 -H "Authorization: Bearer ${AURAFIT_TOKEN}" \
 -H 'Content-Type: application/json' \
 -d '{"message":"What does my sleep look like?","as_of":"2026-09-30"}'
```

## RAG design

- **Time-series retrieval:** fixed parameterized SQL querying only the authenticated `user_id`, filtered by local-day UTC boundaries. No SQL-generation agent and no arbitrary query execution. Supports HRV, resting HR, stress, steps, active energy, and sleep duration.
- **Baseline:** when at least three prior observed days exist, mean of *available daily aggregates* in the previous 3–30 calendar-day window. This is a **trend descriptor**, not the separately engineered Phase 3 median baseline. Missing observations are never silently imputed. Interval steps/energy are summed; HRV/RHR/stress are averaged.
- **Knowledge retrieval:** a small curated allowlisted health-reference collection used for background links. These are source pointers, not a live-updated document index; verify URL/content periodically. This is a bounded retrieval pattern, not a full vector-database RAG deployment.
- **Generation:** deterministic, exact-value, cited responses by default. With optional LangChain dependencies `pip install -e '.[llm]'`, `AURAFIT_AGENT_MODE=langchain`, and `OPENAI_API_KEY`, LangChain's `ChatOpenAI` can rewrite a precomputed paragraph. It has **no tools, database credentials, raw records, user ID, or access to unscoped data**. Numeric/citation integrity checks fall back to the exact deterministic answer on failure, though these checks are not a substitute for a comprehensive safety evaluation.
- **Safety:** emergency and medical-intent screens precede data access; unsupported prompts return no evidence. Experimental wellness disclaimer on every response; no dietary calorie prescriptions or diagnostic claims. No raw input logging in the new module.

## Operational security

This is a **reference service, not production-certified software**. Before public deployment implement TLS/auth gateway, per-user request rate limits and quotas, regulated-data privacy impact assessment, retention/deletion policies, authorization audits, LLM provider agreements and zero-retention settings as appropriate, comprehensive red-team evaluation, egress controls, SQL read-only credentials for agent queries, observability with redaction, and live TimescaleDB integration tests. The same Phase 4 full-privilege connection currently powers the read-only fixed SQL evidence adapter, so database-enforced least-privilege is not yet achieved. Recheck the intent classifier for adversarial paraphrases, multilingual requests, and clinical escalations. Not a clinical decision support tool.

## Tests

`pytest -q` exercises the Phase 1–4 suite plus evidence attribution, scoped SQL, missing data, injection strings, urgent/medical safety redirects, chat schema, authentication, and SSE formatting. Offline tests mock database access. Live TimescaleDB and optional model provider integration remain untested.
