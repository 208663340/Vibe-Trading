"""Discovery and loading of user-owned, read-only connector plugins."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from src.platform.config.paths import get_runtime_root
from src.markets.trading.types import READ_CAPABILITIES, TradingProfile

MANIFEST_FILENAME = "connector.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_REQUIRED_CAPABILITIES = {"account.read", "positions.read"}
_ALLOWED_CAPABILITIES = frozenset(READ_CAPABILITIES)

_CACHE_LIMIT = 16
_ManifestSignature = tuple[tuple[str, int, int], ...]
_CACHE: dict[
    str,
    tuple[_ManifestSignature, list["LocalConnectorPlugin"], list[dict[str, str]]],
] = {}


@dataclass(frozen=True)
class CredentialField:
    """One credential input a local connector manifest declares.

    Attributes:
        name: Snake-case field name used as the credential vault key.
        label: Human-readable label rendered by the connection UI.
        secret: Whether the value must be masked and stored in the OS vault.
        required: Whether the connector refuses to run without the value.
    """

    name: str
    label: str
    secret: bool = True
    required: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Return the field as a JSON-serializable mapping.

        Returns:
            A mapping of the field metadata. It never carries a secret value.
        """
        return {
            "name": self.name,
            "label": self.label,
            "secret": self.secret,
            "required": self.required,
        }


@dataclass(frozen=True)
class LocalConnectorPlugin:
    """A validated, operator-installed read-only connector.

    Attributes:
        profile: Trading profile the manifest declares, always read-only.
        directory: Directory holding the manifest and the adapter module.
        entrypoint: Adapter file name, resolved inside ``directory``.
        auth_type: Free-form authentication label from the manifest.
        credential_fields: Credential inputs the adapter expects.
    """

    profile: TradingProfile
    directory: Path
    entrypoint: str
    auth_type: str
    credential_fields: tuple[CredentialField, ...]

    @property
    def module_path(self) -> Path:
        """Return the adapter module path inside the plugin directory."""
        return self.directory / self.entrypoint

    def public_dict(self) -> dict[str, Any]:
        """Return UI-facing plugin metadata.

        Returns:
            A mapping describing the plugin. Credential values are never
            included — only the declared field metadata.
        """
        return {
            "profile_id": self.profile.id,
            "directory": str(self.directory),
            "auth_type": self.auth_type,
            "credential_fields": [field.to_dict() for field in self.credential_fields],
        }


def plugin_root() -> Path:
    """Return the directory holding operator-installed connector plugins.

    Returns:
        ``<runtime root>/connectors``, the only directory adapters are loaded
        from. It lives under the user's runtime root, never inside the repo.
    """
    return get_runtime_root() / "connectors"


def parse_manifest(path: Path) -> LocalConnectorPlugin:
    """Validate a local manifest as a strictly read-only connector contract.

    Args:
        path: Path to a ``connector.json`` manifest file.

    Returns:
        The validated plugin, with a read-only ``local_plugin`` profile.

    Raises:
        ValueError: If the manifest is unreadable, malformed, declares any
            capability outside the read set, omits ``account.read`` or
            ``positions.read``, is not marked read-only, or names an
            entrypoint that escapes the manifest directory or does not exist.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid connector manifest at {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("connector manifest schema_version must be 1")
    profile_data = payload.get("profile")
    if not isinstance(profile_data, dict):
        raise ValueError("connector manifest profile must be an object")
    profile_id = str(profile_data.get("id") or "").strip().lower()
    connector = str(profile_data.get("connector") or "").strip().lower()
    if not _ID_RE.fullmatch(profile_id) or not _ID_RE.fullmatch(connector):
        raise ValueError(
            "connector and profile ids must use lowercase letters, numbers, dot, dash, or underscore"
        )
    capabilities = tuple(
        str(value).strip().lower() for value in profile_data.get("capabilities", [])
    )
    if not bool(profile_data.get("readonly")) or not _REQUIRED_CAPABILITIES.issubset(
        capabilities
    ):
        raise ValueError(
            "local connector plugins must be read-only and expose account.read + positions.read"
        )
    unsupported = sorted(set(capabilities) - _ALLOWED_CAPABILITIES)
    if unsupported:
        raise ValueError(
            "local connector plugins may declare read capabilities only: "
            + ", ".join(unsupported)
        )
    environment = str(profile_data.get("environment") or "live").strip().lower()
    if environment not in {"paper", "live"}:
        raise ValueError("connector environment must be paper or live")
    label = str(profile_data.get("label") or profile_id).strip()
    if not label or len(label) > 100:
        raise ValueError("connector label must contain 1 to 100 characters")
    entrypoint = str(payload.get("entrypoint") or "adapter.py").strip()
    if Path(entrypoint).name != entrypoint or not entrypoint.endswith(".py"):
        raise ValueError(
            "connector entrypoint must be a Python file in the manifest directory"
        )
    module_path = path.parent / entrypoint
    if not module_path.is_file():
        raise ValueError(f"connector entrypoint does not exist: {module_path}")
    auth = payload.get("auth") if isinstance(payload.get("auth"), dict) else {}
    fields: list[CredentialField] = []
    for raw in auth.get("fields", []):
        if not isinstance(raw, dict):
            raise ValueError("auth fields must be objects")
        name = str(raw.get("name") or "").strip().lower()
        if not _FIELD_RE.fullmatch(name):
            raise ValueError(f"invalid credential field: {name or '?'}")
        fields.append(
            CredentialField(
                name=name,
                label=str(raw.get("label") or name.replace("_", " ").title()).strip(),
                secret=bool(raw.get("secret", True)),
                required=bool(raw.get("required", True)),
            )
        )
    profile = TradingProfile(
        id=profile_id,
        connector=connector,
        label=label,
        environment=environment,  # type: ignore[arg-type]
        transport="local_plugin",
        capabilities=capabilities,
        readonly=True,
        config={"plugin_directory": str(path.parent)},
        notes=str(
            profile_data.get("notes") or "User-installed local read-only connector."
        ).strip(),
    )
    return LocalConnectorPlugin(
        profile=profile,
        directory=path.parent,
        entrypoint=entrypoint,
        auth_type=str(auth.get("type") or "none").strip().lower(),
        credential_fields=tuple(fields),
    )


def _manifest_signature(manifests: list[Path]) -> _ManifestSignature:
    """Fingerprint the manifest set so unchanged directories skip re-parsing.

    Args:
        manifests: Manifest paths found under one plugin root.

    Returns:
        A tuple of ``(path, mtime_ns, size)`` triples. A manifest that
        disappears between the glob and the stat is fingerprinted as missing so
        the next call re-parses.
    """
    signature: list[tuple[str, int, int]] = []
    for manifest in manifests:
        try:
            stat = manifest.stat()
        except OSError:
            signature.append((str(manifest), -1, -1))
            continue
        signature.append((str(manifest), stat.st_mtime_ns, stat.st_size))
    return tuple(signature)


def clear_plugin_cache() -> None:
    """Drop every cached discovery result.

    Discovery is normally invalidated by manifest timestamps; call this when a
    caller replaces manifest content without changing its size or timestamp.
    """
    _CACHE.clear()


def discover_plugins(
    root: Path | None = None,
) -> tuple[list[LocalConnectorPlugin], list[dict[str, str]]]:
    """Discover valid plugins and return field-safe diagnostics for invalid ones.

    Results are cached per plugin root and reused while every manifest keeps
    its timestamp and size, because profile lookups re-run discovery on every
    call. Scaffolding, installing, or editing a connector changes the manifest
    set or its timestamps and therefore forces a re-parse.

    Args:
        root: Plugin directory to scan. Defaults to :func:`plugin_root`.

    Returns:
        A tuple of the valid plugins and one diagnostic mapping per invalid
        directory, each carrying only the directory name and the error text.
    """
    directory = root or plugin_root()
    key = str(directory)
    if not directory.exists():
        _CACHE.pop(key, None)
        return [], []
    manifests = sorted(directory.glob(f"*/{MANIFEST_FILENAME}"))
    signature = _manifest_signature(manifests)
    cached = _CACHE.get(key)
    if cached is not None and cached[0] == signature:
        return list(cached[1]), [dict(error) for error in cached[2]]
    plugins: list[LocalConnectorPlugin] = []
    errors: list[dict[str, str]] = []
    for manifest in manifests:
        try:
            plugins.append(parse_manifest(manifest))
        except ValueError as exc:
            errors.append({"directory": manifest.parent.name, "error": str(exc)[:300]})
    if key not in _CACHE and len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (signature, plugins, errors)
    return list(plugins), [dict(error) for error in errors]


def plugin_by_profile_id(profile_id: str) -> LocalConnectorPlugin:
    """Return the installed plugin that owns a profile id.

    Args:
        profile_id: Profile id declared by an installed connector manifest.

    Returns:
        The matching plugin from the operator's plugin root.

    Raises:
        ValueError: If no installed plugin declares that profile id.
    """
    plugins, _ = discover_plugins()
    for plugin in plugins:
        if plugin.profile.id == profile_id:
            return plugin
    raise ValueError(f"unknown local connector plugin profile: {profile_id}")


def load_adapter(plugin: LocalConnectorPlugin) -> ModuleType:
    """Load a plugin selected by the local operator from its private directory.

    Args:
        plugin: A plugin returned by discovery, so its module path is always a
            file inside the operator's plugin root.

    Returns:
        The imported adapter module.

    Raises:
        RuntimeError: If the module cannot be loaded, or does not implement
            ``check_status``, ``get_account_snapshot`` and ``get_positions``.
    """
    module_name = (
        f"vibe_local_connector_{plugin.profile.id.replace('-', '_').replace('.', '_')}"
    )
    spec = importlib.util.spec_from_file_location(module_name, plugin.module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load connector adapter: {plugin.profile.id}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for operation in ("check_status", "get_account_snapshot", "get_positions"):
        if not callable(getattr(module, operation, None)):
            raise RuntimeError(f"local connector adapter is missing {operation}()")
    return module
