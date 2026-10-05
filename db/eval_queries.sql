-- name: quality_summary
SELECT
    (
        SELECT COUNT(*)
        FROM raw_fhir_resources
        WHERE run_id = %(run_id)s
    ) AS raw_resources,

    (
        SELECT COUNT(*)
        FROM patients
        WHERE run_id = %(run_id)s
    ) AS patients,

    (
        SELECT COUNT(*)
        FROM conditions
        WHERE run_id = %(run_id)s
    ) AS conditions,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
    ) AS observations,

    (
        SELECT COUNT(*)
        FROM conditions
        WHERE run_id = %(run_id)s
          AND code_system = 'http://snomed.info/sct'
          AND code = '44054006'
    ) AS type_2_diabetes_conditions,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND code_system = 'http://loinc.org'
          AND code = '4548-4'
    ) AS hba1c_observations,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND code_system = 'http://loinc.org'
          AND code IN (
              '85354-9',
              '55284-4',
              '8480-6',
              '8462-4'
          )
    ) AS blood_pressure_observations,

    (
        SELECT COUNT(*)
        FROM conditions c
        LEFT JOIN patients p
          ON p.run_id = c.run_id
         AND p.patient_id = c.patient_id
        WHERE c.run_id = %(run_id)s
          AND p.patient_id IS NULL
    ) AS orphan_conditions,

    (
        SELECT COUNT(*)
        FROM observations o
        LEFT JOIN patients p
          ON p.run_id = o.run_id
         AND p.patient_id = o.patient_id
        WHERE o.run_id = %(run_id)s
          AND p.patient_id IS NULL
    ) AS orphan_observations,

    (
        SELECT COUNT(*)
        FROM patients
        WHERE run_id = %(run_id)s
          AND birth_date IS NULL
    ) AS patients_missing_birth_date,

    (
        SELECT COUNT(*)
        FROM conditions
        WHERE run_id = %(run_id)s
          AND (
              code_system IS NULL
              OR code IS NULL
          )
    ) AS conditions_missing_codes,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND (
              code_system IS NULL
              OR code IS NULL
          )
    ) AS observations_missing_codes,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND code_system = 'http://loinc.org'
          AND code = '4548-4'
          AND observed_at IS NULL
    ) AS hba1c_missing_dates,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND code_system = 'http://loinc.org'
          AND code = '4548-4'
          AND unit IS NULL
    ) AS hba1c_missing_units,

    (
        SELECT COUNT(*)
        FROM observations o
        JOIN patients p
          ON p.run_id = o.run_id
         AND p.patient_id = o.patient_id
        WHERE o.run_id = %(run_id)s
          AND o.observed_at::date < p.birth_date
    ) AS observations_before_birth,

    (
        SELECT COUNT(*)
        FROM conditions
        WHERE run_id = %(run_id)s
          AND onset_at IS NOT NULL
          AND abatement_at IS NOT NULL
          AND onset_at > abatement_at
    ) AS onset_after_abatement,

    (
        SELECT COUNT(*)
        FROM observations
        WHERE run_id = %(run_id)s
          AND observed_at::date > %(analysis_date)s::date
    ) AS observations_after_analysis_date,

    (
        SELECT MIN(observed_at)
        FROM observations
        WHERE run_id = %(run_id)s
    ) AS earliest_observation,

    (
        SELECT MAX(observed_at)
        FROM observations
        WHERE run_id = %(run_id)s
    ) AS latest_observation;


-- name: condition_statuses
SELECT
    COALESCE(clinical_status, '<missing>') AS clinical_status,
    COUNT(*) AS count
FROM conditions
WHERE run_id = %(run_id)s
GROUP BY COALESCE(clinical_status, '<missing>')
ORDER BY clinical_status;


-- name: hba1c_units
SELECT
    COALESCE(unit, '<missing>') AS unit,
    COUNT(*) AS count
FROM observations
WHERE run_id = %(run_id)s
  AND code_system = 'http://loinc.org'
  AND code = '4548-4'
GROUP BY COALESCE(unit, '<missing>')
ORDER BY unit;


-- name: cohort_001
SELECT COUNT(DISTINCT patient_id) AS expected_count
FROM patients
WHERE run_id = %(run_id)s;


-- name: cohort_002
SELECT COUNT(DISTINCT patient_id) AS expected_count
FROM patients
WHERE run_id = %(run_id)s
  AND birth_date IS NOT NULL
  AND birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years';


-- name: cohort_003
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'
  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '44054006'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  );


-- name: cohort_004
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'
  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '59621000'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  );


-- name: cohort_005
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '44054006'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  )

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '59621000'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  );


-- name: cohort_006
SELECT COUNT(DISTINCT patient_id) AS expected_count
FROM observations
WHERE run_id = %(run_id)s
  AND code_system = 'http://loinc.org'
  AND code = '4548-4'
  AND observed_at IS NOT NULL;


-- name: cohort_007
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '44054006'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  )

  AND EXISTS (
      SELECT 1
      FROM observations o
      WHERE o.run_id = p.run_id
        AND o.patient_id = p.patient_id
        AND o.code_system = 'http://loinc.org'
        AND o.code = '4548-4'
        AND o.observed_at
            >= %(analysis_date)s::date - INTERVAL '12 months'
        AND o.observed_at
            < %(analysis_date)s::date + INTERVAL '1 day'
  );


-- name: cohort_008
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '44054006'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  )

  AND NOT EXISTS (
      SELECT 1
      FROM observations o
      WHERE o.run_id = p.run_id
        AND o.patient_id = p.patient_id
        AND o.code_system = 'http://loinc.org'
        AND o.code = '4548-4'
        AND o.observed_at
            >= %(analysis_date)s::date - INTERVAL '12 months'
        AND o.observed_at
            < %(analysis_date)s::date + INTERVAL '1 day'
  );


-- name: cohort_009
SELECT COUNT(DISTINCT p.patient_id) AS expected_count
FROM patients p
WHERE p.run_id = %(run_id)s
  AND p.birth_date IS NOT NULL
  AND p.birth_date
      <= %(analysis_date)s::date - INTERVAL '18 years'

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '44054006'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  )

  AND EXISTS (
      SELECT 1
      FROM conditions c
      WHERE c.run_id = p.run_id
        AND c.patient_id = p.patient_id
        AND c.code_system = 'http://snomed.info/sct'
        AND c.code = '59621000'
        AND (
            c.onset_at IS NULL
            OR c.onset_at::date <= %(analysis_date)s::date
        )
        AND (
            c.abatement_at IS NULL
            OR c.abatement_at::date > %(analysis_date)s::date
        )
        AND LOWER(COALESCE(c.clinical_status, ''))
            NOT IN (
                'inactive',
                'resolved',
                'entered-in-error'
            )
  )

  AND NOT EXISTS (
      SELECT 1
      FROM observations o
      WHERE o.run_id = p.run_id
        AND o.patient_id = p.patient_id
        AND o.code_system = 'http://loinc.org'
        AND o.code = '4548-4'
        AND o.observed_at
            >= %(analysis_date)s::date - INTERVAL '12 months'
        AND o.observed_at
            < %(analysis_date)s::date + INTERVAL '1 day'
  );


-- name: cohort_010
SELECT COUNT(DISTINCT patient_id) AS expected_count
FROM patients
WHERE run_id = %(run_id)s
  AND birth_date IS NULL;