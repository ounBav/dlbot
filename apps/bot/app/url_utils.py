import hashlib
import re
from urllib.parse import urlparse

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

PLATFORM_HOSTS: dict[str, set[str]] = {
    "youtube": {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"},
    "tiktok": {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com"},
    "facebook": {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "fb.watch"},
}

PLATFORM_LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "facebook": "Facebook"}


def extract_url(text: str) -> str | None:
    match = URL_RE.search(text or "")
    return match.group(0).rstrip(".,;:!?)") if match else None


def detect_platform(url: str) -> str | None:
    """Return the platform key for an allow-listed host, otherwise None (SSRF guard, NFR-06)."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for platform, hosts in PLATFORM_HOSTS.items():
        if host in hosts:
            return platform
    return None


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:32]
