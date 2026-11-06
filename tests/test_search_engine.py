import os
import sys
import unittest
import pandas as pd

# Add repository root to path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.countries import load_countries
from src.search import SearchService
from src.extractor import ContentExtractor
from src.llm import OllamaSynthesizer
from src.pipeline import PipelineResult

class TestCatalog(unittest.TestCase):
    """Verifies that countries are loaded correctly."""

    def test_countries_loaded(self):
        countries = load_countries()
        self.assertGreaterEqual(len(countries), 190)
        self.assertIn("India", countries)
        self.assertIn("United States", countries)
        self.assertIn("Japan", countries)

class TestSearchService(unittest.TestCase):
    """Verifies search service query handling and formatting."""

    def test_search_service_structure(self):
        service = SearchService()
        results = service.search_duckduckgo("BBC News Wikipedia", max_results=2)
        self.assertIsInstance(results, list)
        if results:
            first = results[0]
            self.assertIn("title", first)
            self.assertIn("url", first)
            self.assertIn("snippet", first)
            self.assertIn("source", first)

    def test_resolve_twitter_handle(self):
        service = SearchService()
        result = service.resolve_twitter_handle("Aaj Tak")
        self.assertIsInstance(result, dict)
        self.assertIn("channel_name", result)
        self.assertIn("handle", result)
        self.assertIn("source_url", result)
        self.assertIn("query", result)
        if result["handle"] != "N/A":
            self.assertTrue(result["handle"].startswith("@"))
            self.assertTrue("aajtak" in result["handle"].lower() or "india" in result["handle"].lower())

class TestContentExtractor(unittest.TestCase):
    """Verifies extraction logic."""

    def test_trafilatura_extraction(self):
        extractor = ContentExtractor()
        # Test on example.com
        res = extractor.extract({"title": "Example", "url": "https://example.com"})
        self.assertIn("char_count", res)
        self.assertIn("method", res)
        self.assertGreater(res["char_count"], 50)

class TestOllamaParser(unittest.TestCase):
    """Verifies parsing of markdown table responses into pandas DataFrames."""

    def test_table_parsing(self):
        synthesizer = OllamaSynthesizer()
        sample_markdown = (
            "Here is the requested table:\n\n"
            "| Rank | Channel Name | Broadcaster / Network | Language / Focus | Reach / Audience | Website / Handle |\n"
            "|---|---|---|---|---|---|\n"
            "| 1 | BBC News | BBC | English; Global news | Global reach | bbc.com |\n"
            "| 2 | Sky News | Sky Group | English; Breaking news | UK & Europe | skynews.com |\n\n"
            "Evidence Summary: BBC and Sky are the two leading British broadcast networks."
        )

        df, summary = synthesizer.parse_response(sample_markdown)
        self.assertEqual(len(df), 2)
        self.assertEqual(list(df.columns), [
            "Rank", "Channel Name", "Broadcaster / Network",
            "Language / Focus", "Reach / Audience", "Website / Handle"
        ])
        self.assertEqual(df.iloc[0]["Channel Name"], "BBC News")
        self.assertIn("BBC and Sky", summary)

class TestPipelineResult(unittest.TestCase):
    """Verifies pipeline result data structure."""

    def test_result_structure(self):
        res = PipelineResult(country="Testland")
        self.assertEqual(res.country, "Testland")
        self.assertEqual(res.status, "pending")
        self.assertEqual(res.duration_sec, 0.0)
        self.assertEqual(res.twitter_evidence, {})

if __name__ == "__main__":
    unittest.main()
