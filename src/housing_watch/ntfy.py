"""ntfy sending (M4): JSON publish to ntfy.sh, so UTF-8 titles need no header encoding.

The topic is the only secret: it is never put in an error message (ntfy's URL and reply
both carry it), and the reply body is ignored.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable

from housing_watch.alerts import Alert

NTFY_URL = "https://ntfy.sh/"
PRIORITY = {"urgent": 5, "high": 4, "low": 2}  # 5 rings through Do Not Disturb
TIMEOUT_S = 20

Post = Callable[[str, bytes, dict[str, str]], object]


class NtfyError(Exception):
    """A send failed. Carries no topic, URL or reply text."""


def http_post(url: str, data: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        resp.read()


def make_send(topic: str, post: Post = http_post) -> Callable[[Alert], None]:
    topic = (topic or "").strip()
    if not topic:
        raise ValueError("NTFY_TOPIC is empty")

    def send(a: Alert) -> None:
        if a.priority not in PRIORITY:
            raise ValueError(f"unknown priority {a.priority!r}")
        msg = {
            "topic": topic,
            "title": a.title,
            "message": a.body,
            "priority": PRIORITY[a.priority],
            "tags": ["house"],
        }
        if a.url:
            msg["click"] = a.url
        data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
        try:
            post(NTFY_URL, data, {"Content-Type": "application/json"})
        except urllib.error.HTTPError as e:
            raise NtfyError(f"ntfy answered HTTP {e.code}") from None
        except Exception as e:  # noqa: BLE001 - any failure, described without its text
            raise NtfyError(f"ntfy send failed: {type(e).__name__}") from None

    return send
