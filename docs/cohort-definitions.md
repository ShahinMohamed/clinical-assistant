# Cohort Definitions

## Purpose

This document defines the initial computable cohorts used by the Clinical
Evidence and Synthetic Cohort Assistant.

These definitions apply only to synthetic Synthea records.

## Global parameters

### Analysis date

The default analysis date is the current UTC calendar date.

The application captures this date once at the beginning of the analysis.

### Data-through date

The data-through date is the latest observation date recorded for the selected
completed ingestion run.

### Recent observation window

The recent observation window begins 12 calendar months before the analysis
date and ends on the analysis date.

Both boundary dates are included.

### Stale dataset

A dataset is considered stale when its data-through date is more than seven
calendar days before the analysis date.

A stale dataset does not prevent the cohort from being calculated, but the
result must display a warning.

Required warning:

> This dataset is not current. Missing recent observations may reflect delayed
> or discontinued data ingestion rather than missing clinical care.

---

## Definition: adult

A synthetic patient is considered an adult when:

- A birth date is present.
- The patient is at least 18 years old on the analysis date.

Patients with missing or invalid birth dates are excluded from adult cohorts
and counted separately.

---

## Definition: active condition

A condition is active on the analysis date when all applicable rules pass:

1. Its onset date is missing or on/before the analysis date.
2. Its abatement date is missing or after the analysis date.
3. Its verification status is not `entered-in-error`.
4. Its clinical status is not:
   - `inactive`
   - `resolved`
   - `entered-in-error`

For a historical date before a recorded abatement date, a condition currently
marked inactive or resolved is still included if the onset rule passes. It was
not yet abated at that historical date. This is a date-based approximation:
FHIR snapshots do not reconstruct every status change or recurrence. Without
an abatement date, an inactive/resolved condition remains excluded.

`entered-in-error` normally belongs to FHIR `verificationStatus`, not
`clinicalStatus`. An entered-in-error verification status always excludes the
condition, regardless of dates. The clinical-status check also rejects that
value defensively if a malformed source puts it in the wrong field.

Clinical status comparison is case-insensitive.

A missing clinical status does not automatically make a condition inactive.

---

## Concept: type 2 diabetes

- Terminology: SNOMED CT
- System: `http://snomed.info/sct`
- Code: `44054006`
- Display: Diabetes mellitus type 2

Prediabetes is not included.

Diabetes complications are not automatically sufficient to place a patient in
the type 2 diabetes cohort unless code `44054006` is also present.

---

## Concept: hypertension

- Terminology: SNOMED CT
- System: `http://snomed.info/sct`
- Code: `59621000`
- Display: Essential hypertension

Other hypertension codes are not included in version one.

---

## Concept: HbA1c

- Terminology: LOINC
- System: `http://loinc.org`
- Code: `4548-4`
- Display: Hemoglobin A1c/Hemoglobin.total in Blood

An HbA1c observation counts as present when:

- It belongs to the selected patient.
- Its code system and code match.
- It has a non-null observation date.
- Its observation date falls inside the requested period.

A numeric value is not required when determining whether an observation
exists.

---

## Cohort 1: all synthetic patients

### Inclusion

- Patient belongs to the selected completed ingestion run.

### Exclusion

- None.

### Output

- Distinct synthetic patient count.

---

## Cohort 2: synthetic adults

### Inclusion

- Patient belongs to the selected ingestion run.
- Birth date is present.
- Age is at least 18 on the analysis date.

### Exclusion

- Missing birth date.
- Invalid birth date.
- Age below 18.

---

## Cohort 3: adults with type 2 diabetes

### Inclusion

- Meets the adult definition.
- Has at least one active type 2 diabetes condition.

### Exclusion

- Does not have the required SNOMED CT code.
- Diabetes condition was resolved before the analysis date.
- Condition was entered in error.

---

## Cohort 4: adults with hypertension

### Inclusion

- Meets the adult definition.
- Has at least one active hypertension condition.

### Exclusion

- Does not have the required SNOMED CT code.
- Hypertension condition was resolved before the analysis date.
- Condition was entered in error.

---

## Cohort 5: adults with diabetes and hypertension

### Inclusion

- Meets the adult definition.
- Meets the active type 2 diabetes definition.
- Meets the active hypertension definition.

### Exclusion

- Fails any inclusion requirement.

---

## Cohort 6: patients with any HbA1c observation

### Inclusion

- Patient belongs to the selected ingestion run.
- Has at least one HbA1c observation with a valid observation date.

### Exclusion

- No matching HbA1c observation.
- HbA1c observation has no usable date.

---

## Cohort 7: diabetes patients with recent HbA1c

### Inclusion

- Meets the adult type 2 diabetes definition.
- Has an HbA1c observation within the recent observation window.

### Exclusion

- No qualifying HbA1c observation during the recent window.

---

## Cohort 8: diabetes patients without recent HbA1c

### Inclusion

- Meets the adult type 2 diabetes definition.
- Has no HbA1c observation within the recent observation window.

### Exclusion

- Has at least one qualifying HbA1c observation during the recent window.

### Interpretation

Allowed wording:

> No matching HbA1c observation was found in the available synthetic dataset
> during the specified period.

Prohibited wording:

> The patient did not receive an HbA1c test.

---

## Cohort 9: primary monitoring-gap cohort

### Name

Synthetic adults with type 2 diabetes, hypertension and no recent HbA1c.

### Inclusion

- Meets the adult definition.
- Has active type 2 diabetes.
- Has active hypertension.
- Has no HbA1c observation during the recent observation window.

### Exclusion

- Missing or invalid birth date.
- Age below 18.
- Type 2 diabetes definition not met.
- Hypertension definition not met.
- Recent HbA1c observation exists.

### Required output metadata

- Cohort count
- Analysis date
- Recent-window start date
- Recent-window end date
- Data-through date
- Freshness in days
- Dataset fingerprint
- Codes used
- Stale-data warning when applicable

---

## Cohort 10: patients excluded for missing birth date

### Inclusion

- Patient belongs to the selected ingestion run.
- Birth date is null or unusable.

### Exclusion

- Valid birth date exists.

---

## General counting rules

- Count distinct patients, not conditions or observations.
- Multiple matching conditions count once per patient.
- Multiple matching observations count once when calculating patient counts.
- Every query must restrict records to one ingestion run.
- All patient joins must include both `run_id` and `patient_id`.
- Ground-truth results must record the analysis date used.

## Safety language

Every user-facing result must state that the data is synthetic.

Cohort findings are descriptive database results. They must not be presented as
diagnoses, treatment recommendations or real-world prevalence estimates.
