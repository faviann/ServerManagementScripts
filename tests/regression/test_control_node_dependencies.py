from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]


def run_playbook(
    playbook: Path,
    *,
    extra_vars: dict[str, object],
    env: dict[str, str] | None = None,
    tags: str | None = None,
    use_uv: bool = True,
) -> subprocess.CompletedProcess[str]:
    command = (
        ["uv", "run", "--locked", "ansible-playbook"]
        if use_uv
        else [str(REPO_ROOT / ".venv" / "bin" / "ansible-playbook")]
    ) + [
        "-i",
        "localhost,",
        "-c",
        "local",
        str(playbook),
        "-e",
        json.dumps(extra_vars),
    ]
    if tags:
        command.extend(["--tags", tags])
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        check=False,
    )


def write_collection_manifest(root: Path, name: str, version: str) -> None:
    namespace, collection = name.split(".")
    manifest = root / "ansible_collections" / namespace / collection / "MANIFEST.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "collection_info": {
                    "namespace": namespace,
                    "name": collection,
                    "version": version,
                }
            }
        ),
        encoding="utf-8",
    )


def test_dependency_manifests_define_one_exact_source_of_truth() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependency_names = {
        re.split(r"[<>=!~]", dependency, maxsplit=1)[0]
        for dependency in project["project"]["dependencies"]
    }
    assert "ansible-core" in dependency_names
    assert "ansible" not in dependency_names

    requirements = yaml.safe_load(
        (REPO_ROOT / "collections" / "requirements.yml").read_text(encoding="utf-8")
    )
    assert set(requirements) == {"collections"}
    declared = {item["name"]: str(item["version"]) for item in requirements["collections"]}
    assert set(declared) == {
        "ansible.posix",
        "community.crypto",
        "community.docker",
        "community.proxmox",
    }
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", version) for version in declared.values())

    role_requirements = yaml.safe_load(
        (REPO_ROOT / "requirements" / "roles.yml").read_text(encoding="utf-8")
    )
    assert set(role_requirements) == {"roles"}
    assert role_requirements["roles"] == [
        {"name": "geerlingguy.docker", "version": "7.9.0"}
    ]


def test_bootstrap_forces_external_role_pin(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    galaxy_log = tmp_path / "galaxy.log"
    fake_galaxy = fake_bin / "ansible-galaxy"
    fake_galaxy.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$*\" >> \"$GALAXY_LOG\"\n",
        encoding="utf-8",
    )
    fake_galaxy.chmod(0o755)

    project_root = tmp_path / "project"
    (project_root / "collections").mkdir(parents=True)
    (project_root / "requirements").mkdir()
    (project_root / "inventory" / "group_vars" / "all").mkdir(parents=True)
    (project_root / "collections" / "requirements.yml").write_text(
        "collections: []\n", encoding="utf-8"
    )
    (project_root / "requirements" / "roles.yml").write_text(
        "roles:\n  - name: example.role\n    version: 2.0.0\n", encoding="utf-8"
    )
    (project_root / "inventory" / "group_vars" / "all" / "vault.yml").write_text(
        "$ANSIBLE_VAULT;1.1;AES256\n", encoding="utf-8"
    )
    home = tmp_path / "home"
    ssh_dir = home / ".ansible" / "ssh"
    ssh_dir.mkdir(parents=True)
    private_key = ssh_dir / "proxmox_lxc"
    public_key = ssh_dir / "proxmox_lxc.pub"
    vault_pass = home / ".ansible" / "vault-pass"
    vault_pass.parent.mkdir(exist_ok=True)
    vault_pass.write_text("placeholder\n", encoding="utf-8")

    result = run_playbook(
        REPO_ROOT / "tests" / "regression" / "fixtures" / "control_node_bootstrap_test.yml",
        extra_vars={
            "control_node_project_root": str(project_root),
            "control_node_home_dir": str(home),
            "control_node_collection_requirements": str(
                project_root / "collections" / "requirements.yml"
            ),
            "control_node_collection_install_path": str(project_root / "collections"),
            "control_node_ssh_private_key_path": str(private_key),
            "control_node_ssh_public_key_path": str(public_key),
            "control_node_vault_password_file": str(vault_pass),
            "control_node_skip_system_packages": True,
        },
        env={
            "ANSIBLE_COLLECTIONS_PATH": str(project_root / "collections"),
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "GALAXY_LOG": str(galaxy_log),
        },
        use_uv=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert private_key.exists()
    assert public_key.exists()
    invocations = galaxy_log.read_text(encoding="utf-8").splitlines()
    role_install = next(line for line in invocations if line.startswith("role install "))
    assert "--force" in role_install.split()


def test_lifecycle_preflight_reports_expected_and_installed_versions(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.yml"
    requirements.write_text(
        "collections:\n  - name: community.proxmox\n    version: 2.0.0\n",
        encoding="utf-8",
    )
    collections = tmp_path / "collections"
    write_collection_manifest(collections, "community.proxmox", "1.6.0")

    result = run_playbook(
        REPO_ROOT / "playbooks" / "lifecycle-prerequisites.yml",
        extra_vars={
            "control_node_collection_requirements": str(requirements),
            "control_node_collection_install_path": str(collections),
        },
        env={"HOMELAB_IAC_LIFECYCLE_WRAPPER": "1"},
        tags="control_node_prerequisites",
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "community.proxmox" in output
    assert "expected 2.0.0" in output
    assert "installed 1.6.0" in output


def test_lifecycle_preflight_accepts_exact_collection_versions(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.yml"
    requirements.write_text(
        "collections:\n  - name: community.proxmox\n    version: 2.0.0\n",
        encoding="utf-8",
    )
    collections = tmp_path / "collections"
    write_collection_manifest(collections, "community.proxmox", "2.0.0")

    result = run_playbook(
        REPO_ROOT / "playbooks" / "lifecycle-prerequisites.yml",
        extra_vars={
            "control_node_collection_requirements": str(requirements),
            "control_node_collection_install_path": str(collections),
        },
        env={"HOMELAB_IAC_LIFECYCLE_WRAPPER": "1"},
        tags="control_node_prerequisites",
    )
    assert result.returncode == 0, result.stdout + result.stderr
