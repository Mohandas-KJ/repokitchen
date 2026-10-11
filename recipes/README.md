# RepoKitchen starter recipes

These are initial declarative recipe files for Antigravity to implement against.

## Included
- `jenkins.json`: official Jenkins LTS APT repository. The signing-key fingerprint is pinned from Jenkins' official verification page.
- `opennebula.json`: OpenNebula 7.2 Community Edition APT repository for Ubuntu 22.04/24.04 and Debian 12/13. **Automatic configuration is deliberately blocked until its current `repo2.key` primary fingerprint is independently verified and pinned.** Do not remove this gate by accepting an arbitrary downloaded key.
- `hadoop.json`: Hadoop uses official Apache release archives and signature/checksum verification, not a standard official APT repository recipe. The `3.5.0` value is only the version observed when this recipe was researched; runtime logic must resolve the current release and ask for confirmation.

Ansible is intentionally omitted because Ubuntu provides an `ansible` package in its standard repositories. A future generic distro-package recipe can represent it without adding a third-party repository.

## Important implementation contract
JSON is data, not executable shell. The program should support only known operation types such as `apt_packages`, `apt_repository`, and `verified_archive`; validate every field; use argument arrays rather than shell string interpolation; require user confirmation before privileged changes; and stop on signature/fingerprint verification failure.

## Official sources
- Jenkins: https://www.jenkins.io/doc/book/installing/linux/
- Jenkins key verification: https://www.jenkins.io/download/verify/
- OpenNebula CE repository: https://docs.opennebula.io/7.2/software/installation_process/frontend_installation/opennebula_repository_configuration_ce/
- Hadoop releases and verification: https://hadoop.apache.org/releases.html
- Hadoop single-node setup: https://hadoop.apache.org/docs/current/hadoop-project-dist/hadoop-common/SingleCluster.html
- Ubuntu Ansible package: https://packages.ubuntu.com/ansible
