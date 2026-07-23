import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_news_agent as agent  # noqa: E402


RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Introducing Example Model</title>
    <link>https://example.com/news/model?utm_source=test</link>
    <pubDate>Wed, 22 Jul 2026 08:30:00 GMT</pubDate>
    <description><![CDATA[<p>A major capability update.</p>]]></description>
  </item>
  <item>
    <title>Old item</title>
    <link>https://example.com/news/old</link>
    <pubDate>Mon, 20 Jul 2026 08:30:00 GMT</pubDate>
  </item>
</channel></rss>"""


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tz = ZoneInfo("Asia/Shanghai")
        self.start = datetime(2026, 7, 22, 0, 0, tzinfo=self.tz)
        self.end = datetime(2026, 7, 23, 0, 0, tzinfo=self.tz)

    @patch.object(agent, "fetch_bytes", return_value=RSS)
    def test_feed_window_and_cleaning(self, _fetch):
        source = {
            "platform": "Example",
            "category": "全球大模型",
            "kind": "feed",
            "source_type": "official_feed",
            "url": "https://example.com/rss.xml",
        }
        items = agent.parse_feed(source, self.start, self.end)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].description, "A major capability update.")
        self.assertEqual(items[0].url, "https://example.com/news/model")

    def test_history_deduplication(self):
        item = agent.NewsItem(
            platform="Example",
            category="全球大模型",
            source_type="official_feed",
            title="Introducing Example Model",
            url="https://example.com/news/model",
            published_at="2026-07-22T16:30:00+08:00",
            description="Update",
        )
        history = {
            "version": 1,
            "items": [
                {
                    "url": item.url,
                    "title_key": agent.title_key(item.title),
                    "reported_at": "2026-07-22T10:00:00+00:00",
                }
            ],
        }
        self.assertEqual(agent.deduplicate([item], history), [])

    def test_feishu_payload_contains_required_keyword(self):
        payload = agent.build_feishu_payload(date(2026, 7, 22), [])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("AI前沿日报", serialized)
        self.assertIn("2026-07-22", serialized)

    def test_report_day_defaults_to_yesterday(self):
        with patch.dict(os.environ, {"REPORT_DATE": "2026-07-22"}):
            self.assertEqual(agent.resolve_report_day(), date(2026, 7, 22))


if __name__ == "__main__":
    unittest.main()
