"""Redirect handler for the link URL shortener.

Invoked via a Lambda Function URL that sits behind CloudFront. For each request it
resolves the slug to a target URL, records a click (every click, no caching), and
returns a 302. Uses only the standard library + boto3 (present in the Lambda runtime),
so the deployment bundle needs no pip install.
"""

import datetime
import os
import uuid

import boto3

_ddb = boto3.resource("dynamodb")
_table = _ddb.Table(os.environ["TABLE_NAME"])
ROOT_REDIRECT = os.environ.get("ROOT_REDIRECT", "")


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

    if not slug:
        return _redirect(ROOT_REDIRECT) if ROOT_REDIRECT else _not_found()

    item = _table.get_item(Key={"PK": slug, "SK": "META"}).get("Item")
    if not item or not item.get("target_url"):
        return _not_found()

    _record_click(slug, headers, event)
    return _redirect(item["target_url"])


def _record_click(slug, headers, event):
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

    try:
        _table.put_item(Item=click)
        _table.update_item(
            Key={"PK": slug, "SK": "META"},
            UpdateExpression="SET click_count = if_not_exists(click_count, :z) + :one",
            ExpressionAttributeValues={":one": 1, ":z": 0},
        )
    except Exception as exc:  # never fail the redirect because logging failed
        print(f"click record error for slug={slug}: {exc}")
