"""Authentication identity shared by HTTP and optional conversation adapters."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

class AuthMethod(str, Enum):
    """How a request proved it was allowed in.

    The distinction that matters is not which mechanism was used but whether the
    mechanism can name a human. A shared bearer key and loopback dev-trust both
    authorise; neither identifies. See :attr:`Principal.attributable`.
    """

    #: One process-wide static bearer key. Authorises, cannot identify.
    SHARED_KEY = "shared_key"
    #: No key configured; the request came from loopback and was trusted.
    LOOPBACK_TRUST = "loopback_trust"
    #: A named human authenticated through an identity provider. Not reachable
    #: today -- listed so that ``attributable`` has a True case to grow into and
    #: so downstream code branches on the enum rather than on a string it
    #: invented.
    FEDERATED_IDENTITY = "federated_identity"


#: Auth methods that can attribute an action to a named human. Everything else
#: proves only that *somebody with the secret* acted.
ATTRIBUTABLE_AUTH_METHODS: frozenset[AuthMethod] = frozenset({AuthMethod.FEDERATED_IDENTITY})


@dataclass(frozen=True)
class Principal:
    """Who a session belongs to, and how much that claim is worth.

    A ``Principal`` exists so that a number produced by this system can be
    traced to an actor. What it must never do is *manufacture* an identity the
    authentication layer could not establish.

    Today the API authenticates with one process-wide static bearer key, or with
    loopback dev-trust when no key is set. Under either, every request from
    every person looks identical. :attr:`attributable` is therefore False, and
    it stays False until an identity provider is wired in. Any control that
    needs a named human -- four-eyes approval, a MiFID II recipient log, SEC
    17a-4 attribution -- must check ``attributable`` and refuse rather than
    quote ``subject`` as if it were a person.

    Attributes:
        subject: Identifier for the actor. Under a shared secret this is a
            role label such as ``"shared-key-holder"``, not a person.
        auth_method: How the request was authorised.
        attributable: Whether ``subject`` names an actual human. Derived from
            ``auth_method`` at construction, never set by a caller -- a
            caller-settable flag is a caller-settable lie.
        tenant: Optional tenant scope, for a future multi-tenant runtime root.
        display_name: Optional human-readable label for the UI.
    """

    subject: str
    auth_method: AuthMethod
    tenant: Optional[str] = None
    display_name: Optional[str] = None
    attributable: bool = field(init=False, default=False)

    def __post_init__(self) -> None:
        if not str(self.subject).strip():
            raise ValueError("a principal must have a non-empty subject")
        object.__setattr__(
            self, "attributable", self.auth_method in ATTRIBUTABLE_AUTH_METHODS
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize the principal to a JSON-safe dictionary.

        Returns:
            A dictionary carrying ``attributable`` explicitly, so a consumer
            reading the serialized form cannot miss it.
        """
        return {
            "subject": self.subject,
            "auth_method": self.auth_method.value,
            "tenant": self.tenant,
            "display_name": self.display_name,
            "attributable": self.attributable,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Principal:
        """Rebuild a principal from its serialized form.

        ``attributable`` in the input is ignored and recomputed from
        ``auth_method``: a stored record whose flag was tampered with, or
        written by an older version, must not be able to assert attribution the
        auth method never supported.

        Args:
            data: Dictionary produced by :meth:`to_dict`.

        Returns:
            A Principal.

        Raises:
            ValueError: If ``auth_method`` is missing or unknown.
        """
        return cls(
            subject=data["subject"],
            auth_method=AuthMethod(data["auth_method"]),
            tenant=data.get("tenant"),
            display_name=data.get("display_name"),
        )
