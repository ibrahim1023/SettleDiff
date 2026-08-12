"""Deterministic SettleDiff domain types and rules."""

from settlediff.domain.matching import (
    MatchConfidence,
    MatchResult,
    MatchStatus,
    MatchStrategy,
    match_activity,
)
from settlediff.domain.models import (
    ArtifactType,
    CheckStatus,
    EvidenceArtifact,
    ExecutionRecord,
    ExpectedContract,
    Finding,
    InvestigationExplanation,
    LedgerRecord,
    LedgerStatus,
    MachineReport,
    PaymentReceipt,
    PurchaseIntent,
    SettlementStatus,
    Severity,
    Verdict,
)
from settlediff.domain.money import Money, UnitMismatchError
from settlediff.domain.redaction import mask_identifier, redact_artifact

__all__ = [
    "ArtifactType",
    "CheckStatus",
    "EvidenceArtifact",
    "ExecutionRecord",
    "ExpectedContract",
    "Finding",
    "InvestigationExplanation",
    "LedgerRecord",
    "LedgerStatus",
    "MachineReport",
    "MatchConfidence",
    "MatchResult",
    "MatchStatus",
    "MatchStrategy",
    "Money",
    "PaymentReceipt",
    "PurchaseIntent",
    "SettlementStatus",
    "Severity",
    "UnitMismatchError",
    "Verdict",
    "mask_identifier",
    "match_activity",
    "redact_artifact",
]
