import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from app.alerts import AlertConfig, AlertEngine


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = AlertConfig("https://dashboard.example", "s" * 32, self.temp.name + "/alerts.sqlite3", {"co2": (1000, 100), "pm25": (35, 5)})
        self.engine = AlertEngine(self.config)
        self.now = 1_800_000_000.0

    def tearDown(self):
        self.temp.cleanup()

    def observe(self, co2=None, pm25=None, offset=0, **extra):
        now = self.now + offset
        reading = {"device_id": "weather-ultra-01", "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(), "co2": co2, "pm25": pm25, **extra}
        self.engine.observe(reading, now)

    def queued(self):
        with sqlite3.connect(self.config.db_path) as db:
            return db.execute("select payload,attempts,next_attempt from alert_outbox").fetchall()

    def test_transition_dedup_hysteresis_and_restart(self):
        self.observe(co2=1200)
        self.observe(co2=1300, offset=1)
        self.engine = AlertEngine(self.config)
        self.observe(co2=1400, offset=2)
        self.assertEqual(len(self.queued()), 1)
        self.observe(co2=950, offset=601)  # Within hysteresis: remains active.
        self.observe(co2=1200, offset=602)
        self.assertEqual(len(self.queued()), 1)
        self.observe(co2=899, offset=603)
        self.observe(co2=1200, offset=604)
        self.assertEqual(len(self.queued()), 2)

    def test_cooldown_suppresses_rapid_recurrence(self):
        self.observe(co2=1200)
        self.observe(co2=800, offset=10)
        self.observe(co2=1200, offset=20)
        self.assertEqual(len(self.queued()), 1)

    def test_invalid_old_wrong_device_and_duplicate_readings(self):
        self.observe(co2=float("nan"))
        self.observe(co2=1200, offset=1, device_id="another-device")
        self.observe(co2=1200, offset=2, timestamp="2020-01-01T00:00:00Z")
        self.observe(co2=1200, offset=3, timestamp="not-a-date")
        self.observe(co2=-10, offset=4)
        self.assertEqual(len(self.queued()), 0)
        self.observe(co2=1200, offset=5)
        self.observe(co2=800, offset=3)
        self.observe(co2=1200, offset=606)
        self.assertEqual(len(self.queued()), 1)

    def test_metrics_are_independent(self):
        self.observe(co2=1200, pm25=40)
        self.assertEqual({json.loads(row[0])["tag"] for row in self.queued()}, {"weather-co2", "weather-pm25"})

    def test_retry_then_success_without_losing_outbox(self):
        self.observe(co2=1200)
        def fail(payload):
            raise OSError("unavailable")
        self.engine.process_one(self.now, fail)
        self.assertEqual(self.queued()[0][1], 1)
        self.assertFalse(self.engine.process_one(self.now + 1, fail))
        self.engine = AlertEngine(self.config)
        delivered = []
        self.engine.process_one(self.now + 5, lambda payload: delivered.append(json.loads(payload)))
        self.assertEqual(len(delivered), 1)
        self.assertEqual(self.queued(), [])

    def test_expired_alert_is_not_delivered(self):
        self.observe(co2=1200)
        self.engine.process_one(self.now + 301, lambda payload: self.fail("Must not send old alert"))
        self.assertEqual(self.queued(), [])


if __name__ == "__main__":
    unittest.main()
