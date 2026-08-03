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

DELAYED_SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url>
    <loc>https://example.com/news/introducing-layers</loc>
    <lastmod>2026-07-31T03:06:55Z</lastmod>
  </url>
</urlset>"""

DELAYED_PAGE = b"""<!doctype html><html><head>
<title>Introducing Layers</title>
<meta property="og:description" content="Precision editing for every element of an image">
<meta property="article:published_time" content="2026-07-29T17:28:48Z">
</head><body><h1>Introducing Layers</h1></body></html>"""

UPDATED_MODEL_PAGE = b"""<!doctype html><html><head>
<title>Introducing GPT-Live</title>
<meta property="og:description" content="A new generation of voice models.">
<meta property="article:published_time" content="2026-07-08T00:00:00Z">
</head><body>
<h1>Introducing GPT-Live</h1>
<p><i><b>Update July 31, 2026:</b> Supported audio generated with GPT-Live now includes
SynthID watermarking, and the verification tool now provides API access.</i></p>
</body></html>"""


EMBEDDED_ARTICLE_LIST = b"""<script>
{"ArticleMeta":{"PublishDate":1784649600000,"ResearchArea":[{"ResearchAreaName":"Models"}]},
"ArticleSubContentEn":{"Title":"Introducing Seedance 2.5","Abstract":"A video creation model with upgraded multimodal referencing.","TitleKey":"introducing-seedance-2-5"},
"ArticleSubContentZh":{"Title":"Seedance 2.5 \\u6b63\\u5f0f\\u53d1\\u5e03","Abstract":"30 \\u79d2\\u957f\\u53d9\\u4e8b\\uff0c\\u591a\\u6a21\\u6001\\u53c2\\u8003\\u80fd\\u529b\\u5168\\u9762\\u5347\\u7ea7","TitleKey":"seedance-2-5"}}]
</script>""".replace(b"\n", b"")


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
        self.assertIn("多模态模型优先", serialized)
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
                "evaluation_basis": "官方基准",
                "evaluation_strengths": "官方基准显示视频与音频协同能力提升。",
                "evaluation_weaknesses": "官方材料未披露复杂场景下的稳定性。",
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
        self.assertIn("测评依据", markdown)
        self.assertIn("测评优点", markdown)
        self.assertIn("测评不足", markdown)
        self.assertIn("建议动作", markdown)

        payload = agent.build_feishu_payload(date(2026, 7, 22), items)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("Example Omni（2.0）", serialized)
        self.assertIn("核心变化", serialized)
        self.assertIn("测评依据", serialized)
        self.assertIn("测评优点", serialized)
        self.assertIn("测评不足", serialized)
        self.assertIn("建议动作", serialized)

    def test_evaluation_without_evidence_is_marked_pending(self):
        evaluation = agent.normalize_evaluation_fields(
            {
                "evaluation_basis": "独立实测",
                "evaluation_strengths": "质量显著提升。",
                "evaluation_weaknesses": "速度较慢。",
            }
        )
        self.assertEqual(evaluation["evaluation_basis"], "待实测")
        self.assertTrue(evaluation["evaluation_strengths"].startswith("待实测："))
        self.assertTrue(evaluation["evaluation_weaknesses"].startswith("待实测："))
        self.assertNotIn("质量显著提升", evaluation["evaluation_strengths"])

    def test_official_evaluation_evidence_is_preserved(self):
        evaluation = agent.normalize_evaluation_fields(
            {
                "evaluation_basis": "官方案例",
                "evaluation_strengths": "人物质感和构图更稳定。",
                "evaluation_weaknesses": "未披露复杂文字生成表现。",
            }
        )
        self.assertEqual(evaluation["evaluation_basis"], "官方案例")
        self.assertIn("人物质感", evaluation["evaluation_strengths"])
        self.assertIn("复杂文字", evaluation["evaluation_weaknesses"])

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

    def test_minor_ai_coding_sdk_release_is_excluded(self):
        item = agent.NewsItem(
            platform="Example SDK",
            category="Agent",
            source_type="official_github_release",
            title="Example SDK 1.4.0 released",
            url="https://example.com/releases/1.4.0",
            published_at="2026-07-22T16:30:00+08:00",
            description="Added model reasoning support.",
        )
        self.assertFalse(agent.is_model_relevant(item))
        self.assertFalse(
            agent.should_keep_selected(
                item,
                "A",
                "新增 reasoning_effort 作为标准聊天模型参数。",
            )
        )

    def test_multimodal_aigc_release_is_included(self):
        item = agent.NewsItem(
            platform="Example Video",
            category="AIGC视频",
            source_type="official_feed",
            title="Introducing Example Video 3",
            url="https://example.com/news/video-3",
            published_at="2026-07-22T16:30:00+08:00",
            description="Released with image-to-video, camera control, and native audio.",
        )
        self.assertTrue(agent.is_model_relevant(item))
        self.assertTrue(
            agent.should_keep_selected(
                item,
                "A",
                "新增图生视频、镜头控制与原生音频能力。",
            )
        )

    def test_multimodal_capability_update_can_enter_as_b_level(self):
        item = agent.NewsItem(
            platform="Luma AI",
            category="AIGC视频",
            source_type="official_sitemap",
            title="Introducing Layers",
            url="https://lumalabs.ai/news/introducing-layers",
            published_at="2026-07-29T16:30:00+08:00",
            description="Released with precision editing and control over every element of an image.",
        )
        selected = agent.fallback_analysis([item])
        self.assertEqual(len(selected), 1)
        self.assertIn(selected[0]["importance"], {"A", "B"})

    def test_multimodal_updates_rank_before_supplementary_updates(self):
        multimodal = agent.NewsItem(
            platform="Example Image",
            category="多模态与图像",
            source_type="official_sitemap",
            title="New layered image editing is available",
            url="https://example.com/image-editing",
            published_at="2026-07-29T15:00:00+08:00",
            description="Adds precision editing for generated and uploaded images.",
        )
        text_model = agent.NewsItem(
            platform="Example Text",
            category="全球大模型",
            source_type="official_feed",
            title="Introducing a flagship reasoning model",
            url="https://example.com/text-model",
            published_at="2026-07-29T16:00:00+08:00",
            description="A frontier language model with reasoning and a larger context window.",
        )
        selected = agent.fallback_analysis([text_model, multimodal])
        self.assertEqual(selected[0]["url"], multimodal.url)

    def test_supplementary_b_level_update_is_excluded(self):
        item = agent.NewsItem(
            platform="Example Agent",
            category="全球大模型",
            source_type="official_feed",
            title="Agent support updated",
            url="https://example.com/agent-update",
            published_at="2026-07-29T16:00:00+08:00",
            description="Adds agent support.",
        )
        self.assertEqual(agent.fallback_analysis([item]), [])

    def test_important_general_text_model_is_included(self):
        item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_feed",
            title="Introducing GPT-Next",
            url="https://example.com/news/gpt-next",
            published_at="2026-07-22T16:30:00+08:00",
            description=(
                "A new flagship reasoning model with a larger context window "
                "is now available through the model API."
            ),
        )
        self.assertTrue(agent.is_model_relevant(item))
        self.assertTrue(
            agent.should_keep_selected(
                item,
                "S",
                "旗舰推理模型正式上线，并扩大上下文窗口与 API 可用范围。",
            )
        )

    def test_major_agent_capability_update_is_included(self):
        item = agent.NewsItem(
            platform="Example Agent",
            category="Agent与AI编程",
            source_type="official_feed",
            title="Introducing Agent Mode",
            url="https://example.com/news/agent-mode",
            published_at="2026-07-22T16:30:00+08:00",
            description=(
                "The coding agent adds computer use, tool calling, subagents, "
                "and long-running workflow support."
            ),
        )
        self.assertTrue(agent.is_model_relevant(item))
        self.assertTrue(
            agent.should_keep_selected(
                item,
                "A",
                "新增计算机操作、工具调用、子 Agent 与长任务能力。",
            )
        )

    def test_material_api_pricing_change_is_included(self):
        item = agent.NewsItem(
            platform="Example Models",
            category="全球大模型",
            source_type="official_feed",
            title="New API pricing for Example Model",
            url="https://example.com/news/api-pricing",
            published_at="2026-07-22T16:30:00+08:00",
            description=(
                "The model API input token price is reduced by 50% and "
                "rate limits are increased."
            ),
        )
        self.assertTrue(agent.is_model_relevant(item))
        self.assertTrue(
            agent.should_keep_selected(
                item,
                "A",
                "输入 Token 价格降低 50%，同时提高 API 限流额度。",
            )
        )

    def test_named_model_pricing_update_is_included_without_generic_model_words(self):
        item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_feed",
            title="Advancing the price-performance frontier with GPT-5.6",
            url="https://example.com/gpt-5-6-pricing",
            published_at="2026-07-30T16:00:00+08:00",
            description="Explore lower GPT-5.6 pricing for Luna and Terra.",
        )
        self.assertTrue(agent.is_model_relevant(item))
        selected = agent.fallback_analysis([item])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["importance"], "A")

    def test_product_customer_story_is_excluded(self):
        item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_feed",
            title="How avatarin built a retail agent with GPT-Realtime",
            url="https://example.com/avatarin",
            published_at="2026-07-30T16:00:00+08:00",
            description="A retailer uses the agent for multilingual customer support.",
        )
        self.assertFalse(agent.is_model_relevant(item))

    def test_realtime_service_incident_is_excluded(self):
        item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_feed",
            title="Image generation unavailable in ChatGPT",
            url="https://status.example/incidents/1",
            published_at="2026-07-22T16:30:00+08:00",
            description="We are investigating elevated error rates and service disruption.",
        )
        self.assertFalse(agent.is_model_relevant(item))
        self.assertEqual(agent.fallback_analysis([item]), [])

    def test_empty_report_is_concise(self):
        markdown = agent.build_markdown(date(2026, 7, 22), [])
        self.assertEqual(
            markdown,
            "今日（2026-07-22）所有监控平台均无经官方核验的多模态模型重点动态或重大通用模型、Agent、API/价格更新\n",
        )

    @patch.object(agent, "fetch_bytes", return_value=EMBEDDED_ARTICLE_LIST)
    def test_embedded_article_list_catches_fresh_model_release(self, _fetch):
        source = {
            "platform": "ByteDance Seed",
            "category": "AIGC视频",
            "kind": "embedded_article_list",
            "source_type": "official_page_list",
            "url": "https://seed.example/en/blog",
            "article_base_url": "https://seed.example/en/blog/",
            "language": "zh",
        }
        items = agent.parse_embedded_article_list(source, self.start, self.end)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Seedance 2.5 正式发布")
        self.assertEqual(
            items[0].url,
            "https://seed.example/en/blog/introducing-seedance-2-5",
        )
        self.assertTrue(agent.is_model_relevant(items[0]))
        self.assertGreaterEqual(agent.candidate_score(items[0]), 7)

    def test_customer_story_url_is_excluded(self):
        item = agent.NewsItem(
            platform="Runway",
            category="AIGC视频",
            source_type="official_sitemap",
            title="How a travel company changed preproduction",
            url="https://runway.example/news/customers/example",
            published_at="2026-07-22T16:30:00+08:00",
            description="A new video production workflow.",
        )
        self.assertFalse(agent.is_model_relevant(item))

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

    def test_sitemap_accepts_short_publication_indexing_delay(self):
        source = {
            "platform": "Luma AI",
            "category": "AIGC视频",
            "kind": "sitemap",
            "source_type": "official_sitemap",
            "url": "https://example.com/sitemap.xml",
            "include": ["/news/"],
            "max_pages": 5,
        }
        start = datetime(2026, 7, 31, 0, 0, tzinfo=self.tz)
        end = datetime(2026, 8, 1, 0, 0, tzinfo=self.tz)

        def fake_fetch(url, timeout=agent.REQUEST_TIMEOUT):
            return DELAYED_SITEMAP if url.endswith("sitemap.xml") else DELAYED_PAGE

        with patch.object(agent, "fetch_bytes", side_effect=fake_fetch):
            items = agent.parse_sitemap(source, start, end)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "Introducing Layers")
        self.assertTrue(agent.is_multimodal_relevant(items[0]))

    @patch.object(agent, "fetch_bytes", return_value=UPDATED_MODEL_PAGE)
    def test_existing_launch_page_explicit_update_becomes_new_multimodal_event(self, _fetch):
        title, description, published = agent.page_metadata(
            "https://example.com/introducing-gpt-live"
        )
        self.assertEqual(title, "Introducing GPT-Live")
        self.assertIn("SynthID watermarking", description)
        self.assertEqual(published.astimezone(self.tz).date(), date(2026, 7, 31))
        item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_sitemap",
            title=title,
            url="https://example.com/introducing-gpt-live",
            published_at=published.astimezone(self.tz).isoformat(),
            description=description,
        )
        self.assertTrue(agent.is_multimodal_relevant(item))
        self.assertTrue(agent.is_model_relevant(item))

    def test_protected_official_model_page_uses_transparent_sitemap_fallback(self):
        source = {
            "platform": "OpenAI Product Updates",
            "category": "全球大模型",
            "source_type": "official_sitemap",
            "allow_page_fallback": True,
        }
        modified = datetime(2026, 8, 3, 3, 38, tzinfo=self.tz)
        item = agent.sitemap_page_fallback(
            source,
            "https://openai.com/index/introducing-gpt-live/",
            modified,
        )
        self.assertIsNotNone(item)
        self.assertIn("could not read", item.description)
        self.assertTrue(agent.is_multimodal_relevant(item))
        self.assertTrue(agent.is_model_relevant(item))
        selected = agent.fallback_analysis([item])
        self.assertEqual(selected[0]["importance"], "A")

    def test_protected_non_model_page_does_not_use_sitemap_fallback(self):
        source = {
            "platform": "OpenAI Product Updates",
            "category": "全球大模型",
            "source_type": "official_sitemap",
            "allow_page_fallback": True,
        }
        modified = datetime(2026, 8, 3, 3, 38, tzinfo=self.tz)
        item = agent.sitemap_page_fallback(
            source,
            "https://openai.com/index/customer-success-story/",
            modified,
        )
        self.assertIsNone(item)

    def test_report_day_defaults_to_yesterday(self):
        with patch.dict(os.environ, {"REPORT_DATE": "2026-07-22"}):
            self.assertEqual(agent.resolve_report_day(), date(2026, 7, 22))


if __name__ == "__main__":
    unittest.main()
