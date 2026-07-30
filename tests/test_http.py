from unittest.mock import MagicMock

import pytest

from ffl_bigquery.http import SourceUnavailable, ThrottledSession


class _Resp:
    def __init__(self, payload=None, status=200, exc=None):
        self._payload, self.status_code, self._exc = payload, status, exc

    def json(self):
        if self._exc:
            raise self._exc
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_sends_user_agent():
    sess = MagicMock()
    sess.get.return_value = _Resp({"ok": True})
    ThrottledSession(user_agent="ffl-bigquery/0.1.0", session=sess,
                     sleep=lambda _: None).get_json("https://x/y", {"a": 1})
    assert sess.get.call_args.kwargs["headers"]["User-Agent"] == "ffl-bigquery/0.1.0"


def test_sleeps_min_interval_between_calls():
    slept: list[float] = []
    sess = MagicMock()
    sess.get.return_value = _Resp({"ok": True})
    t = ThrottledSession(user_agent="ua", min_interval=1.5, jitter=0.0,
                         session=sess, sleep=slept.append)
    t.get_json("https://x/y", {})
    t.get_json("https://x/y", {})
    assert slept and slept[-1] == pytest.approx(1.5)


def test_retries_then_succeeds():
    sess = MagicMock()
    sess.get.side_effect = [RuntimeError("boom"), _Resp({"ok": True})]
    out = ThrottledSession(user_agent="ua", jitter=0.0, session=sess,
                           sleep=lambda _: None).get_json("https://x/y", {})
    assert out == {"ok": True}
    assert sess.get.call_count == 2


def test_raises_source_unavailable_after_max_retries():
    sess = MagicMock()
    sess.get.side_effect = RuntimeError("boom")
    with pytest.raises(SourceUnavailable, match="after 3 attempts"):
        ThrottledSession(user_agent="ua", max_retries=3, jitter=0.0, session=sess,
                         sleep=lambda _: None).get_json("https://x/y", {})


def test_tls_interception_error_names_the_remedy():
    sess = MagicMock()
    sess.get.side_effect = RuntimeError(
        "certificate verify failed: unable to get local issuer certificate"
    )
    with pytest.raises(SourceUnavailable, match="truststore"):
        ThrottledSession(user_agent="ua", max_retries=1, jitter=0.0, session=sess,
                         sleep=lambda _: None).get_json("https://x/y", {})
