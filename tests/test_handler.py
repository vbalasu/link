"""Unit tests for the redirect handler's query-parameter logic (no AWS needed)."""

import importlib.util
import os
import sys
import types
from pathlib import Path
from unittest import mock

import pytest

HANDLER = Path(__file__).resolve().parent.parent / "lambda" / "handler.py"


@pytest.fixture(scope="module")
def h():
    fake_boto3 = types.SimpleNamespace(resource=lambda *_a, **_k: mock.MagicMock())
    with mock.patch.dict(sys.modules, {"boto3": fake_boto3}), \
         mock.patch.dict(os.environ, {"TABLE_NAME": "t", "ROOT_REDIRECT": "https://root.example"}):
        spec = importlib.util.spec_from_file_location("handler", HANDLER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# _merge_query
# --------------------------------------------------------------------------- #
def test_no_query_returns_target_unchanged(h):
    assert h._merge_query("https://e.org/p?a=1#f", "") == "https://e.org/p?a=1#f"


def test_appends_to_target_without_query(h):
    assert h._merge_query("https://e.org/p", "utm_source=youtube") == \
        "https://e.org/p?utm_source=youtube"


def test_appends_to_existing_query(h):
    assert h._merge_query("https://e.org/p?a=1", "utm_source=yt") == \
        "https://e.org/p?a=1&utm_source=yt"


def test_target_wins_on_collision(h):
    out = h._merge_query(
        "https://e.org/p?utm_source=newsletter&ref=vijay",
        "utm_source=youtube&ref=attacker&gclid=abc",
    )
    assert out == "https://e.org/p?utm_source=newsletter&ref=vijay&gclid=abc"


def test_all_colliding_returns_target(h):
    assert h._merge_query("https://e.org/p?a=1", "a=2") == "https://e.org/p?a=1"


def test_fragment_preserved_after_query(h):
    assert h._merge_query("https://e.org/p#faq", "utm_source=x") == \
        "https://e.org/p?utm_source=x#faq"


def test_incoming_encoding_passed_verbatim(h):
    assert h._merge_query("https://e.org/", "q=a%20b&t=c+d") == \
        "https://e.org/?q=a%20b&t=c+d"


def test_collision_detected_through_encoding(h):
    assert h._merge_query("https://e.org/?utm%5Fsource=x", "utm_source=y") == \
        "https://e.org/?utm%5Fsource=x"


def test_repeated_incoming_keys_kept_and_empty_pieces_dropped(h):
    assert h._merge_query("https://e.org/", "tag=a&&tag=b&flag") == \
        "https://e.org/?tag=a&tag=b&flag"


# --------------------------------------------------------------------------- #
# _parse_params
# --------------------------------------------------------------------------- #
def test_parse_empty(h):
    assert h._parse_params("") == ({}, False)


def test_parse_first_value_wins_and_decodes(h):
    assert h._parse_params("tag=a&tag=b&q=a%20b&blank=") == (
        {"tag": "a", "q": "a b", "blank": ""}, False)


def test_parse_skips_empty_keys(h):
    assert h._parse_params("=x&a=1") == ({"a": "1"}, False)


def test_parse_caps_key_count(h):
    params, truncated = h._parse_params("&".join(f"k{i}=v" for i in range(60)))
    assert len(params) == h.MAX_PARAMS and truncated


def test_parse_caps_key_and_value_length(h):
    params, truncated = h._parse_params(f"{'k' * 200}={'v' * 2000}")
    assert truncated
    [(k, v)] = params.items()
    assert len(k) == h.MAX_KEY_LEN and len(v) == h.MAX_VALUE_LEN


# --------------------------------------------------------------------------- #
# handler
# --------------------------------------------------------------------------- #
def _event(path, qs=""):
    return {"rawPath": path, "rawQueryString": qs, "headers": {"user-agent": "ua"}}


def test_handler_redirects_with_params_and_records(h):
    h._table.get_item.return_value = {"Item": {"target_url": "https://e.org/p?ref=v"}}
    h._table.put_item.reset_mock()
    resp = h.handler(_event("/promo", "utm_source=youtube&ref=x"), None)
    assert resp["statusCode"] == 302
    assert resp["headers"]["Location"] == "https://e.org/p?ref=v&utm_source=youtube"
    item = h._table.put_item.call_args.kwargs["Item"]
    assert item["query"] == "utm_source=youtube&ref=x"
    assert item["params"] == {"utm_source": "youtube", "ref": "x"}
    assert "params_truncated" not in item


def test_handler_without_params_is_backwards_compatible(h):
    h._table.get_item.return_value = {"Item": {"target_url": "https://e.org/p"}}
    h._table.put_item.reset_mock()
    resp = h.handler({"rawPath": "/promo", "headers": {}}, None)
    assert resp["headers"]["Location"] == "https://e.org/p"
    item = h._table.put_item.call_args.kwargs["Item"]
    assert not {"query", "params", "params_truncated"} & item.keys()


def test_root_passes_params_through_without_recording(h):
    h._table.put_item.reset_mock()
    resp = h.handler(_event("/", "utm_source=bio"), None)
    assert resp["headers"]["Location"] == "https://root.example?utm_source=bio"
    h._table.put_item.assert_not_called()


def test_unknown_slug_404s(h):
    h._table.get_item.return_value = {}
    assert h.handler(_event("/nope", "utm_source=x"), None)["statusCode"] == 404
