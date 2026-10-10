BEGIN;
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS aurafit;
CREATE TABLE IF NOT EXISTS aurafit.schema_migrations (
  version text PRIMARY KEY,
  applied_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE aurafit.users (
  user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  timezone text NOT NULL CHECK (length(timezone) BETWEEN 3 AND 64),
  daily_sleep_target_minutes smallint NOT NULL DEFAULT 480 CHECK (daily_sleep_target_minutes BETWEEN 360 AND 600),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE aurafit.devices (
  device_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  provider text NOT NULL CHECK (length(provider) BETWEEN 1 AND 64),
  model text CHECK (model IS NULL OR length(model) <= 128),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (device_id, user_id)
);
CREATE INDEX devices_user_idx ON aurafit.devices(user_id);
CREATE TABLE aurafit.ingestion_batches (
  batch_id uuid PRIMARY KEY,
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  source text NOT NULL CHECK (length(source) BETWEEN 1 AND 64),
  payload_sha256 char(64) NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(user_id, source, payload_sha256),
  UNIQUE(batch_id, user_id)
);
CREATE INDEX ingestion_batches_user_received_idx ON aurafit.ingestion_batches(user_id, received_at DESC);

-- One metric per tick makes source-specific resampling and missingness explicit.
CREATE TABLE aurafit.metric_ticks (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  device_id uuid NOT NULL,
  observed_at timestamptz NOT NULL,
  metric text NOT NULL CHECK (metric IN ('hrv_rmssd_ms','resting_hr_bpm','stress_score','steps','active_energy_kcal')),
value double precision NOT NULL
  CHECK (value::text NOT IN ('NaN', 'Infinity', '-Infinity')),
  batch_id uuid NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, device_id, metric, observed_at),
  FOREIGN KEY (device_id, user_id) REFERENCES aurafit.devices(device_id, user_id),
  FOREIGN KEY (batch_id, user_id) REFERENCES aurafit.ingestion_batches(batch_id, user_id),
  CONSTRAINT metric_value_range CHECK (
    (metric='hrv_rmssd_ms' AND value BETWEEN 1 AND 300) OR
    (metric='resting_hr_bpm' AND value BETWEEN 25 AND 220) OR
    (metric='stress_score' AND value BETWEEN 0 AND 100) OR
    (metric='steps' AND value BETWEEN 0 AND 100000 AND value = trunc(value)) OR
    (metric='active_energy_kcal' AND value BETWEEN 0 AND 10000)
  )
);
SELECT create_hypertable('aurafit.metric_ticks', by_range('observed_at', INTERVAL '7 days'), if_not_exists => TRUE, create_default_indexes => FALSE);
CREATE INDEX metric_ticks_user_metric_time_idx ON aurafit.metric_ticks(user_id, metric, observed_at DESC) INCLUDE(value);
CREATE INDEX metric_ticks_device_time_idx ON aurafit.metric_ticks(device_id, observed_at DESC);

CREATE TABLE aurafit.sleep_sessions (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  device_id uuid NOT NULL,
  session_id uuid NOT NULL,
  started_at timestamptz NOT NULL,
  ended_at timestamptz NOT NULL,
  deep_minutes integer NOT NULL CHECK(deep_minutes BETWEEN 0 AND 1440),
  rem_minutes integer NOT NULL CHECK(rem_minutes BETWEEN 0 AND 1440),
  light_minutes integer NOT NULL CHECK(light_minutes BETWEEN 0 AND 1440),
  awake_minutes integer NOT NULL CHECK(awake_minutes BETWEEN 0 AND 1440),
  batch_id uuid NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, session_id, started_at),
  FOREIGN KEY(device_id, user_id) REFERENCES aurafit.devices(device_id, user_id),
  FOREIGN KEY(batch_id, user_id) REFERENCES aurafit.ingestion_batches(batch_id, user_id),
  CHECK(ended_at > started_at AND ended_at <= started_at + interval '24 hours'),
  CHECK(deep_minutes + rem_minutes + light_minutes + awake_minutes > 0),
  CHECK(abs(extract(epoch FROM (ended_at-started_at))/60.0 - (deep_minutes+rem_minutes+light_minutes+awake_minutes)) <= 2)
);
SELECT create_hypertable('aurafit.sleep_sessions', by_range('started_at', INTERVAL '30 days'), if_not_exists => TRUE, create_default_indexes => FALSE);
CREATE INDEX sleep_sessions_user_end_idx ON aurafit.sleep_sessions(user_id, ended_at DESC);

CREATE TABLE aurafit.nutrition_entries (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  entry_id uuid NOT NULL,
  consumed_at timestamptz NOT NULL,
  calories_kcal numeric(9,2) NOT NULL CHECK(calories_kcal BETWEEN 0 AND 10000),
  carbs_g numeric(9,2) NOT NULL CHECK(carbs_g BETWEEN 0 AND 1500),
  protein_g numeric(9,2) NOT NULL CHECK(protein_g BETWEEN 0 AND 1000),
  fat_g numeric(9,2) NOT NULL CHECK(fat_g BETWEEN 0 AND 1000),
  batch_id uuid NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, entry_id, consumed_at),
  FOREIGN KEY(batch_id, user_id) REFERENCES aurafit.ingestion_batches(batch_id, user_id)
);
SELECT create_hypertable('aurafit.nutrition_entries', by_range('consumed_at', INTERVAL '30 days'), if_not_exists => TRUE, create_default_indexes => FALSE);
CREATE INDEX nutrition_entries_user_time_idx ON aurafit.nutrition_entries(user_id, consumed_at DESC);

CREATE TABLE aurafit.subjective_checkins (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  checkin_id uuid NOT NULL,
  observed_at timestamptz NOT NULL,
  mood smallint NOT NULL CHECK(mood BETWEEN 1 AND 10),
  energy smallint NOT NULL CHECK(energy BETWEEN 1 AND 10),
  batch_id uuid NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, checkin_id, observed_at),
  FOREIGN KEY(batch_id, user_id) REFERENCES aurafit.ingestion_batches(batch_id, user_id)
);
SELECT create_hypertable('aurafit.subjective_checkins', by_range('observed_at', INTERVAL '30 days'), if_not_exists => TRUE, create_default_indexes => FALSE);
CREATE INDEX subjective_checkins_user_time_idx ON aurafit.subjective_checkins(user_id, observed_at DESC);

-- Snapshots: date is local to timezone. Values are derived, not raw sensor measurements.
CREATE TABLE aurafit.daily_baselines (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  local_date date NOT NULL,
  timezone text NOT NULL,
  baseline_version text NOT NULL,
  lookback_days smallint NOT NULL CHECK (lookback_days BETWEEN 3 AND 90),
  sample_days smallint NOT NULL CHECK (sample_days BETWEEN 0 AND lookback_days),
  hrv_rmssd_median_ms double precision CHECK(hrv_rmssd_median_ms BETWEEN 1 AND 300),
  resting_hr_median_bpm double precision CHECK(resting_hr_median_bpm BETWEEN 25 AND 220),
  sleep_median_minutes double precision CHECK(sleep_median_minutes BETWEEN 0 AND 1440),
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, local_date, baseline_version)
);
CREATE INDEX daily_baselines_user_date_idx ON aurafit.daily_baselines(user_id, local_date DESC);

CREATE TABLE aurafit.daily_assessments (
  user_id uuid NOT NULL REFERENCES aurafit.users(user_id) ON DELETE CASCADE,
  local_date date NOT NULL,
  model_version text NOT NULL,
  baseline_version text NOT NULL,
  allostatic_load_index double precision NOT NULL CHECK(allostatic_load_index BETWEEN 0 AND 100),
  recovery_score double precision NOT NULL CHECK(recovery_score BETWEEN 0 AND 100),
  overreach_risk_score double precision NOT NULL CHECK(overreach_risk_score BETWEEN 0 AND 100),
  predicted_next_day_recovery double precision CHECK(predicted_next_day_recovery BETWEEN 0 AND 100),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(evidence)='object'),
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, local_date, model_version),
  FOREIGN KEY(user_id, local_date, baseline_version) REFERENCES aurafit.daily_baselines(user_id, local_date, baseline_version)
);
CREATE INDEX daily_assessments_user_date_idx ON aurafit.daily_assessments(user_id, local_date DESC);

CREATE TABLE aurafit.adaptive_targets (
  user_id uuid NOT NULL,
  local_date date NOT NULL,
  model_version text NOT NULL,
  step_target integer CHECK(step_target BETWEEN 0 AND 50000),
  active_energy_target_kcal numeric(9,2) CHECK(active_energy_target_kcal BETWEEN 0 AND 10000),
  energy_budget_score double precision CHECK(energy_budget_score BETWEEN 0 AND 100),
  bedtime_start timestamptz,
  bedtime_end timestamptz,
  rationale jsonb NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(rationale)='object'),
  computed_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(user_id, local_date, model_version),
  FOREIGN KEY(user_id, local_date, model_version) REFERENCES aurafit.daily_assessments(user_id, local_date, model_version) ON DELETE CASCADE,
  CHECK ((bedtime_start IS NULL AND bedtime_end IS NULL) OR (bedtime_start IS NOT NULL AND bedtime_end IS NOT NULL AND bedtime_end > bedtime_start))
);
INSERT INTO aurafit.schema_migrations(version) VALUES ('001_initial');
COMMIT;
