"""CloudWatch-friendly logging for Lambda handlers."""

import logging
import sys

from src.utils.hk_time import HKTFormatter


def configure_logging() -> None:
    """Ensure INFO logs reach stdout (Lambda pre-configures root before basicConfig)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    formatter = HKTFormatter("%(levelname)s %(name)s %(message)s")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(formatter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setLevel(logging.INFO)
            handler.setFormatter(formatter)
