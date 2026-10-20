
"""Evidence-based discrepancy detection for financial operations.

This module analyzes normalized records and reconciliation matches.
It does not modify records, persist results, or execute financial actions.
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from schemas.financial_operations import (
    FinancialFinding,
    FinancialRecord,
    FinancialRecordType,
    FindingSeverity,
    FindingType,
    ReconciliationMatch,
)


def _stable_id(prefix: str, *parts: str) -> str:
    """Generate deterministic IDs so repeated runs do not create random findings."""
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _normalize(value: object) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip().lower()


def _record_evidence_ids(record: FinancialRecord) -> list[str]:
    return [e.evidence_id for e in record.evidence]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _make_finding(
    *,
    finding_type: FindingType,
    severity: FindingSeverity,
    summary: str,
    record_ids: list[str],
    evidence_ids: list[str],
    explanation: str,
    discriminator: str,
) -> FinancialFinding:
    return FinancialFinding(
        finding_id=_stable_id(
            "finding",
            finding_type.value,
            discriminator,
            *sorted(record_ids),
        ),
        finding_type=finding_type,
        severity=severity,
        summary=summary,
        record_ids=_unique(record_ids),
        evidence_ids=_unique(evidence_ids),
        status="open",
        explanation=explanation,
    )


def _find_currency_discrepancies(
    records: list[FinancialRecord],
    match: ReconciliationMatch,
) -> list[FinancialFinding]:
    by_id = {record.record_id: record for record in records}
    involved = [by_id[rid] for rid in match.record_ids if rid in by_id]

    currencies = {
        record.currency.upper()
        for record in involved
        if record.currency and record.currency.strip()
    }

    # A mismatch requires at least two known, different currencies.
    if len(currencies) < 2:
        return []

    evidence_ids = _unique(
        evidence_id
        for record in involved
        for evidence_id in _record_evidence_ids(record)
    )
    return [
        _make_finding(
            finding_type=FindingType.CURRENCY_MISMATCH,
            severity=FindingSeverity.HIGH,
            summary="Reconciliation records use different currencies",
            record_ids=[record.record_id for record in involved],
            evidence_ids=evidence_ids + match.evidence_ids,
            explanation=(
                "The records being compared have different explicit currency "
                f"codes: {', '.join(sorted(currencies))}. Do not compare or "
                "net these amounts without verified currency conversion."
            ),
            discriminator=match.match_id,
        )
    ]


def detect_discrepancies(
    records: list[FinancialRecord],
    matches: list[ReconciliationMatch],
) -> list[FinancialFinding]:
    """Return deterministic findings supported by records and match outcomes.

    Conservative rules:
    - Only explicit match outcomes generate amount/date findings.
    - Different explicit currency codes generate currency findings.
    - Missing fields and unvalidated bank transaction signs generate
      insufficient-evidence findings.
    - Unmatched records alone are not treated as errors or missing documents.
    """
    findings: list[FinancialFinding] = []
    records_by_id = {record.record_id: record for record in records}

    # Analyze the reconciliation outcomes.
    for match in matches:
        involved = [
            records_by_id[record_id]
            for record_id in match.record_ids
            if record_id in records_by_id
        ]
        if not involved:
            continue

        evidence_ids = _unique(
            [
                *match.evidence_ids,
                *(
                    evidence_id
                    for record in involved
                    for evidence_id in _record_evidence_ids(record)
                ),
            ]
        )
        record_ids = [record.record_id for record in involved]
        conflicting = {_normalize(field) for field in match.conflicting_fields}

        if match.status.value == "amount_conflict" or "amount" in conflicting:
            findings.append(
                _make_finding(
                    finding_type=FindingType.AMOUNT_DISCREPANCY,
                    severity=FindingSeverity.HIGH,
                    summary="Conflicting financial amounts require review",
                    record_ids=record_ids,
                    evidence_ids=evidence_ids,
                    explanation=(
                        "The reconciliation matcher reported an amount conflict. "
                        "Review the original document values and transaction "
                        "evidence before deciding whether the payment settles "
                        "the obligation."
                    ),
                    discriminator=match.match_id,
                )
            )

        if (
            match.status.value == "matched_with_date_conflict"
            or "date" in conflicting
            or "transaction_date" in conflicting
            or "payment_date" in conflicting
        ):
            findings.append(
                _make_finding(
                    finding_type=FindingType.DATE_DISCREPANCY,
                    severity=FindingSeverity.WARNING,
                    summary="Reconciliation dates conflict",
                    record_ids=record_ids,
                    evidence_ids=evidence_ids,
                    explanation=(
                        "The reconciliation matcher reported a date conflict. "
                        "Verify the document date, payment date, and statement "
                        "period before confirming settlement."
                    ),
                    discriminator=match.match_id,
                )
            )

        findings.extend(_find_currency_discrepancies(records, match))

        if match.status.value == "requires_review":
            findings.append(
                _make_finding(
                    finding_type=FindingType.INSUFFICIENT_EVIDENCE,
                    severity=FindingSeverity.WARNING,
                    summary="Reconciliation requires additional evidence",
                    record_ids=record_ids,
                    evidence_ids=evidence_ids,
                    explanation=(
                        match.explanation
                        or "The reconciliation could not be confirmed safely. "
                        "Review the source records and supporting evidence."
                    ),
                    discriminator=match.match_id,
                )
            )

    # Analyze explicit evidence gaps on individual records.
    for record in records:
        missing_fields_raw = record.metadata.get("missing_fields", "")
        missing_fields = [
            field.strip()
            for field in re.split(r"[,;|]", missing_fields_raw)
            if field.strip()
        ]

        if missing_fields:
            findings.append(
                _make_finding(
                    finding_type=FindingType.INSUFFICIENT_EVIDENCE,
                    severity=FindingSeverity.WARNING,
                    summary="Financial record has missing fields",
                    record_ids=[record.record_id],
                    evidence_ids=_record_evidence_ids(record),
                    explanation=(
                        "The source extraction reported missing fields: "
                        + ", ".join(missing_fields)
                        + ". Verify the original document before relying on "
                        "this record for reconciliation or payment decisions."
                    ),
                    discriminator="missing_fields:" + ",".join(
                        sorted(_normalize(field) for field in missing_fields)
                    ),
                )
            )

        if (
            record.record_type == FinancialRecordType.BANK_TRANSACTION
            and _normalize(record.metadata.get("sign_status")) == "unresolved"
        ):
            findings.append(
                _make_finding(
                    finding_type=FindingType.INSUFFICIENT_EVIDENCE,
                    severity=FindingSeverity.HIGH,
                    summary="Bank transaction direction is unverified",
                    record_ids=[record.record_id],
                    evidence_ids=_record_evidence_ids(record),
                    explanation=(
                        "The transaction's debit/credit direction is unresolved. "
                        "Do not use it to confirm a payment or calculate an "
                        "outstanding balance until its sign is verified."
                    ),
                    discriminator="unresolved_transaction_sign",
                )
            )

    # Keep output deterministic and avoid exact duplicate findings.
    deduplicated: dict[str, FinancialFinding] = {}
    for finding in findings:
        deduplicated.setdefault(finding.finding_id, finding)

    return sorted(
        deduplicated.values(),
        key=lambda finding: (
            finding.finding_type.value,
            finding.finding_id,
        ),
    )
