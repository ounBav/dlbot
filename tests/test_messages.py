from app import messages


def test_caption_escapes_html_and_formats_duration():
    text = messages.caption("<script>x</script>", "YouTube", 125)
    assert "&lt;script&gt;" in text
    assert "2:05" in text


def test_rate_limited_message_switches_to_daily_wording():
    assert "tomorrow" in messages.rate_limited(7200)
    assert "seconds" in messages.rate_limited(30)


def test_error_message_has_reference():
    assert "Ref: abc123" in messages.error("E-FAILED", "abc123", 50)
