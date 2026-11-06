import os
import sys

# Ensure repository root is on sys.path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import time
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Callable
import pandas as pd

from src.logger import setup_logger, get_logger, LogCallbackHandler
from src.search import SearchService
from src.extractor import ContentExtractor
from src.llm import OllamaSynthesizer

logger = get_logger("pipeline")

@dataclass
class PipelineResult:
    """Encapsulates the complete output of an agent run."""
    country: str
    channels_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    search_results: List[Dict[str, str]] = field(default_factory=list)
    extracted_articles: List[Dict[str, Any]] = field(default_factory=list)
    twitter_evidence: Dict[str, Dict[str, str]] = field(default_factory=dict)
    raw_synthesis: str = ""
    evidence_summary: str = ""
    logs: List[str] = field(default_factory=list)
    duration_sec: float = 0.0
    status: str = "pending"

class NewsDiscoveryEngine:
    """Orchestrates search discovery, content extraction, and LLM channel synthesis."""

    def __init__(
        self,
        searxng_url: Optional[str] = None,
        ollama_host: Optional[str] = None,
        ollama_model: Optional[str] = None
    ):
        setup_logger()
        self.search_service = SearchService(searxng_url=searxng_url)
        self.extractor = ContentExtractor()
        self.synthesizer = OllamaSynthesizer(host=ollama_host, model=ollama_model)

    def run(
        self,
        country: str,
        engine: str = "ddgs",
        max_sources: int = 4,
        deep_reasoning: bool = False,
        search_twitter: bool = True,
        log_callback: Optional[Callable[[str], None]] = None
    ) -> PipelineResult:
        """Executes the full discovery, extraction, synthesis, and Twitter resolution pipeline."""
        start_time = time.time()
        collected_logs: List[str] = []

        def internal_collector(msg: str):
            collected_logs.append(msg)
            if log_callback:
                try:
                    log_callback(msg)
                except Exception:
                    pass

        # Attach temporary log collector to news_engine logger
        root_logger = setup_logger()
        handler = LogCallbackHandler(internal_collector)
        root_logger.addHandler(handler)

        total_stages = 4 if search_twitter else 3
        logger.info("===========================================================================")
        logger.info(f"[PIPELINE] Launching News Discovery Agent for country: '{country}'")
        logger.info(
            f"[PIPELINE] Configuration: engine={engine}, max_sources={max_sources}, "
            f"deep_reasoning={deep_reasoning}, search_twitter={search_twitter}"
        )
        logger.info("===========================================================================")

        result = PipelineResult(country=country)

        try:
            # Stage 1: Search Discovery
            logger.info(f"[PIPELINE] [1/{total_stages}] Initiating web search discovery via '{engine}'...")
            search_results = self.search_service.search_news(
                country=country,
                engine=engine,
                max_results=max_sources
            )
            result.search_results = search_results

            if not search_results:
                logger.warning(f"[PIPELINE] No search results found for '{country}'. Synthesis aborted.")
                result.status = "no_search_results"
                result.duration_sec = round(time.time() - start_time, 2)
                result.logs = collected_logs
                return result

            # Stage 2: Web Extraction and Crawling
            logger.info(f"[PIPELINE] [2/{total_stages}] Extracting web content from {len(search_results)} sources...")
            extracted_articles = self.extractor.extract_batch(
                search_results,
                max_items=max_sources
            )
            result.extracted_articles = extracted_articles

            # Stage 3: LLM Synthesis with Ollama
            logger.info(
                f"[PIPELINE] [3/{total_stages}] Synthesizing top 10 news channels using Ollama "
                f"({self.synthesizer.model}) at {self.synthesizer.host}..."
            )
            synthesis_data = self.synthesizer.synthesize_channels(
                country=country,
                extracted_articles=extracted_articles,
                deep_reasoning=deep_reasoning
            )

            result.channels_df = synthesis_data.get("table_df", pd.DataFrame())
            result.raw_synthesis = synthesis_data.get("raw_text", "")
            result.evidence_summary = synthesis_data.get("evidence_summary", "")

            # Stage 4: Twitter / X Handle Resolution via Search Engine
            if not result.channels_df.empty and search_twitter:
                logger.info(
                    f"[PIPELINE] [4/{total_stages}] Discovering official Twitter handles & web sources "
                    f"for {len(result.channels_df)} channels via search engine..."
                )
                col_name = "Channel Name" if "Channel Name" in result.channels_df.columns else result.channels_df.columns[1]
                channels_list = result.channels_df[col_name].tolist()

                twitter_data = self.search_service.resolve_twitter_handles_batch(
                    channels_list,
                    country=country
                )
                result.twitter_evidence = twitter_data

                # Inject handles and exact search source URLs into DataFrame
                handles = [twitter_data.get(ch, {}).get("handle", "N/A") for ch in channels_list]
                twitter_sources = [twitter_data.get(ch, {}).get("source_url", "") for ch in channels_list]

                result.channels_df["Twitter Handle"] = handles
                result.channels_df["Twitter Source URL"] = twitter_sources

                logger.info(
                    f"[PIPELINE] [4/{total_stages}] Completed Twitter handle discovery: "
                    f"{sum(1 for h in handles if h != 'N/A')}/{len(handles)} resolved."
                )

            if not result.channels_df.empty:
                logger.info(
                    f"[PIPELINE] Successfully compiled {len(result.channels_df)} channels for '{country}'."
                )
                result.status = "success"
            else:
                logger.warning(
                    f"[PIPELINE] Synthesis did not produce a valid channel table for '{country}'."
                )
                result.status = "synthesis_table_empty"

        except Exception as e:
            logger.error(f"[PIPELINE] Execution encountered fatal exception: {str(e)}", exc_info=True)
            result.status = f"error: {str(e)}"

        finally:
            result.duration_sec = round(time.time() - start_time, 2)
            logger.info(f"[PIPELINE] Agent pipeline completed in {result.duration_sec}s. Status: {result.status}")
            logger.info("===========================================================================")
            root_logger.removeHandler(handler)
            result.logs = collected_logs

        return result

def main():
    """Command-line interface to execute the News Discovery Engine."""
    parser = argparse.ArgumentParser(description="News Discovery Engine CLI")
    parser.add_argument("--country", type=str, required=True, help="Country name to discover news channels for")
    parser.add_argument("--engine", type=str, default="ddgs", choices=["ddgs", "searxng"], help="Search engine")
    parser.add_argument("--sources", type=int, default=3, help="Max source URLs to scrape")
    parser.add_argument("--deep", action="store_true", help="Enable deep reasoning in LLM")
    parser.add_argument("--no-twitter", action="store_true", help="Skip Twitter handle discovery")
    args = parser.parse_args()

    engine = NewsDiscoveryEngine()
    result = engine.run(
        country=args.country,
        engine=args.engine,
        max_sources=args.sources,
        deep_reasoning=args.deep,
        search_twitter=not args.no_twitter
    )

    print("\n--- RESULTS SUMMARY ---")
    print(f"Country: {result.country}")
    print(f"Status: {result.status}")
    print(f"Duration: {result.duration_sec}s")
    if not result.channels_df.empty:
        print("\nTop Channels Table:")
        print(result.channels_df.to_string(index=False))
    if result.twitter_evidence:
        print("\nTwitter Web Sources Used:")
        for ch, data in result.twitter_evidence.items():
            print(f"  {ch:20} -> {data.get('handle', 'N/A'):15} Source: {data.get('source_url', 'None')}")
    if result.evidence_summary:
        print("\nEvidence Summary:")
        print(result.evidence_summary)

if __name__ == "__main__":
    main()
