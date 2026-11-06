import os
import asyncio
import concurrent.futures
from typing import List, Dict, Any, Optional
import trafilatura

# Silence crawl4ai cloud notices
os.environ["CRAWL4AI_NO_CLOUD_NOTICE"] = "1"

from src.logger import get_logger

logger = get_logger("extractor")

class ContentExtractor:
    """Extracts clean article and directory text using Trafilatura with Crawl4AI fallback."""

    def __init__(self, min_char_threshold: int = 150, max_chars_per_page: int = 6000):
        self.min_char_threshold = min_char_threshold
        self.max_chars_per_page = max_chars_per_page

    def extract_trafilatura(self, url: str) -> Optional[str]:
        """Fast static text extraction via Trafilatura."""
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return None
            text = trafilatura.extract(
                downloaded,
                include_tables=True,
                include_links=True,
                output_format="txt"
            )
            return text.strip() if text else None
        except Exception as e:
            logger.warning(f"[EXTRACTOR] Trafilatura fetch error for {url}: {str(e)}")
            return None

    def _run_crawl4ai_async(self, url: str) -> Optional[str]:
        """Internal async helper for Crawl4AI."""
        from crawl4ai import AsyncWebCrawler

        async def _crawl():
            async with AsyncWebCrawler(verbose=False) as crawler:
                res = await crawler.arun(url=url)
                if res and res.markdown:
                    return res.markdown.strip()
                return None

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    return pool.submit(lambda: asyncio.run(_crawl())).result(timeout=20)
            else:
                return loop.run_until_complete(_crawl())
        except Exception as e:
            logger.warning(f"[EXTRACTOR] Crawl4AI extraction error for {url}: {str(e)}")
            return None

    def extract_crawl4ai(self, url: str) -> Optional[str]:
        """Dynamic JavaScript text extraction fallback via Crawl4AI."""
        try:
            return self._run_crawl4ai_async(url)
        except Exception as e:
            logger.warning(f"[EXTRACTOR] Crawl4AI runner error for {url}: {str(e)}")
            return None

    def extract(self, item: Dict[str, str]) -> Dict[str, Any]:
        """Extracts readable content for a single search result."""
        url = item.get("url", "")
        title = item.get("title", "Untitled")
        logger.info(f"[EXTRACTOR] Extracting content from: [{title}] -> {url}")

        method_used = "failed"
        content = ""

        # Primary extraction: Trafilatura
        raw_text = self.extract_trafilatura(url)
        if raw_text and len(raw_text) >= self.min_char_threshold:
            content = raw_text
            method_used = "trafilatura"
            logger.info(f"[EXTRACTOR] Trafilatura succeeded ({len(content)} chars)")
        else:
            prev_len = len(raw_text) if raw_text else 0
            logger.info(
                f"[EXTRACTOR] Trafilatura yielded insufficient content ({prev_len} chars). "
                f"Falling back to Crawl4AI Playwright..."
            )
            crawl_text = self.extract_crawl4ai(url)
            if crawl_text and len(crawl_text) >= self.min_char_threshold:
                content = crawl_text
                method_used = "crawl4ai"
                logger.info(f"[EXTRACTOR] Crawl4AI succeeded ({len(content)} chars)")
            elif raw_text:
                # If crawl4ai also got little, keep whatever trafilatura had
                content = raw_text
                method_used = "trafilatura (partial)"
                logger.info(f"[EXTRACTOR] Retaining partial Trafilatura text ({len(content)} chars)")
            else:
                logger.warning(f"[EXTRACTOR] Failed to extract readable text for {url}")

        # Truncate to maximum characters per page to prevent token bloat
        truncated_content = content[:self.max_chars_per_page] if content else ""

        return {
            "title": title,
            "url": url,
            "snippet": item.get("snippet", ""),
            "text": truncated_content,
            "method": method_used,
            "char_count": len(truncated_content)
        }

    def extract_batch(self, items: List[Dict[str, str]], max_items: int = 5) -> List[Dict[str, Any]]:
        """Extracts content for a batch of search results."""
        results = []
        candidates = items[:max_items]
        logger.info(f"[EXTRACTOR] Starting batch extraction for {len(candidates)} URLs")

        for idx, item in enumerate(candidates, start=1):
            logger.info(f"[EXTRACTOR] Processing page {idx}/{len(candidates)}")
            data = self.extract(item)
            results.append(data)

        successful = [r for r in results if r["char_count"] > 0]
        total_chars = sum(r["char_count"] for r in successful)
        logger.info(
            f"[EXTRACTOR] Batch extraction complete: {len(successful)}/{len(candidates)} "
            f"pages extracted ({total_chars} total characters)"
        )
        return results
