CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id BIGSERIAL PRIMARY KEY,
    dataset_fingerprint TEXT NOT NULL UNIQUE,
    source_name TEXT NOT NULL DEFAULT 'Synthea',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    as_of_date TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS raw_fhir_resources (
    raw_id BIGSERIAL PRIMARY KEY,
    run_id BIGINT NOT NULL
        REFERENCES ingestion_runs(run_id)
        ON DELETE CASCADE,
    file_name TEXT NOT NULL,
    entry_index INTEGER NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    patient_id TEXT,
    resource_json JSONB NOT NULL,

    UNIQUE (run_id, file_name, entry_index)
);

CREATE TABLE IF NOT EXISTS patients (
    run_id BIGINT NOT NULL
        REFERENCES ingestion_runs(run_id)
        ON DELETE CASCADE,
    patient_id TEXT NOT NULL,
    birth_date DATE,
    gender TEXT,
    deceased_at TIMESTAMPTZ,

    PRIMARY KEY (run_id, patient_id)
);

CREATE TABLE IF NOT EXISTS conditions (
    run_id BIGINT NOT NULL,
    condition_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    code_system TEXT,
    code TEXT,
    display TEXT,
    clinical_status TEXT,
    onset_at TIMESTAMPTZ,
    abatement_at TIMESTAMPTZ,

    PRIMARY KEY (run_id, condition_id),

    FOREIGN KEY (run_id, patient_id)
        REFERENCES patients(run_id, patient_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS observations (
    run_id BIGINT NOT NULL,
    observation_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    encounter_id TEXT,
    code_system TEXT,
    code TEXT,
    display TEXT,
    observed_at TIMESTAMPTZ,
    numeric_value NUMERIC,
    text_value TEXT,
    unit TEXT,

    PRIMARY KEY (run_id, observation_id),

    FOREIGN KEY (run_id, patient_id)
        REFERENCES patients(run_id, patient_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS raw_fhir_resource_type_idx
    ON raw_fhir_resources (run_id, resource_type);

CREATE INDEX IF NOT EXISTS conditions_patient_idx
    ON conditions (run_id, patient_id);

CREATE INDEX IF NOT EXISTS conditions_code_idx
    ON conditions (run_id, code);

CREATE INDEX IF NOT EXISTS observations_patient_idx
    ON observations (run_id, patient_id);

CREATE INDEX IF NOT EXISTS observations_code_date_idx
    ON observations (run_id, code, observed_at);