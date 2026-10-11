"""Recipe Loader and Validator for RepoKitchen.

Parses declarative JSON recipe files, validates schema conformity,
enforces strict security invariants (HTTPS, safe paths, no arbitrary commands),
and groups recipes by category.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


class RecipeValidationError(Exception):
    """Raised when a recipe file fails structural or security validation."""
    pass


ALLOWED_INSTALLER_TYPES = {"apt_repository", "verified_archive", "native_apt_package"}
ALLOWED_KEYRING_PREFIXES = ("/usr/share/keyrings/", "/etc/apt/keyrings/")
ALLOWED_SOURCE_PREFIXES = ("/etc/apt/sources.list.d/",)
ALLOWED_ARCHIVE_PREFIXES = ("/opt", "/usr/local")
ALLOWED_ENV_PREFIXES = ("/etc/profile.d/",)

FORBIDDEN_EXEC_KEYS = {
    "command", "exec", "shell", "bash", "sh", "raw_script", "script_url",
    "eval", "system", "run_raw", "custom_script"
}

PKG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
IDENTIFIER_RE = re.compile(r"^[a-z0-9_-]+$")
HEX_FINGERPRINT_RE = re.compile(r"^[0-9A-Fa-f\s]+$")


def validate_https_url(url: str, field_name: str) -> None:
    """Ensure a URL is valid, remote, and uses the HTTPS scheme."""
    if not isinstance(url, str) or not url.strip():
        raise RecipeValidationError(f"Field '{field_name}' must be a non-empty string.")
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise RecipeValidationError(
            f"Security violation in '{field_name}': URL must use HTTPS ('{url}')."
        )
    if not parsed.netloc:
        raise RecipeValidationError(f"Malformed URL in '{field_name}': '{url}'.")


def validate_safe_path(path_str: str, allowed_prefixes: tuple[str, ...], field_name: str) -> None:
    """Ensure a filesystem path is absolute, free of traversal ('..'), and under allowed prefixes."""
    if not isinstance(path_str, str) or not path_str.strip():
        raise RecipeValidationError(f"Field '{field_name}' must be a non-empty path string.")

    if not path_str.startswith("/"):
        raise RecipeValidationError(f"Path '{field_name}' must be an absolute path: '{path_str}'.")

    # Path traversal check
    parts = Path(path_str).parts
    if ".." in parts or "." in parts:
        raise RecipeValidationError(f"Path traversal detected in '{field_name}': '{path_str}'.")

    if not any(path_str.startswith(prefix) for prefix in allowed_prefixes):
        allowed_str = ", ".join(allowed_prefixes)
        raise RecipeValidationError(
            f"Path '{path_str}' in '{field_name}' is outside allowed locations ({allowed_str})."
        )


def validate_package_names(names: List[str], field_name: str) -> None:
    """Validate package name strings against standard Debian package name conventions."""
    if not isinstance(names, list) or not names:
        raise RecipeValidationError(f"Field '{field_name}' must be a non-empty list of package names.")
    for name in names:
        if not isinstance(name, str) or not PKG_NAME_RE.match(name.strip()):
            raise RecipeValidationError(f"Invalid package name '{name}' in '{field_name}'.")


def validate_prerequisites(prereqs: List[Dict[str, Any]], field_name: str = "prerequisites") -> None:
    """Validate prerequisite declaration objects."""
    if not isinstance(prereqs, list):
        raise RecipeValidationError(f"Field '{field_name}' must be a list.")
    for item in prereqs:
        if not isinstance(item, dict):
            raise RecipeValidationError(f"Each prerequisite in '{field_name}' must be an object.")
        ptype = item.get("type")
        if ptype != "apt_packages":
            raise RecipeValidationError(
                f"Unsupported prerequisite type '{ptype}' in '{field_name}'. Only 'apt_packages' is supported."
            )
        names = item.get("names", [])
        validate_package_names(names, f"{field_name}.names")


def validate_recipe(data: Dict[str, Any], recipe_name: str = "") -> Dict[str, Any]:
    """Validate full recipe data structure against security and schema rules.

    Args:
        data: Parsed JSON data.
        recipe_name: Optional name/filename for error messages.

    Returns:
        The validated dictionary.

    Raises:
        RecipeValidationError: If any check fails.
    """
    prefix = f"[{recipe_name}] " if recipe_name else ""

    # Check for forbidden arbitrary execution keys
    for key in FORBIDDEN_EXEC_KEYS:
        if key in data:
            raise RecipeValidationError(
                f"{prefix}Forbidden key '{key}' found. Recipes must not specify arbitrary commands."
            )

    # Required top-level fields
    required_keys = [
        "schema_version", "id", "name", "category",
        "description", "official_docs", "platforms",
        "installer", "actions", "security"
    ]
    for key in required_keys:
        if key not in data:
            raise RecipeValidationError(f"{prefix}Missing required top-level field: '{key}'.")

    if data["schema_version"] != 1:
        raise RecipeValidationError(
            f"{prefix}Unsupported schema_version: {data['schema_version']}. Expected 1."
        )

    app_id = data["id"]
    if not isinstance(app_id, str) or not IDENTIFIER_RE.match(app_id):
        raise RecipeValidationError(
            f"{prefix}Invalid recipe id: '{app_id}'. Must be lowercase alphanumeric with hyphens/underscores."
        )

    validate_https_url(data["official_docs"], "official_docs")

    # Security block checks
    security = data.get("security", {})
    if not isinstance(security, dict):
        raise RecipeValidationError(f"{prefix}Field 'security' must be a dictionary.")
    if not security.get("require_https", False):
        raise RecipeValidationError(f"{prefix}Security policy violation: 'require_https' must be true.")

    # Installer block checks
    installer = data.get("installer", {})
    if not isinstance(installer, dict):
        raise RecipeValidationError(f"{prefix}Field 'installer' must be a dictionary.")

    installer_type = installer.get("type")
    if installer_type not in ALLOWED_INSTALLER_TYPES:
        raise RecipeValidationError(
            f"{prefix}Unknown installer type: '{installer_type}'. Supported: {', '.join(sorted(ALLOWED_INSTALLER_TYPES))}."
        )

    if "prerequisites" in installer:
        validate_prerequisites(installer["prerequisites"])

    if installer_type == "apt_repository":
        repo = installer.get("repository", {})
        if not isinstance(repo, dict):
            raise RecipeValidationError(f"{prefix}Missing 'repository' configuration.")

        uri = repo.get("uri") or repo.get("uri_template")
        if not uri:
            raise RecipeValidationError(f"{prefix}Repository must provide 'uri' or 'uri_template'.")
        validate_https_url(uri, "repository.uri")

        source_file = repo.get("source_file", "")
        validate_safe_path(source_file, ALLOWED_SOURCE_PREFIXES, "repository.source_file")
        if not source_file.endswith(".list"):
            raise RecipeValidationError(
                f"{prefix}Repository source_file must have .list extension: '{source_file}'."
            )

        source_template = repo.get("source_line_template", "")
        if not source_template or "deb" not in source_template:
            raise RecipeValidationError(f"{prefix}Repository must provide valid 'source_line_template'.")

        signing_key = installer.get("signing_key", {})
        if not isinstance(signing_key, dict):
            raise RecipeValidationError(f"{prefix}Missing 'signing_key' configuration for apt_repository.")

        key_url = signing_key.get("url", "")
        validate_https_url(key_url, "signing_key.url")

        keyring_path = signing_key.get("keyring", "")
        validate_safe_path(keyring_path, ALLOWED_KEYRING_PREFIXES, "signing_key.keyring")

        expected_fingerprint = signing_key.get("expected_primary_fingerprint")
        if expected_fingerprint is not None:
            if not isinstance(expected_fingerprint, str) or not HEX_FINGERPRINT_RE.match(expected_fingerprint):
                raise RecipeValidationError(
                    f"{prefix}Invalid expected_primary_fingerprint format: '{expected_fingerprint}'."
                )

        install_packages = installer.get("install_packages", [])
        if install_packages:
            validate_package_names(install_packages, "install_packages")

    elif installer_type == "verified_archive":
        install_location = installer.get("install_location", "")
        validate_safe_path(install_location, ALLOWED_ARCHIVE_PREFIXES, "install_location")

        if "environment_file" in installer and installer["environment_file"]:
            validate_safe_path(
                installer["environment_file"], ALLOWED_ENV_PREFIXES, "environment_file"
            )

        verification = installer.get("verification", {})
        if not isinstance(verification, dict):
            raise RecipeValidationError(f"{prefix}Missing 'verification' configuration for verified_archive.")

        keys_page = verification.get("keys_page")
        if keys_page:
            validate_https_url(keys_page, "verification.keys_page")

    elif installer_type == "native_apt_package":
        install_packages = installer.get("install_packages", [])
        validate_package_names(install_packages, "install_packages")

    return data


def get_default_recipes_dir() -> Path:
    """Return the default path to the project recipes directory."""
    # Look for recipes relative to repokitchen package location
    base_dir = Path(__file__).resolve().parent.parent
    recipes_dir = base_dir / "recipes"
    if recipes_dir.is_dir():
        return recipes_dir
    return Path("recipes")


def load_recipe(file_path: Path) -> Dict[str, Any]:
    """Read and validate a single JSON recipe file.

    Args:
        file_path: Path to the recipe JSON file.

    Returns:
        Validated recipe dictionary.
    """
    try:
        content = file_path.read_text(encoding="utf-8")
        data = json.loads(content)
    except json.JSONDecodeError as err:
        raise RecipeValidationError(f"Invalid JSON in '{file_path.name}': {err}") from err
    except OSError as err:
        raise RecipeValidationError(f"Unable to read recipe file '{file_path}': {err}") from err

    return validate_recipe(data, recipe_name=file_path.name)


def load_all_recipes(directory: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load and validate all JSON recipes in a directory.

    Args:
        directory: Optional directory path. Defaults to project 'recipes/'.

    Returns:
        Sorted list of validated recipe dictionaries.
    """
    recipes_dir = directory if directory is not None else get_default_recipes_dir()
    if not recipes_dir.is_dir():
        return []

    recipes: List[Dict[str, Any]] = []
    for file_path in sorted(recipes_dir.glob("*.json")):
        recipe = load_recipe(file_path)
        recipes.append(recipe)

    # Sort recipes alphabetically by display name
    recipes.sort(key=lambda r: r.get("name", "").lower())
    return recipes


def group_recipes_by_category(recipes: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Group a list of recipes by their category field."""
    categories: Dict[str, List[Dict[str, Any]]] = {}
    for recipe in recipes:
        cat = recipe.get("category", "General")
        categories.setdefault(cat, []).append(recipe)
    return categories
