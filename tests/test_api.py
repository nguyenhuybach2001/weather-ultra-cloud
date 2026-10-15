import unittest
from unittest.mock import Mock, patch
from fastapi.testclient import TestClient
from app import main


class ApiCompatibilityTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"WEATHER_PUSH_ENABLED": "false"})
        self.env.start()
        self.client = TestClient(main.app)
        self.client.__enter__()
        main.latest_reading = None
        self.reading = {"device_id": "weather-ultra-01", "timestamp": "2026-10-15T00:00:00Z", "co2": 1200}
        self.headers = {"Authorization": "Bearer " + main.DEVICE_TOKEN}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.env.stop()

    def test_existing_routes_and_auth(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/").json()["service"], "Weather Ultra Cloud")
        self.assertIsNone(self.client.get("/api/weather/current").json())
        self.assertEqual(self.client.post("/api/device/readings", json=self.reading).status_code, 401)
        self.assertIsNone(self.client.get("/api/weather/current").json())
        result = self.client.post("/api/device/readings", json=self.reading, headers=self.headers)
        self.assertEqual(result.json(), {"status": "accepted", "device_id": "weather-ultra-01"})
        self.assertEqual(self.client.get("/api/weather/current").json()["co2"], 1200)

    def test_hook_receives_accepted_data(self):
        engine = Mock()
        with patch.object(main, "alert_engine", engine):
            self.client.post("/api/device/readings", json=self.reading, headers=self.headers)
        engine.observe.assert_called_once()
        self.assertEqual(engine.observe.call_args.args[0]["co2"], 1200)

    def test_alert_storage_failure_does_not_break_ingestion(self):
        engine = Mock()
        engine.observe.side_effect = OSError("test disk failure")
        with patch.object(main, "alert_engine", engine):
            result = self.client.post("/api/device/readings", json=self.reading, headers=self.headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.client.get("/api/weather/current").json()["co2"], 1200)
