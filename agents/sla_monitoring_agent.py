"""SLA Monitoring and Escalation Agent for Enigma.

Proactively monitors maintenance request SLA deadlines, flags approaching
and overdue requests, creates deduplicated persistent escalations, and
manages the escalation lifecycle across property operations.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from repository.domain_repository import DomainRepository
from schemas.domain import (
    MaintenanceRequestRecord,
    MaintenanceRequestStatus,
    SlaEscalationCreate,
    SlaEscalationRecord,
    SlaEscalationStatus,
    SlaEscalationType,
    SlaMonitoringResult,
    WorkOrderStatus,
)


def _to_utc(value: datetime | str) -> datetime:
    """Safely convert ISO string or datetime to timezone-aware UTC datetime."""
    if isinstance(value, str):
        val = value.strip()
        if val.endswith("Z"):
            val = val[:-1] + "+00:00"
        dt = datetime.fromisoformat(val)
    else:
        dt = value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SLAMonitoringAgent:
    """Agent responsible for SLA deadline evaluation, proactive warning detection,
    overdue escalation management, and alert lifecycle deduplication.
    """

    def __init__(self, domain_repo: DomainRepository) -> None:
        self.domain_repo = domain_repo

    def evaluate_request(
        self,
        request: MaintenanceRequestRecord,
        warning_window_hours: float = 2.0,
        now: datetime | None = None,
    ) -> SlaEscalationType | None:
        """Evaluate a single maintenance request's SLA deadline status.

        Returns:
            - SlaEscalationType.OVERDUE if deadline has passed.
            - SlaEscalationType.APPROACHING_DEADLINE if within warning window.
            - None if request is not approaching or overdue, or is excluded.
        """
        if request.sla_deadline_at is None:
            return None

        # Exclude closed and cancelled requests
        if request.status in (
            MaintenanceRequestStatus.CLOSED,
            MaintenanceRequestStatus.CANCELLED,
        ):
            return None

        # For converted requests, check if the linked work order is completed or cancelled
        if (
            request.status == MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER
            and request.work_order_id
        ):
            work_order = self.domain_repo.get_work_order(
                request.property_id, request.work_order_id
            )
            if work_order and work_order.status in (
                WorkOrderStatus.COMPLETED,
                WorkOrderStatus.CANCELLED,
            ):
                return None

        if now is None:
            now = datetime.now(timezone.utc)
        else:
            now = _to_utc(now)

        deadline = _to_utc(request.sla_deadline_at)

        if deadline < now:
            return SlaEscalationType.OVERDUE

        warning_threshold = now + timedelta(hours=warning_window_hours)
        if now <= deadline <= warning_threshold:
            return SlaEscalationType.APPROACHING_DEADLINE

        return None

    def monitor_property_sla(
        self,
        property_id: str,
        warning_window_hours: float = 2.0,
        now: datetime | None = None,
    ) -> SlaMonitoringResult:
        """Run an SLA monitoring cycle for a property.

        - Evaluates approaching and overdue SLA deadlines.
        - Deduplicates active escalations so repeated runs are safe.
        - Transitions approaching alerts to overdue without contradictory alerts.
        - Resolves active escalations for closed/cancelled requests.
        - Preserves acknowledged alert state.
        """
        self.domain_repo._ensure_property_exists(property_id)

        if now is None:
            now = datetime.now(timezone.utc)
        else:
            now = _to_utc(now)

        # 1. Clean up active escalations for requests that are closed, cancelled, or completed
        all_active_escalations = [
            e
            for e in self.domain_repo.list_sla_escalations(
                property_id, limit=1000
            )
            if e.status in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
        ]

        for esc in all_active_escalations:
            req = self.domain_repo.get_maintenance_request(
                property_id, esc.request_id
            )
            if (
                req is None
                or req.status
                in (
                    MaintenanceRequestStatus.CLOSED,
                    MaintenanceRequestStatus.CANCELLED,
                )
            ):
                self.domain_repo.resolve_sla_escalation(
                    property_id,
                    esc.escalation_id,
                    notes="Resolved automatically: maintenance request closed or cancelled.",
                )
            elif (
                req.status == MaintenanceRequestStatus.CONVERTED_TO_WORK_ORDER
                and req.work_order_id
            ):
                wo = self.domain_repo.get_work_order(
                    property_id, req.work_order_id
                )
                if wo and wo.status in (
                    WorkOrderStatus.COMPLETED,
                    WorkOrderStatus.CANCELLED,
                ):
                    self.domain_repo.resolve_sla_escalation(
                        property_id,
                        esc.escalation_id,
                        notes=f"Resolved automatically: work order is {wo.status.value}.",
                    )

        # 2. Evaluate all eligible requests for this property
        eligible_requests = (
            self.domain_repo.get_eligible_maintenance_requests_for_sla(
                property_id
            )
        )

        evaluated_count = 0
        approaching_count = 0
        overdue_count = 0
        new_escalations_count = 0
        existing_escalations_count = 0
        escalation_records: list[SlaEscalationRecord] = []

        for req in eligible_requests:
            condition = self.evaluate_request(
                req, warning_window_hours=warning_window_hours, now=now
            )
            evaluated_count += 1

            if condition == SlaEscalationType.OVERDUE:
                overdue_count += 1
                active_for_req = [
                    e
                    for e in self.domain_repo.list_sla_escalations(
                        property_id, request_id=req.request_id
                    )
                    if e.status
                    in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
                ]

                has_active_overdue = False
                for active_esc in active_for_req:
                    if active_esc.escalation_type == SlaEscalationType.OVERDUE:
                        has_active_overdue = True
                        existing_escalations_count += 1
                        escalation_records.append(active_esc)
                    elif (
                        active_esc.escalation_type
                        == SlaEscalationType.APPROACHING_DEADLINE
                    ):
                        # Transition approaching alert to resolved (superseded by overdue)
                        self.domain_repo.resolve_sla_escalation(
                            property_id,
                            active_esc.escalation_id,
                            notes=f"Superseded by overdue escalation at {now.isoformat()}.",
                        )

                if not has_active_overdue:
                    # Check if this exact overdue condition was already resolved
                    deadline_dt = _to_utc(req.sla_deadline_at)
                    past_escalations = self.domain_repo.list_sla_escalations(
                        property_id,
                        request_id=req.request_id,
                        escalation_type=SlaEscalationType.OVERDUE,
                    )
                    already_handled = any(
                        e.status == SlaEscalationStatus.RESOLVED
                        and _to_utc(e.sla_deadline_at) == deadline_dt
                        for e in past_escalations
                    )
                    if not already_handled:
                        new_record = self.domain_repo.create_sla_escalation(
                            property_id,
                            SlaEscalationCreate(
                                request_id=req.request_id,
                                escalation_type=SlaEscalationType.OVERDUE,
                                sla_deadline_at=deadline_dt,
                                detected_at=now,
                                notes=f"Overdue SLA deadline: passed at {deadline_dt.isoformat()}.",
                            ),
                        )
                        new_escalations_count += 1
                        escalation_records.append(new_record)

            elif condition == SlaEscalationType.APPROACHING_DEADLINE:
                approaching_count += 1
                active_for_req = [
                    e
                    for e in self.domain_repo.list_sla_escalations(
                        property_id, request_id=req.request_id
                    )
                    if e.status
                    in (SlaEscalationStatus.OPEN, SlaEscalationStatus.ACKNOWLEDGED)
                ]

                has_active_approaching = False
                for active_esc in active_for_req:
                    if (
                        active_esc.escalation_type
                        == SlaEscalationType.APPROACHING_DEADLINE
                    ):
                        has_active_approaching = True
                        existing_escalations_count += 1
                        escalation_records.append(active_esc)

                if not has_active_approaching:
                    deadline_dt = _to_utc(req.sla_deadline_at)
                    past_escalations = self.domain_repo.list_sla_escalations(
                        property_id,
                        request_id=req.request_id,
                        escalation_type=SlaEscalationType.APPROACHING_DEADLINE,
                    )
                    already_handled = any(
                        e.status == SlaEscalationStatus.RESOLVED
                        and _to_utc(e.sla_deadline_at) == deadline_dt
                        for e in past_escalations
                    )
                    if not already_handled:
                        new_record = self.domain_repo.create_sla_escalation(
                            property_id,
                            SlaEscalationCreate(
                                request_id=req.request_id,
                                escalation_type=SlaEscalationType.APPROACHING_DEADLINE,
                                sla_deadline_at=deadline_dt,
                                detected_at=now,
                                notes=(
                                    f"Approaching SLA deadline: due within "
                                    f"{warning_window_hours:.1f} hours ({deadline_dt.isoformat()})."
                                ),
                            ),
                        )
                        new_escalations_count += 1
                        escalation_records.append(new_record)

        return SlaMonitoringResult(
            property_id=property_id,
            evaluated_count=evaluated_count,
            approaching_deadline_count=approaching_count,
            overdue_count=overdue_count,
            new_escalations_count=new_escalations_count,
            existing_escalations_count=existing_escalations_count,
            escalations=escalation_records,
            monitoring_timestamp=now,
        )


# Alias for architectural flexibility
SLAMonitoringService = SLAMonitoringAgent
