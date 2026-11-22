import json
from urllib.parse import urlencode

import pytest

from app.webapp_auth import InitDataError, sign, validate

TOKEN = "123456:test-token"
NOW = 1_800_000_000


def make_init_data(token: str = TOKEN, auth_date: int = NOW, user: dict | None = None, **extra) -> str:
    fields = {"auth_date": str(auth_date), "query_id": "AAH", **extra}
    fields["user"] = json.dumps(user if user is not None else {"id": 42, "first_name": "Ann"})
    fields["hash"] = sign(fields, token)
    return urlencode(fields)


def test_valid_init_data_returns_user():
    user = validate(make_init_data(), TOKEN, max_age=3600, now=NOW + 10)
    assert user["id"] == 42


def test_signature_field_is_part_of_the_checked_data():
    data = make_init_data(signature="abc")
    assert validate(data, TOKEN, max_age=3600, now=NOW)["id"] == 42
    with pytest.raises(InitDataError):
        validate(data.replace("signature=abc", "signature=xyz"), TOKEN, max_age=3600, now=NOW)


@pytest.mark.parametrize("data", [
    make_init_data(token="999:other-bot"),                              # signed for another bot
    make_init_data().replace("Ann", "Bob"),                             # tampered user
    make_init_data().rsplit("&hash=", 1)[0],                            # hash missing
    "not a query string",
    "",
])
def test_invalid_init_data_is_rejected(data):
    with pytest.raises(InitDataError):
        validate(data, TOKEN, max_age=3600, now=NOW)


def test_expired_init_data_is_rejected():
    with pytest.raises(InitDataError, match="expired"):
        validate(make_init_data(), TOKEN, max_age=3600, now=NOW + 3601)


def test_user_without_numeric_id_is_rejected():
    with pytest.raises(InitDataError):
        validate(make_init_data(user={"id": "42"}), TOKEN, max_age=3600, now=NOW)
