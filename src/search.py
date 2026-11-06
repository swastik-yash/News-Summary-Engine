import os
import re
import requests
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor
from ddgs import DDGS
from src.logger import get_logger

logger = get_logger("search")

HANDLE_URL_REGEX = re.compile(r"https?://(?:www\.)?(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,15})(?:/|$|\?)", re.IGNORECASE)
HANDLE_TEXT_REGEX = re.compile(r"@([A-Za-z0-9_]{1,15})")
EXCLUDED_SLUGS = {
    "home", "explore", "search", "intent", "share", "hashtag", "i",
    "privacy", "tos", "login", "signup", "posts", "about", "status",
    "who_to_follow", "lists", "settings"
}

class SearchService:
    """Provides web search functionality using DDGS and SearXNG."""

    def __init__(self, searxng_url: Optional[str] = None):
        self.searxng_url = searxng_url or os.getenv("SEARXNG_BASE_URL") or os.getenv("SEARXNG_URL", "http://localhost:8080")

    def search_duckduckgo(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Searches DuckDuckGo using ddgs."""
        logger.info(f"[SEARCH] Querying DuckDuckGo for: '{query}' (limit={max_results})")
        results = []
        try:
            ddgs = DDGS()
            raw_results = ddgs.text(query, max_results=max_results)
            for item in raw_results:
                title = item.get("title", "").strip()
                url = item.get("href", "").strip()
                snippet = item.get("body", "").strip()
                if url:
                    results.append({
                        "title": title,
                        "url": url,
                        "snippet": snippet,
                        "source": "duckduckgo"
                    })
            logger.info(f"[SEARCH] DuckDuckGo returned {len(results)} results")
        except Exception as e:
            logger.error(f"[SEARCH] DuckDuckGo search failed: {str(e)}")
        return results

    def search_searxng(self, query: str, max_results: int = 5) -> List[Dict[str, str]]:
        """Searches SearXNG via JSON API endpoint."""
        logger.info(f"[SEARCH] Querying SearXNG at {self.searxng_url} for: '{query}'")
        results = []
        try:
            resp = requests.get(
                f"{self.searxng_url.rstrip('/')}/search",
                params={"q": query, "format": "json"},
                timeout=4.0
            )
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("results", [])[:max_results]:
                    title = item.get("title", "").strip()
                    url = item.get("url", "").strip()
                    snippet = item.get("content", "").strip()
                    if url:
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                            "source": "searxng"
                        })
                logger.info(f"[SEARCH] SearXNG returned {len(results)} results")
            else:
                logger.warning(f"[SEARCH] SearXNG returned HTTP {resp.status_code}")
        except Exception as e:
            logger.warning(f"[SEARCH] SearXNG unreachable or failed: {str(e)}")
        return results

    def search_news(self, country: str, engine: str = "ddgs", max_results: int = 5) -> List[Dict[str, str]]:
        """Discovers news directory and ranking pages for a given country."""
        query = f"major news channels television networks in {country}"
        results = []

        if engine.lower() == "searxng":
            results = self.search_searxng(query, max_results=max_results)
            if not results:
                logger.info("[SEARCH] Falling back to DuckDuckGo after SearXNG yielded 0 results")
                results = self.search_duckduckgo(query, max_results=max_results)
        else:
            results = self.search_duckduckgo(query, max_results=max_results)

        # If we got fewer than 3 results, perform a broader secondary search
        if len(results) < 3:
            alt_query = f"top news channels in {country}"
            logger.info(f"[SEARCH] Result count is low ({len(results)}). Running secondary query: '{alt_query}'")
            extra = self.search_duckduckgo(alt_query, max_results=max_results)
            seen_urls = {r["url"] for r in results}
            for item in extra:
                if item["url"] not in seen_urls:
                    results.append(item)
                    seen_urls.add(item["url"])

        logger.info(f"[SEARCH] Total candidate URLs collected: {len(results)}")
        for i, item in enumerate(results[:max_results], start=1):
            logger.info(f"[SEARCH] Candidate {i}: [{item['title']}] -> {item['url']}")

        return results[:max_results]

    def resolve_twitter_handle(self, channel_name: str, country: str = "") -> Dict[str, str]:
        """Discovers a channel's official Twitter / X handle via search engine queries."""
        clean_name = re.sub(r"\(.*?\)", "", channel_name).split("/")[0].strip()
        queries = [
            f"{clean_name} twitter",
            f"{clean_name} official twitter",
            f"{clean_name} twitter handle"
        ]

        logger.info(f"[SEARCH] Resolving Twitter handle for '{clean_name}'...")

        for q in queries:
            results = []
            for backend in ["html", "api"]:
                try:
                    ddgs = DDGS()
                    results = list(ddgs.text(q, backend=backend, max_results=3))
                    if results:
                        break
                except Exception:
                    continue

            for item in results:
                url = item.get("href", "")
                title = item.get("title", "")
                snippet = item.get("body", "")

                # 1. Match from URL destination
                m_url = HANDLE_URL_REGEX.search(url)
                if m_url:
                    candidate = m_url.group(1)
                    if candidate.lower() not in EXCLUDED_SLUGS:
                        handle = f"@{candidate}"
                        logger.info(f"[SEARCH] Discovered Twitter handle for '{clean_name}': {handle} (Source: {url})")
                        return {
                            "channel_name": channel_name,
                            "handle": handle,
                            "source_url": url,
                            "source_title": title,
                            "snippet": snippet,
                            "query": q
                        }

                # 2. Match from result Title
                m_title = HANDLE_TEXT_REGEX.search(title)
                if m_title:
                    candidate = m_title.group(1)
                    if candidate.lower() not in EXCLUDED_SLUGS:
                        handle = f"@{candidate}"
                        logger.info(f"[SEARCH] Discovered Twitter handle for '{clean_name}': {handle} from title (Source: {url})")
                        return {
                            "channel_name": channel_name,
                            "handle": handle,
                            "source_url": url,
                            "source_title": title,
                            "snippet": snippet,
                            "query": q
                        }

                # 3. Match from result Snippet
                m_snippet = HANDLE_TEXT_REGEX.search(snippet)
                if m_snippet:
                    candidate = m_snippet.group(1)
                    if candidate.lower() not in EXCLUDED_SLUGS:
                        handle = f"@{candidate}"
                        logger.info(f"[SEARCH] Discovered Twitter handle for '{clean_name}': {handle} from snippet (Source: {url})")
                        return {
                            "channel_name": channel_name,
                            "handle": handle,
                            "source_url": url,
                            "source_title": title,
                            "snippet": snippet,
                            "query": q
                        }

            import time
            time.sleep(0.3)

        logger.info(f"[SEARCH] No verified Twitter handle found for '{clean_name}'")
        return {
            "channel_name": channel_name,
            "handle": "N/A",
            "source_url": "",
            "source_title": "No verified handle found",
            "snippet": "",
            "query": queries[0]
        }

    def resolve_twitter_handles_batch(
        self,
        channels: List[str],
        country: str = "",
        max_workers: int = 4
    ) -> Dict[str, Dict[str, str]]:
        """Concurrently resolves Twitter handles for a list of news channels."""
        logger.info(f"[SEARCH] Starting concurrent Twitter handle resolution for {len(channels)} channels (workers={max_workers})")
        results = {}

        def _worker(ch):
            return ch, self.resolve_twitter_handle(ch, country=country)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_channel = {executor.submit(_worker, ch): ch for ch in channels}
            for future in future_to_channel:
                try:
                    ch, data = future.result()
                    results[ch] = data
                except Exception as e:
                    ch = future_to_channel[future]
                    logger.error(f"[SEARCH] Twitter lookup failed for '{ch}': {str(e)}")
                    results[ch] = {
                        "channel_name": ch,
                        "handle": "N/A",
                        "source_url": "",
                        "source_title": f"Lookup error: {str(e)}",
                        "snippet": "",
                        "query": ""
                    }

        found_count = sum(1 for d in results.values() if d.get("handle") and d["handle"] != "N/A")
        logger.info(f"[SEARCH] Twitter handle resolution complete: {found_count}/{len(channels)} handles discovered.")
        return results
