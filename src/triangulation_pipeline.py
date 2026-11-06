import os
import sys
import time
import argparse
import datetime
from dataclasses import dataclass, field
import pandas as pd
from dotenv import load_dotenv

from src.logger import get_logger
from src.pipeline import NewsDiscoveryEngine
from src.twitter_service import TwitterService
from src.gemini_synthesizer import GeminiSynthesizer

load_dotenv()
logger = get_logger("triangulation")

@dataclass
class TriangulationResult:
    """Complete container for news triangulation run."""
    country: str
    timestamp: str
    duration_sec: float
    ranked_channels: list[dict] = field(default_factory=list)
    ranked_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    posts: list[dict] = field(default_factory=list)
    posts_df: pd.DataFrame = field(default_factory=pd.DataFrame)
    summary: str = ""
    model_used: str = ""
    min_impressions: int = 20000
    hours: int = 24
    status: str = "pending"

class NewsTriangulationEngine:
    """
    End-to-end pipeline:
    1. Discovers top news channels & Twitter handles via local Qwen pipeline.
    2. Enriches with live follower counts & ranks Top 5 via Twitter API v2.
    3. Ingests 24-hour posts with impression count >= threshold (20k default).
    4. Triangulates stories and synthesizes executive brief using Gemini Flash Lite.
    """

    def __init__(self):
        self.discovery_engine = NewsDiscoveryEngine()
        self.twitter_service = TwitterService()
        self.gemini_synthesizer = GeminiSynthesizer()

    def run(
        self,
        country: str,
        min_impressions: int = 20000,
        hours: int = 24,
        top_n_channels: int = 5,
        discovery_sources: int = 4
    ) -> TriangulationResult:
        start_time = time.time()
        logger.info("=" * 75)
        logger.info(f"[SUMMARY] Starting 24-Hour News Summary Engine for: '{country}'")
        logger.info(f"[SUMMARY] Constraints: past {hours}h | min impressions: {min_impressions:,} | top {top_n_channels} channels")
        logger.info("=" * 75)

        # Stage 1: Channel Discovery using existing local Qwen pipeline
        logger.info(f"[SUMMARY] [Stage 1/4] Running channel discovery & Twitter handle resolution...")
        discovery_res = self.discovery_engine.run(
            country=country,
            engine="ddgs",
            max_sources=discovery_sources,
            search_twitter=True
        )

        channels_df = discovery_res.channels_df
        if channels_df.empty:
            logger.error(f"[TRIANGULATION] Stage 1 yielded no discovered channels for '{country}'.")
            duration = round(time.time() - start_time, 2)
            return TriangulationResult(
                country=country,
                timestamp=datetime.datetime.now().isoformat(),
                duration_sec=duration,
                status="discovery_failed"
            )

        channels_records = channels_df.to_dict(orient="records")
        logger.info(f"[TRIANGULATION] Stage 1 complete. Discovered {len(channels_records)} channels.")

        # Stage 2: Twitter API Follower Lookup & Top 5 Ranking
        logger.info(f"[TRIANGULATION] [Stage 2/4] Fetching follower metrics from Twitter API v2...")
        handles = [
            str(c.get("Twitter Handle", "")).lstrip("@").strip()
            for c in channels_records
            if c.get("Twitter Handle") and str(c.get("Twitter Handle")).upper() != "N/A"
        ]

        user_metrics = self.twitter_service.get_users_metrics(handles)
        ranked_channels = self.twitter_service.rank_top_channels(
            channels=channels_records,
            user_metrics=user_metrics,
            top_n=top_n_channels
        )

        # Convert ranked channels to clean DataFrame
        ranked_rows = []
        for rank, ch in enumerate(ranked_channels, 1):
            h = ch.get("username") or ch.get("Twitter Handle") or "N/A"
            fc = ch.get("followers_count", 0)
            ranked_rows.append({
                "Rank": rank,
                "Channel Name": ch.get("Channel Name") or ch.get("name", "Unknown"),
                "Twitter Handle": f"@{h}" if h != "N/A" and not h.startswith("@") else h,
                "Followers": fc,
                "Link": f"https://x.com/{h.lstrip('@')}" if h != "N/A" else ""
            })
        ranked_df = pd.DataFrame(ranked_rows)

        # Stage 3: Ingest 24-Hour Posts Filtered by Impressions >= min_impressions
        logger.info(f"[TRIANGULATION] [Stage 3/4] Ingesting posts past {hours}h with impressions >= {min_impressions:,}...")
        posts = self.twitter_service.fetch_top_channels_posts(
            ranked_channels=ranked_channels,
            hours=hours,
            min_impressions=min_impressions
        )

        # Build posts DataFrame
        post_rows = []
        for p in posts:
            post_rows.append({
                "Channel": p.get("channel_name", ""),
                "Handle": f"@{p.get('username', '')}",
                "Impressions": p.get("impressions", 0),
                "Likes": p.get("likes", 0),
                "Retweets": p.get("retweets", 0),
                "Time (UTC)": p.get("created_at", ""),
                "Text": p.get("text", ""),
                "Link": p.get("url", "")
            })
        posts_df = pd.DataFrame(post_rows)

        # Stage 4: Synthesize factual 24-hour news summary via Google Gemini Flash Lite
        logger.info(f"[SUMMARY] [Stage 4/4] Synthesizing factual news summary for {len(posts)} posts using Google Gemini Flash Lite...")
        gemini_res = self.gemini_synthesizer.synthesize_news_summary(
            country=country,
            ranked_channels=ranked_channels,
            posts=posts
        )

        summary_text = gemini_res.get("summary", "No summary generated.")
        model_used = gemini_res.get("model_used", "none")
        status = "success" if gemini_res.get("success") else "partial_success"

        duration = round(time.time() - start_time, 2)
        logger.info(f"[SUMMARY] News summary pipeline completed in {duration}s. Status: {status}")
        logger.info("=" * 75)

        return TriangulationResult(
            country=country,
            timestamp=datetime.datetime.now().isoformat(),
            duration_sec=duration,
            ranked_channels=ranked_channels,
            ranked_df=ranked_df,
            posts=posts,
            posts_df=posts_df,
            summary=summary_text,
            model_used=model_used,
            min_impressions=min_impressions,
            hours=hours,
            status=status
        )

def main():
    parser = argparse.ArgumentParser(description="24-Hour News Triangulation Engine (Twitter API + Gemini Flash Lite)")
    parser.add_argument("--country", type=str, required=True, help="Country name (e.g. India, United States, Japan)")
    parser.add_argument("--min-impressions", type=int, default=20000, help="Minimum impression count threshold (default 20,000)")
    parser.add_argument("--hours", type=int, default=24, help="Time window in hours (default 24)")
    args = parser.parse_args()

    engine = NewsTriangulationEngine()
    result = engine.run(country=args.country, min_impressions=args.min_impressions, hours=args.hours)

    print("\n" + "=" * 75)
    print(f"24-HOUR NATIONAL NEWS SUMMARY: {result.country.upper()}")
    print("=" * 75)
    print(f"Status: {result.status} | Duration: {result.duration_sec}s | Model: {result.model_used}")
    print(f"Top Broadcasters Analyzed: {len(result.ranked_channels)} | Posts Analyzed (>= {result.min_impressions:,} views): {len(result.posts)}")

    print("\n--- TOP BROADCASTERS BY AUDIENCE ---")
    if not result.ranked_df.empty:
        print(result.ranked_df[["Rank", "Channel Name", "Twitter Handle", "Followers"]].to_string(index=False))

    print("\n--- 24-HOUR NATIONAL NEWS SUMMARY ---")
    print(result.summary)

if __name__ == "__main__":
    main()
