# Phase 7 — Local deployment hardening and integration checks

## Changes
- Production web build served by Nginx; same-origin reverse proxy forwards API traffic and SSE without proxy buffering.
- Database credentials are configured through `.env` (not embedded in Compose), and the DB and API are not published to the host network.
- Health-dependent container startup, restart policy, read-only API/web filesystems, and no-new-privileges.
- Security-related response headers; no-store cache policy (suitable for sensitive dashboards).
- Public health and anonymous access-boundary smoke checks.

## Run
1. `cp .env.example .env`; replace the two password placeholders with the **same** strong URL-safe password. Do not commit `.env`.
2. `docker compose up -d --build db`.
3. Apply the migrations from the **host** using a virtualenv and the localhost port **only if you deliberately expose PostgreSQL**, or (recommended) run the migration inside the private Compose network:
   Use the following command (the shell expansion runs inside the container):
   `docker compose run --rm api sh -c 'aurafit migrate --database-url "$AURAFIT_DATABASE_URL"'`.
   To bootstrap from scratch, database must be healthy before migration. The API container health endpoint uses only liveness so it can start before schema creation.
4. `docker compose up -d --build`.
5. `python scripts/smoke.py http://127.0.0.1:8080`.
6. Visit `http://127.0.0.1:8080/` for the dashboard. Start in Demo mode, then register a user/device and provision a token using the Phase 4 README commands.

## Production boundary
This Compose configuration binds Nginx to loopback by default. It is **not public internet deployment**. For production use a TLS-terminating reverse proxy or managed ingress, secret manager, pinned/reviewed container digests, backups, observability, schema migration orchestration, database-least-privilege roles (separate read-only evidence role), API rate limiting, independent security review, privacy impact assessment, and clinical safety review. Avoid publicly exposing `/docs` or debug information without deliberate access policy. Do not use synthetic model metrics for clinical or nutritional recommendations.

## Validation limitations
Local Python/TypeScript tests do not demonstrate TimescaleDB migration compatibility, concurrent ingestion correctness, API security under hostile traffic, or frontend browser behavior. Run the entire stack, smoke test, and external penetration/access-control review before real user data is permitted.
