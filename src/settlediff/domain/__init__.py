"""Deterministic SettleDiff domain types and rules."""

from settlediff.domain.checks import run_checks
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
    ExplanationRecord,
    ExplanationSource,
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
from settlediff.domain.verdict import PRECEDENCE, derive_verdict

__all__ = [
    "ArtifactType",
    "CheckStatus",
    "EvidenceArtifact",
    "ExecutionRecord",
    "ExpectedContract",
    "ExplanationRecord",
    "ExplanationSource",
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
    "PRECEDENCE",
    "PurchaseIntent",
    "SettlementStatus",
    "Severity",
    "UnitMismatchError",
    "Verdict",
    "mask_identifier",
    "match_activity",
    "derive_verdict",
    "run_checks",
    "redact_artifact",
]
