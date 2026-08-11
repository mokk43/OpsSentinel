# AGENTS.md — OpsSentinel

Guidance for coding agents working in this repository.

## What this project is

OpsSentinel is a **weekday visual OPS-dashboard monitor**:

1. Playwright captures a full-page screenshot
2. An OpenAI-compatible multimodal LLM analyzes it
3. Critical findings POST to a local signals API

It is **visual-only** in v1 (no metrics/API/DOM extraction). Design decisions are frozen in [`docs/PLAN.md`](docs/PLAN.md). Prefer that doc over the older sketch in `ops_dashboard_monitor_solution.md` when they disagree.

## Layout

```text
monitor/                 # installable-by-path Python package (python -m monitor)
  __main__.py            # CLI: run | login | cleanup-screenshots | doctor
  config.py              # pydantic-settings + dashboards.yaml loader
  screenshot.py          # Playwright capture + auth-wall checks + image budget
  analyzer.py            # LLM vision call + JSON parse/retry
  alerter.py             # signals HTTP client + message formatters
  schedule_window.py     # Mon–Fri 09:30–18:10 Asia/Shanghai
  state.py               # flock + consecutive LLM-failure counters
  retention.py           # screenshot sweeper
  models.py              # AnalysisResult / Issue
  logging_setup.py
tests/                   # pytest unit tests (no live LLM/alerter required)
deploy/                  # launchd plist template
docs/PLAN.md             # approved product/engineering plan
dashboards.yaml          # list of boards (id, url, optional markers)
.env.example             # env contract (copy to .env; never commit .env)
```

Runtime artifacts (gitignored): `.env`, `storage_state/`, `screenshots/`, `logs/`, `.monitor.lock`, `state.json`.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

cp .env.example .env          # then fill secrets / model / source UUID
# edit dashboards.yaml with real URL(s)

python -m monitor doctor
python -m monitor login --board <id>
python -m monitor run --force # ignore business window
python -m monitor run         # respects window + lock
python -m monitor cleanup-screenshots

pytest -q
```

`pytest.ini` sets `pythonpath = .` so tests import `monitor` without an editable install.

## Locked product decisions (do not casually reverse)

| Topic | Decision |
|---|---|
| Signal path | Visual only |
| Miss vs false alarm | **Misses hurt more** for board criticals |
| Board page | Immediate when `has_critical_issues` (critical severity only) |
| Client dedupe | **None** |
| High / warning / healthy | Log only — no alert |
| Unsure model cases | severity `high`, not `critical` |
| Board alert name | `Aliyun.OPS` |
| Monitor alert name | `Aliyun.OPS.Monitor` |
| Alert body | Exactly `{ name, message, occurredAt }` |
| Alerter auth | One source UUID + bearer; `POST /v1/sources/{uuid}/signals` |
| Schedule | Mon–Fri **09:30–18:10** `Asia/Shanghai`; 10‑minute one-shot |
| Outside window | Exit 0; **no** monitor page for “scheduled off” |
| Overlap | Skip if lock held (log only) |
| Auth | Semi-manual `login` → Playwright `storage_state` |
| Auth / capture / image budget failure | Immediate `Aliyun.OPS.Monitor` |
| Oversized screenshot | Fail closed — never silent downscale |
| Capture geometry | Single full-page PNG, `device_scale_factor=2` |
| Multi-board | List-ready config; **one POST per critical board** |
| LLM bad JSON | Log first failure; page monitor after **2 consecutive** per board |
| Screenshots | Keep **12 hours**, then delete |
| Board `message` | Multi-line: `[board_id] summary`, URL, critical bullets only |

If a change would alter any row above, update `docs/PLAN.md` and call it out explicitly — do not silently diverge.

## Coding conventions

- **Python 3.11+** style: type hints, `from __future__ import annotations`, pathlib.
- Config via **pydantic-settings** + env; board list via **YAML**. Paths in settings resolve relative to project root.
- Logging via **loguru**. Never log bearer tokens, full `.env`, or raw `storage_state`.
- Prefer small modules with a single job (matches current package split).
- Fail **closed** on watcher faults that create a blind spot (auth, capture, image budget, run timeout). The intentional exception is flaky LLM JSON (K=2 consecutive).
- Alert dispatch: retry once; if a **board** POST fails, attempt a monitor POST; if a **monitor** POST fails, log only (no recursive page loop).
- Do not add APScheduler for v1 — process model is **one-shot CLI + launchd**.
- Do not put secrets or real dashboard URLs in committed files. Keep placeholders in `dashboards.yaml` / `.env.example`.

## Testing expectations

- Add or extend **unit tests** for pure logic you touch: window edges, message formatting, JSON parse, state counters, retention, image budget, alerter HTTP (mocked).
- Do **not** require live LLM, real dashboards, or a running alerter in CI/unit tests.
- After code changes, run: `pytest -q`.
- Manual path when validating end-to-end: `doctor` → `login` → `run --force` on the always-on host.

## Safe change patterns

**Good defaults**

- New optional env knobs with safe defaults
- Better login-wall markers / per-board overrides in YAML
- Clearer monitor `message` detail / stages
- More unit tests around parse and window boundaries
- Prompt clarifications that preserve critical-vs-high rules

**Ask / plan first**

- Changing alert `name` values or JSON contract
- Adding dedupe, resolve events, or multi-run confirmation
- Tiled/clip capture or auto-resize of screenshots
- Metrics/DOM hybrid detection
- 24/7 schedule or timezone changes
- New third-party services or changing the one-shot/launchd model

## Out of scope (v1)

- Shipping screenshots in the alert payload
- Auto SSO / password scraping
- Multi-source alerter routing
- Client-side incident correlation / ack
- Floating LLM model ids like `latest` in production config (pin a model)

## Agent workflow notes

1. Read `docs/PLAN.md` before large behavior changes.
2. Keep diffs focused; do not “clean up” unrelated files or user runtime state.
3. Leave `.env`, `storage_state/`, and live credentials untouched unless the user asks.
4. When documenting, prefer updating `README.md` for operator steps and `docs/PLAN.md` for decision history.
5. Implementation entrypoint for humans and launchd is always: `python -m monitor …` from the project root with venv active.
