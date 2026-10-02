# URL Shortener — Design Specification

Derived from `human-input/01.md` via a structured design interview. This document
records the decisions made, the rationale, and the alternatives rejected, so choices
can be revisited later.

## 1. Goal

A serverless URL-shortening utility that:
- Takes a **target URL** and a **user-friendly slug** as input.
- Serves `https://link.{YOUR_DOMAIN}/{slug}` which redirects to the target.
- Records **every click** with a timestamp and all available request detail.
- Ships a **CLI** to create links and analyze access history.
- Runs on a **custom domain**, costs **$0 when idle**, and is **trivial to set up** for a
  new domain via `config.yaml` + CLI.

## 2. Architecture (AWS)

```
          DNS: link.{domain}  (Route53 ALIAS)
                     │
                     ▼
             CloudFront distribution        ← ACM cert (us-east-1), TLS termination
                     │  (no caching of redirects)
                     ▼
          Lambda Function URL (Python)       ← redirect handler
                     │
          ┌──────────┴───────────┐
          ▼                      ▼
   DynamoDB (single table)   writes click item
   PK=slug                   PK=slug, SK=ts#uuid
```

- **Compute**: Lambda (Python) behind a Lambda Function URL. CloudFront fronts it to
  carry the custom domain + ACM cert on HTTPS (a Function URL cannot host a custom
  domain/cert directly — this is a *derived*, non-optional decision).
- **Storage**: single DynamoDB table, on-demand capacity.
- **Certs**: ACM in `us-east-1` (required for CloudFront), DNS-validated.
- **DNS**: Route53 (see §5).
- **One CDK stack per domain** (own table, Lambda, CloudFront, cert).

## 3. Key Decisions

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| 1 | Cloud provider | **AWS** | Genuine $0 at idle. GCP's clean CDN path requires a global HTTPS load balancer (~$18/mo fixed), violating "no cost when not in use". |
| 2 | Redirect path | **Lambda Function URL, no edge caching** | Every click must reach Lambda to be recorded; caching would bypass it. API Gateway rejected (extra cost/latency, no benefit). |
| 3 | CloudFront in front | **Yes (derived)** | Only clean way to attach custom domain + ACM cert to a Function URL. |
| 4 | Click storage | **One item per click, single table** | Full per-click history + range queries. Atomic counter attr on the link item for fast totals. |
| 5 | Click details | **Everything, including raw IP** | Richest analytics. ⚠️ Raw IP = PII; owner accepts the privacy/compliance responsibility. Captures: timestamp, raw client IP, CloudFront geo (country/region/city), user-agent (+ parsed), referer, Accept-Language. |
| 6 | Retention | **Keep forever** (no TTL) | Full history retained indefinitely. ⚠️ Unbounded storage growth + PII held indefinitely — revisit if volume or compliance pressure grows. |
| 7 | Slug collisions | **Reject; targets immutable** | Creating an existing slug fails. No overwrite/retarget ever. Fix a mistake by choosing a new slug. Charset `[a-zA-Z0-9_-]`, length bounds, reserved-word list. DynamoDB conditional write (`attribute_not_exists`) prevents races. |
| 8 | Language/runtime | **Python** | One toolchain for CLI (Typer/Click) + Lambda (boto3). pipx install. |
| 9 | IaC | **AWS CDK (Python), CLI-wrapped** | `linkctl deploy` reads config.yaml and runs `cdk deploy`. CDK cleanly orchestrates ACM + DNS validation + CloudFront. |
| 10 | DNS | **Route53, fully automated** | CDK auto-creates cert-validation records AND the `link.{domain}` alias → true one-command setup. Requires the (sub)domain be in Route53. |
| 11 | Analytics read path | **CLI queries DynamoDB directly** | Operator-only tool with local AWS creds. No management API/auth to build. CLI aggregates client-side. |
| 12 | Multi-domain | **One stack per domain** | Isolation, independent teardown, simplest model. Re-run with a new config for another domain. |
| 13 | Lifecycle ops | **create, delete, list, inspect** | No retarget (per #7). Delete removes the mapping (clicks optionally retained). |
| 14 | Redirect code | **302 + `Cache-Control: no-store`** | Every repeat visit re-hits Lambda = accurate click counts. 301 rejected (browser-cached → undercounts). |
| 15 | Not-found behavior | **404 for unknown/deleted slug; configurable root redirect** | Bare `link.{domain}/` → fallback URL from config.yaml, or 404 if unset. |

## 4. Data Model (DynamoDB, single table)

**Link item**
- `PK` = `slug`
- `SK` = `META`
- attributes: `target_url`, `created_at`, `click_count` (atomic counter)

**Click item**
- `PK` = `slug`
- `SK` = `{iso8601_timestamp}#{uuid}`
- attributes: `ts`, `ip` (raw), `country`, `region`, `city`, `user_agent`,
  `browser`, `device`, `referer`, `accept_language`

**Queries**
- Resolve redirect: `GetItem(PK=slug, SK=META)`.
- History for a slug: `Query(PK=slug, SK between ts_lo and ts_hi)`.
- Totals: read `click_count` on the META item.
- Top countries/referers/UAs: client-side aggregation over queried click items.

## 5. Setup / Onboarding Flow

Prerequisite: domain (or delegated subdomain) hosted in Route53.

```yaml
# config.yaml
domain: example.com          # link host becomes link.example.com
subdomain: link              # optional, default "link"
aws_region: us-east-1
root_redirect: https://example.com   # optional; bare host fallback (else 404)
```

```
linkctl deploy        # reads config.yaml, runs cdk deploy: cert + CloudFront + Lambda + table + DNS
linkctl destroy       # tears down the stack for this domain
```

## 6. CLI Surface (`linkctl`)

```
linkctl deploy                         # provision/update stack from config.yaml
linkctl destroy                        # tear down stack
linkctl create <slug> <target_url>     # create a link (rejects if slug exists)
linkctl delete <slug>                  # remove the mapping
linkctl list                           # list all slugs + targets + counts
linkctl inspect <slug>                 # show one link's metadata
linkctl stats <slug> [--from --to]     # totals + time-series + top geo/referer/UA
linkctl export <slug> [--format csv|json]  # raw click history
```

## 7. Cost Posture

- Idle: $0 (DynamoDB on-demand, Lambda, CloudFront all pay-per-use).
- Only possible fixed cost: Route53 hosted zone (~$0.50/mo) if newly created for this.
- Storage grows unbounded over time (decision #6, keep-forever) — monitor.

## 8. Applied Defaults (not separately confirmed — flag if wrong)

- Target URL validation: require `http(s)://`, basic URL parse on `create`.
- 404 misses are **not** recorded as clicks (keeps the clicks table clean); revisit if
  miss-tracking is wanted.
- CLI is a standard Python package (`pyproject.toml` with a console-script entry
  point, `linkctl = linkctl.cli:main`), so it is pip/pipx installable.
  - **Recommended**: `pipx install` (isolated venv, on PATH, no dependency clashes).
  - `pip install` also works into any active environment; `pip install -e .` for dev.
  - **Not published to PyPI** (not required by spec). Until then, install from the
    local checkout or a git URL (`pipx install git+https://...`), not the bare name
    from the public index. Publishing to PyPI is a deferred, separate step.
  - **Deploy dependencies are an optional extra**: `pip install linkctl[deploy]` pulls
    the CDK Python libs (`aws-cdk-lib`, `constructs`). The AWS CDK CLI + Node runtime
    that `cdk deploy` shells out to are **not** provided by pip — installed separately.
    Day-to-day commands (`create`, `stats`, ...) stay lightweight without the extra.
- Reserved slugs: `health`, `_health`, `favicon.ico`, `robots.txt` (extendable).

## 9. Open / Revisit Later

- **PII + keep-forever**: raw IPs retained indefinitely. No current retention or
  deletion-on-request mechanism — add if compliance requires.
- Rate limiting / abuse protection on redirect endpoint: none specified.
- No web dashboard (CLI-only). A management API would be the prerequisite if added.
