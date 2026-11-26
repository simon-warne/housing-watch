"""M4 fetching: URLs, host checks and the user-agent (plan M4). No network."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from housing_watch.sssb import (
    USER_AGENT,
    detail_url,
    http_get,
    list_url,
    load_jsonp,
    parse_list,
)

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 11, 26, 17, 17, tzinfo=UTC)


def first_listing():
    payload = load_jsonp((FIX / "2026-11-26-list.jsonp").read_text(encoding="utf-8"))
    return parse_list(payload, T0)[0]


def test_list_url_asks_for_listings_and_total():
    url = list_url()
    assert url.startswith("https://minasidor.sssb.se/widgets/?")
    assert "callback=cb" in url
    assert "objektlistabilder%40lagenheter" in url
    assert "objektsummering%40lagenheter" in url


def test_detail_url_uses_refid_over_https():
    x = first_listing()
    assert x.detail_url.startswith("http://")  # SSSB gives http
    refid = x.detail_url.split("refid=")[1]
    url = detail_url(x)
    assert url.startswith("https://minasidor.sssb.se/widgets/?")
    assert f"refid={refid}" in url
    for w in ("objektinformation%40lagenheter", "objektintresse", "objektintressestatus"):
        assert w in url


@pytest.mark.parametrize(
    "bad",
    [
        "http://evil.example/lediga-bostader/lagenhet/?refid=abc",
        "http://minasidor.sssb.se/lediga-bostader/lagenhet/",
        "http://minasidor.sssb.se/lediga-bostader/lagenhet/?refid=",
        "http://minasidor.sssb.se/lediga-bostader/lagenhet/?refid=a&refid=b",
    ],
)
def test_detail_url_rejects_other_hosts_and_missing_refid(bad):
    with pytest.raises(ValueError):
        detail_url(replace(first_listing(), detail_url=bad))


class FakeResponse:
    def __init__(self, body: bytes, charset="utf-8"):
        self.body, self.charset = body, charset
        self.headers = self

    def get_content_charset(self):
        return self.charset

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_get_is_a_plain_get_with_our_user_agent():
    seen = {}

    def opener(req, timeout):
        seen.update(method=req.get_method(), ua=req.get_header("User-agent"), timeout=timeout)
        seen["url"] = req.full_url
        return FakeResponse(b"cb({});")

    assert http_get(list_url(), opener=opener) == "cb({});"
    assert seen["method"] == "GET"
    assert seen["ua"] == USER_AGENT and "housing-watch" in USER_AGENT
    assert seen["timeout"] == 30


@pytest.mark.parametrize(
    "url", ["https://ntfy.sh/x", "http://minasidor.sssb.se/widgets/", "https://sssb.se/"]
)
def test_http_get_only_talks_to_sssb_widgets(url):
    with pytest.raises(ValueError):
        http_get(url, opener=lambda *a, **k: pytest.fail("must not fetch"))
