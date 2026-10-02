"""Versioned processing policy and safety accounting."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

from unpacksort.models import Reason

GIB = 1024**3


class LimitExceededError(Exception):
    """A stable safety-limit failure."""

    def __init__(self, reason: Reason, *, owner: str | None = None) -> None:
        """Create a limit error with its stable reason."""
        super().__init__(reason.value)
        self.reason = reason
        self.owner = owner


@dataclass(frozen=True, slots=True)
class Policy:
    """All plan-affecting options and versioned safe defaults."""

    layout: str = "hierarchy"
    pdf_only: bool = False
    max_depth: int = 10
    max_members_per_container: int = 100_000
    max_members_run: int = 1_000_000
    max_member_bytes: int = 2 * GIB
    max_container_bytes: int = 20 * GIB
    max_run_bytes: int = 100 * GIB
    max_expansion_ratio: float = 1_000.0
    policy_version: int = 1
    detector_version: int = 1

    def validate(self) -> None:
        """Reject unsafe or nonsensical overrides."""

        if self.layout not in {"hierarchy", "flatten"}:
            msg = "layout must be hierarchy or flatten"
            raise ValueError(msg)
        numeric = {
            "max_depth": self.max_depth,
            "max_members_per_container": self.max_members_per_container,
            "max_members_run": self.max_members_run,
            "max_member_bytes": self.max_member_bytes,
            "max_container_bytes": self.max_container_bytes,
            "max_run_bytes": self.max_run_bytes,
            "max_expansion_ratio": self.max_expansion_ratio,
        }
        for name, value in numeric.items():
            if value <= 0:
                msg = f"{name} must be greater than zero"
                raise ValueError(msg)
        if self.max_members_per_container > self.max_members_run:
            msg = "max_members_per_container cannot exceed max_members_run"
            raise ValueError(msg)
        if self.max_member_bytes > self.max_container_bytes:
            msg = "max_member_bytes cannot exceed max_container_bytes"
            raise ValueError(msg)
        if self.max_container_bytes > self.max_run_bytes:
            msg = "max_container_bytes cannot exceed max_run_bytes"
            raise ValueError(msg)

    def fingerprint(self) -> str:
        """Return the stable compatibility fingerprint."""

        payload = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(slots=True)
class Accounting:
    """Run-wide logical safety accounting."""

    policy: Policy
    members: int = 0
    expanded_bytes: int = 0

    def preflight(self, *, members: int, expanded_bytes: int) -> None:
        """Check declared global use before reading payload bytes."""

        if self.members + members > self.policy.max_members_run:
            raise LimitExceededError(Reason.GLOBAL_COUNT_LIMIT)
        if self.expanded_bytes + expanded_bytes > self.policy.max_run_bytes:
            raise LimitExceededError(Reason.GLOBAL_SIZE_LIMIT)

    def charge(self, size: int) -> None:
        """Charge every logical member occurrence, including duplicates."""

        self.preflight(members=1, expanded_bytes=size)
        self.members += 1
        self.expanded_bytes += size

    @property
    def remaining_bytes(self) -> int:
        """Return the remaining observed-byte allowance."""

        return self.policy.max_run_bytes - self.expanded_bytes


@dataclass(slots=True)
class ContainerAccounting:
    """Expanded-byte budget shared by one complete recursive subtree."""

    policy: Policy
    identifier: str
    expanded_bytes: int = 0

    def preflight(self, size: int) -> None:
        """Check declared subtree bytes before reading a member."""

        if self.expanded_bytes + size > self.policy.max_container_bytes:
            raise LimitExceededError(Reason.CONTAINER_SIZE_LIMIT, owner=self.identifier)

    def charge(self, size: int) -> None:
        """Charge observed bytes to this container and every descendant."""

        self.preflight(size)
        self.expanded_bytes += size

    @property
    def remaining_bytes(self) -> int:
        """Return the remaining observed-byte allowance."""

        return self.policy.max_container_bytes - self.expanded_bytes
