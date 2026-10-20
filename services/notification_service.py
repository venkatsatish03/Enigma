"""Email Notification Service for Financial Operations and Compliance.

Supports:
- Overdue rent notices
- Compliance alerts
- Owner financial statements
- Expense approval notifications
- HTML and plain-text template rendering
- Safe previewing without delivery
- Idempotent duplicate-send prevention
- Configurable SMTP delivery with explicit Simulation Mode
- Error handling, retries, and database audit logging
"""

from __future__ import annotations

import os
import smtplib
from datetime import datetime, timezone
from decimal import Decimal
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, Optional, Tuple

from repository.domain_repository import DomainRepository, EntityNotFoundError, IntegrityConflictError
from schemas.domain import (
    NotificationDeliveryMode,
    NotificationPreviewRequest,
    NotificationPreviewResponse,
    NotificationRecord,
    NotificationSendRequest,
    NotificationStatus,
    NotificationType,
)


class NotificationService:
    def __init__(self, domain_repo: DomainRepository):
        self.repo = domain_repo

    @property
    def is_simulation_mode(self) -> bool:
        """Determines if email service is in simulation mode (default: True)."""
        env_val = os.getenv("EMAIL_SIMULATION_MODE", "true").strip().lower()
        if env_val in ("0", "false", "no", "off"):
            return not bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_USERNAME"))
        return True

    @property
    def smtp_from_email(self) -> str:
        return os.getenv("SMTP_FROM_EMAIL", "finance-notifications@enigma-pm.internal")

    def render_email_template(
        self,
        notification_type: NotificationType,
        context: Dict[str, Any],
        recipient_name: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Render subject, plain text, and HTML body based on notification type and context.
        """
        property_name = context.get("property_name", "Property Portfolio")

        if notification_type == NotificationType.OVERDUE_RENT:
            name = recipient_name or context.get("resident_name", "Resident")
            unit_id = context.get("unit_id", "N/A")
            invoice_num = context.get("invoice_number", "N/A")
            amount = context.get("amount", "0.00")
            due_date = context.get("due_date", "N/A")
            days_overdue = context.get("days_overdue", "0")

            subject = f"[Action Required] Overdue Rent Notice - {property_name} (Unit {unit_id})"
            text_body = (
                f"Dear {name},\n\n"
                f"This is an official notice that rent invoice '{invoice_num}' for Unit {unit_id} at {property_name} "
                f"is currently overdue by {days_overdue} day(s).\n\n"
                f"Outstanding Balance: ${amount}\n"
                f"Original Due Date: {due_date}\n\n"
                f"Please submit payment immediately via the resident portal or contact the management office.\n\n"
                f"Sincerely,\nEnigma Property Management Finance Team"
            )
            html_body = (
                f"<html><body>"
                f"<h2>Overdue Rent Notice</h2>"
                f"<p>Dear <strong>{name}</strong>,</p>"
                f"<p>This is an official notification that rent invoice <code>{invoice_num}</code> for <strong>Unit {unit_id}</strong> "
                f"at <em>{property_name}</em> is overdue by <strong>{days_overdue} day(s)</strong>.</p>"
                f"<table style='border:1px solid #ddd; padding:8px; border-collapse:collapse;'>"
                f"<tr><td style='padding:6px; font-weight:bold;'>Outstanding Amount:</td><td style='padding:6px; color:#c00;'>${amount}</td></tr>"
                f"<tr><td style='padding:6px; font-weight:bold;'>Original Due Date:</td><td style='padding:6px;'>{due_date}</td></tr>"
                f"</table>"
                f"<p>Please submit payment through the resident portal immediately to avoid late penalties.</p>"
                f"<br><p>Enigma Property Management Finance Team</p>"
                f"</body></html>"
            )

        elif notification_type == NotificationType.COMPLIANCE_ALERT:
            title = context.get("title", "Operational Compliance Alert")
            severity = context.get("severity", "HIGH")
            rule_id = context.get("rule_id", "N/A")
            details = context.get("details", "")

            subject = f"[{severity.upper()}] Compliance Alert: {title} ({property_name})"
            text_body = (
                f"COMPLIANCE ALERT - {property_name}\n\n"
                f"Title: {title}\n"
                f"Severity: {severity.upper()}\n"
                f"Rule: {rule_id}\n\n"
                f"Details:\n{details}\n\n"
                f"This matter requires review and resolution according to standard operating compliance procedures.\n"
            )
            html_body = (
                f"<html><body>"
                f"<h2 style='color:#d9534f;'>Compliance Operational Alert</h2>"
                f"<p><strong>Property:</strong> {property_name}</p>"
                f"<p><strong>Rule Triggered:</strong> <code>{rule_id}</code> | <strong>Severity:</strong> <span style='color:red;'>{severity.upper()}</span></p>"
                f"<div style='background:#f9f9f9; padding:12px; border-left:4px solid #d9534f; margin:10px 0;'>"
                f"<strong>{title}</strong><br>{details}"
                f"</div>"
                f"<p>Immediate operational review required.</p>"
                f"</body></html>"
            )

        elif notification_type == NotificationType.OWNER_STATEMENT:
            period = context.get("period", "Current Period")
            billed = context.get("total_billed", "0.00")
            collected = context.get("total_collected", "0.00")
            expenses = context.get("total_expenses", "0.00")
            noi = context.get("net_operating_income", "0.00")

            subject = f"Owner Financial Statement - {property_name} - {period}"
            text_body = (
                f"Owner Financial Statement - {property_name}\n"
                f"Period: {period}\n\n"
                f"Total Billed Rent:      ${billed}\n"
                f"Total Collected Rent:   ${collected}\n"
                f"Operating Expenses:     ${expenses}\n"
                f"----------------------------------------\n"
                f"Net Operating Income:   ${noi}\n\n"
                f"The complete detailed ledger has been recorded in your property portfolio account.\n"
            )
            html_body = (
                f"<html><body>"
                f"<h2>Owner Financial Statement</h2>"
                f"<p><strong>Property:</strong> {property_name} | <strong>Period:</strong> {period}</p>"
                f"<table style='width:100%; max-width:500px; border:1px solid #ccc; border-collapse:collapse;'>"
                f"<tr style='background:#f2f2f2;'><th style='padding:8px; text-align:left;'>Metric</th><th style='padding:8px; text-align:right;'>Amount</th></tr>"
                f"<tr><td style='padding:8px;'>Total Rent Billed</td><td style='padding:8px; text-align:right;'>${billed}</td></tr>"
                f"<tr><td style='padding:8px;'>Total Rent Collected</td><td style='padding:8px; text-align:right; color:green;'>${collected}</td></tr>"
                f"<tr><td style='padding:8px;'>Operating Expenses</td><td style='padding:8px; text-align:right; color:red;'>${expenses}</td></tr>"
                f"<tr style='font-weight:bold; background:#e8f4fd;'><td style='padding:8px;'>Net Operating Income</td><td style='padding:8px; text-align:right;'>${noi}</td></tr>"
                f"</table>"
                f"</body></html>"
            )

        else:
            title = context.get("title", "Financial Notification")
            message = context.get("message", "")
            subject = f"[Enigma Finance] {title} - {property_name}"
            text_body = f"{title}\n\n{message}\n"
            html_body = f"<html><body><h3>{title}</h3><p>{message}</p></body></html>"

        return subject, text_body, html_body

    def preview_notification(
        self,
        property_id: str,
        request: NotificationPreviewRequest,
    ) -> NotificationPreviewResponse:
        """Render and preview email notification without triggering dispatch."""
        subject, text_body, html_body = self.render_email_template(
            request.notification_type,
            request.context_data,
            recipient_name=request.recipient_name,
        )
        return NotificationPreviewResponse(
            notification_type=request.notification_type,
            recipient_email=request.recipient_email,
            recipient_name=request.recipient_name,
            subject=subject,
            body_text=text_body,
            body_html=html_body,
        )

    def send_notification(
        self,
        property_id: str,
        request: NotificationSendRequest,
        created_by: str = "system",
    ) -> NotificationRecord:
        """
        Safely dispatch an email notification.
        Supports simulation mode (verified without external SMTP) and real SMTP.
        """
        subject, text_body, html_body = self.render_email_template(
            request.notification_type,
            request.context_data,
            recipient_name=request.recipient_name,
        )
        if request.subject:
            subject = request.subject

        simulation = self.is_simulation_mode

        initial_status = NotificationStatus.PENDING
        delivery_mode = (
            NotificationDeliveryMode.SIMULATED
            if simulation
            else NotificationDeliveryMode.SMTP
        )

        record = self.repo.create_notification(
            property_id=property_id,
            recipient_email=request.recipient_email,
            subject=subject,
            body_text=text_body,
            notification_type=request.notification_type,
            status=initial_status,
            delivery_mode=delivery_mode,
            recipient_name=request.recipient_name,
            body_html=html_body,
            idempotency_key=request.idempotency_key,
        )

        # If duplicate send returned existing record
        if record.status in (NotificationStatus.SENT, NotificationStatus.SIMULATED):
            return record

        if simulation:
            return self.repo.update_notification_status(
                property_id=property_id,
                notification_id=record.notification_id,
                status=NotificationStatus.SIMULATED,
                error_message="Simulation mode active: verified without live transmission.",
            )

        # Live SMTP delivery
        try:
            host = os.getenv("SMTP_HOST")
            port = int(os.getenv("SMTP_PORT", "587"))
            username = os.getenv("SMTP_USERNAME")
            password = os.getenv("SMTP_PASSWORD")
            use_tls = os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes")

            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = self.smtp_from_email
            msg["To"] = request.recipient_email

            msg.attach(MIMEText(text_body, "plain"))
            if html_body:
                msg.attach(MIMEText(html_body, "html"))

            with smtplib.SMTP(host, port, timeout=10) as server:
                if use_tls:
                    server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)

            return self.repo.update_notification_status(
                property_id=property_id,
                notification_id=record.notification_id,
                status=NotificationStatus.SENT,
            )
        except Exception as exc:
            return self.repo.update_notification_status(
                property_id=property_id,
                notification_id=record.notification_id,
                status=NotificationStatus.FAILED,
                error_message=f"SMTP transmission failed: {str(exc)}",
                increment_retry=True,
            )
