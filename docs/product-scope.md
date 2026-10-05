# Clinical Evidence & Synthetic Cohort Assistant

## Purpose

Build a research and clinical-informatics assistant that:

1. Answers clinical evidence questions using traceable public sources.
2. Constructs and analyzes entirely synthetic patient cohorts.
3. Combines synthetic cohort statistics with published evidence.
4. Shows citations, cohort filters, generated SQL, and data versions.

## Initial clinical domain

Type 2 diabetes with hypertension and cardiovascular-risk monitoring.

## Intended users

- Clinical researchers
- Clinical informaticians
- Healthcare data analysts
- Clinicians performing research or quality-improvement exploration

## Version-one user stories

### Evidence search

As a researcher, I can ask:

"What monitoring is recommended for adults with type 2 diabetes and
hypertension?"

The system returns:

- A concise evidence summary
- Citations
- Publisher and publication date
- Jurisdiction
- Relevant supporting passages
- An insufficiency warning when evidence is inadequate

### Synthetic cohort analysis

As a researcher, I can ask:

"How many synthetic adults with type 2 diabetes and hypertension have no
recorded HbA1c measurement in the last 12 months?"

The system returns:

- Synthetic cohort count
- Inclusion and exclusion filters
- Generated SQL
- Dataset version
- Execution timestamp

### Combined analysis

As a researcher, I can ask:

"Find that synthetic cohort and summarize the relevant monitoring guidance."

The system returns two clearly separated sections:

1. Synthetic cohort findings
2. Published clinical evidence


## Product limitations

This application must not:

- Diagnose a condition
- Recommend a patient-specific treatment
- Prescribe or change medication
- Produce emergency medical advice
- Claim synthetic results represent real populations
- hide the SQL or cohort criteria used
- Answer a clinical claim without supporting evidence
- Accept uploads containing real patient data in version one

## Initial data sources

- Synthea FHIR R4 and CSV
- WHO diabetes guidance
- PubMed abstracts
- PMC Open Access articles
- ClinicalTrials.gov data
- DailyMed drug-label information

## Version-one success criteria

- All patient records are synthetic.
- Every clinical claim has an appropriate citation.
- Every cohort result contains its filters and dataset version.
- Evidence and cohort findings are presented separately.
- Unsupported questions produce an abstention.
- SQL is restricted to approved read-only analytical views.
- Users can inspect the generated SQL.
- The application never presents itself as a diagnostic system.

## Working disclaimer

This system is intended for research, education, and clinical-informatics
exploration. It uses synthetic patient data and does not provide medical
advice, diagnosis, or patient-specific treatment recommendations.