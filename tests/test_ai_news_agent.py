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

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/news/old-post</loc>
    <lastmod>2026-07-22T08:00:00Z</lastmod>
  </url>
</urlset>"""

OLD_PAGE = b"""<!doctype html><html><head>
<title>Old announcement</title>
<script type="application/ld+json">
{"@type":"NewsArticle","datePublished":"2025-10-16T08:00:00Z"}
</script>
</head><body><h1>Old announcement</h1></body></html>"""


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
        self.assertIn("模型与多模态", serialized)
        self.assertIn("2026-07-22", serialized)

    def test_model_report_contains_pm_fields_and_table(self):
        items = [
            {
                "importance": "S",
                "title": "示例多模态模型正式发布",
                "model_or_product": "Example Omni",
                "version": "2.0",
                "capability_change": "新增视频生成与原生音频能力。",
                "type": "多模态",
                "pm_judgement": "可减少视频与音频分段生成的产品链路。",
                "recommended_action": "使用现有业务素材完成质量、时延与成本评测。",
                "platform": "Example",
                "url": "https://example.com/news/model",
                "published_at": "2026-07-22T16:30:00+08:00",
                "original_title": "Example Omni 2.0",
            }
        ]
        markdown = agent.build_markdown(date(2026, 7, 22), items)
        self.assertIn("| 平台 | 模型/产品 | 级别 | 核心变化 | 类型 | 官方来源 |", markdown)
        self.assertIn("PM 判断", markdown)
        self.assertIn("建议动作", markdown)

        payload = agent.build_feishu_payload(date(2026, 7, 22), items)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("Example Omni（2.0）", serialized)
        self.assertIn("核心变化", serialized)
        self.assertIn("建议动作", serialized)

    def test_fallback_excludes_non_model_marketing_news(self):
        item = agent.NewsItem(
            platform="Example",
            category="全球大模型",
            source_type="official_feed",
            title="Customer story: a new partnership",
            url="https://example.com/news/customer",
            published_at="2026-07-22T16:30:00+08:00",
            description="A case study about an enterprise customer.",
        )
        self.assertFalse(agent.is_model_relevant(item))
        self.assertEqual(agent.fallback_analysis([item]), [])

    def test_fallback_excludes_nightly_tool_build(self):
        item = agent.NewsItem(
            platform="Example CLI",
            category="AI编程",
            source_type="official_github_release",
            title="Release v0.52.0-nightly.20260722",
            url="https://example.com/releases/nightly",
            published_at="2026-07-22T16:30:00+08:00",
            description="New model support and agent updates.",
        )
        self.assertFalse(agent.is_model_relevant(item))
        self.assertEqual(agent.fallback_analysis([item]), [])

    def test_empty_report_is_concise(self):
        markdown = agent.build_markdown(date(2026, 7, 22), [])
        self.assertEqual(
            markdown,
            "今日（2026-07-22）所有监控平台均无经官方核验的模型、多模态或 AIGC 能力更新\n",
        )

    def test_sitemap_lastmod_does_not_republish_old_article(self):
        source = {
            "platform": "Example",
            "category": "全球大模型",
            "kind": "sitemap",
            "source_type": "official_sitemap",
            "url": "https://example.com/sitemap.xml",
            "include": ["/news/"],
            "max_pages": 5,
        }

        def fake_fetch(url, timeout=agent.REQUEST_TIMEOUT):
            return SITEMAP if url.endswith("sitemap.xml") else OLD_PAGE

        with patch.object(agent, "fetch_bytes", side_effect=fake_fetch):
            items = agent.parse_sitemap(source, self.start, self.end)
        self.assertEqual(items, [])

    def test_report_day_defaults_to_yesterday(self):
        with patch.dict(os.environ, {"REPORT_DATE": "2026-07-22"}):
            self.assertEqual(agent.resolve_report_day(), date(2026, 7, 22))


if __name__ == "__main__":
    unittest.main()
