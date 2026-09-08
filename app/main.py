import os
import secrets
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel


app = FastAPI(
    title="Weather Ultra Cloud",
    version="0.1.0",
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

    print(
        "CLOUD READING:",
        reading.device_id,
        reading.timestamp,
    )

    return {
        "status": "accepted",
        "device_id": reading.device_id,
    }
