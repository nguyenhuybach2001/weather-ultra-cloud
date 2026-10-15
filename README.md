# Weather Ultra Cloud — optional push alerts

Requires Python 3.10 or newer.

Existing root, health, current-weather and authenticated device-ingestion API routes and response bodies remain unchanged. This repository only receives readings; it does not read Serial, run camera streams, or write Supabase. The push hook runs when the Pi submits an accepted reading to `/api/device/readings`.

## Run

```sh
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
# Export environment variables from your deployment settings (see .env.example).
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Push is off by default and uses only Python standard-library modules. If enabled but misconfigured, a startup error is logged and ingestion remains available. Configure a private `WEATHER_DEVICE_TOKEN` for production; the existing fallback test token is retained for compatibility but is not allowed when enabling push.

## Enable real alerts

1. Deploy the companion dashboard's PWA/push changes. Apply its Supabase subscription-table migration, configure its VAPID keys, server service-role key, pairing code, `APP_ORIGIN` and `PUSH_SEND_SECRET`.
2. Install the dashboard on a phone and enable notifications in Settings with the station pairing code.
3. Set `DASHBOARD_ORIGIN` and the identical server-only `PUSH_SEND_SECRET` here. Do not use the pairing code as the send secret.
4. Set `WEATHER_ALERT_DB_PATH` to a writable file on a **persistent disk**, with an existing parent directory. Run exactly one worker/instance for this outbox file. Ephemeral/serverless filesystems lose alert state and queued notifications on redeploy; use a shared durable queue for multiple replicas.
5. Set your desired `WEATHER_ALERT_CO2_HIGH` and/or `WEATHER_ALERT_PM25_HIGH`, with optional nonnegative `..._HYSTERESIS` smaller than the high threshold. There are deliberately no default environmental/health thresholds. Blank hysteresis is treated as `0`.
6. Set `WEATHER_PUSH_ENABLED=true` and restart the service. Verify the startup logs do not report disabled push.
7. Ensure the Pi actually POSTs readings to this cloud endpoint. If it only writes directly to Supabase, this hook will not run; configure a cloud POST in the Pi sender or provide that sender's source for integration. Supabase telemetry is not rewritten by this change.

## Alert behavior

- Currently the dashboard's single station `weather-ultra-01` and metrics CO₂/PM2.5 are supported.
- A fresh value at or above the configured threshold queues an alert on transition from normal to high. Persistent high readings do not spam users. Falling below `high - hysteresis` resets the condition.
- A 10-minute cooldown suppresses repeat transitions. A suppressed high transition must recover before a later alert can trigger; cooldown expiry alone does not generate reminders.
- Missing, nonfinite, negative, duplicate/out-of-order timestamps, old readings (>120 seconds), and timestamps >30 seconds in the future do not trigger alerts. Use ISO timestamps with timezone offsets and synchronized Pi/cloud clocks.
- State and outbox are stored in SQLite, so restarts retain deduplication. Delivery runs outside the ingestion request, with exponential retry backoff capped at 120 seconds; undelivered alerts expire after 5 minutes.
- A push send accepted by the provider does not prove the phone displayed it. Partial delivery may retry recipients already sent; matching notification tags replace the displayed alert. This is at-least-once delivery, not exactly-once.
- No heartbeat/offline notification is inferred from this API: it has no Pi heartbeat feed. An independent monitor is needed to notify when the Pi loses power/network.
- Alert state is tied to the configured station and thresholds. Stop the process and rotate the SQLite file if intentionally resetting alert state after changing thresholds.

## Tests

```sh
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Tests use temporary SQLite files and fake senders; they never send real push notifications. Deploying this repo and configuring the Pi's production data path are separate steps.
