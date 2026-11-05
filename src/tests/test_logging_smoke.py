"""Check the non-production logging bootstrap."""

import io
import logging
import runpy
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


class LoggingSmokeTest(unittest.TestCase):
    # Ensures the bootstrap enables INFO output even from a WARNING-level logger,
    # keeping the documented local logging check visible on stderr.
    def test_info_message_is_written_to_stderr(self):
        output = io.StringIO()
        root = logging.getLogger()
        script = Path(__file__).resolve().parents[1] / "tools" / "logging_smoke.py"

        with (
            patch.object(root, "handlers", []),
            patch.object(root, "level", logging.WARNING),
            redirect_stderr(output),
        ):
            runpy.run_path(str(script), run_name="__main__")
            self.assertEqual(root.level, logging.INFO)

        self.assertEqual(output.getvalue(), "INFO Local logging check passed.\n")
