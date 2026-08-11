# OpsSentinel

Weekday visual monitor for OPS dashboard(s): Playwright full-page screenshot → multimodal LLM → POST critical findings to a local signals API.

Design decisions are frozen in [`docs/PLAN.md`](docs/PLAN.md). Agent working notes: [`AGENTS.md`](AGENTS.md).

## Features

- Mon–Fri **09:30–18:10** `Asia/Shanghai` (first in-window run is overnight catch-up)
- One-shot CLI every **10 minutes** (launchd); skip if previous run still holds lock
- Full-page PNG at `device_scale_factor=2`; **fail closed** if image exceeds budget
- Semi-manual `login` → Playwright `storage_state`
- Critical-only board alerts: `name=Aliyun.OPS`
- Pipeline/auth failures: `name=Aliyun.OPS.Monitor`
- Invalid LLM JSON: log first failure; page monitor after **2 consecutive** per board
- List-ready multi-dashboard config; **one POST per critical board**
- Screenshot retention: **12 hours**

## Setup

```bash
cd OpsSentinel
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# edit .env and dashboards.yaml
```

### Configure

1. **`dashboards.yaml`** — set real `id` + `url` (list supports multiple later).
2. **`.env`** — `LLM_*`, `ALERT_SOURCE_UUID`, `ALERT_PUSH_CREDENTIAL`, optional markers.
3. **Login once** (headed browser):

```bash
python -m monitor login --board aliyun-ops
```

4. **Doctor**:

```bash
python -m monitor doctor
```

5. **Manual cycle** (ignores business window if needed):

```bash
python -m monitor run --force
```

## CLI

| Command | Purpose |
|---|---|
| `python -m monitor run` | One monitoring cycle (respects window) |
| `python -m monitor run --force` | Run outside business hours |
| `python -m monitor login --board <id>` | Save Playwright storage state |
| `python -m monitor cleanup-screenshots` | Apply 12h retention now |
| `python -m monitor doctor` | Config / auth file checks |

## Alerter contract

```bash
curl --fail-with-body \
  -X POST "${ALERT_BASE_URL}/v1/sources/${ALERT_SOURCE_UUID}/signals" \
  -H "Authorization: Bearer ${ALERT_PUSH_CREDENTIAL}" \
  -H "Content-Type: application/json" \
  --data-binary '{"name":"Aliyun.OPS","message":"...","occurredAt":"2026-08-11T14:32:10+08:00"}'
```

## launchd (always-on Mac)

1. Edit paths in `deploy/com.ops.dashboard-monitor.plist`.
2. Copy to `~/Library/LaunchAgents/`.
3. Load:

```bash
launchctl unload ~/Library/LaunchAgents/com.ops.dashboard-monitor.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.ops.dashboard-monitor.plist
```

The plist fires every 600s; the app exits 0 outside the business window (no monitor alert for “scheduled off”).

## Tests

```bash
source .venv/bin/activate
pytest -q
```

## Layout

```text
monitor/           # Python package
dashboards.yaml
.env.example
deploy/            # launchd plist template
tests/
docs/PLAN.md
AGENTS.md
```
