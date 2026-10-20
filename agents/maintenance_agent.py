"""Maintenance & Property Operations Agent for ENIGMA."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from repository.domain_repository import DomainRepository
from repository.exceptions import EntityNotFoundError
from schemas.domain import (
    VendorCategory,
    WorkOrderCreate,
    WorkOrderPriority,
    WorkOrderRecord,
    WorkOrderStatus,
    WorkOrderUpdate,
)
from schemas.maintenance_agent import (
    AssetRecord,
    AutoDispatchWorkOrderRequest,
    AutoDispatchWorkOrderResult,
    CategoryCostSummary,
    InspectionAnalysisResult,
    InspectionItemStatus,
    InspectionReportCreate,
    MaintenanceCategory,
    MaintenanceCostAnalysisResult,
    MaintenanceFinding,
    MaintenanceFindingSeverity,
    MaintenanceFindingType,
    MaintenanceOperationsAnalysisRequest,
    MaintenanceOperationsAnalysisResult,
    MaintenanceTriageRequest,
    MaintenanceTriageResult,
    PreventiveTaskRecord,
    VendorMatchRecommendation,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MaintenanceAgent:
    """Intelligent agent managing maintenance requests, work orders, preventive schedules,
    property inspections, vendor coordination, asset health, and cost analytics.
    """

    def __init__(self, domain_repo: DomainRepository) -> None:
        self.domain_repo = domain_repo

    # -------------------------------------------------------------------------
    # 1. Maintenance Request Triage & Emergency Handling
    # -------------------------------------------------------------------------

    def triage_request(self, request: MaintenanceTriageRequest) -> MaintenanceTriageResult:
        text = request.description.lower()
        extracted_keywords: list[str] = []

        # 1. Emergency classification checks
        is_emergency = False
        emergency_rationale = None
        safety_instructions: list[str] = []

        if any(w in text for w in ("gas leak", "smell gas", "gas smell", "gas odor", "natural gas", "smelling gas")):
            is_emergency = True
            emergency_rationale = "Suspected gas leak creates imminent explosion and health risk."
            safety_instructions = [
                "Evacuate the unit and building immediately.",
                "Do not turn on or off any electric switches, lights, or appliances.",
                "Do not use phones inside the building.",
                "Call 911 / the emergency gas company from a safe distance.",
            ]
            extracted_keywords.append("gas_leak")

        elif any(w in text for w in ("active flood", "burst pipe", "water pouring", "ceiling collapsing", "flooding")):
            is_emergency = True
            emergency_rationale = "Active severe water leak causing rapid structural damage and safety risk."
            safety_instructions = [
                "Locate and shut off the main water valve immediately.",
                "Stay clear of water pooling near electrical fixtures or appliances.",
                "Disconnect electrical breakers to flooded rooms if safe to do so.",
            ]
            extracted_keywords.append("active_flooding")

        elif any(w in text for w in ("sparking", "electrical fire", "smoke from outlet", "burning smell from panel")):
            is_emergency = True
            emergency_rationale = "Electrical fault presenting direct fire and electrocution hazard."
            safety_instructions = [
                "Immediately switch off the main electrical breaker if safe to approach.",
                "Do not touch smoking outlets or sparking wires.",
                "Keep fire extinguisher ready; call 911 if flame or smoke persists.",
            ]
            extracted_keywords.append("electrical_fire_hazard")

        elif any(w in text for w in ("no heat", "furnace broken", "freezing inside")) and any(
            w in text for w in ("winter", "freezing", "sub zero", "cold weather", "elderly", "infant")
        ):
            is_emergency = True
            emergency_rationale = "Loss of heating during freezing weather threatens resident health and pipe freeze."
            safety_instructions = [
                "Keep faucets on a slight drip to prevent pipes from freezing.",
                "Use approved portable space heaters safely away from flammable materials.",
            ]
            extracted_keywords.append("no_heat_freezing")

        # 2. Category classification
        category = MaintenanceCategory.GENERAL
        suggested_vendor = VendorCategory.GENERAL

        plumbing_words = {"pipe", "leak", "toilet", "sink", "faucet", "drain", "clog", "water heater", "shower", "tub", "plumber"}
        electrical_words = {"outlet", "breaker", "light", "switch", "wire", "flicker", "power", "plug", "electrician"}
        hvac_words = {"ac", "heat", "air condition", "furnace", "thermostat", "cooling", "boiler", "filter", "vent"}
        appliance_words = {"fridge", "refrigerator", "stove", "oven", "dishwasher", "washer", "dryer", "microwave"}
        structural_words = {"window", "door", "lock", "roof", "ceiling", "drywall", "wall", "latch", "hinge", "tile"}
        pest_words = {"roach", "mouse", "rat", "bug", "ant", "pest", "termite", "rodent", "infestation"}
        security_words = {"break-in", "burglar", "vandal", "alarm", "intercom", "gate lock"}

        def _find_words(words: set[str]) -> list[str]:
            return [w for w in words if re.search(r"\b" + re.escape(w) + r"\b", text)]

        matched_pest = _find_words(pest_words)
        matched_security = _find_words(security_words)
        matched_appliance = _find_words(appliance_words)
        matched_plumbing = _find_words(plumbing_words)
        matched_electrical = _find_words(electrical_words)
        matched_hvac = _find_words(hvac_words)
        matched_structural = _find_words(structural_words)

        if matched_pest:
            category = MaintenanceCategory.PEST_CONTROL
            suggested_vendor = VendorCategory.GENERAL
            extracted_keywords.extend(matched_pest)
        elif matched_security:
            category = MaintenanceCategory.SECURITY
            suggested_vendor = VendorCategory.GENERAL
            extracted_keywords.extend(matched_security)
        elif matched_appliance:
            category = MaintenanceCategory.APPLIANCE
            suggested_vendor = VendorCategory.GENERAL
            extracted_keywords.extend(matched_appliance)
        elif matched_plumbing:
            category = MaintenanceCategory.PLUMBING
            suggested_vendor = VendorCategory.PLUMBING
            extracted_keywords.extend(matched_plumbing)
        elif matched_electrical:
            category = MaintenanceCategory.ELECTRICAL
            suggested_vendor = VendorCategory.ELECTRICAL
            extracted_keywords.extend(matched_electrical)
        elif matched_hvac:
            category = MaintenanceCategory.HVAC
            suggested_vendor = VendorCategory.HVAC
            extracted_keywords.extend(matched_hvac)
        elif matched_structural:
            category = MaintenanceCategory.STRUCTURAL
            suggested_vendor = VendorCategory.GENERAL
            extracted_keywords.extend(matched_structural)

        # 3. Priority & SLA Determination
        if is_emergency:
            priority = WorkOrderPriority.EMERGENCY
            sla_hours = 2
            action = "Dispatch emergency vendor immediately and verify resident safety instructions."
        elif any(w in text for w in ("urgent", "overflowing", "no water", "no power", "locked out", "major")):
            priority = WorkOrderPriority.HIGH
            sla_hours = 12
            action = "Assign qualified vendor within 4 hours; resolve within 12 hours."
        elif any(w in text for w in ("slow drain", "dripping", "squeak", "loose", "minor", "cosmetic")):
            priority = WorkOrderPriority.LOW
            sla_hours = 120  # 5 days
            action = "Schedule standard maintenance within normal business hours."
        else:
            priority = WorkOrderPriority.MEDIUM
            sla_hours = 48  # 2 days
            action = "Assign qualified vendor; target completion within 48 hours."

        return MaintenanceTriageResult(
            category=category,
            priority=priority,
            is_emergency=is_emergency,
            emergency_rationale=emergency_rationale,
            immediate_safety_instructions=safety_instructions,
            estimated_sla_hours=sla_hours,
            recommended_action=action,
            suggested_vendor_category=suggested_vendor,
            extracted_keywords=sorted(set(extracted_keywords)),
        )

    # -------------------------------------------------------------------------
    # 2. Vendor Coordination & Auto-Dispatch
    # -------------------------------------------------------------------------

    def recommend_vendors(
        self,
        property_id: str,
        category: VendorCategory,
        unit_id: str | None = None,
    ) -> list[VendorMatchRecommendation]:
        vendors = self.domain_repo.list_vendors(property_id=property_id, limit=100)
        active_wos = self.domain_repo.list_work_orders(property_id=property_id, limit=500)

        # Count active work orders per vendor
        vendor_loads: dict[str, int] = {}
        for wo in active_wos:
            if wo.vendor_id and wo.status in (WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS):
                vendor_loads[wo.vendor_id] = vendor_loads.get(wo.vendor_id, 0) + 1

        recommendations: list[VendorMatchRecommendation] = []
        for v in vendors:
            active_count = vendor_loads.get(v.vendor_id, 0)
            if v.category == category:
                score = 1.0 - min(0.3, active_count * 0.05)
                reason = f"Specialist in {category.value} with {active_count} active assignment(s)."
            elif v.category == VendorCategory.GENERAL:
                score = 0.6 - min(0.2, active_count * 0.05)
                reason = f"General maintenance contractor with {active_count} active assignment(s)."
            else:
                score = 0.2
                reason = f"Secondary contractor category ({v.category.value})."

            recommendations.append(
                VendorMatchRecommendation(
                    vendor_id=v.vendor_id,
                    vendor_name=v.name,
                    category=v.category,
                    match_score=round(max(0.0, score), 2),
                    reason=reason,
                    phone=v.phone,
                    email=v.email,
                    active_work_orders_count=active_count,
                )
            )

        recommendations.sort(key=lambda r: (r.match_score, -r.active_work_orders_count), reverse=True)
        return recommendations

    def auto_dispatch(
        self,
        property_id: str,
        request: AutoDispatchWorkOrderRequest,
    ) -> AutoDispatchWorkOrderResult:
        if request.work_order_id:
            wo = self.domain_repo.get_work_order(property_id, request.work_order_id)
            if not wo:
                raise EntityNotFoundError(f"Work order '{request.work_order_id}' not found.")
            title = wo.title
            desc = wo.description
            unit_id = wo.unit_id
            existing_wo_id = wo.work_order_id
            current_priority = wo.priority
        else:
            if not request.unit_id or not request.title or not request.description:
                raise ValueError("unit_id, title, and description are required to create a new work order.")
            title = request.title
            desc = request.description
            unit_id = request.unit_id
            existing_wo_id = None
            current_priority = None

        triage = self.triage_request(MaintenanceTriageRequest(description=f"{title} - {desc}", unit_id=unit_id))
        priority = current_priority or triage.priority

        candidate_vendors = self.recommend_vendors(
            property_id=property_id,
            category=triage.suggested_vendor_category,
            unit_id=unit_id,
        )

        assigned_vendor = candidate_vendors[0] if (candidate_vendors and request.auto_assign) else None
        now = _utc_now()
        sla_deadline = now + timedelta(hours=triage.estimated_sla_hours)

        if existing_wo_id:
            if assigned_vendor:
                self.domain_repo.update_work_order(
                    property_id=property_id,
                    work_order_id=existing_wo_id,
                    data=WorkOrderUpdate(
                        vendor_id=assigned_vendor.vendor_id,
                        status=WorkOrderStatus.ASSIGNED,
                        priority=priority,
                    ),
                )
            wo_id = existing_wo_id
            final_status = WorkOrderStatus.ASSIGNED if assigned_vendor else wo.status
        else:
            new_wo = self.domain_repo.create_work_order(
                property_id=property_id,
                data=WorkOrderCreate(
                    unit_id=unit_id,
                    resident_id=request.resident_id,
                    vendor_id=assigned_vendor.vendor_id if assigned_vendor else None,
                    title=title,
                    description=desc,
                    priority=priority,
                    status=WorkOrderStatus.ASSIGNED if assigned_vendor else WorkOrderStatus.OPEN,
                    estimated_cost=request.budget_limit,
                ),
            )
            wo_id = new_wo.work_order_id
            final_status = new_wo.status

        audit_msg = (
            f"Work order {wo_id} triaged as {triage.category.value} ({priority.value}). "
            + (f"Auto-assigned to vendor {assigned_vendor.vendor_name}." if assigned_vendor else "Awaiting vendor assignment.")
        )

        return AutoDispatchWorkOrderResult(
            work_order_id=wo_id,
            property_id=property_id,
            title=title,
            category=triage.category,
            priority=priority,
            status=final_status,
            assigned_vendor=assigned_vendor,
            available_candidate_vendors=candidate_vendors,
            sla_deadline=sla_deadline,
            audit_note=audit_msg,
        )

    # -------------------------------------------------------------------------
    # 3. Inspections & Property Checks
    # -------------------------------------------------------------------------

    def record_inspection(
        self,
        property_id: str,
        report: InspectionReportCreate,
    ) -> InspectionAnalysisResult:
        inspection_dict = self.domain_repo.create_inspection(property_id=property_id, data=report)
        items = report.items

        passed = [i for i in items if i.status == InspectionItemStatus.PASS]
        failed = [i for i in items if i.status in (InspectionItemStatus.FAIL, InspectionItemStatus.ATTENTION_NEEDED)]

        generated_wos: list[dict[str, Any]] = []
        findings: list[MaintenanceFinding] = []

        for item in failed:
            title = f"Inspection Deficiency: {item.item_name} (Unit {report.unit_id})"
            desc = (
                f"Deficiency detected during {report.inspection_type.value} inspection by {report.inspector_name}. "
                f"Notes: {item.notes or 'Immediate remediation required.'}"
            )
            created_wo = self.domain_repo.create_work_order(
                property_id=property_id,
                data=WorkOrderCreate(
                    unit_id=report.unit_id,
                    title=title,
                    description=desc,
                    priority=item.severity_if_failed,
                    status=WorkOrderStatus.OPEN,
                ),
            )
            generated_wos.append({
                "work_order_id": created_wo.work_order_id,
                "item_name": item.item_name,
                "priority": item.severity_if_failed.value,
            })

            findings.append(
                MaintenanceFinding(
                    finding_type=MaintenanceFindingType.INSPECTION_DEFICIENCY,
                    severity=MaintenanceFindingSeverity.HIGH if item.severity_if_failed in (WorkOrderPriority.HIGH, WorkOrderPriority.EMERGENCY) else MaintenanceFindingSeverity.WARNING,
                    title=f"Failed Inspection Item: {item.item_name}",
                    description=desc,
                    entity_ids=[inspection_dict["inspection_id"], created_wo.work_order_id],
                    evidence=[f"Unit: {report.unit_id}", f"Status: {item.status.value}", f"Notes: {item.notes or 'None'}"],
                    recommended_action="Dispatch maintenance vendor to resolve the failed item.",
                )
            )

        summary = (
            f"Completed {report.inspection_type.value} inspection for unit {report.unit_id}. "
            f"{len(passed)} passed, {len(failed)} failed/attention required. "
            f"Generated {len(generated_wos)} follow-up work order(s)."
        )

        return InspectionAnalysisResult(
            inspection_id=inspection_dict["inspection_id"],
            property_id=property_id,
            unit_id=report.unit_id,
            inspection_type=report.inspection_type,
            inspection_date=report.inspection_date or date.today(),
            items_inspected=len(items),
            items_passed=len(passed),
            items_failed=len(failed),
            generated_work_orders=generated_wos,
            findings=findings,
            summary=summary,
        )

    # -------------------------------------------------------------------------
    # 4. Preventive Maintenance & Operations Analysis
    # -------------------------------------------------------------------------

    def analyze_operations(
        self,
        property_id: str,
        request: MaintenanceOperationsAnalysisRequest | None = None,
    ) -> MaintenanceOperationsAnalysisResult:
        req = request or MaintenanceOperationsAnalysisRequest()
        if req.as_of_date is not None:
            as_of = req.as_of_date
            as_of_dt = datetime.combine(as_of, datetime.max.time().replace(microsecond=0), tzinfo=timezone.utc)
        else:
            as_of_dt = _utc_now()
            as_of = as_of_dt.date()

        work_orders = self._list_all(
            self.domain_repo.list_work_orders,
            property_id=property_id,
        )
        preventive_tasks = self._list_all(
            self.domain_repo.list_preventive_schedules,
            property_id=property_id,
        )
        assets = self._list_all(
            self.domain_repo.list_assets,
            property_id=property_id,
        )

        findings: list[MaintenanceFinding] = []

        open_wos = [wo for wo in work_orders if wo.status in (WorkOrderStatus.OPEN, WorkOrderStatus.ASSIGNED, WorkOrderStatus.IN_PROGRESS)]
        findings.extend(self._find_duplicate_work_orders(open_wos))

        findings.extend(
            self._find_recurring_unit_issues(
                work_orders=work_orders,
                as_of=as_of_dt,
                lookback_days=req.lookback_days,
            )
        )
        emergency_wos = [wo for wo in open_wos if wo.priority == WorkOrderPriority.EMERGENCY]
        overdue_wos: list[WorkOrderRecord] = []

        # 1. Work Order Overdue and Stalled checks
        for wo in open_wos:
            age_hours = max(0.0, (as_of_dt - wo.created_at).total_seconds() / 3600.0)

            # Default SLA by priority
            sla_threshold = req.overdue_sla_hours_threshold
            if wo.priority == WorkOrderPriority.EMERGENCY:
                sla_threshold = 4
            elif wo.priority == WorkOrderPriority.HIGH:
                sla_threshold = 24

            if age_hours > sla_threshold:
                overdue_wos.append(wo)
                findings.append(
                    MaintenanceFinding(
                        finding_type=MaintenanceFindingType.OVERDUE_WORK_ORDER,
                        severity=MaintenanceFindingSeverity.CRITICAL if wo.priority == WorkOrderPriority.EMERGENCY else MaintenanceFindingSeverity.HIGH,
                        title=f"Work Order Overdue: {wo.title}",
                        description=f"Work order '{wo.title}' ({wo.priority.value}) open for {int(age_hours)} hours, exceeding SLA of {sla_threshold} hours.",
                        entity_ids=[wo.work_order_id],
                        evidence=[f"Priority: {wo.priority.value}", f"Status: {wo.status.value}", f"Created: {wo.created_at.isoformat()}"],
                        recommended_action="Expedite vendor assignment or follow up with contractor immediately.",
                    )
                )

            # Unassigned high priority
            if wo.priority in (WorkOrderPriority.HIGH, WorkOrderPriority.EMERGENCY) and not wo.vendor_id:
                findings.append(
                    MaintenanceFinding(
                        finding_type=MaintenanceFindingType.UNASSIGNED_HIGH_PRIORITY,
                        severity=MaintenanceFindingSeverity.HIGH,
                        title=f"Unassigned Urgent Work Order: {wo.title}",
                        description=f"Work order '{wo.title}' is flagged as {wo.priority.value} but has no assigned contractor.",
                        entity_ids=[wo.work_order_id],
                        evidence=[f"Unit: {wo.unit_id}", f"Created: {wo.created_at.isoformat()}"],
                        recommended_action="Assign a specialized contractor immediately.",
                    )
                )

        # 2. Preventive tasks due / overdue
        due_preventive = 0
        for task in preventive_tasks:
            days_diff = (task.next_due_date - as_of).days
            if days_diff < 0:
                due_preventive += 1
                findings.append(
                    MaintenanceFinding(
                        finding_type=MaintenanceFindingType.PREVENTIVE_MAINTENANCE_OVERDUE,
                        severity=MaintenanceFindingSeverity.WARNING,
                        title=f"Overdue Preventive Task: {task.title}",
                        description=f"Task '{task.title}' was due on {task.next_due_date} ({abs(days_diff)} days overdue).",
                        entity_ids=[task.task_id],
                        evidence=[f"Category: {task.category.value}", f"Interval: {task.interval_days} days"],
                        recommended_action="Create work order to execute scheduled preventive maintenance.",
                    )
                )
            elif days_diff <= 14:
                due_preventive += 1
                findings.append(
                    MaintenanceFinding(
                        finding_type=MaintenanceFindingType.PREVENTIVE_MAINTENANCE_DUE,
                        severity=MaintenanceFindingSeverity.INFO,
                        title=f"Upcoming Preventive Task: {task.title}",
                        description=f"Task '{task.title}' is due in {days_diff} days on {task.next_due_date}.",
                        entity_ids=[task.task_id],
                        evidence=[f"Category: {task.category.value}", f"Due: {task.next_due_date}"],
                        recommended_action="Schedule maintenance visit before due date.",
                    )
                )

        # 3. Asset warranty and health analysis
        for asset in assets:
            if asset.warranty_expiry_date:
                w_days = (asset.warranty_expiry_date - as_of).days
                if 0 <= w_days <= 30:
                    findings.append(
                        MaintenanceFinding(
                            finding_type=MaintenanceFindingType.WARRANTY_EXPIRING_SOON,
                            severity=MaintenanceFindingSeverity.INFO,
                            title=f"Warranty Expiring Soon: {asset.name}",
                            description=f"Manufacturer warranty for {asset.name} (SN: {asset.serial_number or 'N/A'}) expires in {w_days} days.",
                            entity_ids=[asset.asset_id],
                            evidence=[f"Unit: {asset.unit_id or 'Building'}", f"Warranty Expiry: {asset.warranty_expiry_date}"],
                            recommended_action="Inspect asset to submit any pre-warranty expiration claims if needed.",
                        )
                    )
                elif w_days < 0:
                    findings.append(
                        MaintenanceFinding(
                            finding_type=MaintenanceFindingType.WARRANTY_EXPIRED,
                            severity=MaintenanceFindingSeverity.WARNING,
                            title=f"Warranty Expired: {asset.name}",
                            description=f"Manufacturer warranty for {asset.name} (SN: {asset.serial_number or 'N/A'}) expired {abs(w_days)} days ago on {asset.warranty_expiry_date}.",
                            entity_ids=[asset.asset_id],
                            evidence=[f"Unit: {asset.unit_id or 'Building'}", f"Warranty Expiry: {asset.warranty_expiry_date}"],
                            recommended_action="Review asset health and consider budgeting for replacement or maintenance agreement.",
                        )
                    )

        summary = (
            f"Operations scan as of {as_of.isoformat()}: {len(open_wos)} open work order(s), "
            f"{len(emergency_wos)} emergency, {len(overdue_wos)} overdue SLA, "
            f"{due_preventive} preventive task(s) due/overdue, {len(assets)} equipment asset(s) tracked. "
            f"Generated {len(findings)} operational finding(s)."
        )

        return MaintenanceOperationsAnalysisResult(
            property_id=property_id,
            analysis_date=as_of,
            open_work_orders_count=len(open_wos),
            emergency_count=len(emergency_wos),
            overdue_work_orders_count=len(overdue_wos),
            preventive_tasks_due_count=due_preventive,
            assets_tracked_count=len(assets),
            findings=findings,
            summary=summary,
        )


    
    @staticmethod
    def _list_all(
        list_method,
        *,
        property_id: str,
        page_size: int = 500,
    ) -> list:
        """Fetch all pages from a property-scoped repository method."""
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
    def _issue_key(work_order: WorkOrderRecord) -> str:
        """Create a normalized key for conservative text matching."""
        text = f"{work_order.title} {work_order.description}".casefold()
        text = re.sub(r"[^a-z0-9]+", " ", text)

        ignored_words = {
            "the", "a", "an", "and", "or", "in", "on", "at",
            "for", "to", "of", "unit", "please", "repair", "fix",
            "issue", "problem",
        }

        return " ".join(
            word for word in text.split()
            if word not in ignored_words
        )

    def _find_duplicate_work_orders(
        self,
        open_work_orders: list[WorkOrderRecord],
    ) -> list[MaintenanceFinding]:
        groups: dict[tuple[str, str], list[WorkOrderRecord]] = {}

        for wo in open_work_orders:
            key = self._issue_key(wo)

            if key:
                groups.setdefault((wo.unit_id, key), []).append(wo)

        findings = []

        for (unit_id, issue_key), group in groups.items():
            if len(group) < 2:
                continue

            ordered = sorted(group, key=lambda item: item.created_at)

            findings.append(
                MaintenanceFinding(
                    finding_type=MaintenanceFindingType.DUPLICATE_WORK_ORDER,
                    severity=MaintenanceFindingSeverity.WARNING,
                    title="Possible duplicate maintenance work orders",
                    description=(
                        f"{len(group)} active work orders for unit "
                        f"{unit_id} have matching normalized issue text."
                    ),
                    entity_ids=[
                        wo.work_order_id for wo in ordered
                    ],
                    evidence=[
                        f"Unit: {unit_id}",
                        f"Normalized issue: {issue_key}",
                    ] + [
                        f"{wo.work_order_id}: {wo.title}; "
                        f"created {wo.created_at.isoformat()}"
                        for wo in ordered
                    ],
                    recommended_action=(
                        "Review the work orders and confirm whether "
                        "they represent the same maintenance request."
                    ),
                )
            )

        return findings

    def _find_recurring_unit_issues(
        self,
        work_orders: list[WorkOrderRecord],
        as_of: datetime,
        lookback_days: int,
        minimum_occurrences: int = 3,
    ) -> list[MaintenanceFinding]:
        cutoff = as_of - timedelta(days=lookback_days)

        groups: dict[tuple[str, str], list[WorkOrderRecord]] = {}

        for wo in work_orders:
            if wo.created_at < cutoff or wo.created_at > as_of:
                continue

            key = self._issue_key(wo)

            if key:
                groups.setdefault((wo.unit_id, key), []).append(wo)

        findings = []

        for (unit_id, issue_key), group in groups.items():
            if len(group) < minimum_occurrences:
                continue

            ordered = sorted(group, key=lambda item: item.created_at)

            findings.append(
                MaintenanceFinding(
                    finding_type=MaintenanceFindingType.RECURRING_UNIT_ISSUE,
                    severity=MaintenanceFindingSeverity.HIGH,
                    title="Recurring maintenance issue detected",
                    description=(
                        f"Unit {unit_id} has {len(group)} work orders "
                        f"with matching issue text within the last "
                        f"{lookback_days} days."
                    ),
                    entity_ids=[
                        wo.work_order_id for wo in ordered
                    ],
                    evidence=[
                        f"Unit: {unit_id}",
                        f"Lookback start: {cutoff.date().isoformat()}",
                        f"Occurrences: {len(group)}",
                        f"Normalized issue: {issue_key}",
                    ] + [
                        f"{wo.work_order_id}: "
                        f"{wo.created_at.isoformat()} — {wo.title}"
                        for wo in ordered
                    ],
                    recommended_action=(
                        "Investigate the root cause and inspect the unit "
                        "or asset before repeating the repair."
                    ),
                )
            )

        return findings




    # -------------------------------------------------------------------------
    # 5. Cost Tracking & Reporting
    # -------------------------------------------------------------------------

    def analyze_costs(
        self,
        property_id: str,
        as_of_date: date | None = None,
    ) -> MaintenanceCostAnalysisResult:
        as_of = as_of_date or date.today()
        work_orders = self._list_all(
            self.domain_repo.list_work_orders,
            property_id=property_id,
        )

        total_est = Decimal("0.00")
        total_act = Decimal("0.00")
        completed_count = 0
        overruns: list[str] = []

        # Breakdown by category
        category_map: dict[str, dict[str, Any]] = {}
        unit_spends: dict[str, Decimal] = {}
        vendor_spends: dict[str, Decimal] = {}

        for wo in work_orders:
            est = wo.estimated_cost or Decimal("0.00")
            act = wo.actual_cost or Decimal("0.00")

            total_est += est
            total_act += act

            if wo.status == WorkOrderStatus.COMPLETED:
                completed_count += 1

            if wo.actual_cost and wo.estimated_cost and wo.actual_cost > wo.estimated_cost:
                overruns.append(wo.work_order_id)

            # Categorize from title/description
            triage = self.triage_request(MaintenanceTriageRequest(description=f"{wo.title} {wo.description}"))
            cat_name = triage.category.value

            if cat_name not in category_map:
                category_map[cat_name] = {
                    "count": 0,
                    "est": Decimal("0.00"),
                    "act": Decimal("0.00"),
                }
            category_map[cat_name]["count"] += 1
            category_map[cat_name]["est"] += est
            category_map[cat_name]["act"] += act

            # Unit spending
            unit_spends[wo.unit_id] = unit_spends.get(wo.unit_id, Decimal("0.00")) + act

            # Vendor spending
            if wo.vendor_id:
                vendor_spends[wo.vendor_id] = vendor_spends.get(wo.vendor_id, Decimal("0.00")) + act

        breakdown = [
            CategoryCostSummary(
                category=cat,
                work_order_count=data["count"],
                total_estimated_cost=data["est"],
                total_actual_cost=data["act"],
                variance=data["act"] - data["est"],
            )
            for cat, data in sorted(category_map.items())
        ]

        top_units = [
            {"unit_id": u, "total_spend": str(spend)}
            for u, spend in sorted(unit_spends.items(), key=lambda x: x[1], reverse=True)[:5]
        ]
        top_vendors = [
            {"vendor_id": v, "total_spend": str(spend)}
            for v, spend in sorted(vendor_spends.items(), key=lambda x: x[1], reverse=True)[:5]
        ]

        variance = total_act - total_est
        summary = (
            f"Maintenance cost report as of {as_of.isoformat()}: {len(work_orders)} work order(s) "
            f"({completed_count} completed). Total estimated: ${total_est:.2f}, total actual: ${total_act:.2f}. "
            f"Net variance: ${variance:.2f}. {len(overruns)} work order(s) experienced budget overruns."
        )

        return MaintenanceCostAnalysisResult(
            property_id=property_id,
            as_of_date=as_of,
            total_work_orders=len(work_orders),
            completed_work_orders=completed_count,
            total_estimated_spend=total_est,
            total_actual_spend=total_act,
            budget_variance=variance,
            cost_overrun_work_orders=overruns,
            category_breakdown=breakdown,
            top_spending_units=top_units,
            top_spending_vendors=top_vendors,
            summary=summary,
        )
