"""Unit tests for execution logic, security gates, and trust verification in repokitchen.installer."""

import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from repokitchen.detector import OSInfo
from repokitchen.installer import (
    InstallationError,
    Installer,
    SecurityVerificationError,
    detect_java_home,
    re_clean_fingerprint,
    verify_key_fingerprint,
)
from repokitchen.recipe_loader import get_default_recipes_dir, load_recipe


class TestInstaller(unittest.TestCase):
    """Test suite for Installer execution engine, security verification, and error handling."""

    def setUp(self):
        self.recipes_dir = get_default_recipes_dir()
        self.os_noble = OSInfo(
            id="ubuntu",
            version_id="24.04",
            version_codename="noble",
            pretty_name="Ubuntu 24.04 LTS",
            id_like=["debian"],
            architecture="amd64",
        )

    def test_opennebula_blocks_automatic_setup(self):
        recipe = load_recipe(self.recipes_dir / "opennebula.json")
        installer = Installer(dry_run=True)
        result = installer.install(recipe, self.os_noble)

        self.assertFalse(result.success)
        self.assertIn("Automatic installation blocked", result.message)
        self.assertIn("fingerprint", result.message.lower())

    def test_incompatible_os_blocks_install(self):
        recipe = load_recipe(self.recipes_dir / "jenkins.json")
        fedora_info = OSInfo(id="fedora", pretty_name="Fedora Linux 44", id_like=[])
        installer = Installer(dry_run=True)
        result = installer.install(recipe, fedora_info)

        self.assertFalse(result.success)
        self.assertIn("Installation blocked", result.message)

    def test_user_cancellation_aborts_installation(self):
        recipe = load_recipe(self.recipes_dir / "jenkins.json")
        installer = Installer(dry_run=True)
        # Callback returns False (user rejects)
        result = installer.install(
            recipe,
            self.os_noble,
            confirm_callback=lambda plan: False,
        )

        self.assertFalse(result.success)
        self.assertIn("cancelled by user", result.message)
        self.assertEqual(len(result.changes_made), 0)

    @patch("repokitchen.installer.extract_key_fingerprints_gpg")
    def test_fingerprint_mismatch_raises_security_error(self, mock_extract):
        mock_extract.return_value = ["1111222233334444555566667777888899990000"]
        expected = "5E386EADB55F01504CAE8BCF7198F4B714ABFC68"

        with self.assertRaises(SecurityVerificationError) as ctx:
            verify_key_fingerprint(Path("/dummy/key"), expected)

        self.assertIn("Cryptographic trust verification FAILED", str(ctx.exception))

    @patch("repokitchen.installer.extract_key_fingerprints_gpg")
    def test_fingerprint_match_succeeds(self, mock_extract):
        mock_extract.return_value = ["5E38 6EAD B55F 0150 4CAE 8BCF 7198 F4B7 14AB FC68"]
        expected = "5E386EADB55F01504CAE8BCF7198F4B714ABFC68"
        # Should not raise exception
        verify_key_fingerprint(Path("/dummy/key"), expected)

    def test_dry_run_jenkins_completes_simulation(self):
        recipe = load_recipe(self.recipes_dir / "jenkins.json")
        installer = Installer(dry_run=True)
        result = installer.install(
            recipe,
            self.os_noble,
            confirm_callback=lambda plan: True,
        )

        self.assertTrue(result.success)
        self.assertIn("completed successfully", result.message)
        self.assertGreater(len(result.changes_made), 0)
        self.assertGreater(len(result.undo_commands), 0)

    def test_subprocess_failure_stops_workflow(self):
        recipe = load_recipe(self.recipes_dir / "ansible.json")
        mock_runner = MagicMock()
        mock_runner.side_effect = subprocess.CalledProcessError(
            returncode=100, cmd=["apt-get", "update"]
        )

        installer = Installer(dry_run=False, runner=mock_runner)
        result = installer.install(
            recipe,
            self.os_noble,
            confirm_callback=lambda plan: True,
        )

        self.assertFalse(result.success)
        self.assertIn("Installation failed", result.message)
        self.assertIsInstance(result.error, InstallationError)

    def test_re_clean_fingerprint(self):
        dirty = "  5e38 6ead   b55f 0150  "
        clean = re_clean_fingerprint(dirty)
        self.assertEqual(clean, "5E386EADB55F0150")

    @patch("shutil.which")
    @patch("subprocess.run")
    def test_detect_java_home_strips_bin_java(self, mock_run, mock_which):
        mock_which.return_value = "/usr/bin/java"
        mock_proc = MagicMock()
        mock_proc.stdout = "/usr/lib/jvm/java-21-openjdk-amd64/bin/java\n"
        mock_run.return_value = mock_proc

        java_home = detect_java_home()
        self.assertEqual(java_home, "/usr/lib/jvm/java-21-openjdk-amd64")
        self.assertFalse(java_home.endswith("bin/java"))

    def test_hadoop_dry_run_configures_java_home(self):
        recipe = load_recipe(self.recipes_dir / "hadoop.json")
        installer = Installer(dry_run=True)
        result = installer.install(
            recipe,
            self.os_noble,
            confirm_callback=lambda plan: True,
        )

        self.assertTrue(result.success)
        # Check that changes_made mentions JAVA_HOME
        env_changes = [c for c in result.changes_made if "JAVA_HOME=" in c]
        self.assertGreater(len(env_changes), 0)
        self.assertIn("HADOOP_HOME=", env_changes[0])

        # Check logs directory and SSH changes
        log_changes = [c for c in result.changes_made if "logs directory" in c]
        self.assertGreater(len(log_changes), 0)
        ssh_changes = [c for c in result.changes_made if "passwordless SSH" in c]
        self.assertGreater(len(ssh_changes), 0)

        # Check core-site.xml and hdfs-site.xml changes
        xml_changes = [c for c in result.changes_made if "core-site.xml" in c]
        self.assertGreater(len(xml_changes), 0)
        hdfs_xml_changes = [c for c in result.changes_made if "hdfs-site.xml" in c]
        self.assertGreater(len(hdfs_xml_changes), 0)


if __name__ == "__main__":
    unittest.main()
