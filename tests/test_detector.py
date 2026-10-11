"""Unit tests for OS and architecture detection in repokitchen.detector."""

import unittest
from repokitchen.detector import (
    OSInfo,
    check_compatibility,
    detect_os,
    normalize_arch,
    parse_os_release,
)


class TestDetector(unittest.TestCase):
    """Test suite for OS release parsing and platform compatibility checks."""

    def test_parse_os_release_ubuntu_noble(self):
        content = """
NAME="Ubuntu"
VERSION="24.04 LTS (Noble Numbat)"
ID=ubuntu
ID_LIKE=debian
PRETTY_NAME="Ubuntu 24.04 LTS"
VERSION_ID="24.04"
VERSION_CODENAME=noble
UBUNTU_CODENAME=noble
"""
        parsed = parse_os_release(content)
        self.assertEqual(parsed["ID"], "ubuntu")
        self.assertEqual(parsed["VERSION_ID"], "24.04")
        self.assertEqual(parsed["VERSION_CODENAME"], "noble")
        self.assertEqual(parsed["ID_LIKE"], "debian")

    def test_parse_os_release_debian_bookworm(self):
        content = """
PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"
NAME="Debian GNU/Linux"
VERSION_ID="12"
VERSION="12 (bookworm)"
VERSION_CODENAME=bookworm
ID=debian
HOME_URL="https://www.debian.org/"
"""
        parsed = parse_os_release(content)
        self.assertEqual(parsed["ID"], "debian")
        self.assertEqual(parsed["VERSION_ID"], "12")
        self.assertEqual(parsed["VERSION_CODENAME"], "bookworm")

    def test_parse_os_release_comments_and_escapes(self):
        content = """
# This is a comment
ID="test_distro"
EMPTY_LINE=
QUOTED_VAL="test \\"with\\" quotes"
SINGLE_QUOTED='single'
"""
        parsed = parse_os_release(content)
        self.assertEqual(parsed["ID"], "test_distro")
        self.assertEqual(parsed["QUOTED_VAL"], 'test "with" quotes')
        self.assertEqual(parsed["SINGLE_QUOTED"], "single")

    def test_normalize_arch(self):
        self.assertEqual(normalize_arch("x86_64"), "amd64")
        self.assertEqual(normalize_arch("amd64"), "amd64")
        self.assertEqual(normalize_arch("aarch64"), "arm64")
        self.assertEqual(normalize_arch("arm64"), "arm64")
        self.assertEqual(normalize_arch("armv7l"), "armhf")
        self.assertEqual(normalize_arch("i686"), "i386")
        self.assertEqual(normalize_arch("unknown_arch"), "unknown_arch")

    def test_detect_os_from_string(self):
        # Using a custom temporary file or mocked detect_os
        import tempfile
        from pathlib import Path

        with tempfile.NamedTemporaryFile("w", delete=False) as tf:
            tf.write('ID=ubuntu\nVERSION_ID="22.04"\nPRETTY_NAME="Ubuntu 22.04 LTS"\nID_LIKE=debian\n')
            temp_path = tf.name

        try:
            os_info = detect_os(os_release_path=temp_path, arch="x86_64")
            self.assertEqual(os_info.id, "ubuntu")
            self.assertEqual(os_info.version_id, "22.04")
            self.assertEqual(os_info.architecture, "amd64")
            self.assertTrue(os_info.is_debian_family)
        finally:
            Path(temp_path).unlink(missing_ok=True)

    def test_check_compatibility_jenkins(self):
        recipe = {
            "platforms": {
                "family": "debian",
                "supported_ids": ["ubuntu", "debian", "linuxmint"],
            }
        }
        # Supported Ubuntu
        ubuntu_info = OSInfo(id="ubuntu", pretty_name="Ubuntu 24.04", id_like=["debian"])
        compat, _ = check_compatibility(ubuntu_info, recipe)
        self.assertTrue(compat)

        # Supported Linux Mint via id_like
        mint_info = OSInfo(id="linuxmint", pretty_name="Linux Mint", id_like=["ubuntu", "debian"])
        compat, _ = check_compatibility(mint_info, recipe)
        self.assertTrue(compat)

        # Incompatible Fedora
        fedora_info = OSInfo(id="fedora", pretty_name="Fedora 44", id_like=[])
        compat, msg = check_compatibility(fedora_info, recipe)
        self.assertFalse(compat)
        self.assertIn("not Debian/Ubuntu based", msg)

    def test_check_compatibility_opennebula_releases(self):
        recipe = {
            "platforms": {
                "family": "debian",
                "supported_releases": {
                    "ubuntu": ["22.04", "24.04"],
                    "debian": ["12", "13"],
                },
            }
        }
        # Compatible Ubuntu 24.04
        u24 = OSInfo(id="ubuntu", version_id="24.04", pretty_name="Ubuntu 24.04", id_like=["debian"])
        compat, _ = check_compatibility(u24, recipe)
        self.assertTrue(compat)

        # Incompatible Ubuntu release 20.04
        u20 = OSInfo(id="ubuntu", version_id="20.04", pretty_name="Ubuntu 20.04", id_like=["debian"])
        compat, msg = check_compatibility(u20, recipe)
        self.assertFalse(compat)
        self.assertIn("Release '20.04' of ubuntu is not supported", msg)

        # Compatible Debian 12
        deb12 = OSInfo(id="debian", version_id="12", pretty_name="Debian 12", id_like=[])
        compat, _ = check_compatibility(deb12, recipe)
        self.assertTrue(compat)

        # Incompatible distro (Arch Linux)
        arch = OSInfo(id="arch", version_id="", pretty_name="Arch Linux", id_like=[])
        compat, msg = check_compatibility(arch, recipe)
        self.assertFalse(compat)

    def test_check_compatibility_architecture(self):
        recipe = {
            "platforms": {
                "family": "debian",
                "supported_ids": ["ubuntu", "debian"],
                "architectures": ["amd64", "arm64"],
            }
        }
        valid_arch = OSInfo(id="ubuntu", architecture="amd64", id_like=["debian"])
        compat, _ = check_compatibility(valid_arch, recipe)
        self.assertTrue(compat)

        invalid_arch = OSInfo(id="ubuntu", architecture="riscv64", id_like=["debian"])
        compat, msg = check_compatibility(invalid_arch, recipe)
        self.assertFalse(compat)
        self.assertIn("riscv64", msg)


if __name__ == "__main__":
    unittest.main()
