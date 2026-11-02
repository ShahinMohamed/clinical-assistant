import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import app


class HostedAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_large_flow_response_arrives_complete(self):
        payload = {"result": {"status": "completed"}, "flow": [{"output": "evidence " * 20000}]}
        with patch("app.consume_research_budget"), patch("app.execute_request", return_value=payload):
            response = self.client.post("/api/ask", json={"question": "Test evidence question"})
        self.assertGreater(len(response.content), 32768)
        self.assertEqual(response.json(), payload)
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_private_files_are_inaccessible(self):
        for path in ("/.env", "/web/server.py", "/models/embedding/config.json", "/docs"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_invalid_requests_stop_before_research(self):
        with patch("app.execute_request") as execute:
            self.assertEqual(self.client.post("/api/ask", json={"question": "Test"}, headers={"Origin": "https://example.org"}).status_code, 403)
            self.assertEqual(self.client.post("/api/ask", content="text", headers={"Content-Type": "text/plain"}).status_code, 415)
            self.assertEqual(self.client.post("/api/ask", content=b"x" * 17000, headers={"Content-Type": "application/json"}).status_code, 400)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
