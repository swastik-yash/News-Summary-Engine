import os
import json
import requests
from dotenv import load_dotenv
from src.logger import get_logger

load_dotenv()
logger = get_logger("gemini")

class GeminiSynthesizer:
    """
    Client for Google Gemini Flash Lite models to synthesize an objective,
    factual 24-hour national news summary from verified broadcaster posts.
    """

    DEFAULT_MODELS = [
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
        "gemini-3.1-flash-lite"
    ]
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "").strip()
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
        if not self.api_key:
            logger.warning("[GEMINI] No GEMINI_API_KEY found in environment.")

    def synthesize_news_summary(
        self,
        country: str,
        ranked_channels: list[dict],
        posts: list[dict]
    ) -> dict:
        """
        Synthesizes an objective, factual summary of events in country over the past 24 hours.
        Focuses strictly on what happened without LLM meta-commentary or perspective.
        """
        if not self.api_key:
            return {
                "success": False,
                "summary": "Error: GEMINI_API_KEY is not configured.",
                "model_used": "none"
            }

        if not posts:
            logger.warning(f"[GEMINI] No qualifying posts provided for news summary in {country}.")
            return {
                "success": False,
                "summary": f"No posts met the impression threshold for {country} in the past 24 hours.",
                "model_used": "none"
            }

        # Build evidence document
        prompt = self._build_summary_prompt(country, ranked_channels, posts)

        # Try models with fallback
        models_to_try = [self.model] + [m for m in self.DEFAULT_MODELS if m != self.model]

        for current_model in models_to_try:
            url = f"{self.BASE_URL}/{current_model}:generateContent?key={self.api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.2,
                    "maxOutputTokens": 4096
                }
            }

            logger.info(f"[GEMINI] Dispatching news summary request to model '{current_model}' for {len(posts)} posts ({country})...")
            try:
                resp = requests.post(url, json=payload, timeout=45)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        content_parts = candidates[0].get("content", {}).get("parts", [])
                        if content_parts:
                            raw_text = content_parts[0].get("text", "").strip()
                            if len(raw_text) >= 150:
                                logger.info(f"[GEMINI] News summary synthesis completed successfully using '{current_model}' ({len(raw_text)} chars).")
                                return {
                                    "success": True,
                                    "summary": raw_text,
                                    "model_used": current_model,
                                    "post_count": len(posts)
                                }
                            else:
                                logger.warning(f"[GEMINI] Model '{current_model}' returned unexpectedly short output ({len(raw_text)} chars). Trying fallback...")
                else:
                    logger.warning(f"[GEMINI] Model '{current_model}' returned HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                logger.error(f"[GEMINI] Request to '{current_model}' encountered error: {e}")

        return {
            "success": False,
            "summary": "Failed to complete news summary across all available Gemini models.",
            "model_used": "none"
        }

    # Backward compatibility alias
    def triangulate_news_reports(self, country: str, ranked_channels: list[dict], posts: list[dict]) -> dict:
        return self.synthesize_news_summary(country, ranked_channels, posts)

    def _build_summary_prompt(self, country: str, ranked_channels: list[dict], posts: list[dict]) -> str:
        """
        Formats evidence from the top channels and posts into an objective news summary prompt.
        """
        lines = [
            f"You are a Senior Broadcast Journalist specializing in concise, factual news reporting.",
            f"Your mission is to provide an objective, factual summary of what happened in {country} over the past 24 hours based ONLY on the provided verified posts from top national broadcasters.",
            "",
            "CRITICAL CONSTRAINTS:",
            "1. NO EMOJIS: Do NOT output any emojis anywhere in your response under any circumstances.",
            "2. NO LLM PERSPECTIVE OR META-COMMENTARY: Do NOT talk about yourself, about the analysis process, or about how networks compare or differ. Do NOT include meta-statements like 'Coverage was concentrated in a single network' or 'Cross-network consensus is limited'. Simply state the FACTS of what happened.",
            "3. FOCUS ON WHAT HAPPENED: Focus purely on concrete real-world events, decisions, political developments, government statements, legal/police actions, economic updates, and sports events.",
            "4. ATTACH DIRECT LINKS: For every major story or update, you MUST provide direct markdown links to the specific source posts: [Broadcaster Post](URL) or [Headline](URL), noting view counts where relevant.",
            "5. COMPLETENESS GUARANTEE: Even if only a few posts or posts from a single network reached the view threshold, produce a full, detailed, multi-paragraph factual summary covering all events mentioned in the posts.",
            "",
            "TOP BROADCASTERS ANALYZED (RANKED BY AUDIENCE/FOLLOWERS):"
        ]

        for idx, ch in enumerate(ranked_channels, 1):
            name = ch.get("Channel Name") or ch.get("name") or "Broadcaster"
            handle = ch.get("username") or "N/A"
            fc = ch.get("followers_count", 0)
            lines.append(f"{idx}. {name} (@{handle}) - {fc:,} Twitter followers")

        lines.append("")
        lines.append(f"VERIFIED BROADCAST POSTS (LAST 24 HOURS, MINIMUM 20,000 IMPRESSIONS):")
        lines.append(f"Total Qualified Posts: {len(posts)}")
        lines.append("")

        for i, p in enumerate(posts[:60], 1):
            ch = p.get("channel_name", "Broadcaster")
            user = p.get("username", "")
            url = p.get("url", "")
            views = p.get("impressions", 0)
            likes = p.get("likes", 0)
            created = p.get("created_at", "")
            text = p.get("text", "").replace("\n", " ")

            lines.append(f"--- POST {i} ---")
            lines.append(f"Source: {ch} (@{user}) | Views: {views:,} | Likes: {likes:,} | Time: {created}")
            lines.append(f"URL: {url}")
            lines.append(f"Content: {text}")

            cards = p.get("article_cards", [])
            for c in cards:
                title = c.get("title", "").strip()
                desc = c.get("description", "").strip()
                article_url = c.get("url", "").strip()
                if title or desc:
                    lines.append(f"Article Card: '{title}' - '{desc}' ({article_url})")
            lines.append("")

        lines.append("REQUIRED OUTPUT STRUCTURE:")
        lines.append("## 1. Executive 24-Hour National News Summary")
        lines.append("A coherent 2-3 paragraph summary of the major events that took place in the country over the past 24 hours. Stick strictly to facts.")
        lines.append("")
        lines.append("## 2. Key Developing Stories and Events")
        lines.append("For each key event or story:")
        lines.append("- Event Headline")
        lines.append("- What Happened: Detailed factual narrative of the development, key persons, statements, and actions.")
        lines.append("- Direct Source Link: Markdown link to the specific post with view counts (e.g. [Broadcaster Post](URL) - 22,000 views).")
        lines.append("")
        lines.append("## 3. High-Impact & Breaking Updates")
        lines.append("Summary of notable breaking updates or announcements that drew significant attention.")
        lines.append("")
        lines.append("## 4. 24-Hour Key Stories Summary Table")
        lines.append("A markdown table summarizing the day's events:")
        lines.append("| Story / Event | Broadcaster | Summary of What Happened | Direct Post Link |")
        lines.append("")
        lines.append("Remember: Zero emojis and no meta-commentary about the analysis process.")

        return "\n".join(lines)

    # Backward compatibility alias
    def _build_triangulation_prompt(self, country: str, ranked_channels: list[dict], posts: list[dict]) -> str:
        return self._build_summary_prompt(country, ranked_channels, posts)
