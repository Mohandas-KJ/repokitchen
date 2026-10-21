"""Secure Application and Repository Installer for RepoKitchen.

Implements verified, non-shell repository configuration and software
installation with mandatory trust validation, pre-flight safety checks,
confirmation prompts, and rollback/undo logging.
"""

from __future__ import annotations

import getpass
import grp
import os
import pwd
import re
import shutil
import socket
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
                if Path("/usr/lib/jvm/java-21-openjdk-amd64").is_dir():
                    java_home = "/usr/lib/jvm/java-21-openjdk-amd64"
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

            # Configure Hadoop's internal hadoop-env.sh (critical for start-dfs.sh and daemons)
            hadoop_env_file = Path(extracted_dir) / "etc" / "hadoop" / "hadoop-env.sh"
            print(f"\n⚙️  Configuring JAVA_HOME in {hadoop_env_file}...")
            if not self.dry_run:
                try:
                    if hadoop_env_file.exists():
                        content = hadoop_env_file.read_text(encoding="utf-8")
                        if re.search(r"^#?\s*export JAVA_HOME=", content, re.MULTILINE):
                            new_content = re.sub(
                                r"^#?\s*export JAVA_HOME=.*",
                                f"export JAVA_HOME={java_home}",
                                content,
                                count=1,
                                flags=re.MULTILINE,
                            )
                        else:
                            new_content = content + f"\nexport JAVA_HOME={java_home}\n"

                        if f"export JAVA_HOME={java_home}" not in new_content:
                            new_content += f"\nexport JAVA_HOME={java_home}\n"

                        with tempfile.NamedTemporaryFile("w", delete=False) as tmp_h_env:
                            tmp_h_env.write(new_content)
                            tmp_h_path = tmp_h_env.name

                        try:
                            self.execute_command(
                                ["install", "-m", "0644", tmp_h_path, str(hadoop_env_file)],
                                f"Configuring JAVA_HOME={java_home} in {hadoop_env_file}",
                            )
                            changes_made.append(f"Configured JAVA_HOME in {hadoop_env_file}")
                        finally:
                            if os.path.exists(tmp_h_path):
                                os.remove(tmp_h_path)
                except Exception as err:
                    print(f"  ⚠️ Warning: Could not update {hadoop_env_file}: {err}")
            else:
                self.execute_command(
                    ["install", "-m", "0644", "/tmp/simulated_hadoop_env.sh", str(hadoop_env_file)],
                    f"Configuring JAVA_HOME in {hadoop_env_file}",
                )
                changes_made.append(f"Configured JAVA_HOME in {hadoop_env_file}")

            # Configure Hadoop core-site.xml and hdfs-site.xml for single-node HDFS networking
            core_site_file = Path(extracted_dir) / "etc" / "hadoop" / "core-site.xml"
            hdfs_site_file = Path(extracted_dir) / "etc" / "hadoop" / "hdfs-site.xml"

            core_site_content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>\n'
                '<configuration>\n'
                '    <property>\n'
                '        <name>fs.defaultFS</name>\n'
                '        <value>hdfs://localhost:9000</value>\n'
                '    </property>\n'
                '</configuration>\n'
            )

            hdfs_site_content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<?xml-stylesheet type="text/xsl" href="configuration.xsl"?>\n'
                '<configuration>\n'
                '    <property>\n'
                '        <name>dfs.replication</name>\n'
                '        <value>1</value>\n'
                '    </property>\n'
                '</configuration>\n'
            )

            print(f"\n⚙️  Configuring Hadoop HDFS cluster XML configs in {core_site_file} and {hdfs_site_file}...")
            if not self.dry_run:
                try:
                    with tempfile.NamedTemporaryFile("w", delete=False) as tmp_core:
                        tmp_core.write(core_site_content)
                        tmp_core_path = tmp_core.name
                    with tempfile.NamedTemporaryFile("w", delete=False) as tmp_hdfs:
                        tmp_hdfs.write(hdfs_site_content)
                        tmp_hdfs_path = tmp_hdfs.name

                    try:
                        self.execute_command(
                            ["install", "-m", "0644", tmp_core_path, str(core_site_file)],
                            f"Configuring fs.defaultFS=hdfs://localhost:9000 in {core_site_file}",
                        )
                        changes_made.append("Configured core-site.xml (fs.defaultFS=hdfs://localhost:9000)")

                        self.execute_command(
                            ["install", "-m", "0644", tmp_hdfs_path, str(hdfs_site_file)],
                            f"Configuring dfs.replication=1 in {hdfs_site_file}",
                        )
                        changes_made.append("Configured hdfs-site.xml (dfs.replication=1)")
                    finally:
                        if os.path.exists(tmp_core_path):
                            os.remove(tmp_core_path)
                        if os.path.exists(tmp_hdfs_path):
                            os.remove(tmp_hdfs_path)
                except Exception as err:
                    print(f"  ⚠️ Warning: Could not update Hadoop XML configuration: {err}")
            else:
                self.execute_command(
                    ["install", "-m", "0644", "/tmp/simulated_core_site.xml", str(core_site_file)],
                    f"Configuring fs.defaultFS=hdfs://localhost:9000 in {core_site_file}",
                )
                changes_made.append("Configured core-site.xml (fs.defaultFS=hdfs://localhost:9000)")
                self.execute_command(
                    ["install", "-m", "0644", "/tmp/simulated_hdfs_site.xml", str(hdfs_site_file)],
                    f"Configuring dfs.replication=1 in {hdfs_site_file}",
                )
                changes_made.append("Configured hdfs-site.xml (dfs.replication=1)")

            # Configure ~/.bashrc for immediate interactive terminal access
            target_bashrcs = [Path.home() / ".bashrc"]
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                sudo_bashrc = Path(f"/home/{sudo_user}") / ".bashrc"
                if sudo_bashrc not in target_bashrcs:
                    target_bashrcs.append(sudo_bashrc)

            for b_path in target_bashrcs:
                print(f"\n💻 Configuring user environment in {b_path}...")
                if not self.dry_run:
                    try:
                        if b_path.exists():
                            b_content = b_path.read_text(encoding="utf-8")
                            if "HADOOP_HOME" not in b_content:
                                bashrc_block = (
                                    "\n# RepoKitchen Hadoop Environment\n"
                                    f"export JAVA_HOME={java_home}\n"
                                    f"export HADOOP_HOME={extracted_dir}\n"
                                    "export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$JAVA_HOME/bin\n"
                                )
                                with b_path.open("a", encoding="utf-8") as f:
                                    f.write(bashrc_block)
                                changes_made.append(f"Configured Hadoop environment in {b_path}")
                    except Exception as err:
                        print(f"  ⚠️ Warning: Could not update {b_path}: {err}")
                else:
                    print(f"    [DRY-RUN] Appended Hadoop environment variables to {b_path}")
                    changes_made.append(f"Configured Hadoop environment in {b_path}")

            # Determine target non-root user and primary group for logs and SSH
            target_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or getpass.getuser()
            try:
                pwnam = pwd.getpwnam(target_user)
                target_group = grp.getgrgid(pwnam.pw_gid).gr_name
                user_home = Path(pwnam.pw_dir)
                target_uid = pwnam.pw_uid
                target_gid = pwnam.pw_gid
            except KeyError:
                target_group = target_user
                user_home = Path.home()
                target_uid = os.getuid()
                target_gid = os.getgid()

            # Configure Hadoop logs directory permissions
            logs_dir = Path(extracted_dir) / "logs"
            print(f"\n📁 Configuring Hadoop logs directory in {logs_dir}...")
            self.execute_command(
                ["mkdir", "-p", str(logs_dir)],
                f"Creating Hadoop logs directory {logs_dir}",
            )
            self.execute_command(
                ["chown", f"{target_user}:{target_group}", str(logs_dir)],
                f"Assigning ownership of {logs_dir} to {target_user}:{target_group}",
            )
            changes_made.append(f"Configured logs directory with ownership {target_user}:{target_group}: {logs_dir}")

            # Configure OpenSSH and Passwordless SSH for Hadoop cluster daemons
            print(f"\n🔑 Configuring OpenSSH and passwordless SSH for {target_user}...")
            self.execute_command(
                ["apt-get", "install", "-y", "openssh-server"],
                "Installing OpenSSH server for Hadoop cluster daemons",
            )
            self.execute_command(
                ["systemctl", "enable", "--now", "ssh"],
                "Enabling and starting OpenSSH service",
            )
            changes_made.append("Installed and enabled OpenSSH server")

            if not self.dry_run:
                try:
                    ssh_dir = user_home / ".ssh"
                    ssh_dir.mkdir(mode=0o700, exist_ok=True)

                    priv_key = ssh_dir / "id_rsa"
                    pub_key = ssh_dir / "id_rsa.pub"
                    if not pub_key.exists():
                        ed_pub = ssh_dir / "id_ed25519.pub"
                        if ed_pub.exists():
                            pub_key = ed_pub
                        else:
                            subprocess.run(
                                ["ssh-keygen", "-t", "rsa", "-P", "", "-f", str(priv_key)],
                                check=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )

                    if pub_key.exists():
                        pub_content = pub_key.read_text(encoding="utf-8").strip()
                        auth_keys = ssh_dir / "authorized_keys"
                        existing_auth = auth_keys.read_text(encoding="utf-8") if auth_keys.exists() else ""
                        if pub_content not in existing_auth:
                            with auth_keys.open("a", encoding="utf-8") as f:
                                f.write(f"\n{pub_content}\n")

                        auth_keys.chmod(0o600)
                        ssh_dir.chmod(0o700)

                        if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
                            os.chown(ssh_dir, target_uid, target_gid)
                            if priv_key.exists():
                                os.chown(priv_key, target_uid, target_gid)
                            if pub_key.exists():
                                os.chown(pub_key, target_uid, target_gid)
                            if auth_keys.exists():
                                os.chown(auth_keys, target_uid, target_gid)

                    # Scan localhost and hostname to known_hosts to avoid prompt on start-dfs.sh
                    try:
                        hostname = socket.gethostname()
                        known_hosts = ssh_dir / "known_hosts"
                        res = subprocess.run(
                            ["ssh-keyscan", "-H", "localhost", hostname],
                            capture_output=True,
                            text=True,
                            timeout=5,
                        )
                        if res.returncode == 0 and res.stdout.strip():
                            existing_known = known_hosts.read_text(encoding="utf-8") if known_hosts.exists() else ""
                            new_entries = [line for line in res.stdout.splitlines() if line.strip() and line not in existing_known]
                            if new_entries:
                                with known_hosts.open("a", encoding="utf-8") as f:
                                    f.write("\n" + "\n".join(new_entries) + "\n")
                            known_hosts.chmod(0o644)
                            if os.geteuid() == 0 and os.environ.get("SUDO_USER"):
                                os.chown(known_hosts, target_uid, target_gid)
                    except Exception:
                        pass

                    changes_made.append(f"Configured passwordless SSH keys and authorized_keys for {target_user}")
                except Exception as ssh_err:
                    print(f"  ⚠️ Warning: Could not configure passwordless SSH: {ssh_err}")
            else:
                print(f"    [DRY-RUN] Configured passwordless SSH for {target_user}")
                changes_made.append(f"Configured passwordless SSH for {target_user}")

            plan.post_install_notes.extend([
                "Configured 'fs.defaultFS' to 'hdfs://localhost:9000' in core-site.xml to avoid NameNode 'file:///' error.",
                "Configured 'dfs.replication' to '1' in hdfs-site.xml for single-node operation.",
                f"Configured 'export JAVA_HOME={java_home}' in {hadoop_env_file}.",
                "IMPORTANT: Run Hadoop commands as your normal user. NEVER run them with sudo!",
                "Run 'source ~/.bashrc' or open a new terminal session before starting daemons.",
                "First-time setup / clean format: Wipe old temp data and format NameNode with 'rm -rf /tmp/hadoop-* && hdfs namenode -format'.",
                "Start cluster: Run 'start-all.sh' (or 'start-dfs.sh' and 'start-yarn.sh').",
                "Stop cluster: Run 'stop-all.sh' (or 'stop-dfs.sh' and 'stop-yarn.sh').",
                "Verify cluster: Run 'jps' to confirm NameNode, DataNode, SecondaryNameNode, ResourceManager, NodeManager are running.",
                f"Logs are stored in {logs_dir} with {target_user} write permissions.",
            ])

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
