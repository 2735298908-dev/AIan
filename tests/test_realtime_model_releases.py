import sys
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ai_news_agent as agent  # noqa: E402
import realtime_model_releases as realtime  # noqa: E402


class RealtimeModelReleaseTests(unittest.TestCase):
    def setUp(self):
        self.item = agent.NewsItem(
            platform="Luma AI",
            category="AIGC视频",
            source_type="official_sitemap",
            title="Introducing Layers",
            url="https://lumalabs.ai/news/introducing-layers",
            published_at="2026-07-30T01:28:48+08:00",
            description="Precision editing and control over every element of an image.",
        )

    def test_old_filter_rejection_is_reconsidered_once(self):
        history = realtime.empty_history()
        history["seen"] = [
            {
                "url": self.item.url,
                "title_key": agent.title_key(self.item.title),
                "seen_at": "2026-07-31T00:00:00+00:00",
                "rule_version": agent.FILTER_RULE_VERSION - 1,
            }
        ]
        self.assertEqual(realtime.new_candidates([self.item], history), [self.item])

    def test_current_filter_seen_item_is_not_repeated(self):
        history = realtime.empty_history()
        history["seen"] = [
            {
                "url": self.item.url,
                "title_key": agent.title_key(self.item.title),
                "seen_at": "2026-07-31T00:00:00+00:00",
                "rule_version": agent.FILTER_RULE_VERSION,
            }
        ]
        self.assertEqual(realtime.new_candidates([self.item], history), [])

    def test_previously_notified_item_never_repeats_after_rule_change(self):
        history = realtime.empty_history()
        history["notifications"] = [
            {
                "url": self.item.url,
                "title_key": agent.title_key(self.item.title),
                "reported_at": "2026-07-31T00:00:00+00:00",
            }
        ]
        self.assertEqual(realtime.new_candidates([self.item], history), [])

    def test_richer_duplicate_is_evaluated_first(self):
        feed_item = agent.NewsItem(
            platform="OpenAI",
            category="全球大模型",
            source_type="official_feed",
            title="GPT-6 Astra: A new generation of intelligence",
            url="https://openai.com/index/gpt-6-astra",
            published_at="2026-09-03T19:00:00+08:00",
            description="Introducing GPT-6 Astra with computer use and coding.",
        )
        fallback_item = agent.NewsItem(
            platform="OpenAI Product Updates",
            category="全球大模型",
            source_type="official_sitemap",
            title="Official model page updated: gpt 6 astra",
            url="https://openai.com/index/gpt-6-astra",
            published_at="2026-09-04T08:08:00+08:00",
            description="Official sitemap detected a fresh model API update.",
        )
        self.assertEqual(
            realtime.new_candidates([fallback_item, feed_item], realtime.empty_history()),
            [feed_item],
        )


if __name__ == "__main__":
    unittest.main()
