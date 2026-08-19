#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import yaml


PREFERRED_BASE_URLS = [
    "https://auth.faviann.com",
    "http://auth.faviann.vms:9000",
]

DESCRIPTION_LABEL = "Managed from ServerManagementScripts"
REPO_ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT_ROOT = REPO_ROOT / "stacks" / "auth" / "auth" / "appdata" / "authentik" / "blueprints"
FLOW_ROOT = BLUEPRINT_ROOT / "20-flows"

GROUPS_FILE = BLUEPRINT_ROOT / "10-groups.yaml"
ROLES_FILE = BLUEPRINT_ROOT / "15-roles.yaml"
ROLES_BLUEPRINT_INSTANCE_NAME = "repo-auth-roles"
ROLES_BLUEPRINT_DEPLOYED_NAME = str(ROLES_FILE.relative_to(BLUEPRINT_ROOT))
LEGACY_CLEANUP_FILE = BLUEPRINT_ROOT / "90-cleanup-legacy.yaml"
LEGACY_CLEANUP_BLUEPRINT_INSTANCE_NAME = "repo-auth-legacy-cleanup"
LEGACY_CLEANUP_BLUEPRINT_DEPLOYED_NAME = str(LEGACY_CLEANUP_FILE.relative_to(BLUEPRINT_ROOT))
BRAND_FLOWS_FILE = BLUEPRINT_ROOT / "24-brand-flows.yaml"
DEFAULT_AUTH_POLICIES_FILE = BLUEPRINT_ROOT / "25-default-auth-policies.yaml"
REGISTRATION_APPROVAL_FILE = BLUEPRINT_ROOT / "26-registration-approval-flow.yaml"
NAVIDROME_PASSWORD_CHANGE_SYNC_FILE = BLUEPRINT_ROOT / "27-navidrome-password-change-sync.yaml"
PROVIDERS_FILE = BLUEPRINT_ROOT / "30-providers.yaml"
APPLICATIONS_FILE = BLUEPRINT_ROOT / "40-applications.yaml"
SERVICE_ACCOUNTS_FILE = BLUEPRINT_ROOT / "50-service-accounts.yaml"
OUTPOSTS_FILE = BLUEPRINT_ROOT / "60-outposts.yaml"
NOTIFICATIONS_TEMPLATE_FILE = BLUEPRINT_ROOT / "70-notifications.yaml.j2"
PROXMOX_OIDC_BLUEPRINT_FILE = BLUEPRINT_ROOT / "85-proxmox-oidc.yaml.j2"

OIDC_MANIFEST_FILE = REPO_ROOT / "stacks" / "auth" / "auth" / "appdata" / "authentik" / "oidc-apps.yaml"
OIDC_BLUEPRINT_FILE = BLUEPRINT_ROOT / "80-oidc-apps.yaml.j2"
OIDC_BLUEPRINT_DEPLOYED_NAME = "80-oidc-apps.yaml"
OIDC_BLUEPRINT_INSTANCE_NAME = "repo-auth-oidc-apps"
NAVIDROME_PASSWORD_CHANGE_SYNC_BLUEPRINT_NAME = "repo-auth-navidrome-password-change-sync"
NAVIDROME_PASSWORD_CHANGE_SYNC_POLICY_NAME = "navidrome-registration-sync-policy"
DEFAULT_PASSWORD_CHANGE_FLOW_SLUG = "default-password-change"
DEFAULT_PASSWORD_CHANGE_PROMPT_STAGE_NAME = "default-password-change-prompt"

CUSTOM_BLUEPRINT_FILES = [
    {
        "name": "repo-auth-brand-flows",
        "source_path": BRAND_FLOWS_FILE,
        "deployed_relative_path": "24-brand-flows.yaml",
    },
    {
        "name": "repo-auth-default-auth-policies",
        "source_path": DEFAULT_AUTH_POLICIES_FILE,
        "deployed_relative_path": "25-default-auth-policies.yaml",
    },
    {
        "name": "repo-auth-registration-approval-flow",
        "source_path": REGISTRATION_APPROVAL_FILE,
        "deployed_relative_path": "26-registration-approval-flow.yaml",
    },
    {
        "name": "repo-auth-navidrome-password-change-sync",
        "source_path": NAVIDROME_PASSWORD_CHANGE_SYNC_FILE,
        "deployed_relative_path": "27-navidrome-password-change-sync.yaml",
    },
    {
        "name": "repo-auth-notifications",
        "source_path": NOTIFICATIONS_TEMPLATE_FILE,
        "deployed_relative_path": "70-notifications.yaml",
    },
    {
        "name": "repo-auth-proxmox-oidc",
        "source_path": PROXMOX_OIDC_BLUEPRINT_FILE,
        "deployed_relative_path": "85-proxmox-oidc.yaml",
    },
]


@dataclass(frozen=True)
class FindRef:
    model: str
    field: str
    value: Any


@dataclass(frozen=True)
class KeyOfRef:
    identifier: str


def load_oidc_manifest() -> list[dict[str, Any]]:
    data = yaml.safe_load(OIDC_MANIFEST_FILE.read_text(encoding="utf-8"))
    return data.get("apps", [])


def validate_oidc_manifest(apps: list[dict[str, Any]]) -> None:
    required_fields = (
        "name", "slug", "provider_name", "launch_url",
        "client_id", "client_secret_var", "signing_certificate_var", "redirect_uris",
    )
    slugs: set[str] = set()
    client_ids: set[str] = set()
    scope_mappings: dict[str, dict[str, Any]] = {}
    for app in apps:
        for field in required_fields:
            if field not in app:
                raise ValueError(f"App entry missing required field {field!r}: {app!r}")
        slug = app["slug"]
        if slug in slugs:
            raise ValueError(f"Duplicate slug in OIDC manifest: {slug!r}")
        slugs.add(slug)
        client_id = app["client_id"]
        if client_id in client_ids:
            raise ValueError(f"Duplicate client_id in OIDC manifest: {client_id!r}")
        client_ids.add(client_id)
        for uri in app["redirect_uris"]:
            if not uri.startswith("https://"):
                raise ValueError(
                    f"Redirect URI must be absolute https:// in app {slug!r}: {uri!r}"
                )
        for mapping in app.get("custom_scope_mappings", []):
            name = mapping["name"]
            existing = scope_mappings.get(name)
            if existing is None:
                scope_mappings[name] = mapping
            elif existing != mapping:
                raise ValueError(
                    f"Scope mapping name {name!r} appears in multiple apps with conflicting definitions"
                )


def _expr_block(expression: str, indent: int) -> list[str]:
    pad = " " * indent
    content_pad = " " * (indent + 2)
    lines = [f"{pad}expression: |-"]
    for line in expression.splitlines():
        lines.append(f"{content_pad}{line}")
    return lines


def generate_oidc_blueprint_content(apps: list[dict[str, Any]]) -> str:
    lines = [
        "version: 1",
        "metadata:",
        f"  name: {OIDC_BLUEPRINT_INSTANCE_NAME}",
        "  labels:",
        "    blueprints.goauthentik.io/instantiate: 'false'",
        f"    blueprints.goauthentik.io/description: {DESCRIPTION_LABEL}",
        "entries:",
    ]

    seen_scope_mappings: dict[str, dict[str, Any]] = {}
    ordered_scope_names: list[str] = []
    for app in apps:
        for mapping in app.get("custom_scope_mappings", []):
            name = mapping["name"]
            if name not in seen_scope_mappings:
                seen_scope_mappings[name] = mapping
                ordered_scope_names.append(name)

    scope_id_for_name: dict[str, str] = {
        name: f"scope-{slugify(name)}" for name in ordered_scope_names
    }

    for name in ordered_scope_names:
        mapping = seen_scope_mappings[name]
        scope_id = scope_id_for_name[name]
        lines += [
            f"- id: {scope_id}",
            "  model: authentik_providers_oauth2.scopemapping",
            "  state: present",
            "  identifiers:",
            f"    name: {name}",
            "  attrs:",
            f"    name: {name}",
            f"    scope_name: {mapping['scope_name']}",
        ]
        if mapping.get("description"):
            lines.append(f"    description: {mapping['description']}")
        lines += _expr_block(mapping["expression"], 4)
        lines.append("")

    for app in apps:
        slug = app["slug"]
        provider_id = f"provider-{slug}"
        app_id = f"app-{slug}"
        secret_var = app["client_secret_var"]
        cert_var = app["signing_certificate_var"]
        group = app.get("group")
        policy = app.get("policy", "always-allow")
        sub_mode = app.get("sub_mode", "user_email")
        issuer_mode = app.get("issuer_mode", "global")

        lines += [
            f"- id: {provider_id}",
            "  model: authentik_providers_oauth2.oauth2provider",
            "  state: present",
            "  identifiers:",
            f"    name: {app['provider_name']}",
            "  attrs:",
            f"    name: {app['provider_name']}",
            "    authorization_flow: !Find [authentik_flows.flow, [slug, default-provider-authorization-implicit-consent]]",
            "    invalidation_flow: !Find [authentik_flows.flow, [slug, default-provider-invalidation-flow]]",
            "    property_mappings:",
            "    - !Find [authentik_providers_oauth2.scopemapping, [managed, goauthentik.io/providers/oauth2/scope-openid]]",
            "    - !Find [authentik_providers_oauth2.scopemapping, [managed, goauthentik.io/providers/oauth2/scope-profile]]",
        ]
        for mapping in app.get("custom_scope_mappings", []):
            scope_id = scope_id_for_name[mapping["name"]]
            lines.append(f"    - !KeyOf {scope_id}")
        lines += [
            "    client_type: confidential",
            f"    client_id: {app['client_id']}",
            f"    client_secret: \"{{{{ {secret_var} }}}}\"",
            "    access_code_validity: minutes=1",
            "    access_token_validity: hours=24",
            "    refresh_token_validity: days=30",
            "    include_claims_in_id_token: true",
            f"    signing_key: !Find [authentik_crypto.certificatekeypair, [name, {{{{ {cert_var} | tojson }}}}]]",
            "    redirect_uris:",
        ]
        for uri in app["redirect_uris"]:
            lines += [
                "    - matching_mode: strict",
                f"      url: {uri}",
            ]
        lines += [
            f"    sub_mode: {sub_mode}",
            f"    issuer_mode: {issuer_mode}",
            "",
            f"- id: {app_id}",
            "  model: authentik_core.application",
            "  state: present",
            "  identifiers:",
            f"    slug: {slug}",
            "  attrs:",
            f"    name: {app['name']}",
            f"    slug: {slug}",
            f"    provider: !KeyOf {provider_id}",
            "    policy_engine_mode: any",
            f"    launch_url: {app['launch_url']}",
            "    open_in_new_tab: false",
            "    meta_launch_url: ''",
            "    meta_publisher: ''",
            "    meta_description: ''",
            "",
        ]
        if group:
            lines += [
                "- model: authentik_policies.policybinding",
                "  state: absent",
                "  identifiers:",
                f"    target: !KeyOf {app_id}",
                "    order: 0",
                "",
                "- model: authentik_policies.policybinding",
                "  state: present",
                "  identifiers:",
                f"    target: !KeyOf {app_id}",
                "    order: 1",
                "  attrs:",
                f"    target: !KeyOf {app_id}",
                "    order: 1",
                "    enabled: true",
                "    negate: false",
                "    failure_result: false",
                "    timeout: 30",
                f"    group: !Find [authentik_core.group, [name, {group}]]",
                "",
                "- model: authentik_policies.policybinding",
                "  state: present",
                "  identifiers:",
                f"    target: !KeyOf {app_id}",
                "    order: 2",
                "  attrs:",
                f"    target: !KeyOf {app_id}",
                "    order: 2",
                "    enabled: true",
                "    negate: false",
                "    failure_result: false",
                "    timeout: 30",
                "    group: !Find [authentik_core.group, [name, admins]]",
                "",
            ]
        else:
            lines += [
                "- model: authentik_policies.policybinding",
                "  state: present",
                "  identifiers:",
                f"    target: !KeyOf {app_id}",
                "    order: 0",
                "  attrs:",
                f"    target: !KeyOf {app_id}",
                "    order: 0",
                "    enabled: true",
                "    negate: false",
                "    failure_result: false",
                "    timeout: 30",
                f"    policy: !Find [authentik_policies_expression.expressionpolicy, [name, {policy}]]",
                "",
            ]

    if lines and lines[-1] == "":
        lines = lines[:-1]
    return "\n".join(lines) + "\n"


def generate_oidc_blueprint_file() -> Path:
    apps = load_oidc_manifest()
    validate_oidc_manifest(apps)
    content = generate_oidc_blueprint_content(apps)
    OIDC_BLUEPRINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OIDC_BLUEPRINT_FILE.write_text(content, encoding="utf-8")
    return OIDC_BLUEPRINT_FILE


class BlueprintDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def _represent_find(dumper: BlueprintDumper, data: FindRef) -> yaml.Node:
    return dumper.represent_sequence(
        "!Find",
        [data.model, [data.field, data.value]],
        flow_style=True,
    )


def _represent_keyof(dumper: BlueprintDumper, data: KeyOfRef) -> yaml.Node:
    return dumper.represent_scalar("!KeyOf", data.identifier)


BlueprintDumper.add_representer(FindRef, _represent_find)
BlueprintDumper.add_representer(KeyOfRef, _represent_keyof)


VAULT_FILE = REPO_ROOT / "inventory" / "group_vars" / "all" / "vault.yml"
VAULT_PASS_FILE = Path.home() / ".ansible" / "vault-pass"
VAULT_TOKEN_VAR = "vault_auth_blueprint_api_token"
ANSIBLE_VAULT_COMMAND = "uv run --locked ansible-vault".split()


def _extract_vault_token(yaml_str: str, var_name: str) -> str:
    data = yaml.safe_load(yaml_str)
    if var_name not in data:
        raise KeyError(f"{var_name} not found in vault")
    return str(data[var_name]).strip()


def token_from_vault(
    vault_file: Path = VAULT_FILE,
    vault_pass_file: Path = VAULT_PASS_FILE,
    var_name: str = VAULT_TOKEN_VAR,
) -> str:
    result = subprocess.run(
        [*ANSIBLE_VAULT_COMMAND, "view", str(vault_file), "--vault-password-file", str(vault_pass_file)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return _extract_vault_token(result.stdout, var_name)


class AuthentikClient:
    def __init__(self, token: str, base_url: str):
        self.token = token
        self.base_url = base_url.rstrip("/")

    @classmethod
    def from_token_file(cls, token_file: Path, base_url: str | None = None) -> "AuthentikClient":
        token = token_file.read_text(encoding="utf-8").strip()
        resolved_base = base_url or choose_base_url(token)
        return cls(token=token, base_url=resolved_base)

    @classmethod
    def from_vault(cls, base_url: str | None = None) -> "AuthentikClient":
        token = token_from_vault()
        resolved_base = base_url or choose_base_url(token)
        return cls(token=token, base_url=resolved_base)

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        payload: dict[str, Any] | None = None,
        accept: str = "application/json",
    ) -> tuple[int, Any, bytes]:
        url = path_or_url if path_or_url.startswith("http") else f"{self.base_url}{path_or_url}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": accept,
        }
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        req = request.Request(url, headers=headers, data=data, method=method)
        try:
            with request.urlopen(req, timeout=30) as response:
                return response.status, response.headers, response.read()
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"{method} {url} failed with {exc.code}: {body}") from exc

    def request_json(
        self,
        method: str,
        path_or_url: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        _, _, body = self._request(method, path_or_url, payload=payload)
        if not body:
            return None
        return json.loads(body)

    def request_text(self, method: str, path_or_url: str) -> str:
        _, _, body = self._request(method, path_or_url, accept="*/*")
        return body.decode("utf-8")

    def get_paginated(self, path: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}{path}"
        results: list[dict[str, Any]] = []
        while url:
            payload = self.request_json("GET", url)
            if isinstance(payload, dict) and "results" in payload:
                results.extend(payload["results"])
                url = payload.get("next")
                continue
            if isinstance(payload, list):
                results.extend(payload)
                break
            raise RuntimeError(f"Unexpected paginated response shape for {url}")
        return results


def choose_base_url(token: str) -> str:
    for base_url in PREFERRED_BASE_URLS:
        req = request.Request(
            f"{base_url}/api/v3/core/applications/?page_size=1",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        try:
            with request.urlopen(req, timeout=10):
                return base_url
        except Exception:
            continue
    raise RuntimeError("No reachable Authentik API base URL")


def slugify(value: str) -> str:
    chars = []
    for char in value.lower():
        if char.isalnum():
            chars.append(char)
        else:
            chars.append("-")
    slug = "".join(chars)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def blueprint_metadata(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "labels": {
            "blueprints.goauthentik.io/instantiate": "false",
            "blueprints.goauthentik.io/description": DESCRIPTION_LABEL,
        },
    }


def write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            payload,
            Dumper=BlueprintDumper,
            sort_keys=False,
            allow_unicode=False,
            width=4096,
            default_flow_style=False,
        ),
        encoding="utf-8",
    )


def clean_stale_flow_files(expected_paths: set[Path]) -> None:
    FLOW_ROOT.mkdir(parents=True, exist_ok=True)
    for existing_path in FLOW_ROOT.glob("*.yaml"):
        if existing_path not in expected_paths:
            existing_path.unlink()


def managed_ref(mapping: dict[str, Any]) -> FindRef:
    if mapping.get("managed"):
        return FindRef(mapping["meta_model_name"], "managed", mapping["managed"])
    return FindRef(mapping["meta_model_name"], "name", mapping["name"])


def collect_state(client: AuthentikClient) -> dict[str, Any]:
    groups = client.get_paginated("/api/v3/core/groups/?page_size=200")
    users = client.get_paginated("/api/v3/core/users/?page_size=200")
    policies = client.get_paginated("/api/v3/policies/all/?page_size=200")
    property_mappings = client.get_paginated("/api/v3/propertymappings/all/?page_size=200")
    proxy_providers = client.get_paginated("/api/v3/providers/proxy/?page_size=200")
    ldap_providers = client.get_paginated("/api/v3/providers/ldap/?page_size=200")
    applications = client.get_paginated("/api/v3/core/applications/?page_size=200")
    bindings = client.get_paginated("/api/v3/policies/bindings/?page_size=500")
    outposts = client.get_paginated("/api/v3/outposts/instances/?page_size=200")
    flows = client.get_paginated("/api/v3/flows/instances/?page_size=200")

    return {
        "groups": groups,
        "users": users,
        "policies": policies,
        "property_mappings": property_mappings,
        "proxy_providers": proxy_providers,
        "ldap_providers": ldap_providers,
        "applications": applications,
        "bindings": bindings,
        "outposts": outposts,
        "flows": flows,
    }


def build_groups_blueprint(state: dict[str, Any]) -> dict[str, Any]:
    binding_group_pks = {
        binding["group"]
        for binding in state["bindings"]
        if binding.get("group")
    }
    ldapservice = next((user for user in state["users"] if user["username"] == "ldapservice"), None)
    if ldapservice:
        binding_group_pks.update(ldapservice.get("groups", []))

    groups_by_pk = {group["pk"]: group for group in state["groups"]}
    selected_groups = [groups_by_pk[pk] for pk in binding_group_pks if pk in groups_by_pk]
    selected_groups.sort(key=lambda group: group["name"])

    entries = []
    for group in selected_groups:
        attrs = {
            "name": group["name"],
        }
        if group.get("is_superuser"):
            attrs["is_superuser"] = True
        entries.append(
            {
                "model": "authentik_core.group",
                "state": "present",
                "identifiers": {"name": group["name"]},
                "attrs": attrs,
            }
        )

    return {
        "version": 1,
        "metadata": blueprint_metadata("repo-auth-groups"),
        "entries": entries,
    }


def flow_slug_set(state: dict[str, Any]) -> list[str]:
    flow_by_pk = {flow["pk"]: flow["slug"] for flow in state["flows"]}
    slugs: set[str] = set()
    for provider in [*state["proxy_providers"], *state["ldap_providers"]]:
        for field in ["authentication_flow", "authorization_flow", "invalidation_flow"]:
            flow_pk = provider.get(field)
            if not flow_pk:
                continue
            slug = flow_by_pk.get(flow_pk)
            if not slug:
                raise RuntimeError(f"Missing flow slug for provider flow reference {flow_pk}")
            slugs.add(slug)
    return sorted(slugs)


def normalize_flow_blueprint(slug: str, content: str) -> dict[str, Any]:
    blueprint = yaml.safe_load(content)
    metadata = blueprint.get("metadata") or {}
    labels = dict(metadata.get("labels") or {})
    labels["blueprints.goauthentik.io/instantiate"] = "false"
    labels["blueprints.goauthentik.io/description"] = DESCRIPTION_LABEL
    blueprint["metadata"] = {
        "name": f"repo-auth-flow-{slug}",
        "labels": labels,
    }
    return blueprint


def export_flow_blueprints(client: AuthentikClient, slugs: list[str]) -> list[Path]:
    generated_paths: list[Path] = []
    for slug in slugs:
        content = client.request_text("GET", f"/api/v3/flows/instances/{slug}/export/")
        blueprint = normalize_flow_blueprint(slug, content)
        flow_path = FLOW_ROOT / f"{slug}.yaml"
        write_yaml(flow_path, blueprint)
        generated_paths.append(flow_path)
    return generated_paths


def provider_flow_ref(provider: dict[str, Any], field: str, flow_by_pk: dict[str, dict[str, Any]]) -> FindRef | None:
    flow_pk = provider.get(field)
    if not flow_pk:
        return None
    flow = flow_by_pk[flow_pk]
    return FindRef("authentik_flows.flow", "slug", flow["slug"])


def build_providers_blueprint(state: dict[str, Any]) -> dict[str, Any]:
    flow_by_pk = {flow["pk"]: flow for flow in state["flows"]}
    property_mappings_by_pk = {mapping["pk"]: mapping for mapping in state["property_mappings"]}

    entries = []
    proxy_attrs = [
        "name",
        "mode",
        "external_host",
        "internal_host",
        "internal_host_ssl_validation",
        "access_token_validity",
        "refresh_token_validity",
        "client_id",
        "cookie_domain",
        "redirect_uris",
        "skip_path_regex",
        "intercept_header_auth",
        "basic_auth_enabled",
        "basic_auth_user_attribute",
        "basic_auth_password_attribute",
        "jwt_federation_sources",
        "jwt_federation_providers",
    ]

    for provider in sorted(state["proxy_providers"], key=lambda item: item["name"]):
        attrs = {key: provider.get(key) for key in proxy_attrs if key in provider}
        for field in ["authentication_flow", "authorization_flow", "invalidation_flow"]:
            ref = provider_flow_ref(provider, field, flow_by_pk)
            if ref is not None:
                attrs[field] = ref
        if provider.get("certificate"):
            attrs["certificate"] = provider["certificate"]
        attrs["property_mappings"] = [
            managed_ref(property_mappings_by_pk[mapping_pk])
            for mapping_pk in provider.get("property_mappings", [])
        ]
        entries.append(
            {
                "model": "authentik_providers_proxy.proxyprovider",
                "state": "present",
                "identifiers": {"name": provider["name"]},
                "attrs": attrs,
            }
        )

    ldap_attrs = [
        "name",
        "base_dn",
        "bind_mode",
        "search_mode",
        "mfa_support",
        "uid_start_number",
        "gid_start_number",
        "tls_server_name",
    ]

    for provider in sorted(state["ldap_providers"], key=lambda item: item["name"]):
        attrs = {key: provider.get(key) for key in ldap_attrs if key in provider}
        for field in ["authentication_flow", "authorization_flow", "invalidation_flow"]:
            ref = provider_flow_ref(provider, field, flow_by_pk)
            if ref is not None:
                attrs[field] = ref
        if provider.get("certificate"):
            attrs["certificate"] = provider["certificate"]
        attrs["property_mappings"] = [
            managed_ref(property_mappings_by_pk[mapping_pk])
            for mapping_pk in provider.get("property_mappings", [])
        ]
        entries.append(
            {
                "model": "authentik_providers_ldap.ldapprovider",
                "state": "present",
                "identifiers": {"name": provider["name"]},
                "attrs": attrs,
            }
        )

    return {
        "version": 1,
        "metadata": blueprint_metadata("repo-auth-providers"),
        "entries": entries,
    }


def provider_find_ref(provider: dict[str, Any]) -> FindRef:
    model = provider.get("meta_model_name", "authentik_providers_proxy.proxyprovider")
    return FindRef(model, "name", provider["name"])


def build_applications_blueprint(state: dict[str, Any]) -> dict[str, Any]:
    groups_by_pk = {group["pk"]: group for group in state["groups"]}
    policies_by_pk = {policy["pk"]: policy for policy in state["policies"]}
    users_by_pk = {user["pk"]: user for user in state["users"]}
    providers_by_pk = {
        provider["pk"]: provider
        for provider in [*state["proxy_providers"], *state["ldap_providers"]]
    }
    applications_by_pk = {application["pk"]: application for application in state["applications"]}

    app_bindings: dict[str, list[dict[str, Any]]] = {}
    for binding in state["bindings"]:
        target_app = applications_by_pk.get(binding["target"])
        if not target_app:
            continue
        app_bindings.setdefault(target_app["slug"], []).append(binding)

    entries: list[dict[str, Any]] = []
    for application in sorted(state["applications"], key=lambda item: item["slug"]):
        if application["provider"] not in providers_by_pk:
            continue  # Skip manifest-managed OIDC apps (OAuth2 providers not in export scope)
        provider = providers_by_pk[application["provider"]]
        app_id = f"app-{application['slug']}"
        attrs = {
            "name": application["name"],
            "slug": application["slug"],
            "provider": provider_find_ref(provider),
            "policy_engine_mode": application["policy_engine_mode"],
            "launch_url": application["launch_url"],
            "open_in_new_tab": application["open_in_new_tab"],
            "meta_launch_url": application["meta_launch_url"],
            "meta_publisher": application["meta_publisher"],
            "meta_description": application["meta_description"],
        }
        if application.get("meta_icon"):
            attrs["meta_icon"] = application["meta_icon"]
        if application.get("meta_icon_url") is not None:
            attrs["meta_icon_url"] = application["meta_icon_url"]
        if application.get("group"):
            attrs["group"] = application["group"]

        entries.append(
            {
                "id": app_id,
                "model": "authentik_core.application",
                "state": "present",
                "identifiers": {"slug": application["slug"]},
                "attrs": attrs,
            }
        )

        for binding in sorted(app_bindings.get(application["slug"], []), key=lambda item: item["order"]):
            binding_attrs: dict[str, Any] = {
                "target": KeyOfRef(app_id),
                "order": binding["order"],
                "enabled": binding["enabled"],
                "negate": binding["negate"],
                "failure_result": binding["failure_result"],
                "timeout": binding["timeout"],
            }
            if binding.get("group"):
                binding_attrs["group"] = FindRef(
                    "authentik_core.group",
                    "name",
                    groups_by_pk[binding["group"]]["name"],
                )
            if binding.get("user"):
                binding_attrs["user"] = FindRef(
                    "authentik_core.user",
                    "username",
                    users_by_pk[binding["user"]]["username"],
                )
            if binding.get("policy"):
                policy = policies_by_pk[binding["policy"]]
                binding_attrs["policy"] = FindRef(
                    policy["meta_model_name"],
                    "name",
                    policy["name"],
                )
            entries.append(
                {
                    "model": "authentik_policies.policybinding",
                    "state": "present",
                    "identifiers": {
                        "target": KeyOfRef(app_id),
                        "order": binding["order"],
                    },
                    "attrs": binding_attrs,
                }
            )

    return {
        "version": 1,
        "metadata": blueprint_metadata("repo-auth-applications"),
        "entries": entries,
    }


def build_service_accounts_blueprint(state: dict[str, Any]) -> dict[str, Any]:
    ldapservice = next((user for user in state["users"] if user["username"] == "ldapservice"), None)
    if ldapservice is None:
        raise RuntimeError("ldapservice service account not found in live Authentik state")

    attrs: dict[str, Any] = {
        "username": ldapservice["username"],
        "type": ldapservice["type"],
        "is_active": ldapservice["is_active"],
    }
    if ldapservice.get("name"):
        attrs["name"] = ldapservice["name"]
    group_refs = [
        FindRef("authentik_core.group", "name", group["name"])
        for group in sorted(ldapservice.get("groups_obj", []), key=lambda item: item["name"])
    ]
    if group_refs:
        attrs["groups"] = group_refs

    return {
        "version": 1,
        "metadata": blueprint_metadata("repo-auth-service-accounts"),
        "entries": [
            {
                "model": "authentik_core.user",
                "state": "present",
                "identifiers": {"username": ldapservice["username"]},
                "attrs": attrs,
            }
        ],
    }


def build_outposts_blueprint(state: dict[str, Any]) -> dict[str, Any]:
    provider_lookup = {
        provider["pk"]: provider
        for provider in [*state["proxy_providers"], *state["ldap_providers"]]
    }
    entries = []
    for outpost in sorted(state["outposts"], key=lambda item: item["name"]):
        attrs: dict[str, Any] = {
            "name": outpost["name"],
            "type": outpost["type"],
            "providers": [
                provider_find_ref(provider_lookup[provider_pk])
                for provider_pk in outpost.get("providers", [])
            ],
            "config": outpost["config"],
            "refresh_interval_s": outpost["refresh_interval_s"],
        }
        service_connection = outpost.get("service_connection_obj")
        if service_connection:
            attrs["service_connection"] = FindRef(
                service_connection["meta_model_name"],
                "name",
                service_connection["name"],
            )
        entries.append(
            {
                "model": "authentik_outposts.outpost",
                "state": "present",
                "identifiers": {"name": outpost["name"]},
                "attrs": attrs,
            }
        )

    return {
        "version": 1,
        "metadata": blueprint_metadata("repo-auth-outposts"),
        "entries": entries,
    }


def blueprint_plan(flow_slugs: list[str]) -> list[tuple[str, str]]:
    steps = [
        ("repo-auth-groups", "10-groups.yaml"),
        (ROLES_BLUEPRINT_INSTANCE_NAME, ROLES_BLUEPRINT_DEPLOYED_NAME),
    ]
    steps.extend(
        (f"repo-auth-flow-{slug}", f"20-flows/{slug}.yaml") for slug in flow_slugs
    )
    steps.extend(
        (entry["name"], entry["deployed_relative_path"])
        for entry in CUSTOM_BLUEPRINT_FILES
    )
    steps.extend(
        [
            ("repo-auth-providers", "30-providers.yaml"),
            ("repo-auth-applications", "40-applications.yaml"),
            ("repo-auth-service-accounts", "50-service-accounts.yaml"),
            ("repo-auth-outposts", "60-outposts.yaml"),
            (OIDC_BLUEPRINT_INSTANCE_NAME, OIDC_BLUEPRINT_DEPLOYED_NAME),
        ]
    )
    steps.append((LEGACY_CLEANUP_BLUEPRINT_INSTANCE_NAME, LEGACY_CLEANUP_BLUEPRINT_DEPLOYED_NAME))
    return steps


def export_blueprints(client: AuthentikClient) -> dict[str, Any]:
    state = collect_state(client)
    flow_slugs = flow_slug_set(state)

    generate_oidc_blueprint_file()
    write_yaml(GROUPS_FILE, build_groups_blueprint(state))
    flow_paths = export_flow_blueprints(client, flow_slugs)
    clean_stale_flow_files(set(flow_paths))
    write_yaml(PROVIDERS_FILE, build_providers_blueprint(state))
    write_yaml(APPLICATIONS_FILE, build_applications_blueprint(state))
    write_yaml(SERVICE_ACCOUNTS_FILE, build_service_accounts_blueprint(state))
    write_yaml(OUTPOSTS_FILE, build_outposts_blueprint(state))

    return {
        "flow_slugs": flow_slugs,
        "files": [
            str(GROUPS_FILE.relative_to(REPO_ROOT)),
            *[str(path.relative_to(REPO_ROOT)) for path in flow_paths],
            *[str(entry["source_path"].relative_to(REPO_ROOT)) for entry in CUSTOM_BLUEPRINT_FILES],
            str(PROVIDERS_FILE.relative_to(REPO_ROOT)),
            str(APPLICATIONS_FILE.relative_to(REPO_ROOT)),
            str(SERVICE_ACCOUNTS_FILE.relative_to(REPO_ROOT)),
            str(OUTPOSTS_FILE.relative_to(REPO_ROOT)),
            str(OIDC_BLUEPRINT_FILE.relative_to(REPO_ROOT)),
        ],
    }


def find_available_paths(client: AuthentikClient) -> list[dict[str, Any]]:
    available = client.request_json("GET", "/api/v3/managed/blueprints/available/")
    if isinstance(available, dict) and "results" in available:
        return available["results"]
    if isinstance(available, list):
        return available
    raise RuntimeError("Unexpected available blueprints response shape")


def get_instances(client: AuthentikClient) -> list[dict[str, Any]]:
    return client.get_paginated("/api/v3/managed/blueprints/?page_size=200")


def update_instance(client: AuthentikClient, instance_pk: str, payload: dict[str, Any]) -> dict[str, Any]:
    return client.request_json("PATCH", f"/api/v3/managed/blueprints/{instance_pk}/", payload=payload)


def create_instance(client: AuthentikClient, payload: dict[str, Any]) -> dict[str, Any]:
    return client.request_json("POST", "/api/v3/managed/blueprints/", payload=payload)


def apply_instance(client: AuthentikClient, instance_pk: str) -> None:
    client.request_json("POST", f"/api/v3/managed/blueprints/{instance_pk}/apply/")


def delete_instance(client: AuthentikClient, instance_pk: str) -> None:
    client.request_json("DELETE", f"/api/v3/managed/blueprints/{instance_pk}/")


def desired_navidrome_password_change_target_pk(client: AuthentikClient) -> str:
    export = yaml.safe_load(
        client.request_text("GET", f"/api/v3/flows/instances/{DEFAULT_PASSWORD_CHANGE_FLOW_SLUG}/export/")
    )
    entries = export.get("entries", [])

    flow = next(
        (
            entry for entry in entries
            if entry.get("model") == "authentik_flows.flow"
            and entry.get("identifiers", {}).get("slug") == DEFAULT_PASSWORD_CHANGE_FLOW_SLUG
        ),
        None,
    )
    if flow is None:
        raise RuntimeError(f"Export for {DEFAULT_PASSWORD_CHANGE_FLOW_SLUG} did not include the flow entry")
    flow_pk = flow["identifiers"]["pk"]

    prompt_stage = next(
        (
            entry for entry in entries
            if entry.get("model") == "authentik_stages_prompt.promptstage"
            and entry.get("identifiers", {}).get("name") == DEFAULT_PASSWORD_CHANGE_PROMPT_STAGE_NAME
        ),
        None,
    )
    if prompt_stage is None:
        raise RuntimeError(
            f"Export for {DEFAULT_PASSWORD_CHANGE_FLOW_SLUG} did not include prompt stage "
            f"{DEFAULT_PASSWORD_CHANGE_PROMPT_STAGE_NAME}"
        )
    prompt_stage_pk = prompt_stage["identifiers"]["pk"]

    flow_stage_binding = next(
        (
            entry for entry in entries
            if entry.get("model") == "authentik_flows.flowstagebinding"
            and entry.get("identifiers", {}).get("stage") == prompt_stage_pk
            and entry.get("identifiers", {}).get("target") == flow_pk
            and entry.get("identifiers", {}).get("order") == 0
        ),
        None,
    )
    if flow_stage_binding is None:
        raise RuntimeError(
            f"Export for {DEFAULT_PASSWORD_CHANGE_FLOW_SLUG} did not include the order 0 flow-stage binding "
            f"for prompt stage {DEFAULT_PASSWORD_CHANGE_PROMPT_STAGE_NAME}"
        )
    return flow_stage_binding["identifiers"]["pk"]


def ensure_navidrome_password_change_sync_binding(client: AuthentikClient) -> dict[str, Any]:
    policies = client.get_paginated("/api/v3/policies/all/?page_size=200")
    policy = next(
        (
            item for item in policies
            if item.get("name") == NAVIDROME_PASSWORD_CHANGE_SYNC_POLICY_NAME
        ),
        None,
    )
    if policy is None:
        raise RuntimeError(f"Unable to find policy {NAVIDROME_PASSWORD_CHANGE_SYNC_POLICY_NAME}")

    target_pk = desired_navidrome_password_change_target_pk(client)
    desired_payload = {
        "policy": policy["pk"],
        "target": target_pk,
        "order": 0,
        "enabled": True,
        "negate": False,
        "failure_result": False,
        "timeout": 10,
    }
    bindings = client.get_paginated("/api/v3/policies/bindings/?page_size=500")
    existing = next(
        (
            item for item in bindings
            if item.get("policy") == policy["pk"]
            and item.get("target") == target_pk
        ),
        None,
    )
    if existing is not None:
        needs_update = any(existing.get(field) != value for field, value in desired_payload.items())
        if needs_update:
            existing = client.request_json(
                "PATCH",
                f"/api/v3/policies/bindings/{existing['pk']}/",
                payload=desired_payload,
            )
            return {
                "status": "successful",
                "changed": True,
                "action": "updated-binding",
                "binding_pk": existing["pk"],
                "target_pk": target_pk,
            }
        return {
            "status": "successful",
            "changed": False,
            "action": "unchanged",
            "binding_pk": existing["pk"],
            "target_pk": target_pk,
        }

    created = client.request_json(
        "POST",
        "/api/v3/policies/bindings/",
        payload=desired_payload,
    )
    return {
        "status": "successful",
        "changed": True,
        "action": "created-binding",
        "binding_pk": created["pk"],
        "target_pk": target_pk,
    }


def wait_for_instance(
    client: AuthentikClient,
    instance_pk: str,
    instance_name: str,
    previous_last_applied: str | None,
    previous_last_applied_hash: str | None,
    expected_hash: str | None,
) -> dict[str, Any]:
    deadline = time.time() + 120
    instance: dict[str, Any] | None = None
    while time.time() < deadline:
        instance = client.request_json("GET", f"/api/v3/managed/blueprints/{instance_pk}/")
        status = instance.get("status")
        if status == "error":
            message = (
                f"Blueprint instance {instance['name']} entered error state with "
                f"expected hash {expected_hash!r}, observed hash "
                f"{instance.get('last_applied_hash')!r}"
            )
            api_detail = instance.get("detail")
            if api_detail:
                message += f": {api_detail}"
            raise RuntimeError(message)
        if status == "successful" and instance.get("last_applied") != previous_last_applied:
            observed_hash = instance.get("last_applied_hash")
            if expected_hash is not None and observed_hash != expected_hash:
                message = (
                    f"Blueprint instance {instance['name']} reported successful with "
                    f"expected hash {expected_hash!r}, observed hash {observed_hash!r}"
                )
                api_detail = instance.get("detail")
                if api_detail:
                    message += f": {api_detail}"
                raise RuntimeError(message)
            return instance
        time.sleep(2)
    if instance is None:
        raise RuntimeError(
            f"Timed out waiting for blueprint instance {instance_name} to finish with "
            f"expected hash {expected_hash!r}, observed hash {previous_last_applied_hash!r}"
        )
    message = (
        f"Timed out waiting for blueprint instance {instance['name']} to finish with "
        f"expected hash {expected_hash!r}, observed hash "
        f"{instance.get('last_applied_hash')!r}"
    )
    api_detail = instance.get("detail")
    if api_detail:
        message += f": {api_detail}"
    raise RuntimeError(message)


def reconcile_blueprint_instances(client: AuthentikClient, flow_slugs: list[str]) -> dict[str, Any]:
    plan = blueprint_plan(flow_slugs)
    plan_names = {name for name, _ in plan}
    expected_instance_names = plan_names - {NAVIDROME_PASSWORD_CHANGE_SYNC_BLUEPRINT_NAME}
    available = find_available_paths(client)
    available_by_suffix = {item["path"]: item for item in available}
    instances = get_instances(client)
    instances_by_name = {item["name"]: item for item in instances}
    instances_by_path = {item.get("path"): item for item in instances if item.get("path")}
    applied = []
    changed = False
    expected_hashes_by_name: dict[str, str] = {}

    for name, relative_path in plan:
        if name == NAVIDROME_PASSWORD_CHANGE_SYNC_BLUEPRINT_NAME:
            navidrome_result = ensure_navidrome_password_change_sync_binding(client)
            stale_instance = instances_by_name.get(name)
            action_parts = []
            if navidrome_result.get("changed"):
                changed = True
                action_parts.append(navidrome_result.get("action", "updated-binding"))
            if stale_instance is not None:
                delete_instance(client, stale_instance["pk"])
                changed = True
                action_parts.append("deleted-stale-instance")
                instances_by_name.pop(name, None)
                if stale_instance.get("path"):
                    instances_by_path.pop(stale_instance["path"], None)
            applied.append(
                {
                    "name": name,
                    "path": relative_path,
                    "status": navidrome_result["status"],
                    "action": "+".join(action_parts) if action_parts else "unchanged",
                }
            )
            continue

        matched = [item for item in available_by_suffix.values() if item["path"].endswith(relative_path)]
        if len(matched) != 1:
            raise RuntimeError(f"Expected exactly one available blueprint for {relative_path}, found {len(matched)}")
        available_blueprint = matched[0]
        available_path = available_blueprint["path"]
        available_hash = available_blueprint.get("hash")
        if available_hash is not None:
            expected_hashes_by_name[name] = available_hash
        payload = {
            "name": name,
            "path": available_path,
            "enabled": True,
        }
        instance = instances_by_name.get(name) or instances_by_path.get(available_path)
        action_parts = []
        needs_apply = False
        previous_name = None
        previous_path = None

        if instance is None:
            instance = create_instance(client, payload)
            changed = True
            action_parts.append("created")
            needs_apply = True
        else:
            previous_name = instance.get("name")
            previous_path = instance.get("path")
            needs_metadata_update = any(instance.get(field) != value for field, value in payload.items())
            if needs_metadata_update:
                instance = update_instance(client, instance["pk"], payload)
                changed = True
                action_parts.append("updated")
                needs_apply = True
            else:
                needs_apply = (
                    instance.get("status") != "successful"
                    or available_hash is None
                    or instance.get("last_applied_hash") != available_hash
                )

        if needs_apply:
            previous_last_applied = instance.get("last_applied")
            previous_last_applied_hash = instance.get("last_applied_hash")
            apply_instance(client, instance["pk"])
            instance = wait_for_instance(
                client,
                instance["pk"],
                instance["name"],
                previous_last_applied,
                previous_last_applied_hash,
                available_hash,
            )
            changed = True
            action_parts.append("applied")

        applied.append(
            {
                "name": instance["name"],
                "path": instance["path"],
                "status": instance["status"],
                "action": "+".join(action_parts) if action_parts else "unchanged",
            }
        )
        if previous_name and previous_name != instance["name"]:
            instances_by_name.pop(previous_name, None)
        if previous_path and previous_path != instance.get("path"):
            instances_by_path.pop(previous_path, None)
        instances_by_name[instance["name"]] = instance
        instances_by_path[instance.get("path")] = instance

    final_instances = get_instances(client)
    repo_instances = [item for item in final_instances if item["name"] in expected_instance_names]
    missing_names = sorted(expected_instance_names - {item["name"] for item in repo_instances})
    if missing_names:
        raise RuntimeError(
            "Repo-managed blueprint instances missing after reconcile: " + ", ".join(missing_names)
        )
    for instance in repo_instances:
        expected_hash = expected_hashes_by_name.get(instance["name"])
        observed_hash = instance.get("last_applied_hash")
        if expected_hash is not None and instance.get("status") == "error":
            message = (
                f"Blueprint instance {instance['name']} entered error state during final verification with "
                f"expected hash {expected_hash!r}, observed hash {observed_hash!r}"
            )
            api_detail = instance.get("detail")
            if api_detail:
                message += f": {api_detail}"
            raise RuntimeError(message)
        if (
            expected_hash is not None
            and instance.get("status") == "successful"
            and observed_hash != expected_hash
        ):
            message = (
                f"Blueprint instance {instance['name']} reported successful in final verification with "
                f"expected hash {expected_hash!r}, observed hash {observed_hash!r}"
            )
            api_detail = instance.get("detail")
            if api_detail:
                message += f": {api_detail}"
            raise RuntimeError(message)
    failures = [item for item in repo_instances if item.get("status") != "successful"]
    if failures:
        raise RuntimeError(
            "Repo-managed blueprint instances did not all succeed: "
            + ", ".join(f"{item['name']}={item['status']}" for item in failures)
        )
    return {
        "changed": changed,
        "applied": applied,
        "available_paths": [item["path"] for item in available],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export and reconcile Authentik blueprints")
    parser.add_argument(
        "command",
        choices=["export", "apply"],
        help="Export tracked blueprint files or apply tracked blueprint instances",
    )
    parser.add_argument(
        "--token-file",
        default=None,
        help="Path to an Authentik API token file. If omitted, the token is read from the Ansible vault.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Override the Authentik API base URL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.token_file is not None:
        client = AuthentikClient.from_token_file(Path(args.token_file), base_url=args.base_url)
    else:
        client = AuthentikClient.from_vault(base_url=args.base_url)

    if args.command == "export":
        result = export_blueprints(client)
    else:
        generate_oidc_blueprint_file()
        state = collect_state(client)
        result = reconcile_blueprint_instances(client, flow_slug_set(state))

    print(json.dumps({"base_url": client.base_url, **result}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
