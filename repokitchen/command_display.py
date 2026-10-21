"""Command Display and Instruction Generator for RepoKitchen.

Produces complete, copy-pasteable shell instructions and step-by-step
breakdowns from validated recipe metadata for review and manual execution,
rendered with clean Unicode boxes and terminal styling.
"""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from repokitchen.detector import OSInfo, check_compatibility

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE_RE.sub("", text)


def str_display_width(text: str) -> int:
    """Compute the visual display width of a string in a monospace terminal.

    Accounts for full-width characters and emojis that occupy two terminal cells.
    """
    clean = strip_ansi(text)
    width = 0
    for char in clean:
        if unicodedata.east_asian_width(char) in ("W", "F"):
            width += 2
        elif 0x1F300 <= ord(char) <= 0x1FAFF or 0x2600 <= ord(char) <= 0x27BF:
            width += 2
        else:
            width += 1
    return width


class Style:
    """ANSI terminal styling and color codes."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    UNDERLINE = "\033[4m"

    BLACK = "\033[30m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

    GRAY = "\033[90m"
    BRIGHT_RED = "\033[91m"
    BRIGHT_GREEN = "\033[92m"
    BRIGHT_YELLOW = "\033[93m"
    BRIGHT_BLUE = "\033[94m"
    BRIGHT_MAGENTA = "\033[95m"
    BRIGHT_CYAN = "\033[96m"
    BRIGHT_WHITE = "\033[97m"


def should_use_color(force_color: Optional[bool] = None) -> bool:
    """Determine whether ANSI color escape codes should be applied."""
    if force_color is not None:
        return force_color
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def render_box(
    lines: List[str],
    title: str = "",
    min_width: int = 78,
    border_color: str = Style.CYAN,
    title_color: str = Style.BOLD + Style.BRIGHT_WHITE,
    use_color: bool = True,
    pad_vertical: bool = False,
) -> str:
    """Render a block of text lines enclosed in a rounded Unicode box frame.

    Args:
        lines: Content lines to render inside the box.
        title: Optional title string embedded in the top border.
        min_width: Minimum width for the box frame.
        border_color: ANSI code for the border.
        title_color: ANSI code for the title.
        use_color: Whether to include ANSI escape sequences.
        pad_vertical: Whether to add empty lines at top and bottom inside the box.
    """
    bc = border_color if use_color else ""
    tc = title_color if use_color else ""
    rst = Style.RESET if use_color else ""

    plain_title = strip_ansi(title)
    title_w = str_display_width(plain_title) if plain_title else 0
    content_w = max([str_display_width(l) for l in lines] + [title_w + 6], default=0)
    w = max(min_width, content_w + 4)

    output = []
    if plain_title:
        fill_right = w - title_w - 6
        top_line = f"{bc}╭── {tc}{title}{rst}{bc} {'─' * max(0, fill_right)}╮{rst}"
    else:
        top_line = f"{bc}╭{'─' * (w - 2)}╮{rst}"
    output.append(top_line)

    if pad_vertical:
        output.append(f"{bc}│{rst}{' ' * (w - 2)}{bc}│{rst}")

    for line in lines:
        disp_w = str_display_width(line)
        pad = " " * max(0, w - 4 - disp_w)
        output.append(f"{bc}│{rst} {line}{pad} {bc}│{rst}")

    if pad_vertical:
        output.append(f"{bc}│{rst}{' ' * (w - 2)}{bc}│{rst}")

    bot_line = f"{bc}╰{'─' * (w - 2)}╯{rst}"
    output.append(bot_line)
    return "\n".join(output)


@dataclass
class CommandStep:
    """Represents a single step in the installation process."""
    step_num: int
    title: str
    description: str
    commands: List[str]
    notes: List[str] = field(default_factory=list)


@dataclass
class InstallationPlan:
    """Complete structured installation plan for an application."""
    recipe_id: str
    recipe_name: str
    category: str
    method: str
    official_docs: str
    os_info: OSInfo
    is_compatible: bool
    compatibility_message: str
    trust_status: str
    can_auto_install: bool
    auto_install_blocked_reason: Optional[str]
    steps: List[CommandStep]
    post_install_notes: List[str]
    undo_commands: List[str]


def resolve_template_placeholders(
    template: str,
    recipe: Dict[str, Any],
    os_info: OSInfo,
    keyring_path: str = "",
) -> str:
    """Resolve placeholders like {keyring}, {distribution}, and {release} in templates."""
    installer = recipe.get("installer", {})
    repo = installer.get("repository", {})
    dist_map = repo.get("distribution_map", {})

    dist_name = dist_map.get(os_info.id, os_info.id.capitalize() if os_info.id != "unknown" else "Ubuntu")
    release_val = os_info.version_id if os_info.version_id else "24.04"

    result = template
    result = result.replace("{keyring}", keyring_path)
    result = result.replace("{distribution}", dist_name)
    result = result.replace("{release}", release_val)
    return result


def build_installation_plan(
    recipe: Dict[str, Any],
    os_info: OSInfo,
    configure_only: bool = False,
) -> InstallationPlan:
    """Construct an InstallationPlan from a recipe and detected OS.

    Args:
        recipe: Validated recipe dictionary.
        os_info: Detected OSInfo.
        configure_only: If True, only repository setup commands are generated.

    Returns:
        Structured InstallationPlan.
    """
    app_id = recipe.get("id", "")
    name = recipe.get("name", app_id)
    category = recipe.get("category", "General")
    official_docs = recipe.get("official_docs", "")
    installer = recipe.get("installer", {})
    method = installer.get("type", "unknown")
    actions = recipe.get("actions", {})
    post_install_notes = list(actions.get("post_install_notes", []))

    is_compatible, compat_msg = check_compatibility(os_info, recipe)

    # Assess trust status and automatic installation eligibility
    trust_status = "Verified"
    can_auto_install = is_compatible
    blocked_reason = None

    if method == "apt_repository":
        signing_key = installer.get("signing_key", {})
        expected_fp = signing_key.get("expected_primary_fingerprint")
        policy = signing_key.get("verification_policy")

        if policy == "BLOCK_AUTOMATIC_CONFIGURATION_UNTIL_FINGERPRINT_IS_INDEPENDENTLY_PINNED" or not expected_fp:
            trust_status = "UNPINNED FINGERPRINT (BLOCKED)"
            can_auto_install = False
            blocked_reason = (
                f"Automatic repository configuration is blocked because the signing key fingerprint "
                f"for '{name}' has not been independently pinned. Manual review is required."
            )
        else:
            trust_status = f"Pinned primary fingerprint: {expected_fp}"

    elif method == "verified_archive":
        trust_status = "Official Apache PGP signature / SHA512 checksum required before extraction"

    elif method == "native_apt_package":
        trust_status = "Official distribution standard repository package"

    if not is_compatible:
        can_auto_install = False
        blocked_reason = compat_msg

    steps: List[CommandStep] = []
    undo_commands: List[str] = []
    step_num = 1

    # Step: Prerequisites
    prereqs = installer.get("prerequisites", [])
    prereq_pkgs: List[str] = []
    for p in prereqs:
        if p.get("type") == "apt_packages":
            prereq_pkgs.extend(p.get("names", []))

    if prereq_pkgs:
        pkgs_str = " ".join(prereq_pkgs)
        steps.append(
            CommandStep(
                step_num=step_num,
                title="Install Prerequisites",
                description="Update package index and install required dependencies.",
                commands=[
                    "sudo apt-get update",
                    f"sudo apt-get install -y {pkgs_str}",
                ],
            )
        )
        step_num += 1

    # Method-specific steps
    if method == "apt_repository":
        repo = installer.get("repository", {})
        signing_key = installer.get("signing_key", {})
        key_url = signing_key.get("url", "")
        keyring_path = signing_key.get("keyring", "")
        key_format = signing_key.get("format", "ascii-armored")
        expected_fp = signing_key.get("expected_primary_fingerprint")

        keyring_dir = str(Path(keyring_path).parent)
        key_commands = [
            f"sudo install -m 0755 -d {keyring_dir}",
        ]
        if key_format == "openpgp_dearmored":
            key_commands.append(
                f"wget -qO- {key_url} | gpg --dearmor | sudo tee {keyring_path} > /dev/null"
            )
        else:
            key_commands.append(
                f"sudo curl -fsSL -o {keyring_path} {key_url}"
            )
        key_commands.append(f"sudo chmod 0644 {keyring_path}")

        key_notes = []
        if expected_fp:
            key_commands.append(f"gpg --show-keys {keyring_path}")
            key_notes.append(f"Verify fingerprint matches: {expected_fp}")
        else:
            key_notes.append("WARNING: Fingerprint is not yet pinned. Verify independently against official docs.")

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Import Official Signing Key",
                description="Securely download and install vendor repository signing key.",
                commands=key_commands,
                notes=key_notes,
            )
        )
        step_num += 1
        undo_commands.append(f"sudo rm -f {keyring_path}")

        # Repository configuration
        source_file = repo.get("source_file", "")
        source_template = repo.get("source_line_template", "")
        resolved_source = resolve_template_placeholders(
            source_template, recipe, os_info, keyring_path=keyring_path
        )

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Configure APT Repository Source",
                description=f"Add repository definition to {source_file}.",
                commands=[
                    f"echo '{resolved_source}' | sudo tee {source_file} > /dev/null"
                ],
            )
        )
        step_num += 1
        undo_commands.append(f"sudo rm -f {source_file}")

        # Install packages (unless configure_only)
        if not configure_only:
            refresh_cmd = installer.get("refresh_command", ["apt-get", "update"])
            refresh_str = " ".join(["sudo", *refresh_cmd])

            install_pkgs = installer.get("install_packages", [])
            install_cmds = [refresh_str]
            if install_pkgs:
                pkgs_str = " ".join(install_pkgs)
                install_cmds.append(f"sudo apt-get install -y {pkgs_str}")
                undo_commands.append(f"sudo apt-get remove --purge -y {pkgs_str}")

            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Update APT Index and Install Software",
                    description="Refresh repository cache and install application packages.",
                    commands=install_cmds,
                )
            )
            step_num += 1

        undo_commands.append("sudo apt-get update")

    elif method == "verified_archive":
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

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Download Release Archive and Verification Files",
                description="Fetch the official Apache release archive, detached GPG signature, and checksum.",
                commands=[
                    f"curl -fsSLO {base_dl_url}/{artifact_file}",
                    f"curl -fsSLO {base_dl_url}/{sig_file}",
                    f"curl -fsSLO {base_dl_url}/{checksum_file}",
                ],
            )
        )
        step_num += 1

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Verify Release Signature and Checksum",
                description="Import official Apache developer keys and verify the archive authenticity before extraction.",
                commands=[
                    f"curl -fsSL {keys_url} | gpg --import",
                    f"gpg --verify {sig_file} {artifact_file}",
                    f"sha512sum -c {checksum_file}",
                ],
                notes=[
                    "Never extract or run an archive if signature or checksum verification fails."
                ],
            )
        )
        step_num += 1

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Extract Archive to Target Location",
                description=f"Extract verified archive into {install_loc}.",
                commands=[
                    f"sudo tar -xzf {artifact_file} -C {install_loc}",
                ],
            )
        )
        step_num += 1
        undo_commands.append(f"sudo rm -rf {extracted_dir}")

        if env_file:
            hadoop_env_file = f"{extracted_dir}/etc/hadoop/hadoop-env.sh"
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Configure Environment Profile and JAVA_HOME",
                    description=f"Configure JAVA_HOME in {hadoop_env_file}, {env_file}, and ~/.bashrc.",
                    commands=[
                        'if [ -d "/usr/lib/jvm/java-21-openjdk-amd64" ]; then JAVA_HOME_DIR="/usr/lib/jvm/java-21-openjdk-amd64"; else JAVA_BIN=$(readlink -f $(which java) 2>/dev/null || true); JAVA_HOME_DIR=$(dirname $(dirname "$JAVA_BIN") 2>/dev/null || true); [ -n "$JAVA_HOME_DIR" ] && [ -d "$JAVA_HOME_DIR" ] || JAVA_HOME_DIR="/usr/lib/jvm/java-21-openjdk-amd64"; fi',
                        f"sudo sed -i 's|^#\\?\\s*export JAVA_HOME=.*|export JAVA_HOME='\"$JAVA_HOME_DIR\"'|' {hadoop_env_file}",
                        f"grep -q 'export JAVA_HOME=' {hadoop_env_file} || echo \"export JAVA_HOME=$JAVA_HOME_DIR\" | sudo tee -a {hadoop_env_file} > /dev/null",
                        f"echo \"export JAVA_HOME=$JAVA_HOME_DIR\" | sudo tee {env_file} > /dev/null",
                        f"echo 'export HADOOP_HOME={extracted_dir}' | sudo tee -a {env_file} > /dev/null",
                        f"echo 'export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$JAVA_HOME/bin' | sudo tee -a {env_file} > /dev/null",
                        f"sudo chmod 0644 {env_file}",
                        f"grep -q 'HADOOP_HOME' ~/.bashrc || printf '\\n# Hadoop Environment\\nexport JAVA_HOME=%s\\nexport HADOOP_HOME={extracted_dir}\\nexport PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin:$JAVA_HOME/bin\\n' \"$JAVA_HOME_DIR\" >> ~/.bashrc",
                        "source ~/.bashrc",
                    ],
                    notes=[
                        "Automatically detects and sets JAVA_HOME (preferring /usr/lib/jvm/java-21-openjdk-amd64).",
                        f"Configures export JAVA_HOME directly in {hadoop_env_file} so Hadoop daemons and subshells execute properly.",
                        "Configures ~/.bashrc and sources it so Hadoop commands are immediately active in your current shell.",
                    ],
                )
            )
            step_num += 1
            undo_commands.append(f"sudo rm -f {env_file}")

        if app_id == "hadoop":
            # Step 6: Configure core-site.xml and hdfs-site.xml
            core_site_path = f"{extracted_dir}/etc/hadoop/core-site.xml"
            hdfs_site_path = f"{extracted_dir}/etc/hadoop/hdfs-site.xml"
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Configure Hadoop Cluster Endpoints (core-site.xml and hdfs-site.xml)",
                    description="Set fs.defaultFS to hdfs://localhost:9000 and dfs.replication to 1 for single-node cluster.",
                    commands=[
                        f"printf '<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<configuration>\\n    <property>\\n        <name>fs.defaultFS</name>\\n        <value>hdfs://localhost:9000</value>\\n    </property>\\n</configuration>\\n' | sudo tee {core_site_path} > /dev/null",
                        f"printf '<?xml version=\"1.0\" encoding=\"UTF-8\"?>\\n<configuration>\\n    <property>\\n        <name>dfs.replication</name>\\n        <value>1</value>\\n    </property>\\n</configuration>\\n' | sudo tee {hdfs_site_path} > /dev/null",
                    ],
                    notes=[
                        "Configures fs.defaultFS=hdfs://localhost:9000 in core-site.xml to avoid 'Invalid URI for NameNode address (file:///)' error.",
                        "Configures dfs.replication=1 in hdfs-site.xml for pseudo-distributed single-node cluster operation.",
                    ],
                )
            )
            step_num += 1

            # Step 7: Log directory permissions
            logs_dir = f"{extracted_dir}/logs"
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Configure Hadoop Log Directory Permissions",
                    description=f"Create {logs_dir} and assign ownership to your regular user account.",
                    commands=[
                        f"sudo mkdir -p {logs_dir}",
                        f'sudo chown "$USER":"$(id -gn)" {logs_dir}',
                    ],
                    notes=[
                        f"Hadoop startup scripts run as your regular user and require write access to {logs_dir}.",
                        "Assigning ownership specifically to the logs directory preserves root security across the rest of the installation.",
                    ],
                )
            )
            step_num += 1

            # Step 8: Passwordless SSH configuration
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Configure Passwordless SSH for Hadoop Daemons",
                    description="Install OpenSSH server and authorize your user key for localhost and VM hostname.",
                    commands=[
                        "sudo apt-get install -y openssh-server",
                        "sudo systemctl enable --now ssh",
                        "mkdir -p ~/.ssh",
                        '[ -f ~/.ssh/id_rsa ] || ssh-keygen -t rsa -P "" -f ~/.ssh/id_rsa',
                        "touch ~/.ssh/authorized_keys",
                        'grep -q -F "$(cat ~/.ssh/id_rsa.pub 2>/dev/null)" ~/.ssh/authorized_keys 2>/dev/null || cat ~/.ssh/id_rsa.pub >> ~/.ssh/authorized_keys',
                        "chmod 700 ~/.ssh",
                        "chmod 600 ~/.ssh/authorized_keys",
                        "ssh-keyscan -H localhost $(hostname) >> ~/.ssh/known_hosts 2>/dev/null || true",
                    ],
                    notes=[
                        "Hadoop's 'start-dfs.sh' and 'start-yarn.sh' use SSH to start daemons on localhost and the machine hostname.",
                        "Passwordless SSH enables cluster daemons to start without pausing for password authentication.",
                        "Keyscan populates known_hosts to prevent interactive host verification prompts during cluster startup.",
                    ],
                )
            )
            step_num += 1

            # Step 9: Format NameNode and Start Single-Node Cluster
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Format NameNode and Start Single-Node Cluster",
                    description="Clean old metadata, format the HDFS filesystem, and launch all cluster daemons.",
                    commands=[
                        "source ~/.bashrc",
                        'ssh -o BatchMode=yes localhost echo "SSH localhost verified successfully!"',
                        "stop-all.sh 2>/dev/null || true",
                        "rm -rf /tmp/hadoop-*",
                        "hdfs namenode -format -force",
                        "start-all.sh",
                        "jps",
                    ],
                    notes=[
                        "CRITICAL: Never run 'sudo start-all.sh' or 'sudo start-dfs.sh'! Always run them as your normal user.",
                        "Stops running daemons, clears old corrupted filesystem metadata (/tmp/hadoop-*), formats NameNode, and boots cluster.",
                        "Verify active daemons with: 'jps' (expect NameNode, DataNode, SecondaryNameNode, ResourceManager, NodeManager).",
                    ],
                )
            )
            step_num += 1

            post_install_notes.extend([
                "Configured 'fs.defaultFS' to 'hdfs://localhost:9000' in /opt/hadoop-3.5.0/etc/hadoop/core-site.xml.",
                "Configured 'dfs.replication' to '1' in /opt/hadoop-3.5.0/etc/hadoop/hdfs-site.xml for single-node operation.",
                f"Configured 'export JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64' in {hadoop_env_file}.",
                "IMPORTANT: Run Hadoop commands as your normal user. NEVER run them with sudo!",
                "First-time setup / clean format: Wipe old temp data and format NameNode with 'rm -rf /tmp/hadoop-* && hdfs namenode -format'.",
                "Start cluster: Run 'start-all.sh' (or 'start-dfs.sh' and 'start-yarn.sh').",
                "Stop cluster: Run 'stop-all.sh' (or 'stop-dfs.sh' and 'stop-yarn.sh').",
                "Verify cluster: Run 'jps' to confirm NameNode, DataNode, SecondaryNameNode, ResourceManager, NodeManager are running.",
                f"Logs are stored in {extracted_dir}/logs with regular user write permissions.",
            ])

    elif method == "native_apt_package":
        refresh_cmd = installer.get("refresh_command", ["apt-get", "update"])
        refresh_str = " ".join(["sudo", *refresh_cmd])
        install_pkgs = installer.get("install_packages", [])
        pkgs_str = " ".join(install_pkgs)

        steps.append(
            CommandStep(
                step_num=step_num,
                title="Update APT Index and Install Native Package",
                description="Install package directly from standard distribution repositories.",
                commands=[
                    refresh_str,
                    f"sudo apt-get install -y {pkgs_str}",
                ],
            )
        )
        step_num += 1
        undo_commands.append(f"sudo apt-get remove --purge -y {pkgs_str}")

    return InstallationPlan(
        recipe_id=app_id,
        recipe_name=name,
        category=category,
        method=method,
        official_docs=official_docs,
        os_info=os_info,
        is_compatible=is_compatible,
        compatibility_message=compat_msg,
        trust_status=trust_status,
        can_auto_install=can_auto_install,
        auto_install_blocked_reason=blocked_reason,
        steps=steps,
        post_install_notes=post_install_notes,
        undo_commands=undo_commands,
    )


def format_copy_paste_script(plan: InstallationPlan) -> str:
    """Generate a clean, copy-pasteable bash script containing only valid commands and concise comments."""
    lines: List[str] = [
        "#!/usr/bin/env bash",
        f"# RepoKitchen — Installation Script for {plan.recipe_name}",
        f"# Method: {plan.method} | Target: {plan.os_info.pretty_name} [{plan.os_info.architecture}]",
        f"# Official Documentation: {plan.official_docs}",
        "set -euo pipefail",
        "",
    ]

    if plan.auto_install_blocked_reason:
        lines.append(f"# Advisory: {plan.auto_install_blocked_reason}")
        lines.append("")

    for step in plan.steps:
        note_str = f" ({step.notes[0]})" if step.notes else ""
        lines.append(f"# Step {step.step_num}: {step.title}{note_str}")
        for cmd in step.commands:
            lines.append(cmd)
        lines.append("")

    if plan.post_install_notes:
        lines.append("# Post-install Notes:")
        for note in plan.post_install_notes:
            lines.append(f"# - {note}")

    return "\n".join(lines).strip()


def format_full_view(plan: InstallationPlan, use_color: Optional[bool] = None) -> str:
    """Format human-readable instructions with distinguished visual sections and boxed commands.

    Args:
        plan: Structured installation plan.
        use_color: Optional override for ANSI color styling. If None, checks TTY.

    Returns:
        Formatted multi-line text with boxes and styled sections.
    """
    color = should_use_color(use_color)

    rst = Style.RESET if color else ""
    bold = Style.BOLD if color else ""
    cyan = Style.CYAN if color else ""
    b_cyan = Style.BRIGHT_CYAN if color else ""
    b_white = Style.BRIGHT_WHITE if color else ""
    gray = Style.GRAY if color else ""
    green = Style.GREEN if color else ""
    b_green = Style.BRIGHT_GREEN if color else ""
    yellow = Style.YELLOW if color else ""
    b_yellow = Style.BRIGHT_YELLOW if color else ""

    output: List[str] = []

    # 1. Overview Panel (Hero Box)
    compat_icon = "✅" if plan.is_compatible else "❌"
    overview_lines = [
        f"{b_cyan}Application:{rst}    {b_white}{plan.recipe_name}{rst}",
        f"{b_cyan}Category:{rst}       {b_white}{plan.category}{rst}",
        f"{b_cyan}Method:{rst}         {b_white}{plan.method}{rst}",
        f"{b_cyan}Official Docs:{rst}  {b_white}{plan.official_docs}{rst}",
        f"{b_cyan}Detected OS:{rst}    {b_white}{plan.os_info.summary()}{rst}",
        f"{b_cyan}Compatibility:{rst}  {compat_icon} {plan.compatibility_message}",
        f"{b_cyan}Trust Status:{rst}   {plan.trust_status}",
    ]
    output.append(
        render_box(
            overview_lines,
            title=f"🍳 RepoKitchen — Installation Instructions: {plan.recipe_name}",
            border_color=Style.CYAN,
            title_color=Style.BOLD + Style.BRIGHT_WHITE,
            use_color=color,
            pad_vertical=True,
        )
    )

    # 2. Safety / Trust Advisory Panel (if auto-install blocked)
    if plan.auto_install_blocked_reason:
        output.append("")
        advisory_lines = [
            f"{yellow}{plan.auto_install_blocked_reason}{rst}"
        ]
        output.append(
            render_box(
                advisory_lines,
                title="⚠️  SAFETY / TRUST ADVISORY",
                border_color=Style.BRIGHT_YELLOW,
                title_color=Style.BOLD + Style.BRIGHT_YELLOW,
                use_color=color,
                pad_vertical=True,
            )
        )

    # 3. Step-by-Step Breakdown Section
    sep = "═" * 78
    output.append("")
    output.append(f"{cyan}{sep}{rst}")
    output.append(f"  {bold}{b_cyan}📋 STEP-BY-STEP BREAKDOWN{rst}")
    output.append(f"{cyan}{sep}{rst}")

    for step in plan.steps:
        output.append("")
        output.append(f"{bold}{b_cyan}▶ Step {step.step_num} of {len(plan.steps)}: {step.title}{rst}")
        output.append(f"  {step.description}")
        for note in step.notes:
            output.append(f"  {yellow}ℹ️  Note: {note}{rst}")

        # Render commands inside a dedicated, highlighted box
        cmd_lines = [f"{gray}${rst} {b_white}{cmd}{rst}" for cmd in step.commands]
        output.append(
            render_box(
                cmd_lines,
                title=f"Commands to run (Step {step.step_num})",
                border_color=Style.BLUE,
                title_color=Style.BOLD + Style.BRIGHT_CYAN,
                use_color=color,
            )
        )

    # 4. Copy-Paste Script Block
    output.append("")
    output.append(f"{cyan}{sep}{rst}")
    output.append(f"  {bold}{b_cyan}📋 COPY-PASTE INSTALLATION SCRIPT{rst}")
    output.append(f"  {gray}(Copy and run the complete script block below in your terminal){rst}")
    output.append(f"{cyan}{sep}{rst}")
    output.append("")

    raw_script = format_copy_paste_script(plan)
    styled_script_lines = []
    for s_line in raw_script.splitlines():
        if s_line.startswith("#"):
            styled_script_lines.append(f"{gray}{s_line}{rst}")
        elif s_line.startswith("sudo ") or s_line.startswith("curl ") or s_line.startswith("gpg ") or s_line.startswith("set "):
            styled_script_lines.append(f"{b_green}{s_line}{rst}")
        else:
            styled_script_lines.append(f"{b_white}{s_line}{rst}")

    output.append(
        render_box(
            styled_script_lines,
            title="Complete Bash Script (Copy & Paste)",
            border_color=Style.BRIGHT_CYAN,
            title_color=Style.BOLD + Style.BRIGHT_WHITE,
            use_color=color,
        )
    )

    # 5. Post-Install Notes Section
    if plan.post_install_notes:
        output.append("")
        post_lines = [f"• {note}" for note in plan.post_install_notes]
        output.append(
            render_box(
                post_lines,
                title="📌 POST-INSTALL NOTES",
                border_color=Style.MAGENTA,
                title_color=Style.BOLD + Style.BRIGHT_MAGENTA,
                use_color=color,
                pad_vertical=True,
            )
        )

    # 6. Revert / Undo Changes Section
    if plan.undo_commands:
        output.append("")
        undo_lines = [f"{gray}${rst} {b_white}{ucmd}{rst}" for ucmd in plan.undo_commands]
        output.append(
            render_box(
                undo_lines,
                title="🔄 HOW TO UNDO THESE CHANGES",
                border_color=Style.YELLOW,
                title_color=Style.BOLD + Style.BRIGHT_YELLOW,
                use_color=color,
            )
        )

    return "\n".join(output)
