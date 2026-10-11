"""Unit tests for the CLI commands and entrypoint in repokitchen.main."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from repokitchen.main import main


class TestCLI(unittest.TestCase):
    """Test suite for RepoKitchen CLI commands."""

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()):
                main(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_version_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            with redirect_stdout(io.StringIO()):
                main(["--version"])
        self.assertEqual(ctx.exception.code, 0)

    def test_list_subcommand(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--simulate-os", "ubuntu:24.04", "list"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("RepoKitchen Catalog", output)
        self.assertIn("jenkins", output)
        self.assertIn("opennebula", output)
        self.assertIn("hadoop", output)

    def test_show_subcommand_jenkins(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--simulate-os", "ubuntu:24.04", "show", "jenkins"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Installation Instructions: Jenkins", output)
        self.assertIn("COPY-PASTE INSTALLATION SCRIPT", output)
        self.assertIn("pkg.jenkins.io", output)

    def test_show_unknown_recipe(self):
        code = main(["show", "nonexistent_app"])
        self.assertEqual(code, 1)

    def test_check_subcommand(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["--simulate-os", "ubuntu:24.04", "check", "opennebula"])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("Verification Check: OpenNebula Community Edition", output)
        self.assertIn("Can Auto-install: ❌ No", output)

    def test_install_dry_run_yes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main([
                "--simulate-os", "ubuntu:24.04",
                "--dry-run",
                "install", "jenkins",
                "--yes",
            ])
        self.assertEqual(code, 0)
        output = buf.getvalue()
        self.assertIn("SUCCESS", output)


if __name__ == "__main__":
    unittest.main()
