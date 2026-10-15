"""Opaque, channel-agnostic scheduled-delivery target registry."""

from __future__ import annotations

import re
from dataclasses import dataclass

from src.platform.config.loader import load_agent_config

_SAFE_REF = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


@dataclass(frozen=True)
class DeliveryTarget:
    ref: str
    label: str
    channel: str
    target: str

    def public_dict(self) -> dict[str, str]:
        return {"ref": self.ref, "label": self.label, "channel": self.channel}


def list_delivery_targets() -> list[DeliveryTarget]:
    """Load enabled operator targets without exposing raw provider ids."""
    configured = load_agent_config().channels.delivery_targets
    targets: list[DeliveryTarget] = []
    for ref, item in configured.items():
        if not _SAFE_REF.fullmatch(ref) or not item.enabled:
            continue
        targets.append(
            DeliveryTarget(
                ref=ref,
                label=item.label,
                channel=item.channel,
                target=item.target,
            )
        )
    return sorted(targets, key=lambda item: (item.channel, item.label, item.ref))


def resolve_delivery_target(ref: str) -> DeliveryTarget:
    """Resolve an opaque ref or raise a user-facing validation error."""
    for target in list_delivery_targets():
        if target.ref == ref:
            return target
    raise ValueError(f"unknown or disabled delivery target ref {ref!r}")
