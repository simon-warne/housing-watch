"""M4 ntfy sending: JSON publish, priorities, topic never in errors (plan M4)."""

import json
import urllib.error

import pytest

from housing_watch.alerts import Alert
from housing_watch.ntfy import NTFY_URL, NtfyError, make_send

TOPIC = "made-up-topic-123"


def alert(priority="high", url="https://minasidor.sssb.se/x"):
    return Alert(
        kind="winnable",
        listing_id="1-2-3",
        state_key="k",
        priority=priority,
        title="Winnable: Strix, Rum i korridor, 3,841 kr",
        body="Kungshamra · Körsbärsvägen",
        url=url,
    )


@pytest.mark.parametrize("prio,num", [("urgent", 5), ("high", 4), ("low", 2)])
def test_payload_per_priority(prio, num):
    calls = []
    send = make_send(TOPIC, post=lambda url, data, headers: calls.append((url, data, headers)))
    send(alert(prio))
    url, data, headers = calls[0]
    assert url == NTFY_URL == "https://ntfy.sh/"
    body = json.loads(data.decode("utf-8"))
    assert body["topic"] == TOPIC
    assert body["priority"] == num
    assert body["title"].startswith("Winnable")
    assert "Körsbärsvägen" in body["message"]  # UTF-8 survives (no header encoding)
    assert body["click"] == "https://minasidor.sssb.se/x"
    assert headers["Content-Type"] == "application/json"


def test_no_click_without_url():
    calls = []
    make_send(TOPIC, post=lambda u, d, h: calls.append(d))(alert(url=None))
    assert "click" not in json.loads(calls[0])


def test_unknown_priority_raises():
    with pytest.raises(ValueError):
        make_send(TOPIC, post=lambda *a: None)(alert("medium"))


def test_empty_topic_rejected():
    with pytest.raises(ValueError):
        make_send("  ", post=lambda *a: None)


@pytest.mark.parametrize(
    "exc",
    [
        urllib.error.HTTPError(f"https://ntfy.sh/{TOPIC}", 429, f"slow {TOPIC}", {}, None),
        urllib.error.URLError(f"no route to ntfy.sh/{TOPIC}"),
        OSError(f"boom {TOPIC}"),
    ],
)
def test_errors_never_carry_the_topic(exc):
    def post(*a):
        raise exc

    with pytest.raises(NtfyError) as e:
        make_send(TOPIC, post=post)(alert())
    assert TOPIC not in str(e.value)
    assert e.value.__cause__ is None and e.value.__suppress_context__
