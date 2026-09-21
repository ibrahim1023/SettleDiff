"""Deterministic SettleDiff domain types and rules."""

from settlediff.domain.checks import run_checks
from settlediff.domain.delivery import assess_delivery
from settlediff.domain.matching import (
    MatchConfidence,
    MatchResult,
    MatchStatus,
    MatchStrategy,
    match_activity,
)
from settlediff.domain.models import (
    ArtifactType,
    AssetIdentity,
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
from settlediff.domain.retry import RetryRunStateSnapshot, analyze_retry
from settlediff.domain.verdict import PRECEDENCE, derive_verdict

__all__ = [
    "ArtifactType",
    "AssetIdentity",
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
    "RetryRunStateSnapshot",
    "SettlementStatus",
    "Severity",
    "UnitMismatchError",
    "Verdict",
    "analyze_retry",
    "assess_delivery",
    "mask_identifier",
    "match_activity",
    "derive_verdict",
    "run_checks",
    "redact_artifact",
]
