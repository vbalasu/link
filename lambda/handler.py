"""Redirect handler for the link URL shortener.

Invoked via a Lambda Function URL that sits behind CloudFront. For each request it
resolves the slug to a target URL, records a click (every click, no caching), and
returns a 302. Optional query parameters on the short link are recorded with the click
and passed through to the target (the target's own keys win on collision). Uses only the standard library + boto3 (present in the Lambda runtime),
so the deployment bundle needs no pip install.
"""

import datetime
import os
import uuid
from urllib.parse import parse_qsl, unquote_plus, urlsplit, urlunsplit

import boto3

_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(os.environ["TABLE_NAME"])
ROOT_REDIRECT = os.environ.get("ROOT_REDIRECT", "")

# Caps on what a click records from the query string (passthrough is not capped).
MAX_PARAMS = 50
MAX_KEY_LEN = 128
MAX_VALUE_LEN = 1024


def _merge_query(target_url, raw_qs):
    """Append incoming query pairs to target_url, skipping keys the target already has.

    Incoming pairs are appended verbatim (no re-encoding); the target's query and
    #fragment are preserved. With no incoming query, target_url is returned unchanged.
    """
    if not raw_qs:
        return target_url
    parts = urlsplit(target_url)
    existing = {k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}
    extra = [
        piece for piece in raw_qs.split("&")
        if piece and unquote_plus(piece.split("=", 1)[0]) not in existing
    ]
    if not extra:
        return target_url
    query = "&".join(([parts.query] if parts.query else []) + extra)
    return urlunsplit(parts._replace(query=query))


def _parse_params(raw_qs):
    """Return (params, truncated): first value per key, capped for storage."""
    params, truncated = {}, False
    for key, value in parse_qsl(raw_qs or "", keep_blank_values=True):
        if not key:
            continue  # DynamoDB map keys must be non-empty
        if len(key) > MAX_KEY_LEN:
            key, truncated = key[:MAX_KEY_LEN], True
        if key in params:
            continue
        if len(params) >= MAX_PARAMS:
            truncated = True
            break
        if len(value) > MAX_VALUE_LEN:
            value, truncated = value[:MAX_VALUE_LEN], True
        params[key] = value
    return params, truncated


def _redirect(location, code=302):
    return {
        "statusCode": code,
        "headers": {"Location": location, "Cache-Control": "no-store"},
        "body": "",
    }


def _not_found():
    return {
        "statusCode": 404,
        "headers": {
            "Content-Type": "text/html; charset=utf-8",
            "Cache-Control": "no-store",
        },
        "body": "<!doctype html><title>404 Not Found</title>"
        "<h1>404 Not Found</h1><p>No such link.</p>",
    }


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    slug = (event.get("rawPath", "/") or "/").strip("/")
    raw_qs = event.get("rawQueryString", "") or ""

    if not slug:
        if not ROOT_REDIRECT:
            return _not_found()
        return _redirect(_merge_query(ROOT_REDIRECT, raw_qs))

    item = _table.get_item(Key={"PK": slug, "SK": "META"}).get("Item")
    if not item or not item.get("target_url"):
        return _not_found()

    _record_click(slug, headers, event, raw_qs)
    return _redirect(_merge_query(item["target_url"], raw_qs))


def _record_click(slug, headers, event, raw_qs):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    # CloudFront-Viewer-Address is "IP:PORT" (or "[ipv6]:PORT"); fall back to sourceIp.
    addr = headers.get("cloudfront-viewer-address", "")
    if addr:
        ip = addr.rsplit(":", 1)[0].strip("[]")
    else:
        ip = (
            event.get("requestContext", {})
            .get("http", {})
            .get("sourceIp", "")
        )

    click = {
        "PK": slug,
        "SK": f"{now}#{uuid.uuid4().hex[:8]}",
        "ts": now,
        "ip": ip,
        "country": headers.get("cloudfront-viewer-country", ""),
        "region": headers.get("cloudfront-viewer-country-region", ""),
        "city": headers.get("cloudfront-viewer-city", ""),
        "user_agent": headers.get("user-agent", ""),
        "referer": headers.get("referer", ""),
        "accept_language": headers.get("accept-language", ""),
    }
    # Drop empty strings to keep items tidy.
    click = {k: v for k, v in click.items() if v != ""}

    if raw_qs:
        click["query"] = raw_qs
        params, truncated = _parse_params(raw_qs)
        if params:
            click["params"] = params
        if truncated:
            click["params_truncated"] = True

    try:
        _table.put_item(Item=click)
        _table.update_item(
            Key={"PK": slug, "SK": "META"},
            UpdateExpression="SET click_count = if_not_exists(click_count, :z) + :one",
            ExpressionAttributeValues={":one": 1, ":z": 0},
        )
    except Exception as exc:  # never fail the redirect because logging failed
        print(f"click record error for slug={slug}: {exc}")
