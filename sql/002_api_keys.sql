BEGIN;
CREATE TABLE IF NOT EXISTS aurafit.api_keys (
  key_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  token_sha256 char(64) NOT NULL UNIQUE,
  created_at timestamptz NOT NULL DEFAULT now(),
  revoked_at timestamptz,
  CONSTRAINT token_digest_hex CHECK(token_sha256 ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS api_keys_user_idx ON aurafit.api_keys(user_id) WHERE revoked_at IS NULL;
INSERT INTO aurafit.schema_migrations(version) VALUES ('002_api_keys') ON CONFLICT DO NOTHING;
COMMIT;
