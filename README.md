# link

A serverless URL shortener on AWS. Create short links on your own custom domain
(`https://link.yourdomain.tld/slug`), record every click, and analyze access history —
all from a Python CLI, with **$0 cost when idle**. Works with any domain and any TLD
(`.io`, `.dev`, `.co.uk`, …) — nothing assumes `.com`.

> **Status:** implemented and deployed. Two live instances run today, each its own
> isolated CDK stack (separate CloudFront, Lambda, and DynamoDB table):
>
> | Domain | Stack | Config |
> |--------|-------|--------|
> | **https://link.cloudmatica.com** | `link-cloudmatica-com` | [`config.link.cloudmatica.com.yaml`](./config.link.cloudmatica.com.yaml) |
> | **https://link.vbalasu.com** | `link-vbalasu-com` | [`config.link.vbalasu.com.yaml`](./config.link.vbalasu.com.yaml) |
> | **https://go.vbalasu.com** | `link-go-vbalasu-com` | [`config.go.vbalasu.com.yaml`](./config.go.vbalasu.com.yaml) |
>
> The full design and rationale live in [`SPEC.md`](./SPEC.md).

## What it does

- Map a **user-friendly slug** to a **target URL**: `link.yourdomain.tld/promo` → your long URL.
- Serve the redirect from a custom domain over HTTPS.
- **Record every click** — timestamp, geo, user-agent, referer, and more.
- **Optional URL parameters** on any link (`/promo?utm_source=youtube`) are recorded
  with the click and passed through to the target.
- Provide a CLI (`linkctl`) to create links and analyze their access history.
- Scale to zero: no fixed infrastructure cost when no one is clicking.

## Architecture

```
        DNS: link.{domain}  (Route53 ALIAS)
                   │
                   ▼
           CloudFront distribution      ← ACM cert (us-east-1), TLS
                   │  (no caching of redirects)
                   ▼
        Lambda Function URL (Python)     ← redirect handler, logs each click
                   │
                   ▼
          DynamoDB (single table)        ← links + per-click history
```

- **AWS**, chosen for genuine scale-to-zero (GCP's CDN path carries a ~$18/mo
  load-balancer floor).
- **CloudFront → Lambda Function URL → DynamoDB.** Redirects are **not** edge-cached, so
  every click reaches the Lambda and gets recorded. Returns `302` with `Cache-Control:
  no-store` to keep click counts accurate.
- **One AWS CDK stack per domain** (own table, Lambda, CloudFront, cert).
- **Route53** for the domain enables fully automated, one-command setup.

See [`SPEC.md`](./SPEC.md) for the complete design, data model, and the rationale behind
every decision (including the alternatives that were rejected).

## Setup

Prerequisite: your domain (or a delegated subdomain) is hosted in **Route53**.

The `domain` is used verbatim as the Route53 hosted-zone name — **any domain / any TLD**
works (`example.io`, `acme.dev`, `go.example.co.uk`). Nothing infers a `.com`-shaped
registrable domain, so multi-label TLDs are fine.

```yaml
# config.yaml
domain: example.io                   # link host becomes link.example.io
subdomain: link                      # optional, default "link"
aws_region: us-east-1
root_redirect: https://example.io    # optional fallback for the bare host
```

```bash
linkctl deploy     # reads config.yaml, runs cdk deploy: cert + CloudFront + Lambda + table + DNS
linkctl destroy    # tear the stack down
```

### Multiple domains

Each domain is an independent deployment with its own config file. Point any command at
a specific domain with the global `--config` / `-c` option (it goes **before** the
subcommand); it defaults to `config.yaml`, which is a symlink to
`config.go.vbalasu.com.yaml`.

```bash
linkctl --config config.link.vbalasu.com.yaml deploy     # deploy the link.vbalasu.com stack
linkctl -c config.link.vbalasu.com.yaml create promo https://example.org
linkctl create promo https://example.org                 # uses config.yaml (go.vbalasu.com)
```

The stack name defaults to `link-<domain>`, so a second shortener on the **same domain**
(e.g. `go.vbalasu.com` alongside `link.vbalasu.com`) must set `stack_name` explicitly —
otherwise it would update the existing stack and move it to the new host:

```yaml
# config.go.vbalasu.com.yaml
domain: vbalasu.com
subdomain: go
stack_name: link-go-vbalasu-com
```

## CLI

```bash
# All commands accept a leading `--config/-c <file>` (default: config.yaml).
linkctl create <slug> <target_url>        # create a link (rejects if slug exists)
linkctl delete <slug>                     # remove the mapping
linkctl list                              # list all slugs, targets, counts
linkctl inspect <slug>                    # show one link's metadata
linkctl stats <slug> [--from --to]        # totals, time-series, top geo/referer/UA/UTM
linkctl stats <slug> --param utm_source=youtube   # same, filtered by URL params (repeatable)
linkctl export <slug> [--format csv|json] # raw click history (CSV adds utm_* + query)
```

### URL parameters

Any link accepts optional query parameters — no setup, and links shared without them
behave exactly as before:

```
https://go.vbalasu.com/promo?utm_source=youtube&utm_medium=video
```

- **Passed through** to the target. If the target URL already defines a key, the
  target's value wins (so a shared URL can't override your `ref`/campaign ids); new
  keys are appended, before any `#fragment`.
- **Recorded** on the click as `query` (raw string) and `params` (first value per key;
  ≤50 keys, keys ≤128 chars, values ≤1024 chars, `params_truncated` if capped).
- The bare host passes parameters through to `root_redirect` but doesn't record them.

See SPEC.md §10 for details. Tests: `pytest tests`.

## Installation

`linkctl` is a standard Python package (pip/pipx installable).

```bash
# Recommended: isolated install on your PATH
pipx install git+ssh://git@github.com/vbalasu/link.git

# Or from a local checkout
pip install .            # or: pip install -e .   (development)

# Deploy dependencies (AWS CDK Python libs) are an optional extra:
pip install ".[deploy]"
```

Notes:
- Not yet published to PyPI, so install from this repo (local checkout or git URL)
  rather than the bare package name.
- `linkctl deploy` shells out to the **AWS CDK CLI**, which needs Node.js — install that
  separately. Day-to-day commands (`create`, `stats`, …) don't need it.

## Cost

- **Idle: $0** — DynamoDB on-demand, Lambda, and CloudFront are all pay-per-use.
- The only possible fixed cost is a Route53 hosted zone (~$0.50/mo), if newly created.

## License

TBD.
