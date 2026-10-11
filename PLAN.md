# RepoKitchen — Initial Implementation Plan

## 1. Project vision

RepoKitchen is a small, security-conscious CLI for Ubuntu/Debian users who want to configure official third-party software repositories and optionally install the software—without manually searching vendor documentation and assembling commands.

When a user selects an application, RepoKitchen offers:
1. **Configure repository / prepare installation**
2. **Show the commands** for the user to review and run manually
3. **Install automatically** (after showing the planned changes and asking for confirmation)
4. **Back**

RepoKitchen must not pretend every application uses an APT repository. Some software is available from Ubuntu/Debian's own repositories; other software is distributed as verified archives.

## 2. Initial scope

### Target platform
- Ubuntu and Debian first.
- Detect distribution ID, version, and codename from `/etc/os-release`.
- If the OS version is unsupported or cannot be detected, stop safely and explain why.
- Do not assume every Ubuntu release is compatible with every Debian repository, or vice versa.

### Initial catalog
- **Jenkins LTS** — official APT repository. (Systematically follow recipes/jenkins.json)
- **OpenNebula Community Edition** — official APT repository, only for explicitly supported OS releases. (Systematically follow recipes/opennebula.json)
- **Apache Hadoop** — official Apache release archive with signature/checksum verification; do not invent an APT repository. (systematically follow recipes/hadoop.json)
- **Ansible** — do not create a third-party repository recipe initially. It is available in Ubuntu's standard package repositories; use the native package-manager route if included in the catalog. (create ansible.json and hardcode installation commands)

### Non-goals for the first milestone
- No GUI or daemon.
- No web scraping to generate shell commands at runtime.
- No arbitrary shell scripts downloaded and executed.
- No package-manager replacement.
- No automatic support for every Linux distribution.
- No claim that recipes are current unless their URLs, compatibility, and trust metadata have been checked.

## 3. Suggested minimal project structure

Keep this structure small. Do not add layers or dependencies unless needed.

```text
RepoKitchen/
├── PLAN.md
├── README.md
├── LICENSE
├── .gitignore
├── pyproject.toml
├── repokitchen/
│   ├── __init__.py
│   ├── main.py
│   ├── detector.py
│   ├── recipe_loader.py
│   ├── command_display.py
│   └── installer.py
├── recipes/
│   ├── jenkins.json
│   ├── opennebula.json
│   └── hadoop.json
└── tests/
    ├── test_detector.py
    └── test_recipe_loader.py
```

`Ansible` can be represented later by a native APT package recipe if we want it in the catalog. Do not create a needless third-party repository for it.

## 4. Recipe-driven design

Keep one JSON file per application. JSON is declarative data—not executable Python or shell.

Each recipe should describe only what is needed:
- Schema version and stable application ID
- Display name and category
- Official documentation URL
- Supported distribution IDs, versions, and architectures
- Installation method (`apt_repository`, `native_apt_package`, or `verified_archive`)
- Repository URL, suite/components, and source-list path when applicable
- Signing-key URL and keyring path when applicable
- Independently trusted signing-key fingerprint when applicable
- Package names or official archive metadata
- Official verification instructions
- User-facing post-install notes

Validate every recipe before displaying or executing its actions. Reject unknown operation types, malformed URLs, unsupported OS versions, missing trust metadata, and unexpected paths. Do not allow JSON to provide arbitrary executable commands.

## 5. Runtime behaviour

1. Start the CLI and detect the OS.
2. Load available recipes and validate their structure.
3. Display applications by category.
4. On selection, show the method and compatibility for the detected OS.
5. Present the user with the available actions:
   - Show commands
   - Configure repository only (where applicable)
   - Install automatically
   - Back
6. Before privileged changes, display a human-readable summary and ask for confirmation.
7. Perform only known, explicitly implemented operations.
8. Stop immediately on errors; do not continue after a failed key or repository verification.
9. Report what changed and how the user can verify or undo it.

## 6. Security requirements

These requirements are mandatory:

- Use official vendor documentation and official vendor download/repository endpoints.
- Require HTTPS for remote sources.
- Never use deprecated `apt-key`.
- Prefer per-repository keyrings and `signed-by=...`.
- Verify downloaded signing keys against a fingerprint pinned in a reviewed recipe. A key downloaded from the same server as the repository is not independently trusted merely because it downloaded successfully.
- If a trusted fingerprint is missing or cannot be verified, block automatic repository configuration and clearly explain the reason.
- Do not disable APT signature verification or use `trusted=yes`.
- Do not download and execute shell scripts.
- Execute subprocesses with argument arrays; do not use `shell=True` for recipe-provided data.
- Validate all paths and prevent path traversal.
- Require explicit confirmation before changing `/etc/apt`, installing packages, or running privileged commands.
- Never invoke `sudo` invisibly. Explain the required privileges and let the user approve the action.
- Do not overwrite existing source files or keyrings without warning and a backup/clear recovery path.
- Do not log secrets (none should be required by these initial recipes).
- Do not claim a successful install until subprocesses return success and verification completes.

## 7. Recipe-specific notes

### Jenkins LTS
- Follow the official Jenkins Linux installation documentation.
- Use the official Jenkins LTS APT endpoint and its documented signing key.
- Pin the expected primary fingerprint only after confirming it against Jenkins' official key-verification page.
- Check Java requirements against the chosen Jenkins LTS documentation and the target OS release; do not assume a Java version is universally compatible.
- Support showing the commands without executing them.

### OpenNebula Community Edition
- Follow the official OpenNebula CE repository configuration guide.
- Use an explicit OS-release-to-repository mapping. Never construct a repository URL from unvalidated user input.
- Support only releases explicitly listed in the recipe.
- Pin the expected primary fingerprint after independently verifying the current key.
- Until that fingerprint is reviewed and pinned, allow informational/manual display only and block automatic repository configuration.
- Do not imply that adding the repository completes a full OpenNebula deployment; document additional frontend/database requirements.

### Apache Hadoop
- Hadoop is not to be represented as a standard APT repository unless official Apache documentation changes.
- Resolve a version from Apache's official release information at runtime or require the user to select a known release.
- Download only from official Apache release infrastructure or its official mirrors.
- Verify the release signature using Apache's published keys; checksum verification may be an additional check, not a substitute for signature verification when signatures are available.
- Do not extract an archive until verification succeeds.
- Do not configure HDFS/YARN or start daemons automatically in the first milestone.

### Ansible
- If included, install through the distribution's native package manager (`apt install ansible`) for the first version.
- Do not add a PPA or external repository by default.
- Clearly explain that the distribution package may not be the newest upstream Ansible release.

## 8. Testing

Tests must not modify the host system or require root.

At minimum, test:
- Parsing `/etc/os-release` using fixture data.
- Ubuntu/Debian supported and unsupported releases.
- Valid and malformed JSON recipes.
- Missing or mismatched signing-key fingerprint blocks automatic setup.
- Manual command display is generated only from validated recipe data.
- Unknown operation types and suspicious paths are rejected.
- Subprocess errors stop the workflow and are reported.
- `--help` and basic catalog listing work without root privileges.

Use mocks for subprocess calls. Do not run real repository changes in automated tests.

## 9. Dependencies and implementation style

- Use Python 3 and the standard library first.
- Add a CLI/UI dependency only if it materially improves the first milestone.
- Keep functions small and readable.
- Do not create a generic plugin framework prematurely.
- Keep recipe data separate from installer logic.
- Provide clear error messages and actionable recovery guidance.

## 10. Definition of done for the first milestone

- The CLI runs on a supported Ubuntu/Debian system.
- It detects the current OS and shows the initial catalog.
- Selecting an application shows its installation method and supported status.
- The user can view proposed commands without executing them.
- Automatic actions require confirmation and only run validated operations.
- Jenkins and OpenNebula repository setup are blocked if trust metadata is incomplete.
- Hadoop archive verification is mandatory before extraction.
- Tests pass without root access or network access.
- README explains supported systems, usage, security model, limitations, and how to add a recipe.

## 11. Instructions to Antigravity

Implement this plan incrementally. First inspect the repository and existing files; do not overwrite user work. Then implement OS detection and recipe validation, add tests, and implement command display. Only after those are working should automatic installation be added.

Do not invent repository URLs, signing-key fingerprints, package names, or compatibility claims. If any required official value is unverified, leave the relevant automatic action disabled and document exactly what needs verification.

Prefer a small working implementation over a large scaffold. Summarize changed files, tests run, and any remaining trust/compatibility gaps when finished.
