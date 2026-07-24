#!/usr/bin/env python3
"""Push important official AI service incidents to Feishu with deduplication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATE_PATH = ROOT / "data" / "incident_history.json"
REPORT_DIR = ROOT / "reports" / "incidents"
KEYWORD = "AI前沿日报"

PROVIDERS = (
    {
        "name": "OpenAI",
        "api": "https://status.openai.com/api/v2/incidents.json",
        "page": "https://status.openai.com",
    },
    {
        "name": "Anthropic",
        "api": "https://status.claude.com/api/v2/incidents.json",
        "page": "https://status.claude.com",
    },
)

IMPORTANT_IMPACTS = {"major", "critical"}
IMPORTANT_TITLE_WORDS = (
    "outage",
    "unavailable",
    "elevated error",
    "degraded",
    "service disruption",
    "服务中断",
    "故障",
    "错误率",
)
FINAL_STATUSES = {"resolved", "completed", "postmortem"}
STATUS_ZH = {
    "investigating": "调查中",
    "identified": "已定位",
    "monitoring": "恢复监控中",
    "resolved": "已恢复",
    "completed": "已完成",
    "postmortem": "复盘已发布",
}


@dataclass(frozen=True)
class Alert:
    provider: str
    incident_id: str
    incident_name: str
    incident_status: str
    impact: str
    update_id: str
    update_body: str
    updated_at: str
    components: tuple[str, ...]
    url: str


def request_json(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "AIan-realtime-incident-radar/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def load_state(path: Path = STATE_PATH) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "providers": {}}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "providers": {}}
    if not isinstance(value, dict):
        return {"version": 1, "providers": {}}
    value.setdefault("version", 1)
    value.setdefault("providers", {})
    return value


def save_state(state: dict[str, Any], path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def is_important(incident: dict[str, Any]) -> bool:
    impact = str(incident.get("impact") or "").lower()
    title = str(incident.get("name") or "").lower()
    return impact in IMPORTANT_IMPACTS or any(word in title for word in IMPORTANT_TITLE_WORDS)


def update_key(update: dict[str, Any]) -> str:
    explicit = str(update.get("id") or "").strip()
    if explicit:
        return explicit
    material = "|".join(
        str(update.get(field) or "") for field in ("status", "body", "updated_at", "created_at")
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def incident_url(provider_page: str, incident: dict[str, Any]) -> str:
    shortlink = str(incident.get("shortlink") or "").strip()
    if shortlink:
        return shortlink
    return f"{provider_page.rstrip('/')}/incidents/{incident.get('id', '')}"


def collect_alerts(
    provider: dict[str, str],
    payload: dict[str, Any],
    state: dict[str, Any],
) -> list[Alert]:
    provider_state = state["providers"].setdefault(provider["name"], {})
    alerts: list[Alert] = []

    for incident in payload.get("incidents") or []:
        if not isinstance(incident, dict) or not is_important(incident):
            continue
        incident_id = str(incident.get("id") or "").strip()
        if not incident_id:
            continue
        status = str(incident.get("status") or "investigating").lower()
        record = provider_state.get(incident_id)
        if record is None and status in FINAL_STATUSES:
            # Do not replay old resolved incidents on the first run.
            continue

        updates = [item for item in (incident.get("incident_updates") or []) if isinstance(item, dict)]
        updates.sort(key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))
        if not updates:
            updates = [
                {
                    "id": f"{incident_id}:{status}",
                    "status": status,
                    "body": incident.get("name") or "",
                    "updated_at": incident.get("updated_at") or incident.get("created_at") or "",
                }
            ]

        seen = set((record or {}).get("seen_update_ids") or [])
        pending = [item for item in updates if update_key(item) not in seen]
        if record is None and pending:
            # For a newly discovered active incident, send one current summary instead of
            # replaying every earlier update from before the monitor saw it.
            pending = [pending[-1]]

        components = tuple(
            str(component.get("name") or "").strip()
            for component in (incident.get("components") or [])
            if isinstance(component, dict) and str(component.get("name") or "").strip()
        )
        for update in pending:
            update_id = update_key(update)
            alerts.append(
                Alert(
                    provider=provider["name"],
                    incident_id=incident_id,
                    incident_name=str(incident.get("name") or "Service incident").strip(),
                    incident_status=str(update.get("status") or status).lower(),
                    impact=str(incident.get("impact") or "major").lower(),
                    update_id=update_id,
                    update_body=str(update.get("body") or "").strip(),
                    updated_at=str(
                        update.get("updated_at")
                        or update.get("created_at")
                        or incident.get("updated_at")
                        or ""
                    ),
                    components=components,
                    url=incident_url(provider["page"], incident),
                )
            )

    return alerts


def pm_action(alert: Alert) -> str:
    if alert.incident_status in FINAL_STATUSES:
        return "可逐步恢复任务，并核对失败队列、重复请求、扣费和数据完整性。"
    return "建议启用重试、熔断和备用模型；批量生成与 Agent 长任务暂缓执行。"


def build_feishu_payload(alert: Alert) -> dict[str, Any]:
    status_zh = STATUS_ZH.get(alert.incident_status, alert.incident_status or "状态更新")
    components = "、".join(alert.components[:8]) if alert.components else "官方未列出具体组件"
    body = alert.update_body or "官方状态页发布了新的事件进展。"
    if len(body) > 450:
        body = body[:447] + "…"
    color = "green" if alert.incident_status in FINAL_STATUSES else "red"
    icon = "✅" if alert.incident_status in FINAL_STATUSES else "🚨"

    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {
                    "tag": "plain_text",
                    "content": f"{icon} {KEYWORD}｜S级实时进展｜{alert.provider}",
                },
            },
            "elements": [
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": (
                            f"**事件｜** {alert.incident_name}\n"
                            f"**状态｜** {status_zh}\n"
                            f"**官方进展｜** {body}\n"
                            f"**受影响范围｜** {components}\n"
                            f"**对 AI 产品的影响｜** {pm_action(alert)}"
                        ),
                    },
                },
                {
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "text": {"tag": "plain_text", "content": "查看官方状态"},
                            "url": alert.url,
                            "type": "primary",
                        }
                    ],
                },
            ],
        },
    }


def send_feishu(webhook: str, payload: dict[str, Any], timeout: int = 20) -> None:
    request = urllib.request.Request(
        webhook,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("code", result.get("StatusCode", 0)) not in (0, None):
        raise RuntimeError(f"Feishu rejected the message: {result}")


def mark_sent(state: dict[str, Any], alert: Alert) -> None:
    provider_state = state["providers"].setdefault(alert.provider, {})
    record = provider_state.setdefault(
        alert.incident_id,
        {"seen_update_ids": [], "status": alert.incident_status, "url": alert.url},
    )
    seen = record.setdefault("seen_update_ids", [])
    if alert.update_id not in seen:
        seen.append(alert.update_id)
    record["seen_update_ids"] = seen[-30:]
    record["status"] = alert.incident_status
    record["url"] = alert.url
    record["last_seen_at"] = datetime.now(timezone.utc).isoformat()


def append_report(alerts: list[Alert], report_dir: Path = REPORT_DIR) -> Path | None:
    if not alerts:
        return None
    now = datetime.now().astimezone()
    path = report_dir / f"{now.date().isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", encoding="utf-8") as stream:
        if new_file:
            stream.write(f"# S 级实时事件｜{now.date().isoformat()}\n\n")
        for alert in alerts:
            status = STATUS_ZH.get(alert.incident_status, alert.incident_status)
            stream.write(
                f"## {alert.provider}｜{alert.incident_name}\n\n"
                f"- 状态：{status}\n"
                f"- 官方进展：{alert.update_body or '官方状态页发布了新的事件进展。'}\n"
                f"- 影响范围：{'、'.join(alert.components) or '官方未列出具体组件'}\n"
                f"- 时间：{alert.updated_at}\n"
                f"- 来源：{alert.url}\n\n"
            )
    return path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Do not send or persist state.")
    parser.add_argument("--state-path", type=Path, default=STATE_PATH)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = load_state(args.state_path)
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not args.dry_run and not webhook:
        print("FEISHU_WEBHOOK is required.", file=sys.stderr)
        return 2

    sent: list[Alert] = []
    failures: list[str] = []
    for provider in PROVIDERS:
        try:
            payload = request_json(provider["api"])
            alerts = collect_alerts(provider, payload, state)
            for alert in alerts:
                if args.dry_run:
                    print(json.dumps(build_feishu_payload(alert), ensure_ascii=False))
                else:
                    send_feishu(webhook, build_feishu_payload(alert))
                    mark_sent(state, alert)
                    sent.append(alert)
        except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
            failures.append(f"{provider['name']}: {exc}")

    if not args.dry_run:
        save_state(state, args.state_path)
        append_report(sent, args.report_dir)
    print(f"实时事件检查完成：发送 {len(sent)} 条；信源失败 {len(failures)} 个。")
    for failure in failures:
        print(f"warning: {failure}", file=sys.stderr)
    return 1 if len(failures) == len(PROVIDERS) else 0


if __name__ == "__main__":
    raise SystemExit(main())
