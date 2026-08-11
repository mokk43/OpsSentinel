# LLM-Based OPS Dashboard Monitoring Solution

## Overview

A Python-based automated monitoring system that periodically captures screenshots of an OPS dashboard website, uses a multimodal LLM (vision model) to analyze the screenshot for critical issues, and triggers alerts to a local alerting service when problems are detected.

## Architecture

```text
┌─────────────────────┐    ┌──────────────┐    ┌─────────────┐    ┌────────────────┐
│ Scheduler           │───▶│ Screenshot   │───▶│ LLM Vision  │───▶│ Alert Service  │
│ (cron/APScheduler)  │    │ (Playwright) │    │ (Analysis)  │    │ (HTTP POST)    │
└─────────────────────┘    └──────────────┘    └─────────────┘    └────────────────┘
```

## Tech Stack

| Component | Tool | Reason |
|---|---|---|
| Screenshot | **Playwright** (Python) | Headless Chromium, handles JS-rendered dashboards, supports auth/cookies |
| LLM Vision | **OpenAI compatible LLM** via API | Multimodal, understands UI layouts, tables, charts, status indicators |
| Scheduler | **APScheduler** or cron + launchd | Reliable periodic execution on macOS |
| Alert Dispatch | **requests** (HTTP POST) | Call your local alerting service endpoint |
| Config | **pydantic-settings** + `.env` | Clean config management |
| Logging | **loguru** | Simple, structured logging |

## Detailed Workflow

### Step 1: Screenshot Capture

Use Playwright with a persistent browser context (avoids cold start on each run):

```python
from playwright.sync_api import sync_playwright

def capture_dashboard(url: str, output_path: str, cookies: list = None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            device_scale_factor=2  # Retina-quality for better OCR
        )
        if cookies:
            context.add_cookies(cookies)

        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(2000)  # Extra wait for dynamic content
        page.screenshot(path=output_path, full_page=True)
        browser.close()
    return output_path
```

**Key considerations:**

- Use `device_scale_factor=2` for high-DPI screenshots — LLM reads text more accurately.
- `wait_until="networkidle"` ensures all async data loads before capture.
- Support cookies/auth for protected dashboards.
- Optionally capture specific sections (clip regions) if dashboard is large.

### Step 2: LLM Vision Analysis

Send the screenshot to a multimodal LLM with a structured prompt:

```python
import base64
import json
from openai import OpenAI

def analyze_screenshot(image_path: str, client: OpenAI, model: str = "gpt-4o"):
    with open(image_path, "rb") as f:
        image_b64 = base64.b64encode(f.read()).decode()

    system_prompt = """You are an SRE assistant. Analyze OPS dashboard screenshots and flag only issues that need immediate human attention.

    Detect:
    - DOWN / FAILED / ERROR services (red or crossed-out indicators)
    - Resource use >90% (CPU, memory, disk, network), except for ES数据节点 group
    - for ES数据节点 group CPU Usage use > 120%
    - Latency spikes (>5x baseline)
    - Error rate >5%
    - DB/connection pool failures
    - RDS CPU Usage use >70%
    - Growing queue backlog
    - Unexpected node/pod/instance drop
    - Text: critical, fatal, outage, unreachable, timeout
    Ignore:
    - Green/healthy states
    - Yellow warnings marked acknowledged or scheduled
    - Normal metrics
    - Resolved/historical incidents
    Return strict JSON only:
    {
      "status": "critical" | "warning" | "healthy",
      "has_critical_issues": true | false,
      "issues": [
        {
          "component": "<name>",
          "issue": "<short problem>",
          "severity": "critical" | "high",
          "evidence": "<exact text/visual cue from the image>"
      ],
      "summary": "<one-sentence health assessment>"
    }
    If fully healthy: status "healthy", has_critical_issues false, issues [].
    Do not invent issues. If unsure, use severity "high", not "critical".
"""

    import os
    import base64

    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ.get("MOONSHOT_API_KEY"),
        base_url="https://api.moonshot.cn/v1",
    )

    # 在这里，你需要将 kimi.png 文件替换为你想让 Kimi 识别的图片的地址
    image_path = "kimi.png"

    with open(image_path, "rb") as f:
        image_data = f.read()

    # 我们使用标准库 base64.b64encode 函数将图片编码成 base64 格式的 image_url
    image_url = f"data:image/{os.path.splitext(image_path)[1].lstrip('.')};base64,{base64.b64encode(image_data).decode('utf-8')}"


    response = client.chat.completions.create(
        model="kimi-2.5",
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                # 注意这里，content 由原来的 str 类型变更为一个 list，这个 list 中包含多个部分的内容，图片（image_url）是一个部分（part），
                # 文字（text）是一个部分（part）
                "content": [
                    {
                        "type": "image_url", # <-- 使用 image_url 类型来上传图片，内容为使用 base64 编码过的图片内容
                        "image_url": {
                            "url": image_url,
                        },
                    },
                    {
                        "type": "text",
                        "text": "Analyze this OPS dashboard for critical issues.", 
                    },
                ],
            },
        ],
        thinking={"type": "disabled"}
    )

    return json.loads(response.choices[0].message.content)
```


### Step 3: Alert Dispatch

```python
import requests
from datetime import datetime

def send_alert(issues: list, alert_endpoint: str, screenshot_path: str = None):
    for issue in issues:
        payload = {
            "timestamp": datetime.now().isoformat(),
            "source": "ops-dashboard-monitor",
            "severity": issue["severity"],
            "component": issue["component"],
            "message": issue["description"],
            "evidence": issue["evidence"],
        }

        # POST to your local alerting service
        response = requests.post(
            alert_endpoint,
            json=payload,
            timeout=10
        )

        if response.status_code != 200:
            logger.error(f"Alert dispatch failed: {response.status_code}")
```

**Adaptable to various alerting services:**

- Webhook (generic HTTP POST)
- Slack incoming webhook
- PagerDuty Events API
- Local script execution
- macOS native notification (`osascript -e 'display notification ...'`)

### Step 4: Scheduler

#### Option A — APScheduler (in-process, recommended for simplicity)

```python
from apscheduler.schedulers.blocking import BlockingScheduler

scheduler = BlockingScheduler()

@scheduler.scheduled_job('interval', minutes=5)
def monitor_job():
    run_monitoring_cycle()

scheduler.start()
```

#### Option B — macOS launchd (survives reboots, runs as daemon)

Create `~/Library/LaunchAgents/com.ops.dashboard-monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "...">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ops.dashboard-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/path/to/monitor.py</string>
    </array>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ops-monitor.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ops-monitor-error.log</string>
</dict>
</plist>
```

Load:

```bash
launchctl load ~/Library/LaunchAgents/com.ops.dashboard-monitor.plist
```

## Complete Project Structure

```text
ops-dashboard-monitor/
├── monitor.py           # Main entry point
├── config.py            # Configuration (pydantic-settings)
├── screenshot.py        # Playwright screenshot logic
├── analyzer.py          # LLM vision analysis
├── alerter.py           # Alert dispatch
├── .env                 # Secrets (API keys, URLs)
├── requirements.txt     # Dependencies
├── screenshots/         # Captured screenshots (auto-cleaned)
└── logs/                # Log files
```

## Requirements

```text
playwright>=1.40
openai>=1.0
apscheduler>=3.10
requests>=2.31
pydantic-settings>=2.0
loguru>=0.7
```

## Installation

```bash
cd ops-dashboard-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration (`.env`)

```dotenv
# Dashboard
DASHBOARD_URL=https://your-ops-dashboard.internal/status
DASHBOARD_COOKIES=[] # JSON array of cookie objects if auth needed

# LLM
OPENAI_API_KEY=sk-...
LLM_MODEL=kimi-2.6
# Or use a proxy endpoint:
# OPENAI_BASE_URL=https://your-proxy/v1

# Alert
ALERT_ENDPOINT=http://localhost:8080/api/alerts

# Schedule
CHECK_INTERVAL_MINUTES=5

# Storage
SCREENSHOT_DIR=./screenshots
KEEP_SCREENSHOTS_HOURS=24
```
