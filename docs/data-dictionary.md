# Clinical Cohort Data Dictionary

## Purpose

This document defines the PostgreSQL tables and columns available to the
Clinical Evidence and Synthetic Cohort Assistant.

The current dataset contains synthetic Synthea patients. It must not be
described as real patient data or used to make patient-specific clinical
decisions.

## Dataset scope

- Source: Synthea
- Data type: Synthetic FHIR R4
- Database: Dedicated PostgreSQL database
- Intended use: Research, education, cohort exploration and evaluation
- Prohibited use: Diagnosis, prescribing and patient-specific treatment

## Approved query tables

The cohort-query service may access:

- `ingestion_runs`
- `patients`
- `conditions`
- `observations`

The cohort-query service must not access:

- `raw_fhir_resources`
- PostgreSQL system tables
- Authentication tables
- Audit tables
- Tables outside the approved database
- Any future table containing identifying information

---

## Table: ingestion_runs

Stores information about each imported dataset.

| Column | Type | Nullable | Description |
|---|---|---:|---|
| `run_id` | BIGINT | No | Unique ingestion-run identifier |
| `dataset_fingerprint` | TEXT | No | SHA-256 fingerprint of the imported dataset |
| `source_name` | TEXT | No | Dataset source, currently Synthea |
| `started_at` | TIMESTAMPTZ | No | Time ingestion started |
| `completed_at` | TIMESTAMPTZ | Yes | Time ingestion completed |
| `status` | TEXT | No | Running, completed or failed |
| `as_of_date` | TIMESTAMPTZ | Yes | Latest observation date found in the dataset |

### Terminology

Within the application, `ingestion_runs.as_of_date` is presented as
`data_through_date`.

It describes the latest clinical date available in the imported dataset. It is
not the same as the current analysis date.

### Query rules

- Only completed ingestion runs may be queried.
- Every cohort result must identify its `run_id`.
- Every cohort result must include the dataset fingerprint.
- The latest run must not be selected based only on the largest `run_id`;
  its status must also be `completed`.

---

## Table: patients

Contains one row per synthetic patient per ingestion run.

| Column | Type | Nullable | Description |
|---|---|---:|---|
| `run_id` | BIGINT | No | Dataset ingestion run |
| `patient_id` | TEXT | No | Synthetic FHIR Patient identifier |
| `birth_date` | DATE | Yes | Synthetic date of birth |
| `gender` | TEXT | Yes | Synthetic administrative gender |
| `deceased_at` | TIMESTAMPTZ | Yes | Synthetic recorded death time |

### Primary key

- `run_id`
- `patient_id`

### Query rules

- Age is calculated on the selected `analysis_date`.
- Patients with missing birth dates are excluded from age-based cohorts.
- `gender` must not be interpreted as biological sex.
- All patients must be described as synthetic.
- Patient names, addresses and contact information are not available to the
  cohort-query service.

---

## Table: conditions

Contains normalized FHIR Condition resources.

| Column | Type | Nullable | Description |
|---|---|---:|---|
| `run_id` | BIGINT | No | Dataset ingestion run |
| `condition_id` | TEXT | No | FHIR Condition identifier |
| `patient_id` | TEXT | No | Related synthetic patient |
| `code_system` | TEXT | Yes | Clinical terminology system |
| `code` | TEXT | Yes | Condition code |
| `display` | TEXT | Yes | Human-readable condition name |
| `clinical_status` | TEXT | Yes | Active, inactive, resolved or another status |
| `verification_status` | TEXT | Yes | FHIR verification status; entered-in-error records are excluded |
| `onset_at` | TIMESTAMPTZ | Yes | Condition onset time |
| `abatement_at` | TIMESTAMPTZ | Yes | Condition resolution time |

### Primary key

- `run_id`
- `condition_id`

### Relationship

Each condition must reference a patient with the same `run_id`.

### Query rules

- Conditions must be matched using both `code_system` and `code`.
- Display text must not be used as the primary matching method.
- Missing onset dates do not automatically exclude a condition.
- Conditions with verification status `entered-in-error` are excluded.
- Inactive/resolved conditions are excluded unless a recorded abatement date
  places their resolution after the selected historical analysis date.
- Abated conditions are not active after their abatement time.

---

## Table: observations

Contains normalized FHIR Observation resources.

| Column | Type | Nullable | Description |
|---|---|---:|---|
| `run_id` | BIGINT | No | Dataset ingestion run |
| `observation_id` | TEXT | No | FHIR Observation identifier |
| `patient_id` | TEXT | No | Related synthetic patient |
| `encounter_id` | TEXT | Yes | Related FHIR Encounter |
| `code_system` | TEXT | Yes | Observation terminology system |
| `code` | TEXT | Yes | Observation code |
| `display` | TEXT | Yes | Human-readable observation name |
| `observed_at` | TIMESTAMPTZ | Yes | Observation time |
| `numeric_value` | NUMERIC | Yes | Numeric result |
| `text_value` | TEXT | Yes | Text or coded result |
| `unit` | TEXT | Yes | Measurement unit |
| `systolic_value` | NUMERIC | Yes | LOINC 8480-6 component of a BP panel |
| `systolic_unit` | TEXT | Yes | Systolic component unit |
| `diastolic_value` | NUMERIC | Yes | LOINC 8462-4 component of a BP panel |
| `diastolic_unit` | TEXT | Yes | Diastolic component unit |

### Primary key

- `run_id`
- `observation_id`

### Relationship

Each observation must reference a patient with the same `run_id`.

### Query rules

- Observations must be matched using both `code_system` and `code`.
- Display text must not be used as the primary matching method.
- Date-based queries must require a non-null `observed_at`.
- Numeric values must be interpreted with their units.
- BP panels have no top-level numeric value. Use their systolic/diastolic
  columns, not `numeric_value`, for threshold queries. Standalone systolic or
  diastolic observations still use `numeric_value` and `unit`.
- Unit conversion is not supported in version one.
- Missing observations mean only that no matching record exists in the
  imported dataset.

---

## Approved clinical concepts

| Concept | Code system | Code |
|---|---|---|
| Type 2 diabetes mellitus | SNOMED CT | `44054006` |
| Essential hypertension | SNOMED CT | `59621000` |
| HbA1c | LOINC | `4548-4` |
| Blood-pressure panel | LOINC | `85354-9` |
| Blood-pressure panel | LOINC | `55284-4` |
| Systolic blood pressure | LOINC | `8480-6` |
| Diastolic blood pressure | LOINC | `8462-4` |

## Code-system identifiers

| Terminology | Canonical identifier |
|---|---|
| SNOMED CT | `http://snomed.info/sct` |
| LOINC | `http://loinc.org` |

## Join relationships

- `patients.run_id = conditions.run_id`
- `patients.patient_id = conditions.patient_id`
- `patients.run_id = observations.run_id`
- `patients.patient_id = observations.patient_id`
- `ingestion_runs.run_id = patients.run_id`

Every join involving patient data must include `run_id`. Joining only by
`patient_id` is not allowed.

## Date semantics

### Analysis date

The production application uses the current UTC date as `analysis_date`.

The date is captured once at the beginning of a request and reused throughout
that request.

### Data-through date

The data-through date is taken from `ingestion_runs.as_of_date`.

### Freshness

Freshness is the number of calendar days between:

- `analysis_date`
- `data_through_date`

A freshness value greater than seven days requires a stale-data warning.

## Result requirements

Every cohort result must include:

- Ingestion run
- Dataset fingerprint
- Analysis date
- Data-through date
- Freshness in days
- Clinical codes used
- Inclusion criteria
- Exclusion criteria
- Cohort count
- Query execution timestamp
- Generated SQL when Text-to-SQL is introduced
