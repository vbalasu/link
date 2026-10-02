#!/usr/bin/env python3
"""CDK app entrypoint. Values are passed as context by `linkctl deploy`."""

import os
import sys

import aws_cdk as cdk

sys.path.insert(0, os.path.dirname(__file__))
from stack import LinkStack  # noqa: E402

app = cdk.App()

domain = app.node.try_get_context("domain")
if not domain:
    raise SystemExit("missing -c domain=<domain>")
subdomain = app.node.try_get_context("subdomain") or ""
root_redirect = app.node.try_get_context("root_redirect") or ""
account = app.node.try_get_context("account") or os.environ.get("CDK_DEFAULT_ACCOUNT")
region = app.node.try_get_context("region") or os.environ.get("CDK_DEFAULT_REGION") or "us-east-1"

stack_name = "link-" + domain.replace(".", "-")

LinkStack(
    app,
    stack_name,
    domain=domain,
    subdomain=subdomain,
    root_redirect=root_redirect,
    env=cdk.Environment(account=account, region=region),
)

app.synth()
