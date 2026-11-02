import json
import os
import unittest
from unittest.mock import patch

from safety.sql import validate_cohort_sql
from web.security import consume_research_budget, redact_secrets


class SQLBoundaryTests(unittest.TestCase):
    def test_aggregate_with_age_and_cte_is_supported(self):
        query = """
            WITH adults AS (
                SELECT patient_id FROM public.patients
                WHERE run_id = :run_id
                  AND EXTRACT(YEAR FROM AGE(CAST(:analysis_date AS date), birth_date)) >= 18
            ) SELECT COUNT(DISTINCT patient_id) FROM adults
        """
        self.assertEqual(validate_cohort_sql(query), query)

    def test_database_introspection_file_access_and_writes_are_blocked(self):
        queries = [
            "SELECT COUNT(*) FROM pg_catalog.pg_roles",
            "SELECT COUNT(*) FROM raw_fhir_resources",
            "SELECT COUNT(*) FROM evidence_active_index",
            "SELECT COUNT(*) FROM app_private.research_budget",
            "SELECT pg_read_file('/etc/passwd'), COUNT(*) FROM patients",
            "SELECT set_config('statement_timeout', '0', false), COUNT(*) FROM patients",
            "SELECT pg_sleep(60), COUNT(*) FROM patients",
            "SELECT COUNT(*) FROM dblink('host=example.org', 'SELECT 1')",
            "SELECT public.lower('x'), COUNT(*) FROM patients",
            "SELECT COUNT(*) INTO copied FROM patients",
            "WITH deleted AS (DELETE FROM patients RETURNING *) SELECT COUNT(*) FROM deleted",
            "SELECT COUNT(*) FROM patients; DROP TABLE patients",
            "SELECT patient_id FROM patients",
        ]
        for query in queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                validate_cohort_sql(query)


class SecretBoundaryTests(unittest.TestCase):
    @patch.dict(os.environ, {
        "GEMINI_API_KEY": "private-gemini-value",
        "DATABASE_URL": "postgresql://reader:private-db-password@example.org/db",
    }, clear=True)
    def test_nested_outputs_and_partial_database_errors_are_redacted(self):
        result = redact_secrets({"flow": [{"message": "private-gemini-value; password private-db-password"}]})
        self.assertNotIn("private-gemini", json.dumps(result))
        self.assertNotIn("private-db", json.dumps(result))

    @patch.dict(os.environ, {"VERCEL": "1"}, clear=True)
    def test_hosted_research_fails_closed_without_budget_configuration(self):
        with self.assertRaises(RuntimeError):
            consume_research_budget({"X-Forwarded-For": "192.0.2.1"})


if __name__ == "__main__":
    unittest.main()
