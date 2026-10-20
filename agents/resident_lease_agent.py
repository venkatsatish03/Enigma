
"""Read-only, deterministic resident and lease intelligence."""
from __future__ import annotations

from datetime import date, timedelta

from repository.domain_repository import DomainRepository
from schemas.domain import LeaseRecord, LeaseStatus, ResidentRecord
from schemas.resident_lease_agent import (
    FindingSeverity,
    FindingType,
    ResidentLeaseAnalysisRequest,
    ResidentLeaseAnalysisResult,
    ResidentLeaseFinding,
)


class ResidentLeaseAgent:
    """Analyze resident records and lease lifecycle risks for one property.

    This agent is intentionally read-only. It does not modify domain records,
    send communications, or make legal or renewal eligibility decisions.
    """

    def __init__(self, domain_repo: DomainRepository):
        self.domain_repo = domain_repo

    def analyze(
        self,
        property_id: str,
        request: ResidentLeaseAnalysisRequest | None = None,
    ) -> ResidentLeaseAnalysisResult:
        if not property_id or not property_id.strip():
            raise ValueError("property_id must not be empty.")

        request = request or ResidentLeaseAnalysisRequest()
        as_of = request.as_of_date or date.today()

        # Use the repository's property-scoped methods. Fetch up to the
        # repository's supported maximum in this initial implementation.
        residents = self._list_all(
            self.domain_repo.list_residents,
            property_id,
        )

        leases = self._list_all(
            self.domain_repo.list_leases,
            property_id,
        )

        findings: list[ResidentLeaseFinding] = []
        findings.extend(self._find_duplicate_residents(residents))
        findings.extend(self._find_incomplete_residents(residents))
        findings.extend(
            self._find_lease_date_risks(
                leases=leases,
                as_of=as_of,
                expiration_window_days=request.expiration_window_days,
            )
        )
        findings.extend(self._find_overlapping_leases(leases))

        return ResidentLeaseAnalysisResult(
            property_id=property_id,
            analysis_date=as_of,
            expiration_window_days=request.expiration_window_days,
            residents_analyzed=len(residents),
            leases_analyzed=len(leases),
            findings=findings,
            summary=(
                f"Analyzed {len(residents)} residents and {len(leases)} leases; "
                f"identified {len(findings)} finding(s)."
            ),
        )


    @staticmethod
    def _list_all(
        list_method,
        property_id: str,
        page_size: int = 1000,
    ):
        records = []
        offset = 0

        while True:
            page = list_method(
                property_id=property_id,
                limit=page_size,
                offset=offset,
            )
            records.extend(page)

            if len(page) < page_size:
                break

            offset += len(page)

        return records



    @staticmethod
    def _find_duplicate_residents(
        residents: list[ResidentRecord],
    ) -> list[ResidentLeaseFinding]:
        groups: dict[str, list[ResidentRecord]] = {}

        for resident in residents:
            email_key = resident.email.strip().casefold()
            if email_key:
                groups.setdefault(email_key, []).append(resident)

        findings = []
        for email, group in groups.items():
            if len(group) < 2:
                continue

            findings.append(
                ResidentLeaseFinding(
                    finding_type=FindingType.DUPLICATE_RESIDENT,
                    severity=FindingSeverity.WARNING,
                    title="Possible duplicate resident records",
                    description=(
                        f"{len(group)} resident records use the same email address."
                    ),
                    entity_ids=[r.resident_id for r in group],
                    evidence=[
                        f"Email: {email}",
                        *[
                            f"{r.first_name} {r.last_name} "
                            f"(resident_id={r.resident_id}, status={r.status.value})"
                            for r in group
                        ],
                    ],
                    recommended_action=(
                        "Review the records and confirm whether they represent "
                        "the same person before merging or changing any data."
                    ),
                )
            )

        return findings

    @staticmethod
    def _find_incomplete_residents(
        residents: list[ResidentRecord],
    ) -> list[ResidentLeaseFinding]:
        findings = []

        for resident in residents:
            missing = []
            if not resident.first_name.strip():
                missing.append("first_name")
            if not resident.last_name.strip():
                missing.append("last_name")
            if not resident.email.strip():
                missing.append("email")

            if missing:
                findings.append(
                    ResidentLeaseFinding(
                        finding_type=FindingType.INCOMPLETE_RESIDENT,
                        severity=FindingSeverity.WARNING,
                        title="Incomplete resident record",
                        description=(
                            "A resident record is missing required identity fields."
                        ),
                        entity_ids=[resident.resident_id],
                        evidence=[f"Missing fields: {', '.join(missing)}"],
                        recommended_action=(
                            "Review the resident record and complete the missing "
                            "fields using verified information."
                        ),
                    )
                )

        return findings

    @staticmethod
    def _find_lease_date_risks(
        leases: list[LeaseRecord],
        as_of: date,
        expiration_window_days: int,
    ) -> list[ResidentLeaseFinding]:
        findings = []
        window_end = as_of + timedelta(days=expiration_window_days)

        for lease in leases:
            if lease.status != LeaseStatus.ACTIVE:
                continue

            if lease.end_date < as_of:
                findings.append(
                    ResidentLeaseFinding(
                        finding_type=FindingType.EXPIRED_ACTIVE_LEASE,
                        severity=FindingSeverity.HIGH,
                        title="Lease marked active after its end date",
                        description=(
                            f"Lease {lease.lease_id} ended on {lease.end_date.isoformat()} "
                            f"but is still marked active as of {as_of.isoformat()}."
                        ),
                        entity_ids=[lease.lease_id],
                        evidence=[
                            f"start_date={lease.start_date.isoformat()}",
                            f"end_date={lease.end_date.isoformat()}",
                            f"status={lease.status.value}",
                            f"analysis_date={as_of.isoformat()}",
                        ],
                        recommended_action=(
                            "Verify the lease and update its status if appropriate. "
                            "Do not change the status automatically."
                        ),
                    )
                )
            elif lease.start_date > as_of:
                findings.append(
                    ResidentLeaseFinding(
                        finding_type=FindingType.FUTURE_ACTIVE_LEASE,
                        severity=FindingSeverity.WARNING,
                        title="Future lease marked active",
                        description=(
                            f"Lease {lease.lease_id} starts on "
                            f"{lease.start_date.isoformat()} but is already marked active."
                        ),
                        entity_ids=[lease.lease_id],
                        evidence=[
                            f"start_date={lease.start_date.isoformat()}",
                            f"analysis_date={as_of.isoformat()}",
                            f"status={lease.status.value}",
                        ],
                        recommended_action=(
                            "Confirm the intended lease status and start date."
                        ),
                    )
                )
            elif as_of <= lease.end_date <= window_end:
                findings.append(
                    ResidentLeaseFinding(
                        finding_type=FindingType.UPCOMING_LEASE_EXPIRATION,
                        severity=FindingSeverity.WARNING,
                        title="Lease expiration approaching",
                        description=(
                            f"Lease {lease.lease_id} ends on "
                            f"{lease.end_date.isoformat()}, within the next "
                            f"{expiration_window_days} day(s)."
                        ),
                        entity_ids=[lease.lease_id],
                        evidence=[
                            f"end_date={lease.end_date.isoformat()}",
                            f"analysis_date={as_of.isoformat()}",
                            f"window_end={window_end.isoformat()}",
                        ],
                        recommended_action=(
                            "Review the lease and decide whether to initiate a "
                            "renewal discussion or another appropriate next step."
                        ),
                    )
                )

        return findings

    @staticmethod
    def _find_overlapping_leases(
        leases: list[LeaseRecord],
    ) -> list[ResidentLeaseFinding]:
        findings = []
        active_candidates = [
            lease
            for lease in leases
            if lease.status in (LeaseStatus.ACTIVE, LeaseStatus.PENDING)
        ]

        for index, first in enumerate(active_candidates):
            for second in active_candidates[index + 1:]:
                # Date ranges are inclusive. Two leases overlap when each
                # begins on or before the other's end date.
                overlaps = (
                    first.start_date <= second.end_date
                    and second.start_date <= first.end_date
                )
                if not overlaps:
                    continue

                if first.unit_id == second.unit_id:
                    findings.append(
                        ResidentLeaseFinding(
                            finding_type=FindingType.OVERLAPPING_UNIT_LEASES,
                            severity=FindingSeverity.HIGH,
                            title="Overlapping leases for the same unit",
                            description=(
                                f"Leases {first.lease_id} and {second.lease_id} "
                                f"have overlapping dates for unit {first.unit_id}."
                            ),
                            entity_ids=[
                                first.lease_id,
                                second.lease_id,
                                first.unit_id,
                            ],
                            evidence=[
                                f"Lease {first.lease_id}: "
                                f"{first.start_date} to {first.end_date} "
                                f"({first.status.value})",
                                f"Lease {second.lease_id}: "
                                f"{second.start_date} to {second.end_date} "
                                f"({second.status.value})",
                            ],
                            recommended_action=(
                                "Review both lease records and confirm the correct "
                                "occupancy dates before changing either lease."
                            ),
                        )
                    )

                if (
                    first.resident_id == second.resident_id
                    and first.lease_id != second.lease_id
                ):
                    findings.append(
                        ResidentLeaseFinding(
                            finding_type=FindingType.OVERLAPPING_RESIDENT_LEASES,
                            severity=FindingSeverity.WARNING,
                            title="Overlapping leases for the same resident",
                            description=(
                                f"Resident {first.resident_id} has overlapping "
                                f"leases {first.lease_id} and {second.lease_id}."
                            ),
                            entity_ids=[
                                first.lease_id,
                                second.lease_id,
                                first.resident_id,
                            ],
                            evidence=[
                                f"Lease {first.lease_id}: "
                                f"{first.start_date} to {first.end_date}",
                                f"Lease {second.lease_id}: "
                                f"{second.start_date} to {second.end_date}",
                            ],
                            recommended_action=(
                                "Review the records to determine whether the overlap "
                                "is intentional, such as a unit transfer."
                            ),
                        )
                    )

        return findings
