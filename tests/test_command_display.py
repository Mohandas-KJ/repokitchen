"""Unit tests for instruction formatting and command display in repokitchen.command_display."""

import unittest
from repokitchen.command_display import (
    build_installation_plan,
    format_copy_paste_script,
    format_full_view,
    resolve_template_placeholders,
)
from repokitchen.detector import OSInfo
from repokitchen.recipe_loader import get_default_recipes_dir, load_recipe


class TestCommandDisplay(unittest.TestCase):
    """Test suite for command generation, template resolution, and script output."""

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

    def test_jenkins_instructions_generation(self):
        recipe = load_recipe(self.recipes_dir / "jenkins.json")
        plan = build_installation_plan(recipe, self.os_noble)

        self.assertEqual(plan.recipe_id, "jenkins")
        self.assertTrue(plan.is_compatible)
        self.assertTrue(plan.can_auto_install)
        self.assertIn("5E386EADB55F01504CAE8BCF7198F4B714ABFC68", plan.trust_status)

        # Check steps
        step_titles = [s.title for s in plan.steps]
        self.assertIn("Install Prerequisites", step_titles)
        self.assertIn("Import Official Signing Key", step_titles)
        self.assertIn("Configure APT Repository Source", step_titles)
        self.assertIn("Update APT Index and Install Software", step_titles)

        # Check copy-paste script output
        script = format_copy_paste_script(plan)
        self.assertIn("#!/usr/bin/env bash", script)
        self.assertIn("pkg.jenkins.io", script)
        self.assertIn("5E386EADB55F01504CAE8BCF7198F4B714ABFC68", script)
        self.assertIn("jenkins-keyring.asc", script)
        self.assertIn("sudo apt-get install -y jenkins", script)

    def test_opennebula_template_resolution_and_block(self):
        recipe = load_recipe(self.recipes_dir / "opennebula.json")
        plan = build_installation_plan(recipe, self.os_noble)

        self.assertEqual(plan.recipe_id, "opennebula")
        self.assertTrue(plan.is_compatible)
        # Fingerprint is unpinned, so automatic configuration must be blocked
        self.assertFalse(plan.can_auto_install)
        self.assertIsNotNone(plan.auto_install_blocked_reason)
        self.assertIn("blocked", plan.auto_install_blocked_reason.lower())

        script = format_copy_paste_script(plan)
        # Template should have resolved Ubuntu and 24.04
        self.assertIn("https://downloads.opennebula.io/repo/7.2/Ubuntu/24.04", script)
        self.assertIn("/etc/apt/keyrings/opennebula.gpg", script)

    def test_hadoop_verified_archive_instructions(self):
        recipe = load_recipe(self.recipes_dir / "hadoop.json")
        plan = build_installation_plan(recipe, self.os_noble)

        self.assertEqual(plan.recipe_id, "hadoop")
        self.assertEqual(plan.method, "verified_archive")

        script = format_copy_paste_script(plan)
        self.assertIn("hadoop-3.5.0.tar.gz", script)
        self.assertIn("gpg --verify", script)
        self.assertIn("sha512sum -c", script)
        self.assertIn("tar -xzf", script)
        self.assertIn("HADOOP_HOME", script)

    def test_ansible_native_apt_package(self):
        ansible_file = self.recipes_dir / "ansible.json"
        if ansible_file.exists():
            recipe = load_recipe(ansible_file)
            plan = build_installation_plan(recipe, self.os_noble)
            self.assertEqual(plan.method, "native_apt_package")
            script = format_copy_paste_script(plan)
            self.assertIn("sudo apt-get install -y ansible", script)

    def test_format_full_view(self):
        recipe = load_recipe(self.recipes_dir / "jenkins.json")
        plan = build_installation_plan(recipe, self.os_noble)
        full_view = format_full_view(plan)

        self.assertIn("RepoKitchen — Installation Instructions: Jenkins", full_view)
        self.assertIn("STEP-BY-STEP BREAKDOWN", full_view)
        self.assertIn("COPY-PASTE INSTALLATION SCRIPT", full_view)
        self.assertIn("HOW TO UNDO THESE CHANGES", full_view)


if __name__ == "__main__":
    unittest.main()
