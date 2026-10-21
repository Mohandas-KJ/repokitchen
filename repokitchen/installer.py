"""Secure Application and Repository Installer for RepoKitchen.

Implements verified, non-shell repository configuration and software
installation with mandatory trust validation, pre-flight safety checks,
confirmation prompts, and rollback/undo logging.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from repokitchen.command_display import (
    build_installation_plan,
    resolve_template_placeholders,
)
from repokitchen.detector import OSInfo, check_compatibility


class SecurityVerificationError(Exception):
    """Raised when cryptographic or trust verification fails."""
    pass


class InstallationError(Exception):
    """Raised when an installation command or operation fails."""
    pass


@dataclass
class InstallationResult:
    """Outcome of an installation or repository configuration operation."""
    success: bool
    recipe_id: str
    recipe_name: str
    message: str
    changes_made: List[str] = field(default_factory=list)
    undo_commands: List[str] = field(default_factory=list)
    post_install_notes: List[str] = field(default_factory=list)
    error: Optional[Exception] = None


def format_command_display(cmd: List[str]) -> str:
    """Format an argument array into a human-readable shell representation."""
    return " ".join(cmd)


def extract_key_fingerprints_gpg(key_path: Path) -> List[str]:
    """Extract primary and subkey fingerprints from a keyring or GPG key file using gpg.

    Uses `gpg --show-keys --with-colons` for machine-parseable trust verification.
    """
    cmd = ["gpg", "--show-keys", "--with-colons", str(key_path)]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as err:
        raise SecurityVerificationError(
            f"Failed to inspect key file with GPG: {err}"
        ) from err

    fingerprints: List[str] = []
    for line in proc.stdout.splitlines():
        parts = line.split(":")
        # In gpg --with-colons, record type 'fpr' contains fingerprint in field 10 (index 9)
        if len(parts) >= 10 and parts[0] == "fpr":
            fp = parts[9].strip().upper()
            if fp:
                fingerprints.append(fp)

    return fingerprints


def verify_key_fingerprint(
    key_path: Path,
    expected_fingerprint: Optional[str],
) -> None:
    """Verify that the key file matches the pinned expected primary fingerprint."""
    if not expected_fingerprint:
        raise SecurityVerificationError(
            "Security violation: No trusted fingerprint pinned in recipe. Automatic configuration blocked."
        )

    expected_clean = re_clean_fingerprint(expected_fingerprint)
    observed_fingerprints = extract_key_fingerprints_gpg(key_path)

    if not observed_fingerprints:
        raise SecurityVerificationError(
            f"No fingerprints could be read from downloaded key: {key_path}"
        )

    matched = any(
        re_clean_fingerprint(fp) == expected_clean for fp in observed_fingerprints
    )

    if not matched:
        observed_str = ", ".join(observed_fingerprints)
        raise SecurityVerificationError(
            f"Cryptographic trust verification FAILED!\n"
            f"Expected pinned fingerprint: {expected_clean}\n"
            f"Observed key fingerprints:   {observed_str}\n"
            f"Aborting repository setup immediately to prevent key spoofing."
        )


def re_clean_fingerprint(fp: str) -> str:
    """Normalize fingerprint string by removing spaces and capitalizing."""
    return "".join(fp.split()).upper()


def detect_java_home() -> str:
    """Detect Java home directory using `readlink -f $(which java)`, stripping `/bin/java`.

    Inspects the actual binary location resolved through system alternatives.
    """
    java_bin = shutil.which("java")
    if java_bin:
        try:
            proc = subprocess.run(
                ["readlink", "-f", java_bin],
                capture_output=True,
                text=True,
                check=True,
            )
            real_bin = proc.stdout.strip()
            # If path ends with /bin/java, extract parent directory of /bin
            if real_bin.endswith("/bin/java"):
                return str(Path(real_bin).parent.parent)
            elif "/bin/" in real_bin:
                return str(Path(real_bin).parent.parent)
        except Exception:
            pass

        try:
            resolved = Path(java_bin).resolve()
            if resolved.name == "java" and resolved.parent.name == "bin":
                return str(resolved.parent.parent)
        except Exception:
            pass

    # Standard distribution fallback paths on Debian/Ubuntu
    for candidate in [
        "/usr/lib/jvm/java-21-openjdk-amd64",
        "/usr/lib/jvm/java-21-openjdk-arm64",
        "/usr/lib/jvm/java-21-openjdk",
        "/usr/lib/jvm/default-java",
    ]:
        if Path(candidate).is_dir():
            return candidate

    return "/usr/lib/jvm/java-21-openjdk-amd64"


class Installer:
    """Executes validated installation plans with privilege management and safety gates."""

    def __init__(
        self,
        dry_run: bool = False,
        runner: Optional[Callable[[List[str]], subprocess.CompletedProcess]] = None,
    ):
        """Initialize installer.

        Args:
            dry_run: If True, log planned execution without altering the system.
            runner: Optional custom command runner for testing/mocking.
        """
        self.dry_run = dry_run
        self.runner = runner or self._default_runner

    def _default_runner(self, cmd: List[str]) -> subprocess.CompletedProcess:
        """Run an argument array subprocess safely without shell=True."""
        return subprocess.run(cmd, check=True)

    def _needs_sudo(self) -> bool:
        """Check if elevated permissions (sudo) are needed."""
        return os.geteuid() != 0

    def _wrap_sudo(self, cmd: List[str]) -> List[str]:
        """Prefix command with sudo if running unprivileged."""
        if self._needs_sudo() and (not cmd or cmd[0] != "sudo"):
            return ["sudo", *cmd]
        return cmd

    def execute_command(self, cmd: List[str], desc: str = "") -> None:
        """Execute a single command safely."""
        full_cmd = self._wrap_sudo(cmd)
        display_str = format_command_display(full_cmd)

        if desc:
            print(f"  ➜ {desc}")
        print(f"    $ {display_str}")

        if self.dry_run:
            print("    [DRY-RUN] Execution simulated.")
            return

        try:
            self.runner(full_cmd)
        except subprocess.CalledProcessError as err:
            raise InstallationError(
                f"Command failed with exit code {err.returncode}: {display_str}"
            ) from err

    def install(
        self,
        recipe: Dict[str, Any],
        os_info: OSInfo,
        configure_only: bool = False,
        confirm_callback: Optional[Callable[[InstallationPlan], bool]] = None,
    ) -> InstallationResult:
        """Execute installation or repository configuration for a recipe.

        Args:
            recipe: Validated recipe dictionary.
            os_info: Detected OSInfo.
            configure_only: If True, only configure the repository without installing packages.
            confirm_callback: Optional confirmation callback returning True to proceed.

        Returns:
            InstallationResult detailing outcome and changes made.
        """
        plan = build_installation_plan(recipe, os_info, configure_only=configure_only)

        # Safety Check 1: Compatibility
        if not plan.is_compatible:
            return InstallationResult(
                success=False,
                recipe_id=plan.recipe_id,
                recipe_name=plan.recipe_name,
                message=f"Installation blocked: {plan.compatibility_message}",
                undo_commands=[],
            )

        # Safety Check 2: Trust and Pinned Fingerprint
        if not plan.can_auto_install:
            return InstallationResult(
                success=False,
                recipe_id=plan.recipe_id,
                recipe_name=plan.recipe_name,
                message=f"Automatic installation blocked: {plan.auto_install_blocked_reason}",
                undo_commands=[],
            )

        # User Confirmation
        if confirm_callback is not None:
            approved = confirm_callback(plan)
            if not approved:
                return InstallationResult(
                    success=False,
                    recipe_id=plan.recipe_id,
                    recipe_name=plan.recipe_name,
                    message="Installation cancelled by user.",
                    undo_commands=[],
                )

        changes_made: List[str] = []
        try:
            if plan.method == "apt_repository":
                self._install_apt_repository(recipe, os_info, plan, configure_only, changes_made)
            elif plan.method == "verified_archive":
                self._install_verified_archive(recipe, plan, changes_made)
            elif plan.method == "native_apt_package":
                self._install_native_apt(recipe, plan, changes_made)
            else:
                raise InstallationError(f"Unsupported installer method: {plan.method}")

            action_type = "Repository configuration" if configure_only else "Installation"
            return InstallationResult(
                success=True,
                recipe_id=plan.recipe_id,
                recipe_name=plan.recipe_name,
                message=f"{action_type} for '{plan.recipe_name}' completed successfully.",
                changes_made=changes_made,
                undo_commands=plan.undo_commands,
                post_install_notes=plan.post_install_notes,
            )

        except Exception as err:
            return InstallationResult(
                success=False,
                recipe_id=plan.recipe_id,
                recipe_name=plan.recipe_name,
                message=f"Installation failed: {err}",
                changes_made=changes_made,
                undo_commands=plan.undo_commands,
                error=err,
            )

    def _install_prerequisites(self, installer: Dict[str, Any]) -> None:
        """Install declared prerequisite packages."""
        prereqs = installer.get("prerequisites", [])
        prereq_pkgs: List[str] = []
        for p in prereqs:
            if p.get("type") == "apt_packages":
                prereq_pkgs.extend(p.get("names", []))

        if prereq_pkgs:
            print("\n📦 Installing prerequisites...")
            self.execute_command(["apt-get", "update"], "Updating package lists")
            self.execute_command(
                ["apt-get", "install", "-y", *prereq_pkgs],
                f"Installing prerequisites: {', '.join(prereq_pkgs)}",
            )

    def _install_apt_repository(
        self,
        recipe: Dict[str, Any],
        os_info: OSInfo,
        plan: InstallationPlan,
        configure_only: bool,
        changes_made: List[str],
    ) -> None:
        """Handle apt_repository installation workflow."""
        installer = recipe.get("installer", {})
        repo = installer.get("repository", {})
        signing_key = installer.get("signing_key", {})

        self._install_prerequisites(installer)

        key_url = signing_key.get("url", "")
        keyring_path = signing_key.get("keyring", "")
        key_format = signing_key.get("format", "ascii-armored")
        expected_fp = signing_key.get("expected_primary_fingerprint")

        print("\n🔑 Configuring repository signing key...")
        keyring_dir = str(Path(keyring_path).parent)
        self.execute_command(
            ["install", "-m", "0755", "-d", keyring_dir],
            f"Ensuring keyring directory {keyring_dir} exists",
        )

        # Download key to secure temporary file
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_key = Path(tmp_dir) / "downloaded.key"
            if not self.dry_run:
                try:
                    urllib.request.urlretrieve(key_url, str(tmp_key))
                except Exception as err:
                    raise InstallationError(f"Failed to download signing key from {key_url}: {err}") from err

                # Verify fingerprint
                verify_key_fingerprint(tmp_key, expected_fp)
                print(f"  ✅ Pinned signing-key fingerprint verified: {expected_fp}")

                # Format dearmoring if needed
                install_src = tmp_key
                if key_format == "openpgp_dearmored":
                    dearmored = Path(tmp_dir) / "key.gpg"
                    dearmor_cmd = ["gpg", "--dearmor", "-o", str(dearmored), str(tmp_key)]
                    subprocess.run(dearmor_cmd, check=True)
                    install_src = dearmored

                # Backup existing keyring if present
                dest_path = Path(keyring_path)
                if dest_path.exists():
                    backup = dest_path.with_suffix(dest_path.suffix + ".bak")
                    self.execute_command(
                        ["cp", str(dest_path), str(backup)],
                        f"Backing up existing keyring to {backup}",
                    )
                    changes_made.append(f"Created backup of keyring: {backup}")

                # Copy into destination keyring
                self.execute_command(
                    ["install", "-m", "0644", str(install_src), keyring_path],
                    f"Installing verified signing key to {keyring_path}",
                )
            else:
                self.execute_command(
                    ["install", "-m", "0644", "/tmp/simulated_key", keyring_path],
                    f"Simulating key download & verification for {keyring_path}",
                )

            changes_made.append(f"Installed signing keyring: {keyring_path}")

        # Configure repository source file
        print("\n📝 Configuring APT sources list...")
        source_file = repo.get("source_file", "")
        source_template = repo.get("source_line_template", "")
        resolved_source = resolve_template_placeholders(
            source_template, recipe, os_info, keyring_path=keyring_path
        )

        with tempfile.NamedTemporaryFile("w", delete=False) as tmp_src:
            tmp_src.write(f"{resolved_source}\n")
            tmp_src_path = tmp_src.name

        try:
            dest_src = Path(source_file)
            if dest_src.exists() and not self.dry_run:
                backup_src = dest_src.with_suffix(dest_src.suffix + ".bak")
                self.execute_command(
                    ["cp", str(dest_src), str(backup_src)],
                    f"Backing up existing sources list to {backup_src}",
                )
                changes_made.append(f"Created backup of sources list: {backup_src}")

            self.execute_command(
                ["install", "-m", "0644", tmp_src_path, source_file],
                f"Writing repository source entry to {source_file}",
            )
            changes_made.append(f"Configured APT source list: {source_file}")
        finally:
            if os.path.exists(tmp_src_path):
                os.remove(tmp_src_path)

        # Refresh and install software packages
        if not configure_only:
            print("\n🚀 Installing software packages...")
            refresh_cmd = installer.get("refresh_command", ["apt-get", "update"])
            self.execute_command(refresh_cmd, "Refreshing APT cache with new repository")

            install_pkgs = installer.get("install_packages", [])
            if install_pkgs:
                self.execute_command(
                    ["apt-get", "install", "-y", *install_pkgs],
                    f"Installing packages: {', '.join(install_pkgs)}",
                )
                changes_made.append(f"Installed packages: {', '.join(install_pkgs)}")

    def _install_verified_archive(
        self,
        recipe: Dict[str, Any],
        plan: InstallationPlan,
        changes_made: List[str],
    ) -> None:
        """Handle verified_archive installation workflow (e.g. Apache Hadoop)."""
        installer = recipe.get("installer", {})
        self._install_prerequisites(installer)

        version = installer.get("latest_release_observed_at_recipe_creation", "3.5.0")
        artifact_tmpl = installer.get("artifact_name_template", "hadoop-{version}.tar.gz")
        sig_tmpl = installer.get("signature_name_template", "hadoop-{version}.tar.gz.asc")
        checksum_tmpl = installer.get("checksum_name_template", "hadoop-{version}.tar.gz.sha512")

        artifact_file = artifact_tmpl.replace("{version}", version)
        sig_file = sig_tmpl.replace("{version}", version)
        checksum_file = checksum_tmpl.replace("{version}", version)

        base_dl_url = f"https://downloads.apache.org/hadoop/common/hadoop-{version}"
        keys_url = installer.get("verification", {}).get(
            "keys_page", "https://downloads.apache.org/hadoop/common/KEYS"
        )
        install_loc = installer.get("install_location", "/opt")
        env_file = installer.get("environment_file", "/etc/profile.d/hadoop.sh")
        extracted_dir = f"{install_loc}/hadoop-{version}"

        print(f"\n📥 Downloading and verifying {plan.recipe_name} {version}...")

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            archive_path = tmp_path / artifact_file
            sig_path = tmp_path / sig_file
            checksum_path = tmp_path / checksum_file
            keys_path = tmp_path / "KEYS"

            if not self.dry_run:
                # Download files
                urllib.request.urlretrieve(f"{base_dl_url}/{artifact_file}", str(archive_path))
                urllib.request.urlretrieve(f"{base_dl_url}/{sig_file}", str(sig_path))
                urllib.request.urlretrieve(f"{base_dl_url}/{checksum_file}", str(checksum_path))
                urllib.request.urlretrieve(keys_url, str(keys_path))

                # GPG Verification in dedicated keyring
                gpg_home = tmp_path / "gnupg_home"
                gpg_home.mkdir(mode=0o700)
                env = os.environ.copy()
                env["GNUPGHOME"] = str(gpg_home)

                subprocess.run(
                    ["gpg", "--import", str(keys_path)],
                    env=env,
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                try:
                    subprocess.run(
                        ["gpg", "--verify", str(sig_path), str(archive_path)],
                        env=env,
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    print("  ✅ GPG release signature verified successfully.")
                except subprocess.CalledProcessError as err:
                    raise SecurityVerificationError(
                        f"GPG release signature verification failed for {artifact_file}!"
                    ) from err

                # Checksum verification
                sha_cmd = ["sha512sum", "-c", str(checksum_path)]
                try:
                    subprocess.run(
                        sha_cmd,
                        cwd=str(tmp_path),
                        check=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    print("  ✅ SHA-512 checksum verified successfully.")
                except subprocess.CalledProcessError as err:
                    raise SecurityVerificationError(
                        f"SHA-512 checksum verification failed for {artifact_file}!"
                    ) from err

            # Extract archive
            print(f"\n📂 Extracting archive to {install_loc}...")
            self.execute_command(
                ["tar", "-xzf", str(archive_path if not self.dry_run else f"/tmp/{artifact_file}"), "-C", install_loc],
                f"Extracting {artifact_file} to {install_loc}",
            )
            changes_made.append(f"Extracted software directory: {extracted_dir}")

            # Configure environment profile
            if env_file:
                java_home = detect_java_home()
                print(f"  ☕ Configuring JAVA_HOME: {java_home}")
                env_content = (
                    f"export JAVA_HOME={java_home}\n"
                    f"export HADOOP_HOME={extracted_dir}\n"
                    f"export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$JAVA_HOME/bin\n"
                )
                tmp_env = tmp_path / "env.sh"
                tmp_env.write_text(env_content)

                self.execute_command(
                    ["install", "-m", "0644", str(tmp_env if not self.dry_run else "/tmp/env.sh"), env_file],
                    f"Configuring system environment with JAVA_HOME and HADOOP_HOME in {env_file}",
                )
                changes_made.append(f"Created environment profile: {env_file} (JAVA_HOME={java_home}, HADOOP_HOME={extracted_dir})")

    def _install_native_apt(
        self,
        recipe: Dict[str, Any],
        plan: InstallationPlan,
        changes_made: List[str],
    ) -> None:
        """Handle native distribution package installation."""
        installer = recipe.get("installer", {})
        self._install_prerequisites(installer)

        print(f"\n📦 Installing native distribution package '{plan.recipe_name}'...")
        refresh_cmd = installer.get("refresh_command", ["apt-get", "update"])
        self.execute_command(refresh_cmd, "Updating distribution package lists")

        install_pkgs = installer.get("install_packages", [])
        if install_pkgs:
            self.execute_command(
                ["apt-get", "install", "-y", *install_pkgs],
                f"Installing packages: {', '.join(install_pkgs)}",
            )
            changes_made.append(f"Installed native packages: {', '.join(install_pkgs)}")
