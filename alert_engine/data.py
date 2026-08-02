"""Structured read results shared by market and calendar data sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


DATA_OK = "ok"
DATA_NO_DATA = "no_data"
DATA_STALE = "stale_data"
DATA_PROVIDER_ERROR = "provider_error"
DATA_INVALID_INPUT = "invalid_input"
DATA_CALCULATION_ERROR = "calculation_error"


@dataclass(frozen=True)
class DataResult:
    """A value plus enough provenance to distinguish absence from falsehood."""

    value: Any = None
    status: str = DATA_OK
    code: Optional[str] = None
    detail: Optional[str] = None
    observed_at: Optional[datetime] = None
    provider: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == DATA_OK

    @classmethod
    def success(
        cls,
        value: Any,
        *,
        observed_at: Optional[datetime] = None,
        provider: Optional[str] = None,
        detail: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "DataResult":
        return cls(
            value=value,
            status=DATA_OK,
            observed_at=observed_at,
            provider=provider,
            detail=detail,
            metadata=dict(metadata or {}),
        )

    @classmethod
    def failure(
        cls,
        status: str,
        code: str,
        detail: str,
        *,
        observed_at: Optional[datetime] = None,
        provider: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "DataResult":
        return cls(
            value=None,
            status=status,
            code=code,
            detail=detail,
            observed_at=observed_at,
            provider=provider,
            metadata=dict(metadata or {}),
        )
