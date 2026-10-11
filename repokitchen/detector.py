"""Operating System and Architecture Detection for RepoKitchen.

Detects Linux distribution, release version, codename, and architecture
from /etc/os-release and system properties. Checks compatibility against
recipe platform specifications.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class OSInfo:
    """Represents detected operating system details."""
    id: str = "unknown"
    version_id: str = ""
    version_codename: str = ""
    name: str = "Unknown Linux"
    pretty_name: str = "Unknown Linux"
    id_like: List[str] = field(default_factory=list)
    architecture: str = "unknown"
    raw_data: Dict[str, str] = field(default_factory=dict)

    @property
    def is_debian_family(self) -> bool:
        """Check whether the detected OS belongs to the Debian/Ubuntu family."""
        family_ids = {"debian", "ubuntu", "linuxmint", "pop", "elementary", "kali", "raspbian"}
        if self.id.lower() in family_ids:
            return True
        for item in self.id_like:
            if item.lower() in family_ids:
                return True
        return False

    def summary(self) -> str:
        """Formatted one-line summary of OS details."""
        codename_str = f" ({self.version_codename})" if self.version_codename else ""
        return f"{self.pretty_name}{codename_str} [{self.architecture}]"


def normalize_arch(arch_str: str) -> str:
    """Normalize system architecture string to Debian architecture names."""
    normalized = arch_str.strip().lower()
    mapping = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
        "armv7l": "armhf",
        "armv6l": "armel",
        "armhf": "armhf",
        "i386": "i386",
        "i686": "i386",
        "ppc64le": "ppc64el",
        "s390x": "s390x",
        "riscv64": "riscv64",
    }
    return mapping.get(normalized, normalized)


def parse_os_release(content: str) -> Dict[str, str]:
    """Parse the content of an /etc/os-release file into a dictionary.

    Handles quotes, comments, and empty lines according to the os-release specification.
    """
    data: Dict[str, str] = {}
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        # Handle quoted values
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
            # Replace escaped quotes or backslashes
            value = value.replace(r'\"', '"').replace(r"\'", "'").replace(r"\\", "\\")

        data[key] = value
    return data


def detect_os(
    os_release_path: Optional[str | Path] = None,
    arch: Optional[str] = None,
) -> OSInfo:
    """Detect current operating system details.

    Args:
        os_release_path: Optional path to an os-release file (useful for testing).
        arch: Optional architecture override string.

    Returns:
        OSInfo dataclass containing parsed and normalized values.
    """
    candidate_paths = (
        [Path(os_release_path)]
        if os_release_path
        else [Path("/etc/os-release"), Path("/usr/lib/os-release")]
    )

    raw_data: Dict[str, str] = {}
    for path in candidate_paths:
        if path.is_file():
            try:
                content = path.read_text(encoding="utf-8")
                raw_data = parse_os_release(content)
                break
            except (OSError, UnicodeDecodeError):
                continue

    distro_id = raw_data.get("ID", "unknown").lower()
    version_id = raw_data.get("VERSION_ID", "")
    codename = raw_data.get("VERSION_CODENAME", "")
    if not codename and "UBUNTU_CODENAME" in raw_data:
        codename = raw_data["UBUNTU_CODENAME"]
    if not codename and "VERSION" in raw_data:
        # Extract codename from version string e.g. '12 (bookworm)'
        match = re.search(r"\(([^)]+)\)", raw_data["VERSION"])
        if match:
            codename = match.group(1).lower()

    name = raw_data.get("NAME", "Unknown Linux")
    pretty_name = raw_data.get("PRETTY_NAME", name)

    id_like_str = raw_data.get("ID_LIKE", "")
    id_like = [item.strip().lower() for item in id_like_str.split() if item.strip()]

    detected_arch = normalize_arch(arch if arch is not None else platform.machine())

    return OSInfo(
        id=distro_id,
        version_id=version_id,
        version_codename=codename.lower(),
        name=name,
        pretty_name=pretty_name,
        id_like=id_like,
        architecture=detected_arch,
        raw_data=raw_data,
    )


def check_compatibility(os_info: OSInfo, recipe: Dict[str, Any]) -> Tuple[bool, str]:
    """Check whether the detected OS satisfies the platform requirements of a recipe.

    Args:
        os_info: Detected OSInfo object.
        recipe: Parsed recipe dictionary.

    Returns:
        (is_compatible, explanation_message)
    """
    platforms = recipe.get("platforms", {})
    if not platforms:
        return True, "No platform restrictions specified."

    family = platforms.get("family", "debian").lower()
    if family == "debian" and not os_info.is_debian_family:
        return (
            False,
            f"Incompatible OS family: '{os_info.pretty_name}' is not Debian/Ubuntu based.",
        )

    # Check explicit supported_releases mapping if present (e.g. OpenNebula)
    supported_releases = platforms.get("supported_releases")
    if supported_releases and isinstance(supported_releases, dict):
        if os_info.id not in supported_releases:
            supported_distros = ", ".join(supported_releases.keys())
            return (
                False,
                f"Distribution '{os_info.id}' is not supported. Supported: {supported_distros}.",
            )

        allowed_versions = [str(v) for v in supported_releases[os_info.id]]
        if os_info.version_id not in allowed_versions:
            allowed_str = ", ".join(allowed_versions)
            return (
                False,
                f"Release '{os_info.version_id}' of {os_info.id} is not supported. Supported: {allowed_str}.",
            )

    # Check supported_ids list if present
    supported_ids = platforms.get("supported_ids")
    if supported_ids and isinstance(supported_ids, list):
        supported_ids_lower = [str(sid).lower() for sid in supported_ids]
        matched = os_info.id in supported_ids_lower or any(
            like in supported_ids_lower for like in os_info.id_like
        )
        if not matched:
            supported_str = ", ".join(supported_ids_lower)
            return (
                False,
                f"Distribution '{os_info.id}' is not supported. Supported: {supported_str}.",
            )

    # Check architecture restrictions if present
    architectures = platforms.get("architectures")
    if architectures and isinstance(architectures, list):
        archs_lower = [str(a).lower() for a in architectures]
        if os_info.architecture not in archs_lower:
            arch_str = ", ".join(archs_lower)
            return (
                False,
                f"Architecture '{os_info.architecture}' is not supported. Supported: {arch_str}.",
            )

    return True, f"Compatible with {os_info.pretty_name} ({os_info.architecture})"
