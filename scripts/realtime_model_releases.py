#!/usr/bin/env python3
"""Push important same-day AI model and product releases to Feishu."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

from ai_news_agent import (
    MAX_CANDIDATES,
    REPORT_TZ,
    ROOT,
    NewsItem,
    analyze,
    collect_source,
    escape_markdown,
    is_model_relevant,
    load_sources,
    log,
    normalize_url,
    send_feishu,
    title_key,
)


HISTORY_FILE = ROOT / "data" / "realtime_model_history.json"
REPORTS_DIR = ROOT / "reports" / "realtime-models"
PUSH_LEVELS = {
    level.strip().upper()
    for level in os.getenv("REALTIME_PUSH_LEVELS", "S,A").split(",")
    if level.strip()
}


def empty_history() -> dict[str, Any]:
    return {"version": 1, "updated_at": "", "seen": [], "notifications": []}


def load_realtime_history() -> dict[str, Any]:
    if not HISTORY_FILE.exists():
        return empty_history()
    try:
        payload = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return empty_history()
    if not isinstance(payload.get("seen"), list):
        payload["seen"] = []
    if not isinstance(payload.get("notifications"), list):
        payload["notifications"] = []
    return payload


def new_candidates(
    items: list[NewsItem], history: dict[str, Any]
) -> list[NewsItem]:
    seen_urls = {record.get("url", "") for record in history["seen"]}
    seen_titles = {record.get("title_key", "") for record in history["seen"]}
    run_urls: set[str] = set()
    run_titles: set[str] = set()
    result: list[NewsItem] = []
    for item in sorted(items, key=lambda row: row.published_at, reverse=True):
        url = normalize_url(item.url)
        key = title_key(item.title)
        if (
            not url
            or not key
            or url in seen_urls
            or key in seen_titles
            or url in run_urls
            or key in run_titles
        ):
            continue
        run_urls.add(url)
        run_titles.add(key)
        result.append(item)
    return result


def build_feishu_payload(items: list[dict[str, Any]], checked_at: datetime) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": (
                f"⏱️ **发现时间：** {checked_at.strftime('%Y-%m-%d %H:%M')}（UTC+8）\n"
                f"📊 **重大动态：** {len(items)} 条\n"
                "🔎 官方信源｜模型与 AI 产品重大更新｜已自动去重"
            ),
        },
        {"tag": "hr"},
    ]
    for index, item in enumerate(items, 1):
        model = item["model_or_product"]
        if item.get("version"):
            model = f"{model}（{item['version']}）"
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"**{index}. [{item['importance']}] {escape_markdown(item['title'])}**\n"
                    f"**模型/产品：** {escape_markdown(model)}\n"
                    f"**核心变化：** {escape_markdown(item['capability_change'])}\n"
                    f"**PM 判断：** {escape_markdown(item['pm_judgement'])}\n"
                    f"**建议动作：** {escape_markdown(item['recommended_action'])}\n"
                    f"[查看官方来源]({item['url']}) · {escape_markdown(item['platform'])}"
                ),
            }
        )
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": "AI前沿日报 · AI 产品与模型实时雷达 · 重要结论请结合官方原文复核",
                    }
                ],
            },
        ]
    )
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "red" if any(item["importance"] == "S" for item in items) else "orange",
                "title": {
                    "tag": "plain_text",
                    "content": "🚨 AI产品 / 模型重大动态",
                },
            },
            "elements": elements,
        },
    }


def update_history(
    history: dict[str, Any],
    evaluated: list[NewsItem],
    selected: list[dict[str, Any]],
    now: datetime,
) -> None:
    seen_cutoff = now.astimezone(timezone.utc) - timedelta(days=30)
    notification_cutoff = now.astimezone(timezone.utc) - timedelta(days=120)

    retained_seen = []
    for record in history["seen"]:
        try:
            seen_at = datetime.fromisoformat(record.get("seen_at", ""))
        except ValueError:
            continue
        if seen_at >= seen_cutoff:
            retained_seen.append(record)
    retained_notifications = []
    for record in history["notifications"]:
        try:
            reported_at = datetime.fromisoformat(record.get("reported_at", ""))
        except ValueError:
            continue
        if reported_at >= notification_cutoff:
            retained_notifications.append(record)

    seen_at = now.astimezone(timezone.utc).isoformat()
    retained_seen.extend(
        {
            "url": normalize_url(item.url),
            "title_key": title_key(item.title),
            "seen_at": seen_at,
        }
        for item in evaluated
    )
    retained_notifications.extend(
        {**item, "reported_at": seen_at}
        for item in selected
    )
    history.update(
        {
            "version": 1,
            "updated_at": seen_at,
            "seen": retained_seen,
            "notifications": retained_notifications,
        }
    )
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(
        json.dumps(history, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def save_report(history: dict[str, Any], report_day: str) -> Path | None:
    items = []
    for item in history["notifications"]:
        try:
            local_day = (
                datetime.fromisoformat(item.get("reported_at", ""))
                .astimezone(REPORT_TZ)
                .date()
                .isoformat()
            )
        except ValueError:
            continue
        if local_day == report_day:
            items.append(item)
    if not items:
        return None
    lines = [
        f"# AI 产品与模型实时动态｜{report_day}",
        "",
        "仅记录已实时推送至飞书的 S/A 级重大更新；完整信息仍会进入次日 9 点日报。",
        "",
    ]
    for index, item in enumerate(items, 1):
        lines.extend(
            [
                f"## {index}. [{item['importance']}] {item['title']}",
                "",
                f"- 模型/产品：{item['model_or_product']}"
                + (f"（{item['version']}）" if item.get("version") else ""),
                f"- 核心变化：{item['capability_change']}",
                f"- PM 判断：{item['pm_judgement']}",
                f"- 建议动作：{item['recommended_action']}",
                f"- 官方来源：{item['url']}",
                "",
            ]
        )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / f"{report_day}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    now = datetime.now(REPORT_TZ)
    history = load_realtime_history()
    # The first run intentionally scans only today to avoid a noisy historical
    # backfill. Later runs include yesterday to cover releases posted near midnight.
    first_day = now.date() if not history["seen"] else now.date() - timedelta(days=1)
    start = datetime.combine(first_day, dt_time.min, REPORT_TZ)
    end = now + timedelta(minutes=10)
    sources = load_sources()

    collected: list[NewsItem] = []
    failures = 0
    with ThreadPoolExecutor(max_workers=min(10, len(sources))) as pool:
        futures = {
            pool.submit(collect_source, source, start, end): source
            for source in sources
        }
        for future in as_completed(futures):
            source = futures[future]
            try:
                collected.extend(future.result())
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log(f"实时信源失败：{source['platform']} ({exc})")

    candidates = [
        item
        for item in new_candidates(collected, history)
        if is_model_relevant(item)
    ][:MAX_CANDIDATES]
    log(
        f"实时扫描 {len(sources)} 个信源，采集 {len(collected)} 条，"
        f"新增有效候选 {len(candidates)} 条；失败信源 {failures}"
    )
    if not candidates:
        return 0

    # Reuse the daily analyzer so the conservative keyword fallback remains
    # available when GitHub Models is temporarily unavailable.
    selected = analyze(candidates, now.date())
    selected = [
        item for item in selected
        if item.get("importance", "").upper() in PUSH_LEVELS
    ]

    if selected:
        send_feishu(build_feishu_payload(selected, now))
    else:
        log("新增候选未达到实时 S/A 级门槛，不推送飞书")

    update_history(history, candidates, selected, now)
    report_path = save_report(history, now.date().isoformat())
    if report_path:
        log(f"实时动态已归档：{report_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
