# link

A serverless URL shortener on AWS. Create short links on your own custom domain
(`https://link.yourdomain.tld/slug`), record every click, and analyze access history —
all from a Python CLI, with **$0 cost when idle**. Works with any domain and any TLD
(`.io`, `.dev`, `.co.uk`, …) — nothing assumes `.com`.

> **Status:** implemented and deployed. A live instance runs at
> **https://link.cloudmatica.com** (one CDK stack, `link-cloudmatica-com`).
> The full design and rationale live in [`SPEC.md`](./SPEC.md).

## What it does

- Map a **user-friendly slug** to a **target URL**: `link.yourdomain.tld/promo` → your long URL.
- Serve the redirect from a custom domain over HTTPS.
- **Record every click** — timestamp, geo, user-agent, referer, and more.
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

## CLI

```bash
linkctl create <slug> <target_url>        # create a link (rejects if slug exists)
linkctl delete <slug>                     # remove the mapping
linkctl list                              # list all slugs, targets, counts
linkctl inspect <slug>                    # show one link's metadata
linkctl stats <slug> [--from --to]        # totals, time-series, top geo/referer/UA
linkctl export <slug> [--format csv|json] # raw click history
```

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
