"""Load and validate config.yaml."""

from pathlib import Path

import yaml

_DEFAULTS = {
    "subdomain": "link",
    "aws_region": "us-east-1",
    "root_redirect": "",
    "aws_profile": None,
}


def load_config(path: str = "config.yaml") -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"{path} not found. Run linkctl from your project directory."
        )
    data = yaml.safe_load(p.read_text()) or {}
    cfg = {**_DEFAULTS, **data}

    if not cfg.get("domain"):
        raise ValueError("config.yaml must set 'domain'")

    sub = cfg["subdomain"]
    cfg["host"] = f"{sub}.{cfg['domain']}" if sub else cfg["domain"]
    # Stack name is derived from the domain verbatim — no TLD assumptions.
    cfg["stack_name"] = "link-" + cfg["domain"].replace(".", "-")
    return cfg
