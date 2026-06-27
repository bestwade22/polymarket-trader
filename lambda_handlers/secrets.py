"""Load credentials from AWS Secrets Manager into the process environment."""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, Optional

import boto3

logger = logging.getLogger(__name__)

SECRET_ENV_KEYS = (
    "API_NINJAS_KEY",
    "PRIVATE_KEY",
    "DEPOSIT_WALLET_ADDRESS",
    "GITHUB_PAT",
    "DRY_RUN",
)


@lru_cache(maxsize=1)
def load_secrets() -> Dict[str, Any]:
    arn = os.environ.get("SECRETS_ARN", "").strip()
    if not arn:
        logger.warning("SECRETS_ARN not set; using existing environment only")
        return {key: os.environ.get(key, "") for key in SECRET_ENV_KEYS}

    client = boto3.client("secretsmanager")
    response = client.get_secret_value(SecretId=arn)
    payload = response.get("SecretString") or ""
    if not payload:
        raise RuntimeError(f"Secret {arn} has no SecretString")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise RuntimeError(f"Secret {arn} must be a JSON object")
    return data


def apply_secrets(secrets: Optional[Dict[str, Any]] = None) -> None:
    data = secrets if secrets is not None else load_secrets()
    for key in SECRET_ENV_KEYS:
        value = data.get(key)
        if value is not None and str(value).strip():
            os.environ[key] = str(value)
