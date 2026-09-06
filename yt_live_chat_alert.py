"""
YouTube Live Chat Keyword Alert Bot (Hybrid Edition)
---------------------------------------------------
Watches your favorite streamer's channel. Automatically detects when they
go live, finds the video ID, and monitors that stream's live chat for
keywords, sending alerts via:

1. Telegram
2. ntfy.sh (with direct click-to-open YouTube stream links)

HYBRID ARCHITECTURE:
- Primary Engine (0 Quota Native Innertube): Streams live chat directly using
  YouTube's web Innertube protocol with 0 daily quota consumption.
- Fallback Engine 1 (chat-downloader): Optional secondary scraper.
- Fallback Engine 2 (Official API): Automatically and seamlessly takes over
  using the official YouTube Data API (with multi-key rotation) if web scraping
  is ever temporarily blocked.
- Smart Keyword Matching: Uses regex word boundaries to avoid false positives
  (e.g., 'ff' won't trigger on 'offline' or 'different').
- Anti-Spam Rate Limiting: Debounces rapid chat spam so your phone isn't
  flooded with repetitive notifications.

Configuration (Environment Variables):
    YT_CHANNEL                  -> channel ID (UC...), @handle, or full channel URL (Required)
    YOUTUBE_API_KEY             -> Single API key, OR
    YOUTUBE_API_KEYS            -> Comma-separated list of API keys for auto-rotation
    TELEGRAM_BOT_TOKEN          -> Telegram bot token (Optional)
    TELEGRAM_CHAT_ID            -> Telegram chat/channel ID (Optional)
    NTFY_TOPIC                  -> ntfy.sh topic name (Optional)
    KEYWORDS                    -> Comma-separated keywords (default: "solo, 1v1, ff, free fire")
    ALERT_COOLDOWN_SECONDS      -> Cooldown per keyword in seconds (default: 10)
    IDLE_CHECK_INTERVAL_SECONDS -> How often to check for live stream (default: 180s)
    QUOTA_BACKOFF_SECONDS       -> Wait time if all API keys hit quota (default: 3600s)
"""

import os
import re
import sys
import time
import json
import logging
import threading
from pathlib import Path
from collections import deque
import concurrent.futures
from typing import Optional, Tuple, List, Dict, Callable, Any
from urllib.parse import urlparse, parse_qs

import requests

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ALERT_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="alert_worker")


try:
    from chat_downloader import ChatDownloader
    CHAT_DOWNLOADER_AVAILABLE = True
except ImportError:
    CHAT_DOWNLOADER_AVAILABLE = False

try:
    from googleapiclient.discovery import build, Resource
    from googleapiclient.errors import HttpError
    GOOGLE_API_AVAILABLE = True
except ImportError:
    GOOGLE_API_AVAILABLE = False
    Resource = None
    HttpError = Exception


# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION (.env Auto-Loader)
# ─────────────────────────────────────────────────────────────────────────────

def _load_env_file():
    """Automatically loads .env file from the script's directory if present."""
    env_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, val = line.split("=", 1)
                        key = key.strip()
                        val = val.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = val
        except Exception:
            pass

_load_env_file()

# YouTube Channel to monitor (Channel ID, @handle, or URL)
FAVORITE_CHANNEL = os.environ.get("YT_CHANNEL", "").strip()

# YouTube API keys (optional if running pure scraper, recommended as fallback)
_raw_keys = os.environ.get("YOUTUBE_API_KEYS", "") or os.environ.get("YOUTUBE_API_KEY", "")
YOUTUBE_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]

# Telegram notifications
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

# ntfy.sh notifications
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh").strip().rstrip("/")

# WhatsApp notifications & bot control (Twilio API)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_WHATSAPP_NUMBER = os.environ.get("TWILIO_WHATSAPP_NUMBER", "").strip()  # e.g. "whatsapp:+14155238886"
TARGET_WHATSAPP_NUMBER = os.environ.get("TARGET_WHATSAPP_NUMBER", "").strip()  # e.g. "whatsapp:+919876543210"
ADMIN_WHATSAPP_NUMBER = os.environ.get("ADMIN_WHATSAPP_NUMBER", "").strip()    # Allowed phone number for commands

# Keywords to watch for.
_raw_keywords = os.environ.get("KEYWORDS", "")
if _raw_keywords:
    KEYWORDS = [k.strip() for k in _raw_keywords.split(",") if k.strip()]
else:
    KEYWORDS = [
        "solo",
        "1v1",
        "1 v 1",
        "1vs1",
        "1 vs 1",
        "ff",
        "free fire",
        "free",
        "fire",
        "NAVEEN-PNTk",
    ]

# Anti-spam: Ignore exact duplicate messages for this duration (seconds)
SPAM_COOLDOWN_SECONDS = int(os.environ.get("SPAM_COOLDOWN_SECONDS", os.environ.get("USER_SPAM_COOLDOWN_SECONDS", "30")))
USER_SPAM_COOLDOWN_SECONDS = SPAM_COOLDOWN_SECONDS
DUPLICATE_MESSAGE_COOLDOWN_SECONDS = SPAM_COOLDOWN_SECONDS
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "0"))

# How often to check whether the channel has gone live while idle (seconds)
IDLE_CHECK_INTERVAL_SECONDS = int(os.environ.get("IDLE_CHECK_INTERVAL_SECONDS", "30"))

# If all YouTube API quotas get exceeded, wait this long before retrying
QUOTA_BACKOFF_SECONDS = int(os.environ.get("QUOTA_BACKOFF_SECONDS", "3600"))

# Fallback wait time if YouTube doesn't provide a polling interval in API mode
FALLBACK_POLL_SECONDS = 5

# Minimum polling interval in API mode to avoid excessive quota burn
MIN_API_POLL_SECONDS = 3.0


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)

log = logging.getLogger("yt_alert")


# ─────────────────────────────────────────────────────────────────────────────
# EXCEPTIONS & API MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class QuotaExceededError(Exception):
    """Raised when all configured YouTube API keys have exhausted their daily quota."""
    pass


class YouTubeApiManager:
    """Manages YouTube Data API client instances and automatic key rotation."""

    def __init__(self, api_keys: List[str]):
        self.api_keys = api_keys
        self.current_index = 0
        self._services: Dict[int, Resource] = {}

    @property
    def has_keys(self) -> bool:
        return bool(self.api_keys)

    def get_service(self) -> Optional[Resource]:
        if not self.has_keys or not GOOGLE_API_AVAILABLE:
            return None

        if self.current_index not in self._services:
            key = self.api_keys[self.current_index]
            self._services[self.current_index] = build("youtube", "v3", developerKey=key)

        return self._services[self.current_index]

    def rotate_key(self) -> bool:
        """Rotates to the next available API key. Returns False if all keys exhausted."""
        if len(self.api_keys) <= 1:
            log.warning("Only one API key configured. Cannot rotate.")
            return False

        self.current_index = (self.current_index + 1) % len(self.api_keys)
        masked_key = self.api_keys[self.current_index][:6] + "..." + self.api_keys[self.current_index][-4:]
        log.info(f"Rotated to next YouTube API key [{self.current_index + 1}/{len(self.api_keys)}]: {masked_key}")
        return True


def is_quota_exceeded_error(error: Exception) -> bool:
    """Check if an HttpError is a YouTube quota-exceeded error."""
    if not GOOGLE_API_AVAILABLE or not isinstance(error, HttpError):
        return False

    details = getattr(error, "error_details", None)
    if isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict) and detail.get("reason") == "quotaExceeded":
                return True

    return "quotaExceeded" in str(error)


# ─────────────────────────────────────────────────────────────────────────────
# CHANNEL RESOLUTION (Zero-Quota First with API Fallback)
# ─────────────────────────────────────────────────────────────────────────────

_WEB_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "SOCS=CAESEwgDEgk2OTg5Nzg0MzEaAmVuIAEaBgiA_LyaBg; CONSENT=PENDING+999",
}


def extract_video_id(url_or_id: str) -> Optional[str]:
    """
    Extracts an 11-character YouTube video ID if the input is a video link or bare ID.
    Supports:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://www.youtube.com/live/VIDEO_ID
      - Bare 11-character alphanumeric video ID
    """
    s = url_or_id.strip()
    if not s or s.startswith("UC") or s.startswith("@"):
        return None

    if len(s) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", s):
        return s

    m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    m = re.search(r"youtube\.com/(?:watch\?.*?v=|live/)([a-zA-Z0-9_-]{11})", s)
    if m:
        return m.group(1)

    return None


def resolve_channel_details(
    api_manager: Optional[YouTubeApiManager],
    channel_input: str,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Accepts channel ID (UC...), @handle, full channel URL, or direct video URL/ID.
    Returns: (canonical_channel_id, handle, channel_title)
    Tries zero-quota web lookup first, then official API fallback.
    """
    channel_input = channel_input.strip()
    if not channel_input:
        return None, None, None

    channel_id: Optional[str] = None
    handle: Optional[str] = None
    title: Optional[str] = None

    def _clean_title(t: Optional[str]) -> Optional[str]:
        if not t:
            return None
        t = t.strip()
        if t.endswith(" - YouTube"):
            t = t[:-10].strip()
        return t or None

    def _clean_handle(h: Optional[str]) -> Optional[str]:
        if not h:
            return None
        h = h.strip().lstrip("/")
        if not h.startswith("@"):
            h = f"@{h}"
        return h

    # Case 0: Direct video link or ID provided
    vid = extract_video_id(channel_input)
    if vid:
        try:
            resp = requests.get(f"https://www.youtube.com/watch?v={vid}", headers=_WEB_HEADERS, timeout=10)
            if resp.ok:
                m = re.search(r'itemprop="channelId"\s+content="(UC[\w-]{22})"', resp.text) or re.search(r'"channelId":"(UC[\w-]{22})"', resp.text)
                if m:
                    channel_id = m.group(1)
                hm = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', resp.text) or re.search(r'"vanityChannelUrl":"http[s]?://www\.youtube\.com/(@[^"]+)"', resp.text)
                if hm:
                    handle = _clean_handle(hm.group(1))
                tm = re.search(r'<meta property="og:title" content="([^"]+)"', resp.text)
                if tm:
                    title = _clean_title(tm.group(1))
        except Exception as e:
            log.debug(f"Web video lookup failed for {vid}: {e}")

        # API fallback for video
        if (not channel_id or not handle) and api_manager:
            service = api_manager.get_service()
            if service:
                try:
                    res = service.videos().list(part="snippet", id=vid).execute()
                    items = res.get("items", [])
                    if items:
                        snip = items[0]["snippet"]
                        channel_id = channel_id or snip.get("channelId")
                        title = title or snip.get("channelTitle")
                except Exception as e:
                    log.debug(f"API video lookup failed for {vid}: {e}")

        # If video gave us channel_id but no handle yet, resolve channel_id
        if channel_id and not handle:
            sub_cid, sub_handle, sub_title = resolve_channel_details(api_manager, channel_id)
            return channel_id, sub_handle or handle, sub_title or title
        return channel_id or vid, handle, title

    # Extract ID or handle from full URL
    match = re.search(
        r"youtube\.com/(channel/(UC[\w-]{22})|@([\w.-]+))",
        channel_input,
    )
    if match:
        if match.group(2):
            channel_input = match.group(2)
        elif match.group(3):
            channel_input = "@" + match.group(3)

    # Case 1: Already a standard channel ID
    if channel_input.startswith("UC") and len(channel_input) == 24:
        channel_id = channel_input
        try:
            url = f"https://www.youtube.com/channel/{channel_id}"
            resp = requests.get(url, headers=_WEB_HEADERS, timeout=10)
            if resp.ok:
                hm = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', resp.text) or re.search(r'"vanityChannelUrl":"http[s]?://www\.youtube\.com/(@[^"]+)"', resp.text)
                if hm:
                    handle = _clean_handle(hm.group(1))
                tm = re.search(r'<meta property="og:title" content="([^"]+)"', resp.text)
                if tm:
                    title = _clean_title(tm.group(1))
        except Exception as e:
            log.debug(f"Web channel lookup failed for {channel_id}: {e}")

        # API fallback for channel ID
        if (not handle or not title) and api_manager:
            service = api_manager.get_service()
            if service:
                try:
                    res = service.channels().list(part="snippet", id=channel_id).execute()
                    items = res.get("items", [])
                    if items:
                        snip = items[0]["snippet"]
                        title = title or _clean_title(snip.get("title"))
                        cust = snip.get("customUrl")
                        if cust:
                            handle = handle or _clean_handle(cust)
                except Exception as e:
                    log.debug(f"API channel lookup failed for {channel_id}: {e}")

        return channel_id, handle, title

    # Case 2: Handle input (@handle or handle)
    handle = _clean_handle(channel_input)

    # Step 1: Try zero-quota web scraping first
    try:
        url = f"https://www.youtube.com/{handle}"
        resp = requests.get(url, headers=_WEB_HEADERS, timeout=10)
        if resp.ok:
            cid_m = re.search(r'itemprop="channelId"\s+content="(UC[\w-]{22})"', resp.text) or re.search(r'"browseId":"(UC[\w-]{22})"', resp.text)
            if cid_m:
                channel_id = cid_m.group(1)
            tm = re.search(r'<meta property="og:title" content="([^"]+)"', resp.text)
            if tm:
                title = _clean_title(tm.group(1))
            hm = re.search(r'"canonicalBaseUrl":"(/@[^"]+)"', resp.text)
            if hm:
                handle = _clean_handle(hm.group(1))
    except Exception as e:
        log.debug(f"Web handle resolution failed for {handle}: {e}")

    # Step 2: Try Official API if configured
    if (not channel_id or not title) and api_manager:
        service = api_manager.get_service()
        if service:
            try:
                response = service.channels().list(
                    part="snippet",
                    forHandle=handle,
                ).execute()
                items = response.get("items", [])
                if items:
                    channel_id = channel_id or items[0]["id"]
                    snip = items[0]["snippet"]
                    title = title or _clean_title(snip.get("title"))
                    cust = snip.get("customUrl")
                    if cust:
                        handle = _clean_handle(cust)
            except Exception as e:
                log.warning(f"API handle resolution failed for {handle}: {e}")

    return channel_id, handle, title


def resolve_channel_id(api_manager: YouTubeApiManager, channel_input: str) -> str:
    """
    Accepts a channel ID, @handle, full channel URL, or direct video URL/ID,
    and returns the canonical channel ID (UC...).
    """
    cid, _, _ = resolve_channel_details(api_manager, channel_input)
    if cid:
        return cid
    raise RuntimeError(f"Could not resolve channel ID for input: {channel_input}")


# ─────────────────────────────────────────────────────────────────────────────
# LIVE STREAM DETECTION (Multi-Tier Zero-Quota + Official API Fallback)
# ─────────────────────────────────────────────────────────────────────────────

def is_video_actually_live(video_id: str, api_manager: Optional[YouTubeApiManager] = None) -> bool:
    """
    Verifies if a candidate video is ACTUALLY live right now.
    Prevents false triggers on past recorded streams or offline channel pages.
    """
    try:
        # Check 1: Live chat page continuation
        chat_url = f"https://www.youtube.com/live_chat?v={video_id}"
        resp = requests.get(chat_url, headers=_WEB_HEADERS, timeout=10)
        if '"continuation":"' in resp.text and '"INNERTUBE_API_KEY":"' in resp.text:
            return True

        # Check 2: Watch page for live markers
        watch_url = f"https://www.youtube.com/watch?v={video_id}"
        w_resp = requests.get(watch_url, headers=_WEB_HEADERS, timeout=10)
        if re.search(r'"isLiveNow":\s*true', w_resp.text):
            return True
        if re.search(r'"isLive":\s*true', w_resp.text):
            return True
        if '<meta itemprop="isLiveBroadcast" content="True">' in w_resp.text:
            return True
        if '"BADGE_STYLE_TYPE_LIVE_NOW"' in w_resp.text:
            return True
    except Exception as e:
        log.debug(f"Web live check error for {video_id}: {e}")

    # Check 3: Official API fallback if available
    if api_manager and api_manager.has_keys:
        try:
            status, _ = get_stream_status_api(api_manager, video_id)
            if status == "live":
                return True
        except Exception:
            pass

    return False


def is_video_from_channel(
    video_id: str,
    channel_id: str,
    api_manager: Optional[YouTubeApiManager] = None,
) -> bool:
    """
    Verifies that a video ACTUALLY belongs to the specified channel,
    preventing recommended, featured, or sidebar streams from false-triggering.
    """
    if not video_id or not channel_id:
        return False

    # Check 1: Official API check if available (fast and 100% accurate)
    if api_manager and api_manager.has_keys:
        service = api_manager.get_service()
        if service:
            try:
                resp = service.videos().list(part="snippet", id=video_id).execute()
                items = resp.get("items", [])
                if items:
                    owner_cid = items[0]["snippet"].get("channelId", "")
                    return owner_cid == channel_id
            except Exception:
                pass

    # Check 2: Zero-quota watch page check
    try:
        url = f"https://www.youtube.com/watch?v={video_id}"
        resp = requests.get(url, headers=_WEB_HEADERS, timeout=10)
        if resp.ok:
            m = re.search(r'itemprop="channelId"\s+content="(UC[\w-]{22})"', resp.text)
            if not m:
                m = re.search(r'"channelId":"(UC[\w-]{22})"', resp.text)
            if not m:
                m = re.search(r'"externalChannelId":"(UC[\w-]{22})"', resp.text)
            if m:
                return m.group(1) == channel_id
    except Exception as e:
        log.debug(f"Channel ownership verification error for {video_id}: {e}")

    return False


def find_live_video_id_api(api_manager: YouTubeApiManager, channel_id: str) -> Optional[str]:
    """
    Official API search for active live broadcast strictly belonging to the channel.
    Returns video_id or None.
    """
    service = api_manager.get_service()
    if not service:
        return None
    try:
        response = service.search().list(
            part="id",
            channelId=channel_id,
            eventType="live",
            type="video",
            maxResults=1,
        ).execute()
        items = response.get("items", [])
        if items:
            vid = items[0]["id"]["videoId"]
            log.info(f"Official API identified channel live broadcast: {vid}")
            return vid
    except Exception as e:
        if is_quota_exceeded_error(e):
            log.warning("YouTube API quota exceeded during live search. Rotating key...")
            api_manager.rotate_key()
        else:
            log.warning(f"YouTube API live stream search error: {e}")
    return None


def find_candidate_video_id(
    channel_id: str,
    api_manager: Optional[YouTubeApiManager] = None,
) -> Optional[str]:
    """
    Multi-tier live stream detector with strict channel ownership verification:
    1. Official YouTube Data API first if configured (100% accurate & channel-locked)
    2. Zero-quota web check on /channel/UC.../live (verified by channelId)
    3. Zero-quota web check on /channel/UC.../streams (verified by channelId)
    """
    # Strategy 1: Official YouTube Data API first if keys are available
    # API search by channelId guarantees zero false positives and never picks up other channels!
    if api_manager and api_manager.has_keys:
        api_vid = find_live_video_id_api(api_manager, channel_id)
        if api_vid:
            log.info(f"Found active live stream via Official API: {api_vid}")
            return api_vid

    candidate_ids = []

    # Strategy 2: Check /channel/UC.../live
    live_url = f"https://www.youtube.com/channel/{channel_id}/live"
    try:
        resp = requests.get(live_url, headers=_WEB_HEADERS, timeout=10, allow_redirects=True)
        if resp.ok and "consent.youtube.com" not in resp.url:
            query = parse_qs(urlparse(resp.url).query)
            redirected_v = query.get("v", [None])[0]
            if redirected_v and redirected_v not in candidate_ids:
                candidate_ids.append(redirected_v)

            # Extract videoIds from liveStreamability
            streamability = re.findall(r'"liveStreamability"[^}]+?"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
            for vid in streamability:
                if vid not in candidate_ids:
                    candidate_ids.append(vid)

            # Extract videoIds from watchEndpoint
            watch_endpoints = re.findall(r'"watchEndpoint":\s*\{\s*"videoId":"([a-zA-Z0-9_-]{11})"', resp.text)
            for vid in watch_endpoints:
                if vid not in candidate_ids:
                    candidate_ids.append(vid)
    except Exception as e:
        log.debug(f"Web /live lookup error for channel {channel_id}: {e}")

    for vid in candidate_ids:
        if is_video_actually_live(vid, api_manager=api_manager):
            if is_video_from_channel(vid, channel_id, api_manager=api_manager):
                log.info(f"Found active live stream for channel {channel_id}: {vid}")
                return vid
            else:
                log.info(f"Ignoring recommended live stream {vid} (does not belong to channel {channel_id})")

    # Strategy 3: Check /channel/UC.../streams
    streams_url = f"https://www.youtube.com/channel/{channel_id}/streams"
    streams_candidates = []
    try:
        resp = requests.get(streams_url, headers=_WEB_HEADERS, timeout=10, allow_redirects=True)
        if resp.ok and "consent.youtube.com" not in resp.url:
            for m in re.finditer(r'"videoId":"([a-zA-Z0-9_-]{11})"', resp.text):
                vid = m.group(1)
                if vid not in candidate_ids and vid not in streams_candidates:
                    streams_candidates.append(vid)
                if len(streams_candidates) >= 6:
                    break
    except Exception as e:
        log.debug(f"Web /streams lookup error for channel {channel_id}: {e}")

    for vid in streams_candidates:
        if is_video_actually_live(vid, api_manager=api_manager):
            if is_video_from_channel(vid, channel_id, api_manager=api_manager):
                log.info(f"Found active live stream via /streams for channel {channel_id}: {vid}")
                return vid
            else:
                log.info(f"Ignoring non-channel live stream {vid}")

    return None


def get_stream_status_api(api_manager: YouTubeApiManager, video_id: str) -> Tuple[str, Optional[str]]:
    """
    Official API check for stream status and active live chat ID.
    Returns (status, live_chat_id).
    """
    service = api_manager.get_service()
    if not service:
        return "unknown", None

    try:
        response = service.videos().list(
            part="snippet,liveStreamingDetails",
            id=video_id,
        ).execute()

        items = response.get("items", [])
        if not items:
            return "none", None

        snippet = items[0].get("snippet", {})
        details = items[0].get("liveStreamingDetails", {})

        status = snippet.get("liveBroadcastContent", "none")
        live_chat_id = details.get("activeLiveChatId")

        return status, live_chat_id

    except Exception as error:
        if is_quota_exceeded_error(error):
            raise QuotaExceededError(str(error)) from error
        log.warning(f"Error checking stream status via API: {error}")
        return "unknown", None


# ─────────────────────────────────────────────────────────────────────────────
# KEYWORD MATCHING & SPAM DEBOUNCING
# ─────────────────────────────────────────────────────────────────────────────

def compile_keyword_patterns(keywords: List[str]) -> List[Tuple[str, re.Pattern]]:
    """Compile regex patterns for keywords with word boundaries."""
    return [
        (kw.strip(), re.compile(r"\b" + re.escape(kw.strip()) + r"\b", re.IGNORECASE))
        for kw in keywords
        if kw.strip()
    ]


_KEYWORD_PATTERNS = compile_keyword_patterns(KEYWORDS)

# Anti-spam deduplication tracking
_user_message_timestamps: Dict[Tuple[str, str], float] = {}   # (author, normalized_text) -> timestamp
_global_message_timestamps: Dict[str, float] = {}             # normalized_text -> timestamp


def _cleanup_anti_spam_cache(now: float, max_age: float = 300.0) -> None:
    """Purges expired anti-spam timestamps to keep memory usage minimal."""
    cutoff = now - max_age
    for cache in (_user_message_timestamps, _global_message_timestamps):
        expired = [k for k, ts in cache.items() if ts < cutoff]
        for k in expired:
            del cache[k]


def reset_anti_spam_cache() -> None:
    """Purges all anti-spam and cooldown timestamps so new keywords or messages trigger alerts immediately."""
    _user_message_timestamps.clear()
    _global_message_timestamps.clear()


def strip_emoji_placeholders(text: str) -> str:
    """Remove YouTube custom emoji tags like :rocket-red: from text."""
    cleaned = re.sub(r":[a-zA-Z0-9_-]+:", "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def matches_keyword(text: str, patterns: Optional[List[Tuple[str, re.Pattern]]] = None) -> Optional[str]:
    """
    Checks whether the text matches any configured keyword using word boundaries.
    Returns the matched keyword string, or None.
    """
    active_patterns = patterns if patterns is not None else _KEYWORD_PATTERNS
    for keyword, pattern in active_patterns:
        if pattern.search(text):
            return keyword
    return None


def should_send_alert(author: str, keyword: str, text: str) -> bool:
    """
    Evaluates anti-spam filters before dispatching alerts:
    - If the same user repeats the exact same message within 30s (e.g. 'solo', 'solo', 'solo'), it blocks as spam.
    - If the user sends different messages (e.g. 'solo', 'solo pola', 'solo va'), each unique message triggers an alert.
    - If anyone sends the exact same spam text across chat, duplicate is ignored for 30s.
    """
    now = time.time()
    _cleanup_anti_spam_cache(now)

    author_key = author.strip().lower()

    # Normalize message text by removing emojis, punctuation, collapsing whitespace and lowercasing
    cleaned = strip_emoji_placeholders(text)
    normalized = re.sub(r"[^\w\s]", "", cleaned.lower())
    normalized_text = re.sub(r"\s+", " ", normalized).strip()
    if not normalized_text:
        normalized_text = cleaned.strip().lower()

    # 1. Check if this exact message was already alerted recently (global duplicate text - 30s)
    last_global_msg = _global_message_timestamps.get(normalized_text, 0.0)
    if now - last_global_msg < SPAM_COOLDOWN_SECONDS:
        remaining = int(SPAM_COOLDOWN_SECONDS - (now - last_global_msg))
        log.info(f"SPAM FILTER: Ignored duplicate chat spam '{normalized_text}' from '{author}' ({remaining}s cooldown remaining).")
        return False

    # 2. Check if this user already sent this exact message within the 30s window
    last_user_msg = _user_message_timestamps.get((author_key, normalized_text), 0.0)
    if now - last_user_msg < SPAM_COOLDOWN_SECONDS:
        remaining = int(SPAM_COOLDOWN_SECONDS - (now - last_user_msg))
        log.info(f"SPAM FILTER: Ignored repeated message '{normalized_text}' from '{author}' ({remaining}s cooldown remaining).")
        return False

    # Passed filters: record timestamps and allow alert
    _global_message_timestamps[normalized_text] = now
    _user_message_timestamps[(author_key, normalized_text)] = now
    return True


# ─────────────────────────────────────────────────────────────────────────────
# ALERT DISPATCH (Telegram & ntfy)
# ─────────────────────────────────────────────────────────────────────────────

def format_author(author: str) -> str:
    """Ensure username is cleanly formatted with exactly one leading '@'."""
    clean = (author or "").strip().lstrip("@")
    return f"@{clean}" if clean else "@Anonymous"


def send_telegram_alert(author: str, keyword: str, text: str, video_id: str) -> None:
    """Send formatted alert to Telegram with stream link."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    author_display = format_author(author)
    stream_url = f"https://youtu.be/{video_id}"
    message = (
        f"🚨 *YouTube Live Chat Alert*\n\n"
        f"• *Keyword:* `{keyword}`\n"
        f"• *User:* {author_display}\n"
        f"• *Message:* {text}\n\n"
        f"🔗 [Watch Stream Live]({stream_url})"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram alert sent.")
    except requests.HTTPError:
        # Fallback without markdown formatting in case text contains unescaped special characters
        try:
            plain_text = (
                f"YouTube Live Chat Alert\n\n"
                f"• Keyword: {keyword}\n"
                f"• User: {author_display}\n"
                f"• Message: {text}\n\n"
                f"Watch Stream: {stream_url}"
            )
            resp = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": plain_text,
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            resp.raise_for_status()
            log.info("Telegram alert sent (plain text fallback).")
        except Exception as fallback_err:
            log.error(f"Telegram alert fallback failed: {fallback_err}")
    except Exception as error:
        log.error(f"Telegram alert failed: {error}")


def send_ntfy_alert(author: str, keyword: str, text: str, video_id: str) -> None:
    """Send high-priority alert to ntfy with direct click-action URL."""
    if not NTFY_TOPIC:
        return

    author_display = format_author(author)
    stream_url = f"https://youtu.be/{video_id}"
    message_body = f"{author_display}: {text}"
    headers = {
        "Title": f'Live Chat: "{keyword}"',
        "Priority": "5",
        "Tags": "rotating_light,youtube",
        "Click": stream_url,
    }

    # 1. Try direct topic POST (standard endpoint)
    try:
        url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
        resp = requests.post(url, data=message_body.encode("utf-8"), headers=headers, timeout=10)
        if resp.ok:
            log.info("ntfy alert sent.")
            return
        log.warning(f"ntfy direct alert returned status {resp.status_code}")
    except Exception as error:
        log.error(f"ntfy alert direct post failed: {error}")

    # 2. Fallback to root JSON POST
    try:
        resp = requests.post(
            NTFY_SERVER,
            json={
                "topic": NTFY_TOPIC,
                "title": f'Live Chat: "{keyword}"',
                "message": message_body,
                "priority": 5,
                "tags": ["rotating_light", "youtube"],
                "click": stream_url,
            },
            timeout=10,
        )
        if resp.ok:
            log.info("ntfy alert sent via JSON fallback.")
    except Exception as error:
        log.error(f"ntfy alert fallback failed: {error}")


def send_telegram_live_alert(video_id: str) -> None:
    """Send alert to Telegram when streamer goes live."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    stream_url = f"https://youtu.be/{video_id}"
    message = (
        f"🔴 *Streamer is LIVE!* 🎥\n\n"
        f"The channel has started live streaming.\n\n"
        f"🔗 [Watch Stream Live]({stream_url})"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info("Telegram stream-live alert sent.")
    except Exception as error:
        log.error(f"Telegram stream-live alert failed: {error}")


def send_ntfy_live_alert(video_id: str) -> None:
    """Send high-priority alert to ntfy when streamer goes live."""
    if not NTFY_TOPIC:
        return

    stream_url = f"https://youtu.be/{video_id}"
    alert_body = "Streamer has gone live! Click to watch the stream."
    headers = {
        "Title": "🔴 Streamer is LIVE!",
        "Priority": "4",
        "Tags": "tv,red_circle,youtube",
        "Click": stream_url,
    }

    try:
        url = f"{NTFY_SERVER}/{NTFY_TOPIC}"
        resp = requests.post(url, data=alert_body.encode("utf-8"), headers=headers, timeout=10)
        if resp.ok:
            log.info("ntfy stream-live alert sent.")
            return
    except Exception as error:
        log.error(f"ntfy stream-live alert failed: {error}")

    try:
        resp = requests.post(
            NTFY_SERVER,
            json={
                "topic": NTFY_TOPIC,
                "title": "🔴 Streamer is LIVE!",
                "message": alert_body,
                "priority": 4,
                "tags": ["tv", "red_circle", "youtube"],
                "click": stream_url,
            },
            timeout=10,
        )
        if resp.ok:
            log.info("ntfy stream-live alert sent via fallback.")
    except Exception as error:
        log.error(f"ntfy stream-live alert fallback failed: {error}")


# ─────────────────────────────────────────────────────────────────────────────
# ALERT DISPATCH: WHATSAPP (Twilio API)
# ─────────────────────────────────────────────────────────────────────────────

def send_whatsapp_message(body: str, to_number: Optional[str] = None) -> bool:
    """Send a WhatsApp message via Twilio API."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER):
        return False

    recipient = (to_number or TARGET_WHATSAPP_NUMBER or ADMIN_WHATSAPP_NUMBER).strip()
    if not recipient:
        return False

    if not recipient.startswith("whatsapp:"):
        recipient = f"whatsapp:{recipient}"
    from_num = TWILIO_WHATSAPP_NUMBER.strip()
    if not from_num.startswith("whatsapp:"):
        from_num = f"whatsapp:{from_num}"

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
    try:
        resp = requests.post(
            url,
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={
                "From": from_num,
                "To": recipient,
                "Body": body,
            },
            timeout=10,
        )
        resp.raise_for_status()
        log.info(f"WhatsApp message sent to {recipient}.")
        return True
    except Exception as e:
        log.error(f"WhatsApp delivery failed: {e}")
        return False


def send_whatsapp_alert(author: str, keyword: str, text: str, video_id: str) -> None:
    """Send formatted alert to WhatsApp with stream link."""
    author_display = format_author(author)
    stream_url = f"https://youtu.be/{video_id}"
    msg = (
        f"🚨 *YouTube Live Chat Alert*\n\n"
        f"• *Keyword:* `{keyword}`\n"
        f"• *User:* {author_display}\n"
        f"• *Message:* {text}\n\n"
        f"🔗 Watch Stream: {stream_url}"
    )
    send_whatsapp_message(msg)


def send_whatsapp_live_alert(video_id: str) -> None:
    """Send alert to WhatsApp when streamer goes live."""
    stream_url = f"https://youtu.be/{video_id}"
    msg = (
        f"🔴 *Streamer is LIVE!* 🎥\n\n"
        f"The channel has started live streaming.\n\n"
        f"🔗 Watch Stream: {stream_url}"
    )
    send_whatsapp_message(msg)


def dispatch_live_alerts(video_id: str) -> None:
    """Dispatches 'Streamer is Live' alerts to all configured channels concurrently."""
    log.info(f"Dispatching 'Streamer is LIVE' alert for video {video_id}...")
    _ALERT_EXECUTOR.submit(send_telegram_live_alert, video_id)
    _ALERT_EXECUTOR.submit(send_ntfy_live_alert, video_id)
    _ALERT_EXECUTOR.submit(send_whatsapp_live_alert, video_id)


def dispatch_alerts(
    author: str,
    keyword: str,
    text: str,
    video_id: str,
    on_match: Optional[Callable[[str, str, str, str], None]] = None,
) -> None:
    """Dispatches alerts to all configured channels concurrently if not throttled."""
    if not should_send_alert(author, keyword, text):
        return

    log.info(f"MATCH [{keyword}] by {author}: {text}")
    _ALERT_EXECUTOR.submit(send_telegram_alert, author, keyword, text, video_id)
    _ALERT_EXECUTOR.submit(send_ntfy_alert, author, keyword, text, video_id)
    _ALERT_EXECUTOR.submit(send_whatsapp_alert, author, keyword, text, video_id)
    if on_match:
        try:
            on_match(author, keyword, text, video_id)
        except Exception as e:
            log.warning(f"Error in on_match callback: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CHAT MONITORING: PRIMARY ENGINE (Native YouTube Innertube / 0 Quota)
# ─────────────────────────────────────────────────────────────────────────────

def extract_live_chat_continuation(html: str) -> Optional[str]:
    """
    Extracts the 'Live chat' continuation token (raw real-time stream)
    instead of the default 'Top chat' token (filtered and delayed stream).
    """
    # Method 1: Regex targeting the "Live chat" subMenu reloadContinuationData
    m = re.search(
        r'"title":\s*"Live chat".*?"reloadContinuationData":\s*\{\s*"continuation":\s*"([^"]+)"',
        html,
        re.DOTALL,
    )
    if m:
        return m.group(1)

    # Method 2: Inspect ytInitialData JSON object if present
    data_match = re.search(r'(?:window\["ytInitialData"\]|var ytInitialData)\s*=\s*({.+?});\s*</script>', html)
    if data_match:
        try:
            data = json.loads(data_match.group(1))
            contents = data.get("contents", {}).get("liveChatRenderer", {})
            header = contents.get("header", {}).get("liveChatHeaderRenderer", {})
            items = header.get("viewSelector", {}).get("sortFilterSubMenuRenderer", {}).get("subMenuItems", [])
            for it in items:
                if it.get("title") == "Live chat":
                    cont = it.get("continuation", {}).get("reloadContinuationData", {}).get("continuation")
                    if cont:
                        return cont
        except Exception:
            pass

    # Method 3: Fallback to the first continuation token found on the page
    default_match = re.search(r'"continuation":"([^"]+)"', html)
    if default_match:
        return default_match.group(1)

    return None


def watch_chat_innertube(
    video_id: str,
    stop_event: Optional[threading.Event] = None,
    patterns: Optional[List[Tuple[str, re.Pattern]]] = None,
    on_match: Optional[Callable[[str, str, str, str], None]] = None,
    on_message: Optional[Callable[[str, str], None]] = None,
    get_patterns: Optional[Callable[[], List[Tuple[str, re.Pattern]]]] = None,
) -> bool:
    """
    Streams YouTube live chat in real time directly through YouTube's web
    Innertube protocol with 0 daily quota usage.
    Returns True when the stream ends normally, or False if an error occurred.
    """
    live_chat_url = f"https://www.youtube.com/live_chat?v={video_id}"
    log.info(f"Connecting to live chat stream via 0-Quota Innertube Engine: {live_chat_url}")

    session = requests.Session()
    session.headers.update(_WEB_HEADERS)

    try:
        resp = session.get(live_chat_url, timeout=10)
        if not resp.ok:
            log.warning(f"Live chat webpage returned status {resp.status_code}")
            return False

        api_key_match = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', resp.text)
        context_match = re.search(r'"INNERTUBE_CONTEXT":\s*({.+?}),"INNERTUBE_CONTEXT_CLIENT_NAME"', resp.text)

        if not (api_key_match and context_match):
            log.warning("Could not extract Innertube tokens from live chat page.")
            return False

        continuation = extract_live_chat_continuation(resp.text)
        if not continuation:
            log.warning("Could not extract continuation token from live chat page.")
            return False

        api_key = api_key_match.group(1)
        context = json.loads(context_match.group(1))

        post_url = f"https://www.youtube.com/youtubei/v1/live_chat/get_live_chat?key={api_key}"
        seen_message_ids = set()
        seen_message_order = deque()

        log.info("Live chat connection established (Live Chat mode). Listening for messages in real time...")

        while True:
            if stop_event and stop_event.is_set():
                log.info("Chat monitor stop requested.")
                return False

            payload = {"context": context, "continuation": continuation}
            chat_resp = session.post(post_url, json=payload, timeout=10)

            if chat_resp.status_code != 200:
                log.warning(f"Innertube chat API returned HTTP {chat_resp.status_code}")
                return False

            data = chat_resp.json()
            cont_contents = data.get("continuationContents", {}).get("liveChatContinuation", {})
            actions = cont_contents.get("actions", [])

            for act in actions:
                chat_item = act.get("addChatItemAction", {}).get("item", {})
                item = (
                    chat_item.get("liveChatTextMessageRenderer")
                    or chat_item.get("liveChatPaidMessageRenderer")
                    or chat_item.get("liveChatMembershipItemRenderer")
                )
                if item:
                    msg_id = item.get("id")
                    if msg_id and msg_id in seen_message_ids:
                        continue
                    if msg_id:
                        seen_message_ids.add(msg_id)
                        seen_message_order.append(msg_id)
                        if len(seen_message_order) > 2000:
                            old_id = seen_message_order.popleft()
                            seen_message_ids.discard(old_id)

                    author = item.get("authorName", {}).get("simpleText", "Anonymous")
                    runs = item.get("message", {}).get("runs", [])
                    raw_text = "".join(r.get("text", "") for r in runs)

                    if raw_text:
                        if on_message:
                            try:
                                on_message(author, raw_text)
                            except Exception:
                                pass
                        cleaned_text = strip_emoji_placeholders(raw_text)
                        active_patterns = get_patterns() if get_patterns else patterns
                        matched_kw = matches_keyword(cleaned_text, patterns=active_patterns)
                        if matched_kw:
                            dispatch_alerts(author, matched_kw, cleaned_text, video_id, on_match=on_match)

            # Extract continuation for next batch
            conts = cont_contents.get("continuations", [])
            if not conts:
                log.info("Live chat continuation ended (stream over).")
                return True

            cont_data = conts[0].get("invalidationContinuationData") or conts[0].get("timedContinuationData", {})
            continuation = cont_data.get("continuation")
            timeout_ms = cont_data.get("timeoutMs")

            if not continuation:
                log.info("No further chat continuation token. Stream is over.")
                return True

            poll_delay = min(1.5, max(1.0, timeout_ms / 1000.0)) if timeout_ms else 1.5
            if stop_event:
                if stop_event.wait(poll_delay):
                    log.info("Chat monitor stopped during poll delay.")
                    return False
            else:
                time.sleep(poll_delay)

    except Exception as e:
        log.warning(f"Native Innertube streamer encountered an issue: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHAT MONITORING: SECONDARY SCRAPER (chat-downloader / 0 Quota)
# ─────────────────────────────────────────────────────────────────────────────

def watch_chat_chatdownloader(
    video_id: str,
    stop_event: Optional[threading.Event] = None,
    patterns: Optional[List[Tuple[str, re.Pattern]]] = None,
    on_match: Optional[Callable[[str, str, str, str], None]] = None,
    on_message: Optional[Callable[[str, str], None]] = None,
    get_patterns: Optional[Callable[[], List[Tuple[str, re.Pattern]]]] = None,
) -> bool:
    """Secondary 0-quota fallback using chat-downloader."""
    if not CHAT_DOWNLOADER_AVAILABLE:
        return False

    stream_url = f"https://www.youtube.com/watch?v={video_id}"
    log.info(f"Connecting to live chat stream via chat-downloader fallback: {stream_url}")

    try:
        downloader = ChatDownloader()
        chat = downloader.get_chat(stream_url)

        for message in chat:
            if stop_event and stop_event.is_set():
                log.info("chat-downloader stop requested.")
                return False

            author = message.get("author", {}).get("name", "Anonymous")
            raw_text = message.get("message", "")
            if not raw_text:
                continue

            if on_message:
                try:
                    on_message(author, raw_text)
                except Exception:
                    pass

            cleaned_text = strip_emoji_placeholders(raw_text)
            active_patterns = get_patterns() if get_patterns else patterns
            matched_kw = matches_keyword(cleaned_text, patterns=active_patterns)
            if matched_kw:
                dispatch_alerts(author, matched_kw, cleaned_text, video_id, on_match=on_match)

        log.info("Chat stream completed (stream ended).")
        return True
    except Exception as e:
        log.warning(f"chat-downloader encountered error: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CHAT MONITORING: FALLBACK ENGINE (Official YouTube Data API)
# ─────────────────────────────────────────────────────────────────────────────

def watch_chat_api(
    api_manager: YouTubeApiManager,
    live_chat_id: str,
    video_id: str,
    stop_event: Optional[threading.Event] = None,
    patterns: Optional[List[Tuple[str, re.Pattern]]] = None,
    on_match: Optional[Callable[[str, str, str, str], None]] = None,
    on_message: Optional[Callable[[str, str], None]] = None,
    get_patterns: Optional[Callable[[], List[Tuple[str, re.Pattern]]]] = None,
) -> None:
    """
    Monitors live chat using the official YouTube Data API.
    Handles automatic key rotation if quota is exceeded.
    """
    if not api_manager.has_keys:
        log.warning("No YouTube API keys configured for API fallback mode.")
        if stop_event:
            stop_event.wait(FALLBACK_POLL_SECONDS)
        else:
            time.sleep(FALLBACK_POLL_SECONDS)
        return

    log.info(f"Monitoring live chat via Official API (chat ID: {live_chat_id})")
    next_page_token = None

    while True:
        if stop_event and stop_event.is_set():
            log.info("API chat monitor stop requested.")
            return

        service = api_manager.get_service()
        if not service:
            return

        try:
            request = service.liveChatMessages().list(
                liveChatId=live_chat_id,
                part="snippet,authorDetails",
                pageToken=next_page_token,
                maxResults=200,
            )
            response = request.execute()

        except HttpError as error:
            if is_quota_exceeded_error(error):
                log.warning("Active API key quota exceeded.")
                if api_manager.rotate_key():
                    continue
                else:
                    raise QuotaExceededError("All YouTube API keys exhausted.") from error

            error_text = str(error)
            if "liveChatEnded" in error_text or "live chat is no longer live" in error_text.lower():
                log.info("Live chat has ended. Stream is over.")
                return

            log.error(f"YouTube API error while polling chat: {error}")
            if stop_event:
                if stop_event.wait(FALLBACK_POLL_SECONDS):
                    return
            else:
                time.sleep(FALLBACK_POLL_SECONDS)
            continue

        except Exception as error:
            log.error(f"Unexpected error polling live chat API: {error}")
            if stop_event:
                if stop_event.wait(FALLBACK_POLL_SECONDS):
                    return
            else:
                time.sleep(FALLBACK_POLL_SECONDS)
            continue

        for item in response.get("items", []):
            try:
                author = item["authorDetails"]["displayName"]
                raw_text = item["snippet"]["displayMessage"]
            except KeyError:
                continue

            if on_message:
                try:
                    on_message(author, raw_text)
                except Exception:
                    pass

            cleaned_text = strip_emoji_placeholders(raw_text)
            active_patterns = get_patterns() if get_patterns else patterns
            matched_kw = matches_keyword(cleaned_text, patterns=active_patterns)

            if matched_kw:
                dispatch_alerts(author, matched_kw, cleaned_text, video_id, on_match=on_match)

        next_page_token = response.get("nextPageToken")
        polling_millis = response.get("pollingIntervalMillis", FALLBACK_POLL_SECONDS * 1000)
        poll_interval = max(MIN_API_POLL_SECONDS, polling_millis / 1000.0)
        if stop_event:
            if stop_event.wait(poll_interval):
                return
        else:
            time.sleep(poll_interval)


# ─────────────────────────────────────────────────────────────────────────────
# HYBRID CHAT WATCHER
# ─────────────────────────────────────────────────────────────────────────────

def watch_chat_hybrid(
    api_manager: YouTubeApiManager,
    video_id: str,
    live_chat_id: Optional[str] = None,
    stop_event: Optional[threading.Event] = None,
    patterns: Optional[List[Tuple[str, re.Pattern]]] = None,
    on_match: Optional[Callable[[str, str, str, str], None]] = None,
    on_message: Optional[Callable[[str, str], None]] = None,
    get_patterns: Optional[Callable[[], List[Tuple[str, re.Pattern]]]] = None,
) -> None:
    """
    Executes primary 0-quota Innertube engine -> chat-downloader -> Official API fallback.
    """
    # Tier 1: Primary Native Innertube (0 Quota)
    if watch_chat_innertube(
        video_id,
        stop_event=stop_event,
        patterns=patterns,
        on_match=on_match,
        on_message=on_message,
        get_patterns=get_patterns,
    ):
        return

    if stop_event and stop_event.is_set():
        return

    # Tier 2: chat-downloader (0 Quota)
    if watch_chat_chatdownloader(
        video_id,
        stop_event=stop_event,
        patterns=patterns,
        on_match=on_match,
        on_message=on_message,
        get_patterns=get_patterns,
    ):
        return

    if stop_event and stop_event.is_set():
        return

    # Tier 3: Official YouTube Data API
    if api_manager.has_keys:
        log.info("Scrapers unavailable. Falling back to Official YouTube Data API...")
        if not live_chat_id:
            status, live_chat_id = get_stream_status_api(api_manager, video_id)
            if status != "live" or not live_chat_id:
                log.info("Stream does not appear to have an active live chat via API.")
                return

        watch_chat_api(
            api_manager,
            live_chat_id,
            video_id,
            stop_event=stop_event,
            patterns=patterns,
            on_match=on_match,
            on_message=on_message,
            get_patterns=get_patterns,
        )
    else:
        log.warning(
            "Scrapers failed and no YOUTUBE_API_KEY is configured for API fallback. "
            "Waiting before next retry..."
        )
        if stop_event:
            stop_event.wait(FALLBACK_POLL_SECONDS)
        else:
            time.sleep(FALLBACK_POLL_SECONDS)


# ─────────────────────────────────────────────────────────────────────────────
# CONTROLLER: LIVE CHAT ALERT BOT (Thread-Safe & Dynamic)
# ─────────────────────────────────────────────────────────────────────────────

_STATE_FILE = Path(__file__).resolve().parent / "bot_state.json"


class LiveChatAlertBot:
    """
    Thread-safe, state-managed YouTube Live Chat Alert Bot.
    Can be started, stopped, configured, and queried for status
    dynamically via WhatsApp webhooks, REST API, or Python code.
    """

    def __init__(
        self,
        channel: Optional[str] = None,
        keywords: Optional[List[str]] = None,
        api_keys: Optional[List[str]] = None,
        idle_check_interval: Optional[int] = None,
    ):
        self.channel = (channel or FAVORITE_CHANNEL).strip()
        self.channel_handle: Optional[str] = self.channel if self.channel.startswith("@") else None
        self.channel_title: Optional[str] = None
        self.keywords = list(keywords) if keywords is not None else list(KEYWORDS)
        self.api_keys = list(api_keys) if api_keys is not None else list(YOUTUBE_API_KEYS)
        self.api_manager = YouTubeApiManager(self.api_keys)
        self.idle_check_interval = idle_check_interval or IDLE_CHECK_INTERVAL_SECONDS

        # Load persisted state if running with default configuration
        if channel is None and keywords is None:
            self._load_state()

        self.keyword_patterns = compile_keyword_patterns(self.keywords)

        self.channel_id: Optional[str] = None
        self.direct_video_id: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.RLock()

        self.state = "STOPPED"  # STOPPED, STARTING, RESOLVING, MONITORING, LIVE_STREAMING, ERROR
        self.current_video_id: Optional[str] = None
        self.total_matches = 0
        self.messages_scanned = 0
        self.start_time: Optional[float] = None
        self.last_check_time: Optional[float] = None
        self.last_alert_time: Optional[float] = None
        self.last_alert_details: Optional[Dict[str, str]] = None
        self.last_error: Optional[str] = None

    def _load_state(self) -> None:
        if _STATE_FILE.exists():
            try:
                with open(_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved_channel = data.get("channel")
                saved_handle = data.get("channel_handle")
                saved_title = data.get("channel_title")
                saved_keywords = data.get("keywords")
                if saved_channel:
                    self.channel = saved_channel
                if saved_handle:
                    self.channel_handle = saved_handle
                if saved_title:
                    self.channel_title = saved_title
                if saved_keywords and isinstance(saved_keywords, list):
                    self.keywords = [k.strip() for k in saved_keywords if k.strip()]
                display = self.channel_handle or self.channel
                log.info(f"Loaded persistent bot state: target='{display}', keywords={self.keywords}")
            except Exception as e:
                log.warning(f"Could not load bot state file: {e}")

    def _save_state(self) -> None:
        try:
            data = {
                "channel": self.channel,
                "channel_handle": self.channel_handle,
                "channel_title": self.channel_title,
                "keywords": self.keywords,
            }
            with open(_STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            log.warning(f"Could not save bot state file: {e}")

    def _resolve_target_info_locked(self) -> None:
        """Resolves channel ID, @handle, and title for self.channel, caching results."""
        if not self.channel:
            return

        # If already resolved or previously attempted for this exact target, skip
        if (self.channel_id and self.channel_handle) or getattr(self, "_resolution_attempted_target", None) == self.channel:
            return

        self._resolution_attempted_target = self.channel
        try:
            cid, handle, title = resolve_channel_details(self.api_manager, self.channel)
            if cid:
                self.channel_id = cid
            if handle:
                self.channel_handle = handle
            if title:
                self.channel_title = title
            self._save_state()
        except Exception as e:
            log.debug(f"Target resolution attempt failed for '{self.channel}': {e}")

    def get_target_display(self) -> str:
        """
        Returns a friendly display string for the target channel.
        Prefers @handle (e.g. '@iamkokkikumar (KOKKI KUMAR YT)'), avoiding raw channel IDs.
        """
        with self._lock:
            if not self.channel:
                return "None configured"

            # If handle not yet resolved and target is a channel ID or URL, resolve once
            if not self.channel_handle and (
                self.channel.startswith("UC")
                or "youtube.com" in self.channel
                or "youtu.be" in self.channel
            ):
                self._resolve_target_info_locked()

            if self.channel_handle:
                handle = self.channel_handle if self.channel_handle.startswith("@") else f"@{self.channel_handle}"
                if self.channel_title and self.channel_title.strip().lower() != handle.lstrip("@").lower():
                    return f"{handle} ({self.channel_title.strip()})"
                return handle

            if self.channel.startswith("@"):
                if self.channel_title:
                    return f"{self.channel} ({self.channel_title.strip()})"
                return self.channel

            if self.channel_title:
                return self.channel_title

            return self.channel

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self) -> Tuple[bool, str]:
        with self._lock:
            if self.is_running:
                return False, f"Bot is already running (state: {self.state})."

            if not self.channel:
                return False, "Target YouTube channel is not set. Use: CHANNEL <url or @handle>"

            self._stop_event.clear()
            self.state = "STARTING"
            self.start_time = time.time()
            self.last_error = None
            self._thread = threading.Thread(target=self._worker_loop, name="yt_alert_worker", daemon=True)
            self._thread.start()
            target_display = self.get_target_display()
            log.info(f"LiveChatAlertBot worker started for target: {target_display}")
            return True, f"🟢 Bot started! Monitoring channel: {target_display}"

    def stop(self) -> Tuple[bool, str]:
        with self._lock:
            if not self.is_running and self.state == "STOPPED":
                return False, "Bot is not currently running."

            log.info("LiveChatAlertBot stop requested...")
            self._stop_event.set()
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=6.0)

        with self._lock:
            self.state = "STOPPED"
            self.current_video_id = None
            log.info("LiveChatAlertBot stopped successfully.")
            return True, "🛑 Bot stopped successfully."

    def update_channel(self, new_channel: str) -> Tuple[bool, str]:
        new_channel = new_channel.strip()
        if not new_channel:
            return False, "Target channel cannot be empty."

        was_running = self.is_running
        if was_running:
            self.stop()

        with self._lock:
            self.channel = new_channel
            self.channel_id = None
            self.channel_handle = new_channel if new_channel.startswith("@") else None
            self.channel_title = None
            self.direct_video_id = None
            self.current_video_id = None
            self.messages_scanned = 0
            self._resolution_attempted_target = None
            if not self.channel_handle:
                self._resolve_target_info_locked()
            display_name = self.get_target_display()
            self._save_state()

        if was_running:
            self.start()

        return True, f"✅ Target channel updated to: {display_name}"

    def update_keywords(self, new_keywords: List[str]) -> Tuple[bool, str]:
        cleaned = [k.strip() for k in new_keywords if k.strip()]
        if not cleaned:
            return False, "Keywords list cannot be empty."

        with self._lock:
            self.keywords = cleaned
            self.keyword_patterns = compile_keyword_patterns(self.keywords)
            reset_anti_spam_cache()
            self._save_state()

        log.info(f"Keywords dynamically updated: {self.keywords}")
        return True, f"✅ Keywords updated ({len(cleaned)} keywords): {', '.join(cleaned)}"

    def _on_match(self, author: str, keyword: str, text: str, video_id: str) -> None:
        with self._lock:
            self.total_matches += 1
            self.last_alert_time = time.time()
            self.last_alert_details = {
                "author": author,
                "keyword": keyword,
                "text": text,
                "video_id": video_id,
            }

    def _on_message(self, author: str, text: str) -> None:
        with self._lock:
            self.messages_scanned += 1

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            uptime_seconds = int(time.time() - self.start_time) if (self.is_running and self.start_time) else 0
            uptime_str = (
                f"{uptime_seconds // 3600}h {(uptime_seconds % 3600) // 60}m {uptime_seconds % 60}s"
                if uptime_seconds
                else "Offline"
            )

            return {
                "state": self.state,
                "is_running": self.is_running,
                "target_channel": self.channel,
                "target_display": self.get_target_display(),
                "channel_handle": self.channel_handle,
                "channel_title": self.channel_title,
                "resolved_channel_id": self.channel_id,
                "direct_video_id": self.direct_video_id,
                "current_video_id": self.current_video_id,
                "is_stream_live": bool(self.current_video_id),
                "stream_url": f"https://youtu.be/{self.current_video_id}" if self.current_video_id else None,
                "keywords": list(self.keywords),
                "total_matches": self.total_matches,
                "messages_scanned": self.messages_scanned,
                "uptime": uptime_str,
                "last_check_time": self.last_check_time,
                "last_alert_time": self.last_alert_time,
                "last_alert_details": self.last_alert_details,
                "last_error": self.last_error,
            }

    def get_status_text(self) -> str:
        s = self.get_status()
        state_emoji = "🟢" if s["is_running"] else "🛑"
        live_emoji = "🔴 LIVE NOW" if s["is_stream_live"] else "⚪ Offline"

        lines = [
            f"{state_emoji} *YouTube Alert Bot: {s['state']}*",
            f"• *Target:* {s['target_display'] or s['target_channel'] or 'None'}",
            f"• *Stream Status:* {live_emoji}",
        ]
        if s["is_stream_live"]:
            lines.append(f"• *Live Stream:* {s['stream_url']}")
            lines.append(f"• *Chat Scanned:* {s['messages_scanned']} messages ({s['total_matches']} alerts)")
        else:
            lines.append(f"• *Total Matches:* {s['total_matches']}")

        lines.extend([
            f"• *Keywords ({len(s['keywords'])}):* {', '.join(s['keywords'][:6])}{'...' if len(s['keywords']) > 6 else ''}",
            f"• *Uptime:* {s['uptime']}",
        ])
        if s.get("last_alert_details"):
            last = s["last_alert_details"]
            author_display = format_author(last.get("author", ""))
            lines.append(f"• *Last Match:* `{last['keyword']}` by {author_display}")
        if s.get("last_error"):
            lines.append(f"⚠️ *Last Error:* {s['last_error']}")
        return "\n".join(lines)

    def _worker_loop(self) -> None:
        log.info(f"Bot worker started for target: {self.get_target_display()}")
        last_notified_video_id = None

        while not self._stop_event.is_set():
            # Step 1: Ensure channel ID or direct video ID is resolved
            if not self.channel_id and not self.direct_video_id:
                try:
                    self.state = "RESOLVING"
                    vid = extract_video_id(self.channel)
                    if vid:
                        self.direct_video_id = vid
                        log.info(f"Target identified as direct video ID: {vid}")
                        self._resolve_target_info_locked()
                    else:
                        self._resolve_target_info_locked()
                        if not self.channel_id:
                            self.channel_id = resolve_channel_id(self.api_manager, self.channel)
                        log.info(f"Resolved target '{self.get_target_display()}' -> Channel ID: {self.channel_id}")
                except Exception as e:
                    self.last_error = f"Failed to resolve target '{self.channel}': {e}"
                    log.error(self.last_error)
                    self.state = "ERROR"
                    if self._stop_event.wait(self.idle_check_interval):
                        break
                    continue

            self.state = "MONITORING"
            self.last_check_time = time.time()

            try:
                candidate_id = None
                if self.direct_video_id:
                    if is_video_actually_live(self.direct_video_id, self.api_manager):
                        candidate_id = self.direct_video_id
                elif self.channel_id:
                    candidate_id = find_candidate_video_id(self.channel_id, self.api_manager)

                if candidate_id:
                    is_live = True
                    live_chat_id = None

                    if self.api_manager.has_keys:
                        try:
                            status, live_chat_id = get_stream_status_api(self.api_manager, candidate_id)
                            if status == "upcoming":
                                is_live = False
                            elif status == "none":
                                is_live = False
                        except QuotaExceededError:
                            is_live = True

                    if is_live:
                        self.state = "LIVE_STREAMING"
                        self.current_video_id = candidate_id
                        if last_notified_video_id != candidate_id:
                            dispatch_live_alerts(candidate_id)
                            last_notified_video_id = candidate_id

                        log.info(f"Stream is LIVE ({candidate_id}). Monitoring chat in real time...")
                        watch_chat_hybrid(
                            self.api_manager,
                            candidate_id,
                            live_chat_id,
                            stop_event=self._stop_event,
                            patterns=self.keyword_patterns,
                            on_match=self._on_match,
                            on_message=self._on_message,
                            get_patterns=lambda: self.keyword_patterns,
                        )
                        self.current_video_id = None
                        self.state = "MONITORING"
                        log.info("Stream chat session ended. Resuming channel checks...")
                else:
                    self.current_video_id = None
                    self.state = "MONITORING"
                    last_notified_video_id = None

            except QuotaExceededError:
                self.last_error = f"All YouTube API quotas exceeded. Backing off {QUOTA_BACKOFF_SECONDS}s."
                log.error(self.last_error)
                if self._stop_event.wait(QUOTA_BACKOFF_SECONDS):
                    break
                continue

            except Exception as e:
                self.last_error = f"Worker loop error: {e}"
                log.error(self.last_error, exc_info=True)
                if self._stop_event.wait(FALLBACK_POLL_SECONDS):
                    break
                continue

            if self._stop_event.wait(self.idle_check_interval):
                break

        self.state = "STOPPED"
        log.info("LiveChatAlertBot worker loop exited.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN RUN LOOP (CLI FOREGROUND ENTRYPOINT)
# ─────────────────────────────────────────────────────────────────────────────

def run():
    """CLI Entrypoint for running the bot directly in foreground."""
    if not FAVORITE_CHANNEL:
        log.error("YT_CHANNEL environment variable must be set.")
        sys.exit(1)

    log.info("=" * 65)
    log.info(" YouTube Live Chat Keyword Alert Bot (Hybrid Edition)")
    log.info("=" * 65)
    log.info(f"Target Channel   : {FAVORITE_CHANNEL}")
    log.info(f"Keywords Watched : {KEYWORDS}")
    log.info(f"User Spam Filter : Ignores duplicates from same user for {USER_SPAM_COOLDOWN_SECONDS}s")
    log.info(f"Alert Cooldown   : {ALERT_COOLDOWN_SECONDS}s per keyword")
    log.info(f"Primary Engine   : Native Innertube (0 Quota)")
    log.info(f"API Fallback     : {f'{len(YOUTUBE_API_KEYS)} API key(s) configured' if YOUTUBE_API_KEYS else 'None (0-Quota Only)'}")
    log.info(f"Telegram Alerts  : {'Configured' if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID else 'Disabled'}")
    log.info(f"ntfy.sh Alerts   : {f'Configured ({NTFY_TOPIC})' if NTFY_TOPIC else 'Disabled'}")
    log.info(f"WhatsApp Alerts  : {'Configured (Twilio)' if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_WHATSAPP_NUMBER else 'Disabled'}")
    log.info("=" * 65)

    bot = LiveChatAlertBot()
    bot.start()

    try:
        while bot.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("Stopping bot on user request (Ctrl+C)...")
        bot.stop()
        log.info("Bot stopped cleanly.")
        sys.exit(0)


if __name__ == "__main__":
    run()