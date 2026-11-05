"""Verify local console logging without contacting AWS."""

import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger(__name__).info("Local logging check passed.")
