"""linkctl — create short links and analyze click history on your own domain."""

import csv
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

import typer
from botocore.exceptions import ClientError

from .aws import account_id, session, stack_outputs, table
from .config import load_config

app = typer.Typer(
    add_completion=False,
    help="Serverless URL shortener on your own domain.",
    no_args_is_help=True,
)

# Which config file commands operate on (one deployment per domain).
_state = {"config": "config.yaml"}


@app.callback()
def _main(
    config: str = typer.Option(
        "config.yaml", "--config", "-c",
        help="Config file for the target domain (default: config.yaml).",
    ),
):
    _state["config"] = config

SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
RESERVED = {"health", "_health", "favicon.ico", "robots.txt"}


def _err(msg: str):
    typer.secho(f"error: {msg}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _validate_slug(slug: str):
    if slug in RESERVED:
        _err(f"'{slug}' is a reserved slug")
    if not SLUG_RE.match(slug):
        _err("slug must be 1-128 chars of [A-Za-z0-9_-]")


def _validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _err("target URL must be an absolute http(s):// URL")


def _repo_root() -> Path:
    """Directory holding cdk.json (expected to be the current project dir)."""
    if Path("cdk.json").exists():
        return Path.cwd()
    _err("cdk.json not found — run `linkctl deploy` from the project directory")


# --------------------------------------------------------------------------- #
# Infra
# --------------------------------------------------------------------------- #
@app.command()
def deploy():
    """Provision/update the AWS stack for this domain (reads config.yaml)."""
    cfg = load_config(_state["config"])
    root = _repo_root()
    acct = account_id(cfg)
    region = cfg["aws_region"]

    env = os.environ.copy()
    if cfg.get("aws_profile"):
        env["AWS_PROFILE"] = cfg["aws_profile"]

    ctx = [
        "-c", f"domain={cfg['domain']}",
        "-c", f"subdomain={cfg['subdomain']}",
        "-c", f"root_redirect={cfg['root_redirect']}",
        "-c", f"account={acct}",
        "-c", f"region={region}",
    ]

    typer.echo(f"→ bootstrapping aws://{acct}/{region} (idempotent)")
    subprocess.run(
        ["npx", "cdk", "bootstrap", f"aws://{acct}/{region}", *ctx],
        cwd=root, env=env, check=True,
    )
    typer.echo(f"→ deploying stack {cfg['stack_name']}")
    subprocess.run(
        ["npx", "cdk", "deploy", "--require-approval", "never", *ctx],
        cwd=root, env=env, check=True,
    )
    out = stack_outputs(cfg)
    typer.secho(f"\n✓ deployed. Links live at https://{out.get('LinkHost')}/<slug>",
                fg=typer.colors.GREEN)


@app.command()
def destroy():
    """Tear down the stack (DynamoDB table is retained)."""
    cfg = load_config(_state["config"])
    root = _repo_root()
    acct = account_id(cfg)
    env = os.environ.copy()
    if cfg.get("aws_profile"):
        env["AWS_PROFILE"] = cfg["aws_profile"]
    ctx = [
        "-c", f"domain={cfg['domain']}",
        "-c", f"subdomain={cfg['subdomain']}",
        "-c", f"root_redirect={cfg['root_redirect']}",
        "-c", f"account={acct}",
        "-c", f"region={cfg['aws_region']}",
    ]
    subprocess.run(["npx", "cdk", "destroy", "--force", *ctx], cwd=root, env=env, check=True)


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
@app.command()
def create(slug: str, target_url: str):
    """Create a short link. Fails if the slug already exists (immutable)."""
    _validate_slug(slug)
    _validate_url(target_url)
    cfg = load_config(_state["config"])
    import datetime

    try:
        table(cfg).put_item(
            Item={
                "PK": slug,
                "SK": "META",
                "target_url": target_url,
                "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "click_count": 0,
            },
            ConditionExpression="attribute_not_exists(PK)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            _err(f"slug '{slug}' already exists (slugs are immutable)")
        raise
    typer.secho(f"✓ https://{cfg['host']}/{slug}  →  {target_url}", fg=typer.colors.GREEN)


@app.command()
def delete(slug: str, keep_clicks: bool = typer.Option(True, help="Retain click history.")):
    """Delete a link's mapping. Click history is retained by default."""
    cfg = load_config(_state["config"])
    t = table(cfg)
    if not t.get_item(Key={"PK": slug, "SK": "META"}).get("Item"):
        _err(f"slug '{slug}' not found")
    t.delete_item(Key={"PK": slug, "SK": "META"})
    if not keep_clicks:
        _delete_clicks(t, slug)
        typer.echo("  (click history also deleted)")
    typer.secho(f"✓ deleted '{slug}'", fg=typer.colors.GREEN)


def _delete_clicks(t, slug: str):
    from boto3.dynamodb.conditions import Key

    kwargs = {"KeyConditionExpression": Key("PK").eq(slug) & Key("SK").gt("META")}
    while True:
        resp = t.query(**kwargs)
        with t.batch_writer() as batch:
            for it in resp.get("Items", []):
                batch.delete_item(Key={"PK": it["PK"], "SK": it["SK"]})
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


@app.command(name="list")
def list_links():
    """List all links with their targets and click counts."""
    from boto3.dynamodb.conditions import Attr

    cfg = load_config(_state["config"])
    t = table(cfg)
    items, kwargs = [], {"FilterExpression": Attr("SK").eq("META")}
    while True:
        resp = t.scan(**kwargs)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    if not items:
        typer.echo("no links yet")
        return
    items.sort(key=lambda i: i["PK"])
    width = max(len(i["PK"]) for i in items)
    for i in items:
        clicks = int(i.get("click_count", 0))
        typer.echo(f"{i['PK']:<{width}}  {clicks:>7} clicks  →  {i.get('target_url','')}")


@app.command()
def inspect(slug: str):
    """Show one link's metadata."""
    cfg = load_config(_state["config"])
    item = table(cfg).get_item(Key={"PK": slug, "SK": "META"}).get("Item")
    if not item:
        _err(f"slug '{slug}' not found")
    item["click_count"] = int(item.get("click_count", 0))
    typer.echo(json.dumps(item, indent=2, default=str))


# --------------------------------------------------------------------------- #
# Analytics
# --------------------------------------------------------------------------- #
def _query_clicks(t, slug: str, since: str = None, until: str = None):
    from boto3.dynamodb.conditions import Key

    cond = Key("PK").eq(slug)
    lo = since or "0"
    hi = (until + "~") if until else "~"  # '~' sorts after '#'-prefixed click SKs
    cond = cond & Key("SK").between(lo, hi)
    kwargs = {"KeyConditionExpression": cond}
    out = []
    while True:
        resp = t.query(**kwargs)
        out.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return [i for i in out if i["SK"] != "META"]


@app.command()
def stats(
    slug: str,
    from_: str = typer.Option(None, "--from", help="ISO date/time lower bound."),
    to: str = typer.Option(None, "--to", help="ISO date/time upper bound."),
    top: int = typer.Option(5, help="How many top values per dimension."),
):
    """Totals, daily time-series, and top countries/referers/user-agents."""
    cfg = load_config(_state["config"])
    t = table(cfg)
    if not t.get_item(Key={"PK": slug, "SK": "META"}).get("Item"):
        _err(f"slug '{slug}' not found")
    clicks = _query_clicks(t, slug, from_, to)

    typer.secho(f"{slug}: {len(clicks)} clicks", fg=typer.colors.CYAN, bold=True)
    if not clicks:
        return

    by_day = Counter(c["ts"][:10] for c in clicks if c.get("ts"))
    typer.echo("\nby day:")
    for day in sorted(by_day):
        typer.echo(f"  {day}  {by_day[day]}")

    for dim, label in (("country", "countries"), ("referer", "referers"),
                       ("user_agent", "user-agents")):
        counts = Counter(c[dim] for c in clicks if c.get(dim))
        if counts:
            typer.echo(f"\ntop {label}:")
            for val, n in counts.most_common(top):
                typer.echo(f"  {n:>5}  {val}")


@app.command()
def export(
    slug: str,
    format: str = typer.Option("csv", help="csv or json."),
    output: str = typer.Option(None, "--output", "-o", help="File (default: stdout)."),
):
    """Export raw click history for a slug."""
    cfg = load_config(_state["config"])
    clicks = _query_clicks(table(cfg), slug)
    fields = ["ts", "ip", "country", "region", "city", "user_agent",
              "referer", "accept_language", "SK"]

    if format == "json":
        text = json.dumps(clicks, indent=2, default=str)
    elif format == "csv":
        import io

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in sorted(clicks, key=lambda x: x.get("ts", "")):
            w.writerow(c)
        text = buf.getvalue()
    else:
        _err("format must be csv or json")

    if output:
        Path(output).write_text(text)
        typer.secho(f"✓ wrote {len(clicks)} clicks to {output}", fg=typer.colors.GREEN)
    else:
        sys.stdout.write(text)


if __name__ == "__main__":
    app()
