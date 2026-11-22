from app.downloader import classify_error


def test_classify_too_large():
    assert classify_error("File is larger than max-filesize") == "E-TOOBIG"


def test_classify_unsupported():
    assert classify_error("Unsupported URL: https://x") == "E-UNSUPPORTED"


def test_classify_private():
    assert classify_error("Private video. Sign in if you've been granted access") == "E-PRIVATE"


def test_classify_unknown_defaults_to_failed():
    assert classify_error("something odd happened") == "E-FAILED"
