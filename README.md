# 🍳 RepoKitchen

**Official sources. Fresh installs.**

RepoKitchen is a lightweight, security-conscious Linux CLI for Ubuntu and Debian users who want to configure official third-party software repositories and install software safely—without manually searching vendor documentation and assembling complex terminal commands.

---

## 🌟 Vision & Key Objectives

When a user selects an application, RepoKitchen provides:
1. **View installation instructions**: Generates a complete, copy-pasteable script and step-by-step breakdown with official vendor URLs, verified keyrings, and security notes.
2. **Configure repository only**: Prepares and adds the APT repository and signing key without installing packages.
3. **Install automatically**: Runs verified installation operations with strict pre-flight confirmation, cryptographic fingerprint checks, and rollback/undo instructions.
4. **Safety gates**: Blocks automatic setup whenever trust metadata (such as GPG key fingerprints) is incomplete or unverified.

---

## 🐧 Supported Systems

- **Primary Targets**: Ubuntu (e.g., 22.04 LTS, 24.04 LTS) and Debian (e.g., 12 Bookworm, 13 Trixie).
- **Detection**: Automatically parses `/etc/os-release` and system architecture to evaluate compatibility.
- **Safety**: Safe error reporting and blocking when executed on unsupported distributions (e.g., Fedora, Arch).
- **Simulation**: Test and preview installation commands for different distributions using `--simulate-os <distro:version>` (e.g. `--simulate-os ubuntu:24.04`).

---

## 📦 Software Catalog

| Application | Category | Method | Trust / Fingerprint Status | Automatic Install |
| :--- | :--- | :--- | :--- | :---: |
| **Jenkins LTS** | DevOps | `apt_repository` | Pinned Primary Fingerprint (`5E386EADB55F...`) | ✅ Supported |
| **OpenNebula CE** | Cloud | `apt_repository` | Unpinned Fingerprint (Policy: Blocked) | ❌ Blocked (Manual only) |
| **Apache Hadoop** | Big Data | `verified_archive` | Official Apache GPG Signature & SHA-512 | ✅ Supported |
| **Ansible** | Automation | `native_apt_package`| Standard Distribution Package Repository | ✅ Supported |

---

## 🚀 Getting Started

RepoKitchen requires Python 3.10+ and uses only the Python Standard Library.

### Running Directly
```bash
# Launch interactive menu
python3 -m repokitchen.main

# List available recipes and compatibility
python3 -m repokitchen.main list

# View complete installation instructions and copy-paste script
python3 -m repokitchen.main show jenkins

# Check OS compatibility and cryptographic trust status
python3 -m repokitchen.main check opennebula

# Simulate automatic installation (dry-run)
python3 -m repokitchen.main install jenkins --dry-run
```

### Installation via pip
```bash
pip install .
repokitchen
```

---

## 💻 CLI Usage

```text
usage: repokitchen [-h] [--version] [--recipes-dir RECIPES_DIR]
                   [--os-release OS_RELEASE] [--simulate-os SIMULATE_OS]
                   [--dry-run]
                   {list,show,check,install} ...
```

### Subcommands
- `repokitchen list`: Display all catalog applications, categories, installation methods, and compatibility with the current system.
- `repokitchen show <recipe_id>`: Show complete step-by-step installation instructions, prerequisite commands, and a ready-to-run copy-paste bash script.
- `repokitchen check <recipe_id>`: Check whether the application is compatible with the detected OS and verify its cryptographic trust status.
- `repokitchen install <recipe_id>`: Automatically run installation commands with pre-flight confirmation.
  - `--configure-only`: Only configure the repository and keyring without installing packages.
  - `-y`, `--yes`: Non-interactive mode (skip interactive confirmation).
  - `--dry-run`: Simulate all commands and modifications without altering system files.

---

## 🛡️ Security Model

RepoKitchen enforces strict security invariants:
1. **Official Sources Only**: Every recipe links to official documentation and official vendor download endpoints.
2. **Strict HTTPS**: Insecure HTTP endpoints are strictly rejected during recipe validation.
3. **No Deprecated `apt-key`**: Repositories use modern per-repository keyrings located in `/usr/share/keyrings` or `/etc/apt/keyrings`, bound via `signed-by=...` in source lists.
4. **Cryptographic Fingerprint Pinning**: Downloaded GPG keys are inspected with GPG and matched against independently pinned fingerprints before being written to disk.
5. **Trust Gatekeeping**: If a recipe does not have an independently verified and pinned fingerprint (such as OpenNebula's `repo2.key`), automatic repository setup is deliberately blocked, prompting the user to review manual commands.
6. **No Arbitrary Shell Scripts**: Recipes are declarative JSON files, not executable shell scripts. Execution uses safe subprocess argument arrays without `shell=True`.
7. **Path Traversal Protection**: All filesystem destinations (`sources.list.d`, keyrings, `/opt`) are strictly validated against directory traversal (`..`) and disallowed path prefixes.
8. **Transparent Privileges & Rollback**: Elevated operations (`sudo`) are never invoked invisibly. Pre-flight summaries are displayed, and rollback/undo commands are printed upon completion.

---

## 📖 How to Add a Recipe

Recipes are stored as JSON files under `recipes/<id>.json`. Each recipe must conform to `schema_version: 1`:

```json
{
  "schema_version": 1,
  "id": "mytool",
  "name": "My Tool",
  "category": "Utilities",
  "description": "Short description of the software.",
  "official_docs": "https://example.com/docs",
  "platforms": {
    "family": "debian",
    "supported_ids": ["ubuntu", "debian"]
  },
  "installer": {
    "type": "apt_repository",
    "repository": {
      "name": "My Tool Repository",
      "uri": "https://pkg.example.com/debian",
      "source_file": "/etc/apt/sources.list.d/mytool.list",
      "source_line_template": "deb [signed-by={keyring}] https://pkg.example.com/debian stable main"
    },
    "signing_key": {
      "url": "https://pkg.example.com/key.gpg",
      "keyring": "/usr/share/keyrings/mytool-keyring.gpg",
      "format": "ascii-armored",
      "expected_primary_fingerprint": "1234567890ABCDEF1234567890ABCDEF12345678"
    },
    "prerequisites": [
      {
        "type": "apt_packages",
        "names": ["ca-certificates", "curl"]
      }
    ],
    "install_packages": ["mytool"]
  },
  "actions": {
    "configure_only": true,
    "install_after_configure": true,
    "post_install_notes": ["Run 'mytool --version' to test."]
  },
  "security": {
    "require_https": true
  }
}
```

---

## 🧪 Testing

RepoKitchen includes a comprehensive test suite covering OS detection, recipe validation, command generation, installer security gates, and CLI operation:

```bash
python3 -m unittest discover -s tests -v
```

All tests run completely offline and require no elevated/root privileges.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
