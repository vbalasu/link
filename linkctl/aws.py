"""AWS helpers: boto3 session + resolving the stack's DynamoDB table."""

from functools import lru_cache

import boto3


def session(cfg: dict) -> boto3.Session:
    # aws_profile from config wins; otherwise fall back to AWS_PROFILE / default chain.
    return boto3.Session(
        profile_name=cfg.get("aws_profile"),
        region_name=cfg["aws_region"],
    )


@lru_cache(maxsize=None)
def _outputs(stack_name: str, profile, region) -> dict:
    sess = boto3.Session(profile_name=profile, region_name=region)
    cfn = sess.client("cloudformation")
    stacks = cfn.describe_stacks(StackName=stack_name)["Stacks"]
    return {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}


def stack_outputs(cfg: dict) -> dict:
    try:
        return _outputs(cfg["stack_name"], cfg.get("aws_profile"), cfg["aws_region"])
    except Exception as exc:
        raise RuntimeError(
            f"Could not read stack '{cfg['stack_name']}'. Is it deployed? ({exc})"
        )


def table(cfg: dict):
    name = stack_outputs(cfg)["TableName"]
    return session(cfg).resource("dynamodb").Table(name)


def account_id(cfg: dict) -> str:
    return session(cfg).client("sts").get_caller_identity()["Account"]
