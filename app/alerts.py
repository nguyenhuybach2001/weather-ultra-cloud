"""Optional persistent alert outbox. No network call occurs in the ingest path."""
from contextlib import contextmanager
import asyncio
import json
import logging
import math
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertConfig:
    origin: str
    secret: str
    db_path: str
    thresholds: dict[str, tuple[float, float]]
    device_id: str = "weather-ultra-01"
    cooldown: float = 600
    max_reading_age: float = 120
    ttl: float = 300

    @classmethod
    def from_environment(cls):
        if os.getenv("WEATHER_PUSH_ENABLED", "false").lower() != "true":
            return None
        origin = os.environ["DASHBOARD_ORIGIN"].rstrip("/")
        url = urlsplit(origin)
        if url.scheme != "https" or not url.hostname or url.path or url.query or url.fragment or url.username:
            raise ValueError("DASHBOARD_ORIGIN must be an HTTPS origin")
        secret = os.environ["PUSH_SEND_SECRET"]
        if len(secret) < 32:
            raise ValueError("PUSH_SEND_SECRET must contain at least 32 characters")
        if not os.getenv("WEATHER_DEVICE_TOKEN") or os.getenv("WEATHER_DEVICE_TOKEN") == "weather-ultra-test-token":
            raise ValueError("Configure a private WEATHER_DEVICE_TOKEN before enabling push")
        thresholds = {}
        for metric in ("co2", "pm25"):
            raw = os.getenv(f"WEATHER_ALERT_{metric.upper()}_HIGH")
            if raw:
                high = float(raw)
                hysteresis = float(os.getenv(f"WEATHER_ALERT_{metric.upper()}_HYSTERESIS") or "0")
                if not math.isfinite(high) or not math.isfinite(hysteresis) or high <= 0 or not 0 <= hysteresis < high:
                    raise ValueError("Alert thresholds must be finite; 0 <= hysteresis < high")
                thresholds[metric] = (high, hysteresis)
        if not thresholds:
            raise ValueError("Configure at least one alert threshold")
        # An explicit path forces the operator to decide where persistence lives.
        return cls(origin, secret, os.environ["WEATHER_ALERT_DB_PATH"], thresholds)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward the server secret to a redirect destination.
        return None


class AlertEngine:
    def __init__(self, config: AlertConfig):
        self.config = config
        with self.connect() as db:
            db.executescript("""
                create table if not exists alert_state (
                    metric text primary key, active integer not null,
                    last_sent real not null, last_observed real not null
                );
                create table if not exists alert_outbox (
                    id integer primary key autoincrement,
                    payload text not null, created real not null,
                    next_attempt real not null, attempts integer not null default 0
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.config.db_path, timeout=5)
        try:
            db.execute("pragma busy_timeout=5000")
            with db:
                yield db
        finally:
            db.close()

    def observe(self, reading: dict, now: float | None = None):
        now = time.time() if now is None else now
        if reading.get("device_id") != self.config.device_id:
            return
        try:
            timestamp = datetime.fromisoformat(reading["timestamp"].replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                return
            observed = timestamp.timestamp()
        except (ValueError, TypeError, KeyError):
            return
        if now - observed > self.config.max_reading_age or observed - now > 30:
            return
        with self.connect() as db:
            db.execute("begin immediate")
            for metric, (high, hysteresis) in self.config.thresholds.items():
                value = reading.get(metric)
                if not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                    continue
                row = db.execute("select active,last_sent,last_observed from alert_state where metric=?", (metric,)).fetchone()
                active, last_sent, last_observed = row or (0, -self.config.cooldown, 0)
                if observed <= last_observed:
                    continue
                if value < high - hysteresis:
                    active = 0
                elif value >= high and not active:
                    active = 1
                    if now - last_sent >= self.config.cooldown:
                        label, unit = ("CO₂", "ppm") if metric == "co2" else ("PM2.5", "µg/m³")
                        payload = json.dumps({
                            "title": f"Weather Ultra · {label}",
                            "body": f"{label}: {value:g} {unit}, vượt ngưỡng cấu hình {high:g} {unit}. Mở ứng dụng để kiểm tra.",
                            "tag": f"weather-{metric}", "url": "/",
                        }, ensure_ascii=False)
                        db.execute("insert into alert_outbox(payload,created,next_attempt) values(?,?,?)", (payload, now, now))
                        last_sent = now
                db.execute("insert into alert_state values(?,?,?,?) on conflict(metric) do update set active=excluded.active,last_sent=excluded.last_sent,last_observed=excluded.last_observed", (metric, active, last_sent, observed))

    def send(self, payload: str):
        request = Request(self.config.origin + "/api/push/send", data=payload.encode(), headers={"Authorization": "Bearer " + self.config.secret, "Content-Type": "application/json"}, method="POST")
        with build_opener(NoRedirect()).open(request, timeout=55) as response:
            data = json.load(response)
            if data.get("failed", 0):
                raise RuntimeError("Some push recipients failed")
            return data

    def process_one(self, now: float | None = None, sender=None):
        """Single delivery worker. Run one uvicorn process per outbox file."""
        now = time.time() if now is None else now
        with self.connect() as db:
            expired = db.execute("delete from alert_outbox where created < ?", (now - self.config.ttl,)).rowcount
            if expired:
                log.warning("Expired %s undelivered alerts", expired)
            row = db.execute("select id,payload,attempts from alert_outbox where next_attempt <= ? order by id limit 1", (now,)).fetchone()
        if not row:
            return False
        event_id, payload, attempts = row
        try:
            result = (sender or self.send)(payload)
            if isinstance(result, dict) and result.get("failed", 0):
                raise RuntimeError("Partial push failure")
        except Exception as exc:
            # Do not log exceptions containing URLs, credentials or subscription bodies.
            log.warning("Push delivery failed (%s); will retry", type(exc).__name__)
            with self.connect() as db:
                db.execute("update alert_outbox set attempts=attempts+1,next_attempt=? where id=?", (now + min(120, 5 * 2 ** min(attempts, 5)), event_id))
        else:
            with self.connect() as db:
                db.execute("delete from alert_outbox where id=?", (event_id,))
            log.info("Push accepted: %s", result)
        return True

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.process_one)
            except Exception:
                log.exception("Alert outbox unavailable")
            try:
                await asyncio.wait_for(stop.wait(), timeout=1)
            except asyncio.TimeoutError:
                pass
