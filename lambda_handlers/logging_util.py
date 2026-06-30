"""CloudWatch-friendly logging for Lambda handlers."""

import logging
import sys


def configure_logging() -> None:
    """Ensure INFO logs reach stdout (Lambda pre-configures root before basicConfig)."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            handler.setLevel(logging.INFO)
