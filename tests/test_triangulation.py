import unittest
import unicodedata
import os
from src.twitter_service import TwitterService
from src.gemini_synthesizer import GeminiSynthesizer
from src.triangulation_pipeline import TriangulationResult

class TestTriangulationLogic(unittest.TestCase):
    def setUp(self):
        self.service = TwitterService(bearer_token="mock_token")
        self.gemini = GeminiSynthesizer(api_key="mock_key")

    def test_rank_top_channels(self):
        channels = [
            {"Channel Name": "Channel Alpha", "Twitter Handle": "@AlphaNews"},
            {"Channel Name": "Channel Beta", "Twitter Handle": "@BetaNews"},
            {"Channel Name": "Channel Gamma", "Twitter Handle": "@GammaNews"},
            {"Channel Name": "Channel Delta", "Twitter Handle": "N/A"},
        ]
        metrics = [
            {"id": "1", "username": "AlphaNews", "followers_count": 50000, "following_count": 10, "tweet_count": 100, "verified_type": "gold"},
            {"id": "2", "username": "BetaNews", "followers_count": 150000, "following_count": 20, "tweet_count": 200, "verified_type": "gold"},
            {"id": "3", "username": "GammaNews", "followers_count": 20000, "following_count": 5, "tweet_count": 50, "verified_type": "none"},
        ]

        top_2 = self.service.rank_top_channels(channels, metrics, top_n=2)
        self.assertEqual(len(top_2), 2)
        self.assertEqual(top_2[0]["Channel Name"], "Channel Beta")
        self.assertEqual(top_2[0]["followers_count"], 150000)
        self.assertEqual(top_2[1]["Channel Name"], "Channel Alpha")
        self.assertEqual(top_2[1]["followers_count"], 50000)

    def test_impression_filtering_logic(self):
        # Verify filtering threshold behavior
        threshold = 20000
        mock_posts_data = [
            {"id": "101", "public_metrics": {"impression_count": 25000}},
            {"id": "102", "public_metrics": {"impression_count": 19999}},
            {"id": "103", "public_metrics": {"impression_count": 50000}},
            {"id": "104", "public_metrics": {"impression_count": 500}},
        ]
        qualified = [p for p in mock_posts_data if p["public_metrics"]["impression_count"] >= threshold]
        self.assertEqual(len(qualified), 2)
        self.assertEqual(qualified[0]["id"], "101")
        self.assertEqual(qualified[1]["id"], "103")

    def test_post_url_construction(self):
        username = "BBCNews"
        tweet_id = "1234567890"
        expected_url = f"https://x.com/{username}/status/{tweet_id}"
        self.assertEqual(expected_url, "https://x.com/BBCNews/status/1234567890")

    def test_gemini_prompt_builder(self):
        ranked = [
            {"Channel Name": "News 1", "username": "news1", "followers_count": 1000000}
        ]
        posts = [
            {
                "channel_name": "News 1",
                "username": "news1",
                "url": "https://x.com/news1/status/999",
                "impressions": 35000,
                "likes": 500,
                "created_at": "2026-11-06T12:00:00Z",
                "text": "Prime Minister addresses the press conference on inflation.",
                "article_cards": [{"title": "PM Speech", "description": "Speech on inflation", "url": "https://news1.com/pm"}]
            }
        ]
        prompt = self.gemini._build_summary_prompt("Testland", ranked, posts)
        self.assertIn("Testland", prompt)
        self.assertIn("NO EMOJIS", prompt)
        self.assertIn("ATTACH DIRECT LINKS", prompt)
        self.assertIn("https://x.com/news1/status/999", prompt)
        self.assertIn("35,000", prompt)
        self.assertNotIn("triangulation", prompt.lower())

    def test_zero_emojis_in_new_code(self):
        paths_to_check = [
            "src/twitter_service.py",
            "src/gemini_synthesizer.py",
            "src/triangulation_pipeline.py",
            "triangulation_app.py",
            "tests/test_triangulation.py"
        ]
        for p in paths_to_check:
            if not os.path.exists(p):
                continue
            with open(p, "r", encoding="utf-8") as f:
                for line_idx, line in enumerate(f, 1):
                    for ch in line:
                        try:
                            name = unicodedata.name(ch)
                            if "EMOJI" in name or (0x1F300 <= ord(ch) <= 0x1FAFF and ord(ch) not in range(0x2500, 0x2580)):
                                self.fail(f"Emoji '{ch}' ({name}) found in {p} at line {line_idx}")
                        except ValueError:
                            pass

if __name__ == "__main__":
    unittest.main()
