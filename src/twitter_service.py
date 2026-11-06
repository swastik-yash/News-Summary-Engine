import os
import time
import datetime
import requests
from dotenv import load_dotenv
from src.logger import get_logger

load_dotenv()
logger = get_logger("twitter")

class TwitterService:
    """
    Service client for Twitter / X API v2.
    Handles user follower metrics, ranking, and 24-hour timeline ingestion
    filtered by impression count thresholds.
    """

    BASE_URL = "https://api.twitter.com/2"

    def __init__(self, bearer_token: str | None = None):
        self.bearer_token = bearer_token or os.getenv("TWITTER_BEARER_TOKEN", "").strip()
        if not self.bearer_token:
            logger.warning("[TWITTER] No TWITTER_BEARER_TOKEN found in environment.")

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.bearer_token}",
            "User-Agent": "NewsEngineTriangulation/1.0"
        }

    def get_users_metrics(self, usernames: list[str]) -> list[dict]:
        """
        Batch query user profiles and public metrics (followers count)
        for up to 100 handles in a single HTTP call.
        """
        clean_usernames = []
        for u in usernames:
            if not u or u.upper() == "N/A" or u.startswith("Error"):
                continue
            clean = u.lstrip("@").strip()
            if clean and clean not in clean_usernames:
                clean_usernames.append(clean)

        if not clean_usernames:
            logger.warning("[TWITTER] No valid usernames provided for lookup.")
            return []

        # Batch up to 100 handles
        target_batch = clean_usernames[:100]
        usernames_param = ",".join(target_batch)
        endpoint = f"{self.BASE_URL}/users/by"
        params = {
            "usernames": usernames_param,
            "user.fields": "public_metrics,verified_type,description,profile_image_url"
        }

        logger.info(f"[TWITTER] Batch fetching metrics for {len(target_batch)} handles: {target_batch}")
        try:
            resp = requests.get(endpoint, headers=self._get_headers(), params=params, timeout=15)
            if resp.status_code != 200:
                logger.error(f"[TWITTER] User lookup failed with HTTP {resp.status_code}: {resp.text}")
                return []

            payload = resp.json()
            users_data = payload.get("data", [])
            logger.info(f"[TWITTER] Retrieved metrics for {len(users_data)} accounts.")

            results = []
            for u in users_data:
                metrics = u.get("public_metrics", {})
                results.append({
                    "id": u.get("id"),
                    "name": u.get("name"),
                    "username": u.get("username"),
                    "followers_count": metrics.get("followers_count", 0),
                    "following_count": metrics.get("following_count", 0),
                    "tweet_count": metrics.get("tweet_count", 0),
                    "verified_type": u.get("verified_type", "none"),
                    "description": u.get("description", "")
                })
            return results
        except Exception as e:
            logger.error(f"[TWITTER] Exception during user metrics lookup: {e}")
            return []

    def rank_top_channels(self, channels: list[dict], user_metrics: list[dict], top_n: int = 5) -> list[dict]:
        """
        Merges channel discovery data with live Twitter follower metrics,
        ranks channels descending by follower count, and selects the top N channels.
        """
        # Index metrics by lowercase username
        metrics_by_user = {m["username"].lower(): m for m in user_metrics if m.get("username")}

        ranked = []
        for ch in channels:
            handle = str(ch.get("Twitter Handle", "")).lstrip("@").strip()
            ch_copy = dict(ch)
            m = metrics_by_user.get(handle.lower())
            if m:
                ch_copy["twitter_id"] = m["id"]
                ch_copy["username"] = m["username"]
                ch_copy["followers_count"] = m["followers_count"]
                ch_copy["following_count"] = m["following_count"]
                ch_copy["tweet_count"] = m["tweet_count"]
                ch_copy["verified_type"] = m["verified_type"]
            else:
                ch_copy["twitter_id"] = None
                ch_copy["username"] = handle if handle and handle.upper() != "N/A" else None
                ch_copy["followers_count"] = 0
                ch_copy["following_count"] = 0
                ch_copy["tweet_count"] = 0
                ch_copy["verified_type"] = "none"
            ranked.append(ch_copy)

        # Sort descending by followers_count
        ranked.sort(key=lambda x: x.get("followers_count", 0), reverse=True)
        top_channels = ranked[:top_n]

        logger.info(f"[TWITTER] Ranked top {len(top_channels)} channels by follower count:")
        for idx, c in enumerate(top_channels, 1):
            ch_name = c.get("Channel Name") or c.get("name") or "Unknown"
            h = c.get("username") or "N/A"
            fc = c.get("followers_count", 0)
            logger.info(f"[TWITTER]   {idx}. {ch_name} (@{h}): {fc:,} followers")

        return top_channels

    def get_channel_recent_posts(
        self,
        user_id: str,
        username: str,
        hours: int = 24,
        min_impressions: int = 20000,
        max_results: int = 50
    ) -> list[dict]:
        """
        Fetches posts from the last `hours` (default 24), filtering for
        impression count >= min_impressions.
        Extracts long-form note_tweet text, article card metadata, and direct post URLs.
        """
        if not user_id:
            logger.warning(f"[TWITTER] Cannot fetch tweets for @{username}: Missing user_id.")
            return []

        now = datetime.datetime.now(datetime.timezone.utc)
        start_time = (now - datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")

        endpoint = f"{self.BASE_URL}/users/{user_id}/tweets"
        params = {
            "start_time": start_time,
            "max_results": min(max(max_results, 5), 100),
            "tweet.fields": "created_at,public_metrics,entities,note_tweet,conversation_id,lang",
            "exclude": "retweets"
        }

        logger.info(f"[TWITTER] Fetching posts for @{username} (ID: {user_id}) since {start_time} (min views: {min_impressions:,})...")
        try:
            resp = requests.get(endpoint, headers=self._get_headers(), params=params, timeout=15)
            if resp.status_code != 200:
                logger.error(f"[TWITTER] Tweet lookup for @{username} failed with HTTP {resp.status_code}: {resp.text}")
                return []

            payload = resp.json()
            tweets_data = payload.get("data", [])
            logger.info(f"[TWITTER] Received {len(tweets_data)} raw posts from past {hours} hours for @{username}.")

            qualified_posts = []
            for t in tweets_data:
                metrics = t.get("public_metrics", {})
                impressions = metrics.get("impression_count", 0)

                if impressions < min_impressions:
                    continue

                tweet_id = t.get("id")
                post_url = f"https://x.com/{username}/status/{tweet_id}"

                # Extract long-form text if note_tweet is present
                note = t.get("note_tweet", {})
                full_text = note.get("text") or t.get("text", "")

                # Extract linked article card previews
                entities = t.get("entities", {})
                urls_data = entities.get("urls", [])
                article_cards = []
                for u in urls_data:
                    title = u.get("title")
                    desc = u.get("description")
                    exp_url = u.get("expanded_url")
                    if title or desc:
                        article_cards.append({
                            "title": title or "",
                            "description": desc or "",
                            "url": exp_url or u.get("url", "")
                        })

                post_entry = {
                    "tweet_id": tweet_id,
                    "username": username,
                    "url": post_url,
                    "text": full_text,
                    "created_at": t.get("created_at"),
                    "impressions": impressions,
                    "likes": metrics.get("like_count", 0),
                    "retweets": metrics.get("retweet_count", 0),
                    "replies": metrics.get("reply_count", 0),
                    "quotes": metrics.get("quote_count", 0),
                    "bookmarks": metrics.get("bookmark_count", 0),
                    "article_cards": article_cards,
                    "conversation_id": t.get("conversation_id"),
                    "lang": t.get("lang", "")
                }
                qualified_posts.append(post_entry)

            logger.info(f"[TWITTER] @{username}: {len(qualified_posts)}/{len(tweets_data)} posts met >= {min_impressions:,} impressions threshold.")
            return qualified_posts

        except Exception as e:
            logger.error(f"[TWITTER] Exception fetching tweets for @{username}: {e}")
            return []

    def fetch_top_channels_posts(
        self,
        ranked_channels: list[dict],
        hours: int = 24,
        min_impressions: int = 20000
    ) -> list[dict]:
        """
        Iterates over the top channels, fetches qualifying posts from the past 24 hours,
        and aggregates them with channel attribution.
        """
        all_posts = []
        for ch in ranked_channels:
            user_id = ch.get("twitter_id")
            username = ch.get("username")
            ch_name = ch.get("Channel Name") or ch.get("name") or username

            if not user_id or not username:
                logger.info(f"[TWITTER] Skipping {ch_name}: No verified Twitter ID.")
                continue

            channel_posts = self.get_channel_recent_posts(
                user_id=user_id,
                username=username,
                hours=hours,
                min_impressions=min_impressions
            )

            for p in channel_posts:
                p["channel_name"] = ch_name
                all_posts.append(p)

            # Modest delay between accounts to avoid rate bursts
            time.sleep(0.3)

        # Sort all aggregated posts by impression count descending
        all_posts.sort(key=lambda x: x.get("impressions", 0), reverse=True)
        logger.info(f"[TWITTER] Aggregated a total of {len(all_posts)} high-impact posts across all top channels.")
        return all_posts
