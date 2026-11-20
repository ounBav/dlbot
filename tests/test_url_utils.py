import pytest

from app.url_utils import detect_platform, extract_url, url_hash


def test_extract_url_from_surrounding_text():
    assert extract_url("look https://youtu.be/abc, great") == "https://youtu.be/abc"


def test_extract_url_none_when_absent():
    assert extract_url("hello there") is None


@pytest.mark.parametrize("url,expected", [
    ("https://www.youtube.com/watch?v=abc", "youtube"),
    ("https://youtu.be/abc", "youtube"),
    ("https://www.tiktok.com/@user/video/123", "tiktok"),
    ("https://vm.tiktok.com/ZMabc/", "tiktok"),
    ("https://fb.watch/xyz/", "facebook"),
    ("https://www.facebook.com/user/videos/1", "facebook"),
    ("https://example.com/video", None),
    ("https://youtube.com.evil.example/x", None),   # look-alike host
    ("http://127.0.0.1/admin", None),               # SSRF attempt
])
def test_detect_platform(url, expected):
    assert detect_platform(url) == expected


def test_url_hash_is_stable_and_short():
    assert url_hash("https://youtu.be/abc") == url_hash("https://youtu.be/abc")
    assert len(url_hash("https://youtu.be/abc")) == 32
