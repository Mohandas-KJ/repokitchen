"""Command Display and Instruction Generator for RepoKitchen.

Produces complete, copy-pasteable shell instructions and step-by-step
breakdowns from validated recipe metadata for review and manual execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from repokitchen.detector import OSInfo, check_compatibility


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
            steps.append(
                CommandStep(
                    step_num=step_num,
                    title="Configure Environment Profile",
                    description=f"Set up environment variables in {env_file}.",
                    commands=[
                        f"echo 'export HADOOP_HOME={extracted_dir}' | sudo tee {env_file} > /dev/null",
                        f"echo 'export PATH=$PATH:$HADOOP_HOME/bin:$HADOOP_HOME/sbin' | sudo tee -a {env_file} > /dev/null",
                        f"sudo chmod 0644 {env_file}",
                    ],
                )
            )
            step_num += 1
            undo_commands.append(f"sudo rm -f {env_file}")

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
    """Generate a clean, copy-pasteable bash script block containing all commands."""
    lines: List[str] = [
        "#!/usr/bin/env bash",
        f"# ==============================================================================",
        f"# RepoKitchen — Installation Script for {plan.recipe_name}",
        f"# Method: {plan.method}",
        f"# Target System: {plan.os_info.pretty_name} [{plan.os_info.architecture}]",
        f"# Official Documentation: {plan.official_docs}",
        f"# ==============================================================================",
        "set -euo pipefail",
        "",
    ]

    if plan.auto_install_blocked_reason:
        lines.append(f"# NOTE / NOTICE:")
        lines.append(f"# {plan.auto_install_blocked_reason}")
        lines.append("")

    for step in plan.steps:
        lines.append(f"# --- Step {step.step_num}: {step.title} ---")
        lines.append(f"# {step.description}")
        for note in step.notes:
            lines.append(f"# Note: {note}")
        for cmd in step.commands:
            lines.append(cmd)
        lines.append("")

    lines.append(f"# --- Installation Complete ---")
    if plan.post_install_notes:
        for note in plan.post_install_notes:
            lines.append(f"# Post-install: {note}")

    return "\n".join(lines)


def format_full_view(plan: InstallationPlan) -> str:
    """Format human-readable instructions with step breakdown and copy-pasteable code block."""
    sep = "=" * 76
    sub_sep = "-" * 76
    output: List[str] = [
        sep,
        f"🍳 RepoKitchen — Installation Instructions: {plan.recipe_name}",
        sep,
        f"Category:        {plan.category}",
        f"Method:          {plan.method}",
        f"Official Docs:   {plan.official_docs}",
        f"Detected OS:     {plan.os_info.summary()}",
        f"Compatibility:   {'✅' if plan.is_compatible else '❌'} {plan.compatibility_message}",
        f"Trust Status:    {plan.trust_status}",
    ]

    if plan.auto_install_blocked_reason:
        output.extend([
            "",
            "⚠️  SAFETY / TRUST ADVISORY:",
            f"   {plan.auto_install_blocked_reason}",
        ])

    output.extend(["", sub_sep, "📋 STEP-BY-STEP BREAKDOWN", sub_sep])

    for step in plan.steps:
        output.append(f"\nStep {step.step_num}: {step.title}")
        output.append(f"  {step.description}")
        for note in step.notes:
            output.append(f"  ℹ️  {note}")
        output.append("  Commands:")
        for cmd in step.commands:
            output.append(f"    $ {cmd}")

    output.extend([
        "",
        sub_sep,
        "📋 COPY-PASTE INSTALLATION SCRIPT",
        "   (Copy and run the commands below in your terminal)",
        sub_sep,
        "",
        format_copy_paste_script(plan),
    ])

    if plan.post_install_notes:
        output.extend([
            "",
            sub_sep,
            "📌 POST-INSTALL NOTES",
            sub_sep,
        ])
        for note in plan.post_install_notes:
            output.append(f"• {note}")

    if plan.undo_commands:
        output.extend([
            "",
            sub_sep,
            "🔄 HOW TO UNDO THESE CHANGES",
            sub_sep,
        ])
        for ucmd in plan.undo_commands:
            output.append(f"  $ {ucmd}")

    output.append(sep)
    return "\n".join(output)
