import asyncio
from contextlib import asynccontextmanager
import logging
import os
import secrets
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from app.alerts import AlertConfig, AlertEngine

log = logging.getLogger(__name__)
alert_engine: AlertEngine | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global alert_engine
    stop = asyncio.Event()
    worker = None
    try:
        config = AlertConfig.from_environment()
        if config:
            alert_engine = AlertEngine(config)
            worker = asyncio.create_task(alert_engine.run(stop))
    except Exception:
        # Alert configuration must not take down the existing ingestion API.
        log.exception("Push alerts disabled: check configuration and writable database path")
    try:
        yield
    finally:
        stop.set()
        if worker:
            await worker
        alert_engine = None



app = FastAPI(
    title="Weather Ultra Cloud",
    version="0.1.0",
    lifespan=lifespan,
)


DEVICE_TOKEN = os.getenv(
    "WEATHER_DEVICE_TOKEN",
    "weather-ultra-test-token",
)


class WeatherReading(BaseModel):
    device_id: str
    timestamp: str

    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None

    co2: Optional[float] = None

    pm1: Optional[float] = None
    pm25: Optional[float] = None
    pm4: Optional[float] = None
    pm10: Optional[float] = None

    lux: Optional[float] = None
    uv: Optional[float] = None

    device_status: str = "online"


latest_reading: Optional[dict] = None


@app.get("/")
async def root():
    return {
        "service": "Weather Ultra Cloud",
        "status": "online",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
    }


@app.get("/api/weather/current")
async def current_weather():
    return latest_reading


@app.post("/api/device/readings")
async def receive_reading(
    reading: WeatherReading,
    authorization: str | None = Header(
        default=None
    ),
):
    global latest_reading

    expected = f"Bearer {DEVICE_TOKEN}"

    if (
        authorization is None
        or not secrets.compare_digest(
            authorization,
            expected,
        )
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid device token",
        )

    latest_reading = reading.model_dump()
    if alert_engine:
        try:
            await asyncio.to_thread(alert_engine.observe, latest_reading)
        except Exception:
            log.exception("Could not queue alert; reading remains accepted")

    print(
        "CLOUD READING:",
        reading.device_id,
        reading.timestamp,
    )

    return {
        "status": "accepted",
        "device_id": reading.device_id,
    }
