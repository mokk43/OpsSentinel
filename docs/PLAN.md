# OpsSentinel — Implementation Plan

Shared understanding from design grilling. Do not implement until this plan is explicitly approved.

## Goal

Weekday visual monitor for OPS dashboard(s): Playwright full-page screenshot → multimodal LLM → POST critical findings to a local signals API. Misses hurt more than false alarms for **board** criticals; watcher self-health pages on real pipeline failures (with a narrow exception for flaky LLM JSON).

## Decisions (locked)

| Topic | Decision |
|---|---|
| Signal path | **Visual only** (no metrics/API/DOM extraction in v1) |
| Error preference | **Misses hurt more** than false alarms |
| When board is critical | **Page immediately** every detection |
| Client-side dedupe | **None** — every critical evaluation POSTs |
| Pipeline failure | **Page immediately** as monitor alert |
| Host | **Always-on machine** |
| Schedule window | **Mon–Fri 09:30–18:10**, timezone **`Asia/Shanghai`** |
| Overnight / weekend | **No runs** outside window; **first weekday run = catch-up** (pages whatever is currently critical) |
| Auth | **Semi-manual login helper** → Playwright `storage_state.json`; runtime loads it |
| Auth failure | Detect login wall → **do not analyze** → `Aliyun.OPS.Monitor` |
| Capture | **One full-page** screenshot, `device_scale_factor=2`, prefer PNG |
| Oversized image | **Fail closed** → monitor alert; never silently downscale-to-mush |
| LLM client | **OpenAI-compatible**, fully env-configurable (`base_url`, `api_key`, `model`) |
| What pages from LLM | **Only when `has_critical_issues: true`** (critical severity present) |
| Non-critical LLM output | **`high` / healthy → log only** (no alert) |
| Unsure model cases | Prompt rule: **unsure → `high`**, not critical |
| Alerter API | Existing: `POST /v1/sources/{SOURCE_UUID}/signals` |
| Board alert `name` | **`Aliyun.OPS`** |
| Monitor alert `name` | **`Aliyun.OPS.Monitor`** |
| Alert body | Exactly `{ name, message, occurredAt }` |
| Source / credential | **One** source UUID + bearer token for both names |
| Process model | **One-shot CLI per tick** + launchd (or equivalent) interval |
| Interval | **Every 10 minutes** (app no-ops outside business window) |
| Overlap | **Skip** if previous run holds lock (log only, no monitor page) |
| Dashboards | **List-ready config**, one entry now; multiple later |
| Multi-board fanout | **One POST per critical board** per cycle |
| Screenshots retention | **All shots kept 12 hours**, then delete |
| Invalid LLM JSON | Log only first failure; **page monitor after 2 consecutive** bad LLM outcomes per board; success resets counter |
| Board `message` format | **Multi-line structured** (board id, summary, URL, issue bullets) |

## Architecture

```text
launchd (every ~10 min)
        │
        ▼
┌───────────────────┐
│ monitor run       │  one-shot process
│  • window gate    │
│  • flock          │
│  • per dashboard  │
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Screenshot        │  Playwright + storage_state
│ full page, dsf=2  │  assert real board (not login)
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ LLM vision        │  OpenAI-compatible API
│ structured JSON   │  critical-only pages
└─────────┬─────────┘
          │
          ▼
┌───────────────────┐
│ Alerter           │  POST signal
│ Aliyun.OPS or     │  name/message/occurredAt
│ Aliyun.OPS.Monitor│
└───────────────────┘
```

## Project layout

```text
ops-dashboard-monitor/   # or repo root package
├── pyproject.toml / requirements.txt
├── .env.example
├── config.py            # pydantic-settings
├── monitor.py           # CLI: run, login
├── screenshot.py
├── analyzer.py
├── alerter.py
├── schedule_window.py   # Asia/Shanghai business hours + first-run catch-up
├── state.py             # flock, consecutive LLM-failure counters, last-success markers
├── retention.py         # 12h screenshot cleanup
├── dashboards.yaml      # list of dashboards (id, url, optional storage_state path)
├── storage_state/       # gitignored Playwright state files
├── screenshots/{board_id}/
├── logs/
└── deploy/
    └── com.ops.dashboard-monitor.plist
```

## Configuration

### Environment (`.env`)

```dotenv
# Timezone / schedule (window hardcoded or configurable; TZ explicit)
TZ_NAME=Asia/Shanghai
CHECK_INTERVAL_MINUTES=10
# Window: Mon–Fri 09:30–18:10 inclusive of last start at 18:10

# LLM (OpenAI-compatible)
LLM_BASE_URL=https://api.moonshot.cn/v1
LLM_API_KEY=
LLM_MODEL=   # pin a specific model id; do not use floating "latest" in prod

# Alerter
ALERT_BASE_URL=http://127.0.0.1:9780
ALERT_SOURCE_UUID=
ALERT_PUSH_CREDENTIAL=

# Storage
SCREENSHOT_DIR=./screenshots
SCREENSHOT_KEEP_HOURS=12
STORAGE_STATE_DIR=./storage_state
LOCK_FILE=./.monitor.lock

# Runtime
RUN_TIMEOUT_SECONDS=480   # hard cap so lock cannot stick forever (~8 min < 10 min interval)
LOG_DIR=./logs
```

### Dashboards file (`dashboards.yaml`)

```yaml
dashboards:
  - id: aliyun-ops
    url: https://example.internal/ops
    # optional override; default: storage_state/{id}.json
    # storage_state: storage_state/aliyun-ops.json
```

## CLI

```bash
# One-time / refresh auth (headed browser)
python -m monitor login --board aliyun-ops

# Single monitoring cycle (launchd invokes this)
python -m monitor run

# Optional helpers
python -m monitor cleanup-screenshots
python -m monitor doctor   # config, state file exists, alerter reachability (optional v1.1)
```

### `run` control flow

1. Load config + dashboard list.
2. If **outside** Mon–Fri 09:30–18:10 `Asia/Shanghai` → log skip → **exit 0** (no monitor alert for “scheduled off”).
3. Acquire **flock**; if not acquired → log “previous run active” → **exit 0**.
4. Start overall deadline timer (`RUN_TIMEOUT_SECONDS`).
5. For each dashboard entry:
   1. Capture full-page screenshot (load `storage_state`).
   2. **Post-goto assertions**: real board markers (URL/title/selector). If login wall / auth dead → POST `Aliyun.OPS.Monitor` → next board.
   3. If image exceeds API size/dimension budget → POST `Aliyun.OPS.Monitor` → next board (no LLM).
   4. Call LLM vision analyzer.
   5. If invalid JSON / schema fail:
      - increment consecutive LLM-failure counter for board
      - if counter ≥ **2** → POST `Aliyun.OPS.Monitor`
      - else log only
      - do **not** treat as healthy board
   6. If valid JSON:
      - reset LLM-failure counter for board
      - if `has_critical_issues` → POST `Aliyun.OPS` (one message for that board)
      - else log status/summary/issues (`high` stays log-only)
6. Run screenshot retention sweeper (or end-of-run cleanup): delete files older than 12h.
7. Release lock; exit non-zero only for unexpected process-level crashes (launchd logs).

**Morning catch-up:** no special branch beyond “first in-window run of the day performs a normal cycle.” Whatever is red overnight is paged on that first successful critical detection. Persist optional `last_successful_run_at` for observability only.

## Screenshot

- Chromium headless, viewport e.g. 1920×1080, `device_scale_factor=2`.
- `storage_state` from login helper.
- `goto(url, wait_until="networkidle", timeout=…)`, short settle wait, then `page.screenshot(path=…, full_page=True)` (PNG).
- Save to `screenshots/{board_id}/{YYYYMMDDTHHMMSS+0800}.png`.
- Before LLM: check file size / dimensions against model limit; on exceed → monitor alert, no resize-to-fit.

## Auth helper (`login`)

- Headed browser; user completes login manually.
- On success (manual confirm or detected board marker), write `storage_state/{board_id}.json`.
- Document refresh procedure when monitor pages `auth-expired`.

## LLM analysis

- OpenAI-compatible `chat.completions` with image_url (base64 data URL) + text.
- System prompt encodes detection rules (DOWN/FAILED, thresholds including ES数据节点 CPU >120%, RDS CPU >70%, etc.) and:
  - return **strict JSON only**
  - `has_critical_issues` true iff any issue `severity == critical`
  - unsure → `high`, not `critical`
  - do not invent issues; evidence must cite visible cue/text
- Expected schema:

```json
{
  "status": "critical" | "warning" | "healthy",
  "has_critical_issues": true,
  "issues": [
    {
      "component": "string",
      "issue": "string",
      "severity": "critical" | "high",
      "evidence": "string"
    }
  ],
  "summary": "string"
}
```

- Parse with JSON schema validation (pydantic). Optional: strip markdown fences once before parse.
- **No second LLM call required** on success path; one retry allowed only for parse/format failure before counting toward consecutive failure (implementation detail: either 0 or 1 in-cycle retry — prefer **one** in-cycle format retry, then count as one failed cycle toward K=2 if still bad).

## Alerting

### HTTP

```bash
curl --fail-with-body \
  -X POST "${ALERT_BASE_URL}/v1/sources/${ALERT_SOURCE_UUID}/signals" \
  -H "Authorization: Bearer ${ALERT_PUSH_CREDENTIAL}" \
  -H "Content-Type: application/json" \
  --data-binary '{"name":"...","message":"...","occurredAt":"..."}'
```

### Board critical — `name: "Aliyun.OPS"`

`occurredAt`: now in `Asia/Shanghai` offset ISO-8601.

`message` (multi-line, length-capped ~4KB):

```text
[{board_id}] {summary}
URL: {url}

- {component} | {issue} | {evidence}
- ...
```

Only **critical** severity issues in the bullet list. If truncated: final line `…and N more`.

### Monitor failure — `name: "Aliyun.OPS.Monitor"`

```text
[{board_id}|global] {stage}: {short_reason}
Detail: {truncated exception or raw LLM excerpt}
```

Stages examples: `auth`, `capture`, `image_budget`, `llm_parse`, `timeout`, `config`.

### Dispatch failure policy

- Retry **once** with short backoff on transient errors.
- If posting a **board** alert fails after retry → attempt `Aliyun.OPS.Monitor` describing dispatch failure.
- If posting a **monitor** alert fails → **log only** (no recursive page loop). Rely on launchd stdout/stderr.

## Scheduling (macOS launchd)

- Plist invokes one-shot `run` every 10 minutes (simple interval is fine).
- App enforces business window + weekday.
- `RunAtLoad` optional; prefer calendar/interval only.
- Logs: `StandardOutPath` / `StandardErrorPath` under `logs/` or `/tmp`.
- Always-on host; do not rely on laptop sleep/wake.

## State on disk

| File | Purpose |
|---|---|
| `.monitor.lock` | flock for single instance |
| `state.json` | per-board consecutive LLM failures; optional last run timestamps |
| `storage_state/{id}.json` | Playwright auth |
| `screenshots/...` | 12h retention |

## Logging

- **loguru** (or stdlib): cycle start/end, skip reasons, per-board outcome, alert HTTP status, retention deletes.
- Never log full bearer token or raw storage_state.

## Dependencies

```text
playwright
openai
requests
pydantic-settings
pyyaml
loguru
```

(No APScheduler required for v1 one-shot model.)

## Implementation order

1. **Scaffold** — package layout, settings, `.env.example`, `dashboards.yaml`, logging.
2. **Alerter client** — POST helper + unit tests with mocked HTTP; message formatters.
3. **Schedule window + lock + state** — pure functions + tests (timezone edges: 09:29, 09:30, 18:10, 18:11, weekend).
4. **Login + screenshot** — Playwright capture, auth assertion hooks, image budget check.
5. **Analyzer** — prompt, parse/validate, consecutive-failure counter integration.
6. **`monitor run` orchestration** — wire steps; fail-closed paths; retention.
7. **launchd plist** + install notes.
8. **Manual dry-run** on always-on host with real board + alerter.

## Explicit non-goals (v1)

- Metrics/API/DOM hybrid detection
- Client-side alert dedupe / state-based resolve events
- Tiled/clip capture fallback (full-page only; oversize = fail closed)
- Multi-source alerter routing
- Auto SSO / password scraping
- 24/7 monitoring
- Shipping screenshots in the alert payload

## Open only at implementation time (not product decisions)

- Exact Playwright assertion selectors/title patterns for “real board vs login” (need one sample of each page).
- Exact LLM max upload bytes / max image dimension for the pinned model.
- Final pinned `LLM_MODEL` string.
- Real `ALERT_SOURCE_UUID` / credential (env only).
- Concrete dashboard URL(s) and `id`s.

## Approval gate

Reply **approve** (or note deltas) to this plan before any implementation work starts.
