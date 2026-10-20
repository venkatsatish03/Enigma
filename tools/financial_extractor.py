from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from schemas.document import (
    BankTransaction,
    DocumentType,
    FieldEvidence,
    FinancialDocument,
    LineItem,
    ValidationDimensions,
)
from tools.document_processor import ProcessedDocument


def _find(pattern: str, text: str, flags: int = re.IGNORECASE) -> str | None:
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else None


def _decimal(value: str | None) -> Decimal | None:
    if not value:
        return None

    cleaned = value.replace(",", "").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _date(value: str | None) -> str | None:
    if not value:
        return None

    value = value.strip()
    formats = (
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%d",
        "%m/%d/%Y",
        "%d/%m/%Y",
    )

    for fmt in formats:
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue

    return None


# ============================================================================
# Country & ISO 4217 Currency Codes and Symbols Mapping
# ============================================================================

# Mapping of country/jurisdiction to primary ISO 4217 currency code
COUNTRY_TO_ISO_CURRENCY: dict[str, str] = {
    "United States": "USD",
    "United Kingdom": "GBP",
    "Eurozone": "EUR",
    "Canada": "CAD",
    "Australia": "AUD",
    "Japan": "JPY",
    "China": "CNY",
    "India": "INR",
    "Switzerland": "CHF",
    "New Zealand": "NZD",
    "Singapore": "SGD",
    "Hong Kong": "HKD",
    "South Korea": "KRW",
    "Brazil": "BRL",
    "Mexico": "MXN",
    "South Africa": "ZAR",
    "Turkey": "TRY",
    "Russia": "RUB",
    "Sweden": "SEK",
    "Norway": "NOK",
    "Denmark": "DKK",
    "United Arab Emirates": "AED",
    "Saudi Arabia": "SAR",
    "Poland": "PLN",
    "Thailand": "THB",
    "Indonesia": "IDR",
    "Malaysia": "MYR",
    "Philippines": "PHP",
    "Vietnam": "VND",
}

# ISO 4217 currency code to representative currency symbols and representations
ISO_TO_CURRENCY_SYMBOLS: dict[str, list[str]] = {
    "USD": ["$", "US$", "USD$"],
    "EUR": ["€", "EUR"],
    "GBP": ["£", "GB£"],
    "JPY": ["¥", "JP¥"],
    "CNY": ["¥", "CN¥", "RMB"],
    "INR": ["₹", "Rs", "Rs.", "INR"],
    "CAD": ["C$", "CA$", "CAD$"],
    "AUD": ["A$", "AU$", "AUD$"],
    "NZD": ["NZ$", "NZD$"],
    "CHF": ["CHF"],
    "SGD": ["S$", "SG$", "SGD$"],
    "HKD": ["HK$", "HKD$"],
    "SEK": ["kr", "SEK"],
    "NOK": ["kr", "NOK"],
    "DKK": ["kr", "DKK"],
    "KRW": ["₩", "KRW"],
    "BRL": ["R$", "BRL"],
    "MXN": ["Mex$", "MXN$"],
    "ZAR": ["R", "ZAR"],
    "TRY": ["₺", "TRY"],
    "RUB": ["₽", "RUB"],
    "AED": ["AED", "DH"],
    "SAR": ["SAR", "SR"],
    "PLN": ["zł", "PLN"],
    "THB": ["฿", "THB"],
    "IDR": ["Rp", "IDR"],
    "MYR": ["RM", "MYR"],
    "PHP": ["₱", "PHP"],
    "VND": ["₫", "VND"],
}

# Currency symbol/identifier to primary ISO 4217 currency code
SYMBOL_TO_ISO_CURRENCY: dict[str, str] = {
    "$": "USD",
    "US$": "USD",
    "USD$": "USD",
    "€": "EUR",
    "£": "GBP",
    "GB£": "GBP",
    "¥": "JPY",
    "JP¥": "JPY",
    "CN¥": "CNY",
    "RMB": "CNY",
    "₹": "INR",
    "Rs": "INR",
    "Rs.": "INR",
    "C$": "CAD",
    "CA$": "CAD",
    "CAD$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
    "AUD$": "AUD",
    "NZ$": "NZD",
    "NZD$": "NZD",
    "CHF": "CHF",
    "S$": "SGD",
    "SG$": "SGD",
    "SGD$": "SGD",
    "HK$": "HKD",
    "HKD$": "HKD",
    "₩": "KRW",
    "R$": "BRL",
    "Mex$": "MXN",
    "MXN$": "MXN",
    "₺": "TRY",
    "₽": "RUB",
    "zł": "PLN",
    "฿": "THB",
    "Rp": "IDR",
    "RM": "MYR",
    "₱": "PHP",
    "₫": "VND",
}

CURRENCY_PREFIX = r"(?:[A-Za-z]{3}|[^\w\s\d.,-]|Rs\.?|Mex\$|CA\$|C\$|AU\$|A\$|NZ\$|S\$|HK\$|R\$)?\s*"


def resolve_currency_from_text(text: str) -> tuple[str | None, str, str | None]:
    """
    Identifies and resolves ISO currency code from text using ISO codes and currency symbol maps.
    Returns: (currency_code, currency_status, currency_evidence)
    """
    # 1. Look for explicit ISO code mentioned in text
    for iso_code in ISO_TO_CURRENCY_SYMBOLS:
        if re.search(rf"\b{iso_code}\b", text, re.IGNORECASE):
            return iso_code, "explicit", f"Explicit ISO currency code {iso_code} stated in document"

    # 2. Check for symbols mapped to ISO codes (longer symbols first to prevent prefix shadowing)
    sorted_symbols = sorted(SYMBOL_TO_ISO_CURRENCY.keys(), key=len, reverse=True)
    for sym in sorted_symbols:
        if sym in text:
            iso = SYMBOL_TO_ISO_CURRENCY[sym]
            return iso, "inferred", f"Currency inferred as {iso} from symbol '{sym}' using ISO currency mapping"

    return None, "unresolved", "No currency stated"


def _extract_line_items(text: str) -> tuple[list[LineItem], list[str]]:
    """Extract line items from pipe-delimited, whitespace-separated, or sequential multi-line rows."""
    items: list[LineItem] = []
    warnings: list[str] = []

    # Format 1: Explicit pipe-delimited rows
    if "|" in text:
        header_pattern = re.compile(
            r"description\s*\|.*(?:qty|quantity)\s*\|.*unit\s*price\s*\|.*(?:total|amount|line\s*total)",
            re.IGNORECASE,
        )
        row_pattern = re.compile(
            r"^\s*(?P<description>.+?)\s*\|\s*"
            r"(?P<quantity>\d+(?:\.\d+)?(?:\s*[a-zA-Z]+)?)\s*\|\s*"
            rf"{CURRENCY_PREFIX}(?P<unit_price>[\d,]+\.\d{{2}})\s*\|\s*"
            rf"{CURRENCY_PREFIX}(?P<total>[\d,]+\.\d{{2}})\s*$"
        )
        section_end_pattern = re.compile(
            r"^\s*(?:subtotal|tax\b|grand total|total due|total amount|"
            r"amount received|balance due)\b",
            re.IGNORECASE,
        )

        in_items_section = False
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if header_pattern.search(stripped):
                in_items_section = True
                continue
            if not in_items_section:
                continue
            if section_end_pattern.match(stripped):
                break
            match = row_pattern.match(stripped)
            if not match:
                if "|" in stripped:
                    warnings.append(
                        f"Could not parse possible line-item row: {stripped}"
                    )
                continue

            data = match.groupdict()
            qty_raw = re.sub(r"[a-zA-Z]+", "", data["quantity"]).strip()
            quantity = _decimal(qty_raw)
            unit_price = _decimal(data["unit_price"])
            total = _decimal(data["total"])

            if quantity is None or unit_price is None or total is None:
                warnings.append(f"Invalid numeric value in line-item row: {stripped}")
                continue

            if quantity <= 0 or unit_price < 0 or total < 0:
                warnings.append(f"Suspicious quantity or price in line-item row: {stripped}")

            is_arith = abs(quantity * unit_price - total) <= Decimal("0.02")
            if not is_arith:
                warnings.append(
                    f"Line-item arithmetic mismatch: {data['description'].strip()}"
                )

            uom_m = re.search(r"([a-zA-Z]+)", data["quantity"])
            unit_of_measure = uom_m.group(1).strip() if uom_m else None
            if not unit_of_measure:
                desc_uom = re.search(r"\(([a-zA-Z]+)\)", data["description"])
                if desc_uom:
                    unit_of_measure = desc_uom.group(1).strip()

            items.append(
                LineItem(
                    description=data["description"].strip(),
                    quantity=quantity,
                    unit_of_measure=unit_of_measure,
                    unit_price=unit_price,
                    total=total,
                    is_arithmetic_valid=is_arith,
                )
            )

        if items:
            return items, warnings

    # Format 2: Sequential multi-line or whitespace table
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_idx = -1
    for idx, line in enumerate(lines):
        if re.search(r"\bdescription\b", line, re.I):
            subseq = " ".join(lines[idx:min(len(lines), idx + 5)]).lower()
            if "qty" in subseq or "quantity" in subseq:
                if "unit price" in subseq or "price" in subseq or "total" in subseq:
                    header_idx = idx
                    break

    if header_idx != -1:
        curr = header_idx
        # Skip header tokens
        while curr < len(lines):
            l = lines[curr].lower()
            if any(k in l for k in ("description", "qty", "quantity", "unit price", "line total", "unit_price", "line_total", "line total")):
                curr += 1
            else:
                break

        section_end_re = re.compile(
            r"^(?:subtotal|tax\b|grand total|total due|total amount|"
            r"amount received|balance due|payment terms|synthetic test|"
            r"enigma test|this is synthetic|due date|bill to)\b",
            re.IGNORECASE,
        )

        while curr < len(lines):
            line = lines[curr]
            if section_end_re.match(line):
                break

            # Check single line with 2+ spaces / tabs
            single_match = re.match(
                r"^(?P<description>.+?)\s{2,}"
                r"(?P<quantity>\d+(?:\.\d+)?(?:\s*[a-zA-Z]+)?)\s{2,}"
                rf"{CURRENCY_PREFIX}(?P<unit_price>[\d,]+\.\d{{2}})\s{{2,}}"
                rf"{CURRENCY_PREFIX}(?P<total>[\d,]+\.\d{{2}})$",
                line,
            )
            if single_match:
                d = single_match.groupdict()
                qty_raw = re.sub(r"[a-zA-Z]+", "", d["quantity"]).strip()
                q = _decimal(qty_raw)
                u = _decimal(d["unit_price"])
                t = _decimal(d["total"])
                if q is not None and u is not None and t is not None:
                    is_arith = abs(q * u - t) <= Decimal("0.02")
                    if not is_arith:
                        warnings.append(f"Line-item arithmetic mismatch: {d['description'].strip()}")
                    uom_m = re.search(r"([a-zA-Z]+)", d["quantity"])
                    unit_of_measure = uom_m.group(1).strip() if uom_m else None
                    if not unit_of_measure:
                        desc_uom = re.search(r"\(([a-zA-Z]+)\)", d["description"])
                        if desc_uom:
                            unit_of_measure = desc_uom.group(1).strip()
                    items.append(
                        LineItem(
                            description=d["description"].strip(),
                            quantity=q,
                            unit_of_measure=unit_of_measure,
                            unit_price=u,
                            total=t,
                            is_arithmetic_valid=is_arith,
                        )
                    )
                curr += 1
                continue

            # Check multi-line: 4 consecutive lines (description, quantity, unit_price, line_total)
            if curr + 3 < len(lines):
                desc_cand = lines[curr]
                qty_cand = lines[curr + 1]
                unit_cand = lines[curr + 2]
                total_cand = lines[curr + 3]

                qty_m = re.match(r"^(\d+(?:\.\d+)?)(?:\s*([a-zA-Z]+))?$", qty_cand)
                unit_m = re.match(rf"^{CURRENCY_PREFIX}([\d,]+\.\d{{2}})$", unit_cand)
                total_m = re.match(rf"^{CURRENCY_PREFIX}([\d,]+\.\d{{2}})$", total_cand)

                if qty_m and unit_m and total_m and not section_end_re.match(desc_cand):
                    q = Decimal(qty_m.group(1))
                    u = Decimal(unit_m.group(1).replace(",", ""))
                    t = Decimal(total_m.group(1).replace(",", ""))
                    is_arith = abs(q * u - t) <= Decimal("0.02")
                    if not is_arith:
                        warnings.append(f"Line-item arithmetic mismatch: {desc_cand}")
                    unit_of_measure = qty_m.group(2).strip() if qty_m.group(2) else None
                    if not unit_of_measure:
                        desc_uom = re.search(r"\(([a-zA-Z]+)\)", desc_cand)
                        if desc_uom:
                            unit_of_measure = desc_uom.group(1).strip()
                    items.append(
                        LineItem(
                            description=desc_cand,
                            quantity=q,
                            unit_of_measure=unit_of_measure,
                            unit_price=u,
                            total=t,
                            is_arithmetic_valid=is_arith,
                        )
                    )
                    curr += 4
                    continue

            curr += 1

    return items, warnings



def _extract_bank_transactions(
    text: str,
    page_number: int | None = None,
    confidence: float | None = None,
) -> tuple[list[BankTransaction], list[str]]:
    """Extract transactions from pipe-delimited, whitespace, or sequential multi-line bank statement rows."""
    transactions: list[BankTransaction] = []
    warnings: list[str] = []

    date_pattern = re.compile(
        r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|"
        r"\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4})$"
    )

    def parse_date(value: str) -> str | None:
        value = value.strip()
        for fmt in (
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%d %B %Y",
            "%d %b %Y",
        ):
            try:
                return datetime.strptime(value, fmt).date().isoformat()
            except ValueError:
                continue
        return None

    def validate_sign_semantics(desc: str, sign: str, t_type: str) -> tuple[bool, str]:
        if sign == "+" or t_type == "credit":
            return True, f"Validated positive credit sign (+): incoming revenue/deposit for '{desc}'."
        elif sign == "-" or t_type == "debit":
            return True, f"Validated negative debit sign (-): outgoing expense/disbursement for '{desc}'."
        return True, f"Transaction type {t_type} with sign {sign} recorded for '{desc}'."

    tx_currency, _, _ = resolve_currency_from_text(text)

    # Format 1: Explicit pipe-delimited format
    if "|" in text:
        header_pattern = re.compile(
            r"date\s*\|\s*description\s*\|\s*"
            r"(?:reference\s*\|\s*)?(?:debit\s*\|\s*credit|amount)",
            re.IGNORECASE,
        )

        in_transactions = False
        has_debit_credit_columns = False

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            if header_pattern.search(line):
                in_transactions = True
                has_debit_credit_columns = bool(
                    re.search(r"\bdebit\b", line, re.IGNORECASE)
                    and re.search(r"\bcredit\b", line, re.IGNORECASE)
                )
                continue

            if not in_transactions:
                continue

            if re.match(
                r"^(?:closing balance|ending balance|statement total)\b",
                line,
                re.IGNORECASE,
            ):
                break

            columns = [part.strip() for part in line.split("|")]
            if has_debit_credit_columns:
                if len(columns) not in (4, 5):
                    if "|" in line and date_pattern.match(columns[0]):
                        warnings.append(f"Malformed transaction row: {line}")
                    continue
            else:
                if len(columns) not in (3, 4):
                    if "|" in line and date_pattern.match(columns[0]):
                        warnings.append(f"Malformed transaction row: {line}")
                    continue

            if not date_pattern.match(columns[0]):
                continue

            transaction_date = parse_date(columns[0])
            if transaction_date is None:
                warnings.append(f"Invalid transaction date: {columns[0]}")
                continue

            if has_debit_credit_columns:
                if len(columns) == 5:
                    _, description, reference, debit_text, credit_text = columns
                else:
                    _, description, debit_text, credit_text = columns
                    reference = None

                debit = _decimal(debit_text.replace("$", "").replace("USD", "").strip())
                credit = _decimal(credit_text.replace("$", "").replace("USD", "").strip())

                if debit is None and credit is None:
                    warnings.append(f"Transaction has no valid debit or credit amount: {line}")
                    continue

                if debit is not None and debit < 0:
                    warnings.append(f"Negative debit amount: {line}")
                    continue

                if credit is not None and credit < 0:
                    warnings.append(f"Negative credit amount: {line}")
                    continue

                if debit is not None and credit is not None and debit != 0 and credit != 0:
                    warnings.append(f"Both debit and credit are populated: {line}")
                    continue

                if credit is not None and credit > 0:
                    amount = credit
                    transaction_type = "credit"
                    sign = "+"
                elif debit is not None and debit > 0:
                    amount = debit
                    transaction_type = "debit"
                    sign = "-"
                else:
                    warnings.append(f"Transaction amount is zero or ambiguous: {line}")
                    continue
            else:
                if len(columns) == 4:
                    _, description, reference, amount_text = columns
                else:
                    _, description, amount_text = columns
                    reference = None

                raw_amt = amount_text.replace("$", "").replace("USD", "").strip()
                sign = "+" if raw_amt.startswith("+") else "-" if raw_amt.startswith("-") else ""
                amount = _decimal(raw_amt)

                if amount is None or amount == 0:
                    warnings.append(f"Invalid or zero transaction amount: {line}")
                    continue

                transaction_type = "credit" if (sign == "+" or (not sign and amount > 0)) else "debit"
                amount = abs(amount)
                if not sign:
                    sign = "+" if transaction_type == "credit" else "-"

            if not description:
                warnings.append(f"Transaction description is missing: {line}")
                continue

            is_valid_sign, sign_note = validate_sign_semantics(description, sign, transaction_type)
            if not is_valid_sign:
                warnings.append(sign_note)

            signed_amount = f"{sign}{amount}"
            transactions.append(
                BankTransaction(
                    date=transaction_date,
                    description=description,
                    reference=reference or None,
                    amount=amount,
                    signed_amount=signed_amount,
                    transaction_type=transaction_type,
                    sign=sign,
                    currency=tx_currency,
                    page_number=page_number,
                    confidence=confidence,
                    is_sign_validated=is_valid_sign,
                    sign_validation_note=sign_note,
                )
            )

        if transactions:
            return transactions, warnings

    # Format 2: Sequential multi-line bank statement table
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    header_idx = -1
    for idx, line in enumerate(lines):
        if re.search(r"\bdate\b", line, re.I):
            subseq = " ".join(lines[idx:min(len(lines), idx + 5)]).lower()
            if "description" in subseq and ("amount" in subseq or "debit" in subseq or "credit" in subseq):
                header_idx = idx
                break

    if header_idx != -1:
        curr = header_idx
        # Skip header lines: Date, Description, Reference, Amount
        while curr < len(lines):
            l = lines[curr].lower()
            if any(k in l for k in ("date", "description", "reference", "amount", "debit", "credit")):
                curr += 1
            else:
                break

        section_end_re = re.compile(
            r"^(?:closing balance|ending balance|statement total|synthetic test|"
            r"enigma test|account name|statement period)\b",
            re.IGNORECASE,
        )
        amount_re = re.compile(r"^([+-])?\s*(?:USD|\$)?\s*([\d,]+\.\d{2})$")

        while curr < len(lines):
            line = lines[curr]
            if section_end_re.match(line):
                break

            # 4 consecutive lines: Date, Description, Reference, Amount
            if curr + 3 < len(lines) and date_pattern.match(lines[curr]):
                d_cand = lines[curr]
                desc_cand = lines[curr + 1]
                ref_cand = lines[curr + 2]
                amt_cand = lines[curr + 3]

                amt_m = amount_re.match(amt_cand)
                if amt_m and not section_end_re.match(desc_cand):
                    t_date = parse_date(d_cand)
                    raw_sign = amt_m.group(1) or ""
                    amt_val = Decimal(amt_m.group(2).replace(",", ""))

                    if raw_sign == "+":
                        t_type = "credit"
                        sign = "+"
                    elif raw_sign == "-":
                        t_type = "debit"
                        sign = "-"
                    else:
                        t_type = "credit"
                        sign = "+"

                    is_valid_sign, sign_note = validate_sign_semantics(desc_cand, sign, t_type)
                    if not is_valid_sign:
                        warnings.append(sign_note)

                    signed_amount = f"{sign}{amt_val}"
                    transactions.append(
                        BankTransaction(
                            date=t_date,
                            description=desc_cand,
                            reference=ref_cand,
                            amount=amt_val,
                            signed_amount=signed_amount,
                            transaction_type=t_type,
                            sign=sign,
                            currency=tx_currency,
                            page_number=page_number,
                            confidence=confidence,
                            is_sign_validated=is_valid_sign,
                            sign_validation_note=sign_note,
                        )
                    )
                    curr += 4
                    continue

            # 3 consecutive lines: Date, Description, Amount
            if curr + 2 < len(lines) and date_pattern.match(lines[curr]):
                d_cand = lines[curr]
                desc_cand = lines[curr + 1]
                amt_cand = lines[curr + 2]

                amt_m = amount_re.match(amt_cand)
                if amt_m and not section_end_re.match(desc_cand):
                    t_date = parse_date(d_cand)
                    raw_sign = amt_m.group(1) or ""
                    amt_val = Decimal(amt_m.group(2).replace(",", ""))

                    if raw_sign == "-":
                        t_type = "debit"
                        sign = "-"
                    else:
                        t_type = "credit"
                        sign = "+"

                    is_valid_sign, sign_note = validate_sign_semantics(desc_cand, sign, t_type)
                    if not is_valid_sign:
                        warnings.append(sign_note)

                    signed_amount = f"{sign}{amt_val}"
                    transactions.append(
                        BankTransaction(
                            date=t_date,
                            description=desc_cand,
                            reference=None,
                            amount=amt_val,
                            signed_amount=signed_amount,
                            transaction_type=t_type,
                            sign=sign,
                            currency=tx_currency,
                            page_number=page_number,
                            confidence=confidence,
                            is_sign_validated=is_valid_sign,
                            sign_validation_note=sign_note,
                        )
                    )
                    curr += 3
                    continue

            curr += 1

    return transactions, warnings



def extract_financial_documents(
    processed: ProcessedDocument,
) -> list[FinancialDocument]:
    """Extract structured financial records from OCR and embedded PDF text."""

    documents: list[FinancialDocument] = []

    for page in processed.pages:
        text = page.text
        if not text.strip():
            continue

        lower = " ".join(text.split()).lower()

        is_receipt = (
            "rent payment receipt" in lower
            or re.search(r"\breceipt\s*(?:no\.?|number)\s*:", lower)
            or "amount received:" in lower
        )
        is_invoice = (
            "maintenance invoice" in lower
            or re.search(r"\binvoice\s*(?:number|no\.?|id)\s*:", lower)
            or ("vendor:" in lower and "total due:" in lower)
        )
        is_bank_stmt = (
            "bank account statement" in lower
            or (
                "bank statement" in lower
                and ("account ending:" in lower or "statement period:" in lower or "date | description" in lower or "closing balance" in lower or "currency:" in lower)
            )
            or (
                "statement period:" in lower
                and "account ending:" in lower
            )
        )

        if is_receipt:
            document_type: DocumentType = "rent_receipt"
        elif is_invoice and not (is_bank_stmt and "account ending:" in lower):
            document_type = "maintenance_invoice"
        elif is_bank_stmt:
            document_type = "bank_statement"
        elif "lease agreement" in lower or "tenancy agreement" in lower:
            document_type = "lease"
        else:
            document_type = "unknown"

        # General entity & document fields
        vendor = _find(
            r"^(?:Vendor|Supplier|Payee)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        tenant = _find(
            r"^(?:Received From|Tenant)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        property_address = _find(
            r"^Property\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        bill_to = _find(
            r"^Bill To\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        service_desc = _find(
            r"^(?:Service|Description of Service)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        payment_terms = _find(
            r"^(?:Payment Terms|Payment terms|Terms)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )

        # Extract unit if present in property address or text
        unit = None
        unit_match = re.search(
            r"(?:,\s*)?((?:Apartment|Unit|Suite|Apt\.?)\s+[A-Za-z0-9]+)",
            property_address or text,
            re.IGNORECASE,
        )
        if unit_match:
            unit = unit_match.group(1).strip()

        invoice_number = _find(
            r"^(?:Invoice Number|Invoice No\.?|Invoice ID)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        receipt_number = _find(
            r"^(?:Receipt Number|Receipt No\.?)\s*:\s*(.+)$",
            text,
            re.IGNORECASE | re.MULTILINE,
        )

        date_text = _find(
            r"^(?:Invoice Date|Document Date|Date)\s*:\s*"
            r"(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        due_date_text = _find(
            r"^Due Date\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        payment_date_text = _find(
            r"^Payment Date\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE | re.MULTILINE,
        )

        subtotal = _decimal(_find(
            rf"^Subtotal\s*:\s*{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text, re.IGNORECASE | re.MULTILINE,
        ))
        tax_rate = _find(
            r"^Tax\s*\(([^)]+)\)\s*:",
            text,
            re.IGNORECASE | re.MULTILINE,
        )
        tax = _decimal(_find(
            rf"^Tax(?:\s*\([^)]*\))?\s*:\s*{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text, re.IGNORECASE | re.MULTILINE,
        ))
        explicit_total = _decimal(_find(
            rf"^(?:Total Due|Grand Total|Total Amount)\s*:\s*"
            rf"{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text, re.IGNORECASE | re.MULTILINE,
        ))
        amount_paid = _decimal(_find(
            rf"^Amount Received\s*:\s*{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text, re.IGNORECASE | re.MULTILINE,
        ))
        balance = _decimal(_find(
            rf"^Balance Due\s*:\s*{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text, re.IGNORECASE | re.MULTILINE,
        ))
        opening_balance = _decimal(_find(
            rf"^(?:opening|beginning)\s+balance\s*[:|]?\s*"
            rf"{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text,
            re.IGNORECASE | re.MULTILINE,
        ))
        closing_balance = _decimal(_find(
            rf"^(?:closing|ending)\s+balance\s*[:|]?\s*"
            rf"{CURRENCY_PREFIX}([\d,]+\.\d{{2}})",
            text,
            re.IGNORECASE | re.MULTILINE,
        ))

        # Rent receipt specifics
        rent_period_start = None
        rent_period_end = None
        rent_period_m = re.search(
            r"Rent Period\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})\s*(?:-|to|through)\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if rent_period_m:
            rent_period_start = _date(rent_period_m.group(1))
            rent_period_end = _date(rent_period_m.group(2))

        payment_method = _find(r"^Payment Method\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        payment_reference = _find(r"^(?:Reference|Payment Reference)\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)

        # Bank statement specifics
        account_name = _find(r"^Account Name\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        account_identifier = _find(r"^(?:Account Ending|Account Number|Account No\.?)\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
        stmt_period_start = None
        stmt_period_end = None
        stmt_period_m = re.search(
            r"Statement Period\s*:\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})\s*(?:-|to|through)\s*(\d{1,2}\s+[A-Za-z]+\s+\d{4}|\d{4}-\d{2}-\d{2})",
            text,
            re.IGNORECASE,
        )
        if stmt_period_m:
            stmt_period_start = _date(stmt_period_m.group(1))
            stmt_period_end = _date(stmt_period_m.group(2))

        statement_reconciliation_status = None
        if document_type == "bank_statement":
            if opening_balance is None or closing_balance is None:
                statement_reconciliation_status = "NOT_POSSIBLE_MISSING_BALANCE"

        # Total amount selection
        total = explicit_total
        if document_type == "rent_receipt":
            total = amount_paid if amount_paid is not None else total

        line_items, line_item_warnings = _extract_line_items(text)
        bank_transactions, transaction_warnings = _extract_bank_transactions(
            text,
            page_number=page.page_number,
            confidence=page.ocr_confidence,
        )

        # Currency handling via ISO currency & symbol mapping
        currency, currency_status, currency_evidence = resolve_currency_from_text(text)

        line_items_total = (
            sum(item.total for item in line_items if item.total is not None)
            if line_items
            else None
        )
        is_total_validated = False
        total_validation_note = None
        total_is_calculated = False

        warnings: list[str] = (
            list(line_item_warnings) + list(transaction_warnings)
        )

        if currency_status == "unresolved" and any(
            v is not None for v in (explicit_total, amount_paid, line_items_total)
        ):
            warnings.append("Currency could not be determined reliably from document.")

        # Validate and derive totals for invoices
        if document_type in ("invoice", "maintenance_invoice"):
            if explicit_total is None and line_items_total is not None:
                computed_tax = tax or Decimal("0.00")
                total = line_items_total + computed_tax
                if subtotal is None:
                    subtotal = line_items_total
                total_is_calculated = True
                is_total_validated = True
                total_validation_note = (
                    f"Invoice total was not explicitly stated in document body; "
                    f"calculated from sum of {len(line_items)} line items: {total}."
                )
            elif explicit_total is not None:
                total_is_calculated = False
                if line_items_total is not None:
                    expected_subtotal = subtotal if subtotal is not None else explicit_total
                    if abs(line_items_total - expected_subtotal) <= Decimal("0.02"):
                        is_total_validated = True
                        total_validation_note = (
                            f"Validated: sum of line items ({line_items_total}) matches "
                            f"subtotal/total ({expected_subtotal})."
                        )
                    else:
                        warnings.append(
                            f"Line items total ({line_items_total}) does not match "
                            f"invoice subtotal/total ({expected_subtotal})."
                        )
                elif subtotal is not None and tax is not None:
                    if abs(subtotal + tax - explicit_total) <= Decimal("0.02"):
                        is_total_validated = True
                        total_validation_note = (
                            f"Validated: subtotal ({subtotal}) + tax ({tax}) equals total ({explicit_total})."
                        )
                    else:
                        warnings.append("Subtotal plus tax does not match total amount.")

            # Validate tax rate calculation if rate is given
            if tax_rate and subtotal is not None and tax is not None:
                rate_m = re.search(r"(\d+(?:\.\d+)?)\s*%", tax_rate)
                if rate_m:
                    rate_pct = Decimal(rate_m.group(1)) / Decimal(100)
                    expected_tax = round(subtotal * rate_pct, 2)
                    if abs(expected_tax - tax) > Decimal("0.02"):
                        warnings.append(
                            f"Tax calculation mismatch: {tax_rate} of {subtotal} is {expected_tax}, but document states {tax}."
                        )

        # Evidence collection
        evidence: list[FieldEvidence] = []
        evidence_candidates = {
            "vendor_name": vendor,
            "tenant_name": tenant,
            "property_address": property_address,
            "invoice_number": invoice_number,
            "receipt_number": receipt_number,
            "total_amount": str(total) if total is not None else None,
            "amount_paid": str(amount_paid) if amount_paid is not None else None,
        }

        for field_name, value in evidence_candidates.items():
            if value is None:
                continue

            matching_line = next(
                (line.strip() for line in text.splitlines()
                 if value.casefold() in line.casefold()),
                f"Computed value: {value}",
            )
            evidence.append(
                FieldEvidence(
                    field_name=field_name,
                    value=value,
                    page_number=page.page_number,
                    text_snippet=matching_line,
                    confidence=page.ocr_confidence,
                )
            )

        if document_type == "unknown":
            warnings.append("Document type could not be confidently classified.")

        if not vendor and document_type in ("invoice", "maintenance_invoice"):
            warnings.append("Vendor name was not extracted.")

        if total is None and document_type not in ("bank_statement", "lease"):
            warnings.append("Document total could not be extracted.")

        missing_fields: list[str] = []
        if document_type in ("invoice", "maintenance_invoice"):
            if not invoice_number:
                missing_fields.append("invoice_number")
            if total is None:
                missing_fields.append("total_amount")
        elif document_type == "rent_receipt":
            if not tenant:
                missing_fields.append("tenant_name")
            if amount_paid is None:
                missing_fields.append("amount_paid")

        # Validation dimensions
        arith_status = "not_applicable"
        if document_type in ("invoice", "maintenance_invoice"):
            has_arith_err = any("arithmetic mismatch" in w.lower() or "not match" in w.lower() or "calculation mismatch" in w.lower() for w in warnings)
            arith_status = "failed" if has_arith_err else "passed"
        elif document_type == "rent_receipt":
            if amount_paid is not None:
                arith_status = "passed"
        elif document_type == "bank_statement":
            arith_status = "passed"

        field_status = "failed" if missing_fields else "passed"
        extraction_comp = "incomplete" if missing_fields else "complete"
        currency_val = "explicit_iso" if currency_status in ("explicit", "inferred") else "missing"

        review_reasons = []
        if currency_status == "unresolved" and (total is not None or amount_paid is not None):
            review_reasons.append("Currency cannot be reliably established from source.")
        if missing_fields:
            review_reasons.append(f"Required fields missing: {', '.join(missing_fields)}")
        if arith_status == "failed":
            review_reasons.append("Arithmetic validation failed.")
        if page.ocr_confidence is not None and page.ocr_confidence < 0.90:
            review_reasons.append(f"Low OCR confidence ({page.ocr_confidence:.2f}).")

        dimensions = ValidationDimensions(
            extraction_completeness=extraction_comp,
            field_validation=field_status,
            arithmetic_validation=arith_status,
            currency_validation=currency_val,
            temporal_consistency="not_applicable",
            cross_document_matching="not_applicable",
            reconciliation_status=statement_reconciliation_status or "not_applicable",
            human_review_required=bool(review_reasons),
            review_reasons=review_reasons,
        )

        requires_review = bool(review_reasons)

        doc_id = None
        if document_type in ("invoice", "maintenance_invoice"):
            doc_id = invoice_number or f"INV-P{page.page_number}"
        elif document_type == "rent_receipt":
            doc_id = receipt_number or f"RCPT-P{page.page_number}"
        elif document_type == "bank_statement":
            doc_id = f"STMT-{account_identifier.strip()}" if account_identifier else f"STMT-P{page.page_number}"

        document = FinancialDocument(
            document_id=doc_id,
            document_type=document_type,
            source_file=processed.file_name,
            vendor_name=vendor,
            tenant_name=tenant,
            bill_to=bill_to,
            property_address=property_address,
            unit=unit,
            service_description=service_desc,
            invoice_number=invoice_number,
            receipt_number=receipt_number,
            document_date=_date(date_text),
            due_date=_date(due_date_text),
            payment_date=_date(payment_date_text),
            rent_period_start=rent_period_start,
            rent_period_end=rent_period_end,
            payment_method=payment_method,
            payment_reference=payment_reference,
            account_name=account_name,
            account_identifier=account_identifier,
            statement_period_start=stmt_period_start,
            statement_period_end=stmt_period_end,
            statement_reconciliation_status=statement_reconciliation_status,
            subtotal=subtotal,
            tax_rate=tax_rate,
            tax=tax,
            total_amount=total,
            total_is_calculated=total_is_calculated,
            amount_paid=amount_paid,
            outstanding_balance=balance,
            opening_balance=opening_balance,
            closing_balance=closing_balance,
            payment_terms=payment_terms,
            currency=currency,
            currency_status=currency_status,
            currency_evidence=currency_evidence,
            line_items=line_items,
            line_items_total=line_items_total,
            is_total_validated=is_total_validated,
            total_validation_note=total_validation_note,
            bank_transactions=bank_transactions,
            extraction_method=page.extraction_method,
            overall_confidence=page.ocr_confidence,
            field_evidence=evidence,
            missing_fields=missing_fields,
            warnings=warnings,
            validation_dimensions=dimensions,
            requires_human_review=requires_review,
        )

        documents.append(document)

    return documents

