"""CLI entry point and interactive user interface for RepoKitchen.

Provides both an interactive terminal menu and non-interactive subcommands
for viewing verified installation instructions and running automatic installations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from repokitchen import __version__
from repokitchen.command_display import (
    InstallationPlan,
    build_installation_plan,
    format_full_view,
)
from repokitchen.detector import OSInfo, check_compatibility, detect_os
from repokitchen.installer import InstallationResult, Installer
from repokitchen.recipe_loader import (
    RecipeValidationError,
    get_default_recipes_dir,
    group_recipes_by_category,
    load_all_recipes,
    load_recipe,
)


def confirm_prompt(plan: InstallationPlan) -> bool:
    """Display pre-flight summary and prompt user for confirmation."""
    sep = "-" * 70
    print(f"\n{sep}")
    print(f"⚠️  PRE-FLIGHT CONFIRMATION: {plan.recipe_name}")
    print(sep)
    print(f"Method:               {plan.method}")
    print(f"Target OS:            {plan.os_info.summary()}")
    print(f"Trust Status:         {plan.trust_status}")
    print(f"Privileges Required:  sudo (elevated root privileges for /etc and /usr)")

    if plan.steps:
        print("\nPlanned operations:")
        for step in plan.steps:
            print(f"  • {step.title}")

    print(sep)
    try:
        response = input("Do you wish to proceed with the automatic execution? [y/N]: ").strip().lower()
        return response in ("y", "yes")
    except (KeyboardInterrupt, EOFError):
        print("\nOperation cancelled.")
        return False


def display_result(result: InstallationResult) -> None:
    """Format and print the outcome of an installation operation."""
    sep = "=" * 70
    print(f"\n{sep}")
    if result.success:
        print(f"✅ SUCCESS: {result.message}")
        if result.changes_made:
            print("\n📁 Modifications Made:")
            for change in result.changes_made:
                print(f"   • {change}")

        if result.post_install_notes:
            print("\n📌 Post-Install Guidance:")
            for note in result.post_install_notes:
                print(f"   • {note}")

        if result.undo_commands:
            print("\n🔄 To revert or undo these changes, run:")
            for ucmd in result.undo_commands:
                print(f"   $ {ucmd}")
    else:
        print(f"❌ FAILED: {result.message}")
        if result.undo_commands and result.changes_made:
            print("\n🔄 Rollback / Clean up commands:")
            for ucmd in result.undo_commands:
                print(f"   $ {ucmd}")
    print(sep)


def handle_app_selection(
    recipe: Dict[str, Any],
    os_info: OSInfo,
    dry_run: bool = False,
) -> None:
    """Present actions for a selected application in interactive mode."""
    installer = Installer(dry_run=dry_run)

    while True:
        plan = build_installation_plan(recipe, os_info)
        app_name = plan.recipe_name
        is_compat = plan.is_compatible
        compat_icon = "✅" if is_compat else "❌"

        sep = "=" * 70
        print(f"\n{sep}")
        print(f"📦 Application: {app_name} ({plan.category})")
        print(sep)
        print(f"Description:    {recipe.get('description', '')}")
        print(f"Method:         {plan.method}")
        print(f"Official Docs:  {plan.official_docs}")
        print(f"Compatibility:  {compat_icon} {plan.compatibility_message}")
        print(f"Trust Status:   {plan.trust_status}")

        if plan.auto_install_blocked_reason:
            print(f"\n⚠️  Notice: {plan.auto_install_blocked_reason}")

        print("\nAvailable Actions:")
        print("  [1] View installation instructions (review and copy-paste complete commands)")
        can_cfg = plan.method == "apt_repository"
        if can_cfg:
            print("  [2] Configure repository only (without installing packages)")
            print("  [3] Install automatically (run all installation commands)")
        else:
            print("  [2] Install automatically (run all installation commands)")
        print("  [b] Back to catalog")
        print("  [q] Exit RepoKitchen")

        try:
            choice = input("\nSelect action: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

        if choice == "1":
            # View complete installation instructions
            print("\n" + format_full_view(plan))
            input("\nPress Enter to return to action menu...")

        elif (can_cfg and choice == "2"):
            # Configure repository only
            result = installer.install(
                recipe,
                os_info,
                configure_only=True,
                confirm_callback=confirm_prompt,
            )
            display_result(result)
            input("\nPress Enter to continue...")

        elif (can_cfg and choice == "3") or (not can_cfg and choice == "2"):
            # Install automatically
            result = installer.install(
                recipe,
                os_info,
                configure_only=False,
                confirm_callback=confirm_prompt,
            )
            display_result(result)
            input("\nPress Enter to continue...")

        elif choice == "b":
            break
        elif choice == "q":
            print("\nGoodbye!")
            sys.exit(0)
        else:
            print("Invalid choice, please select an option from the menu.")


def run_interactive(
    recipes: List[Dict[str, Any]],
    os_info: OSInfo,
    dry_run: bool = False,
) -> None:
    """Run the main interactive menu loop."""
    sep = "=" * 70
    while True:
        print(f"\n{sep}")
        print("🍳 RepoKitchen — Official sources. Fresh installs.")
        print(sep)
        print(f"Detected OS: {os_info.summary()}")
        if not os_info.is_debian_family:
            print("⚠️  Warning: RepoKitchen is tailored for Ubuntu and Debian.")
            print("   Recipes may be incompatible with the currently detected system.")
        print(sep)

        categories = group_recipes_by_category(recipes)
        app_list: List[Dict[str, Any]] = []
        item_idx = 1

        print("Catalog:")
        for cat, cat_recipes in sorted(categories.items()):
            print(f"\n  📁 {cat}:")
            for r in cat_recipes:
                is_compat, _ = check_compatibility(os_info, r)
                compat_mark = "✅" if is_compat else "⚠️ "
                print(f"    [{item_idx}] {compat_mark} {r.get('name')} — {r.get('description', '')[:55]}...")
                app_list.append(r)
                item_idx += 1

        print("\n  [q] Quit")
        try:
            choice = input(f"\nSelect an application [1-{len(app_list)}, q]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting.")
            sys.exit(0)

        if choice.lower() == "q":
            print("Goodbye!")
            break

        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(app_list):
                selected_recipe = app_list[idx - 1]
                handle_app_selection(selected_recipe, os_info, dry_run=dry_run)
            else:
                print("Invalid number. Please select a number from the menu.")
        else:
            print("Invalid input. Please enter a valid number or 'q'.")


def resolve_simulated_os(sim_arg: str) -> Optional[OSInfo]:
    """Parse simulated OS flag formatted as distro:version e.g. ubuntu:24.04."""
    if not sim_arg or ":" not in sim_arg:
        return None
    distro, version = sim_arg.split(":", 1)
    distro = distro.strip().lower()
    version = version.strip()

    name = f"{distro.capitalize()} {version}"
    id_like = ["debian"] if distro in ("ubuntu", "linuxmint", "pop") else []
    return OSInfo(
        id=distro,
        version_id=version,
        version_codename="",
        name=name,
        pretty_name=name,
        id_like=id_like,
        architecture="amd64",
    )


def create_parser() -> argparse.ArgumentParser:
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(
        prog="repokitchen",
        description="RepoKitchen — Safe Linux repository setup and software installation.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"repokitchen {__version__}",
    )
    parser.add_argument(
        "--recipes-dir",
        type=Path,
        default=None,
        help="Path to custom directory containing recipe JSON files.",
    )
    parser.add_argument(
        "--os-release",
        type=Path,
        default=None,
        help="Path to a custom os-release file for OS detection testing.",
    )
    parser.add_argument(
        "--simulate-os",
        type=str,
        default=None,
        help="Simulate an OS target release (e.g. 'ubuntu:24.04', 'debian:12').",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate commands and actions without modifying system files.",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # subcommand: list
    subparsers.add_parser("list", help="List available software recipes in the catalog.")

    # subcommand: show
    show_parser = subparsers.add_parser(
        "show",
        help="Show complete installation instructions and copy-paste commands.",
    )
    show_parser.add_argument("recipe_id", help="Application ID (e.g. jenkins, hadoop, opennebula).")

    # subcommand: check
    check_parser = subparsers.add_parser(
        "check",
        help="Check OS compatibility and cryptographic trust status.",
    )
    check_parser.add_argument("recipe_id", help="Application ID to verify.")

    # subcommand: install
    install_parser = subparsers.add_parser(
        "install",
        help="Automatically install software or configure repository.",
    )
    install_parser.add_argument("recipe_id", help="Application ID to install.")
    install_parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Do not prompt for confirmation; proceed automatically.",
    )
    install_parser.add_argument(
        "--configure-only",
        action="store_true",
        help="Only configure the repository without installing packages.",
    )

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    # Detect OS
    if args.simulate_os:
        os_info = resolve_simulated_os(args.simulate_os)
        if not os_info:
            print(f"Error: Invalid --simulate-os format '{args.simulate_os}'. Expected 'distro:version'.")
            return 1
    else:
        os_info = detect_os(os_release_path=args.os_release)

    # Load Recipes
    recipes_dir = args.recipes_dir or get_default_recipes_dir()
    try:
        recipes = load_all_recipes(recipes_dir)
    except RecipeValidationError as err:
        print(f"Recipe Validation Error: {err}", file=sys.stderr)
        return 1

    if not recipes:
        print(f"No recipes found in '{recipes_dir}'.", file=sys.stderr)
        return 1

    recipes_by_id = {r["id"]: r for r in recipes}

    # Handle Subcommands
    if args.subcommand == "list":
        print(f"\n🍳 RepoKitchen Catalog (Detected OS: {os_info.summary()}):")
        print("=" * 70)
        categories = group_recipes_by_category(recipes)
        for cat, cat_recipes in sorted(categories.items()):
            print(f"\n📁 {cat}:")
            for r in cat_recipes:
                is_compat, _ = check_compatibility(os_info, r)
                mark = "✅ Compatible" if is_compat else "❌ Incompatible"
                print(f"  • {r['id']:<14} {r['name']:<28} [{mark}] ({r.get('installer', {}).get('type', '')})")
        print("\n" + "=" * 70)
        return 0

    if args.subcommand == "show":
        recipe = recipes_by_id.get(args.recipe_id)
        if not recipe:
            print(f"Error: Unknown recipe '{args.recipe_id}'. Run 'repokitchen list' to view available recipes.", file=sys.stderr)
            return 1
        plan = build_installation_plan(recipe, os_info)
        print(format_full_view(plan))
        return 0

    if args.subcommand == "check":
        recipe = recipes_by_id.get(args.recipe_id)
        if not recipe:
            print(f"Error: Unknown recipe '{args.recipe_id}'.", file=sys.stderr)
            return 1
        plan = build_installation_plan(recipe, os_info)
        print(f"\nVerification Check: {plan.recipe_name}")
        print("-" * 50)
        print(f"Detected OS:     {os_info.summary()}")
        print(f"Compatibility:   {'✅' if plan.is_compatible else '❌'} {plan.compatibility_message}")
        print(f"Trust Status:    {plan.trust_status}")
        print(f"Can Auto-install: {'✅ Yes' if plan.can_auto_install else '❌ No'}")
        if plan.auto_install_blocked_reason:
            print(f"Blocked Reason:   {plan.auto_install_blocked_reason}")
        return 0

    if args.subcommand == "install":
        recipe = recipes_by_id.get(args.recipe_id)
        if not recipe:
            print(f"Error: Unknown recipe '{args.recipe_id}'.", file=sys.stderr)
            return 1

        installer = Installer(dry_run=args.dry_run)
        confirm_fn = None if args.yes else confirm_prompt
        result = installer.install(
            recipe,
            os_info,
            configure_only=args.configure_only,
            confirm_callback=confirm_fn,
        )
        display_result(result)
        return 0 if result.success else 1

    # Default to interactive mode if no subcommand was supplied
    run_interactive(recipes, os_info, dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
