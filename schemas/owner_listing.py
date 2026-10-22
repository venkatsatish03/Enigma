"""Owner property listing schemas for the customer portal."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ListingType(str, Enum):
    FOR_RENT = "for_rent"
    FOR_SALE = "for_sale"
    LONG_TERM_LET = "long_term_let"
    SHORT_TERM_RENTAL = "short_term_rental"
    ROOM_RENTAL = "room_rental"
    COMMERCIAL_LEASE = "commercial_lease"


class PropertyType(str, Enum):
    DETACHED_HOUSE = "detached_house"
    SEMI_DETACHED_HOUSE = "semi_detached_house"
    TERRACED_HOUSE = "terraced_house"
    FLAT_APARTMENT = "flat_apartment"
    CONDOMINIUM = "condominium"
    TOWNHOUSE = "townhouse"
    STUDIO = "studio"
    ROOM = "room"
    OTHER = "other"


class PropertyStatus(str, Enum):
    AVAILABLE = "available"
    OCCUPIED = "occupied"
    UNDER_OFFER = "under_offer"
    PENDING = "pending"
    SOLD = "sold"
    LET = "let"


class Furnishing(str, Enum):
    FURNISHED = "furnished"
    UNFURNISHED = "unfurnished"
    PARTLY_FURNISHED = "partly_furnished"


class Parking(str, Enum):
    GARAGE = "garage"
    DRIVEWAY = "driveway"
    ALLOCATED_SPACE = "allocated_space"
    STREET_PARKING = "street_parking"
    NONE = "none"
    OTHER = "other"


class PaymentFrequency(str, Enum):
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    OTHER = "other"


class ListingMediaRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    media_id: str
    listing_id: str
    original_filename: str
    media_kind: str
    mime_type: str
    size_bytes: int
    created_at: datetime


class OwnerListingDraft(BaseModel):
    """A listing can be saved incrementally; submit performs completeness checks."""

    listing_type: ListingType = ListingType.FOR_RENT
    owner_full_name: str | None = Field(default=None, max_length=160)
    authorized_representative: str | None = Field(default=None, max_length=160)
    contact_email: str | None = Field(default=None, max_length=254)
    phone_country_code: str | None = Field(default=None, max_length=8)
    contact_phone: str | None = Field(default=None, max_length=32)

    country: str | None = Field(default=None, max_length=100)
    region: str | None = Field(default=None, max_length=120)
    street_address: str | None = Field(default=None, max_length=240)
    unit_number: str | None = Field(default=None, max_length=40)
    city: str | None = Field(default=None, max_length=120)
    state_or_county: str | None = Field(default=None, max_length=120)
    postal_code: str | None = Field(default=None, max_length=32)
    property_type: PropertyType | None = None
    property_status: PropertyStatus = PropertyStatus.AVAILABLE
    property_size: Decimal | None = Field(default=None, gt=0, max_digits=12, decimal_places=2)
    property_size_unit: str = Field(default="square_meters", pattern="^(square_meters|square_feet)$")

    bedrooms: int | None = Field(default=None, ge=0, le=100)
    bedroom_configuration: str | None = Field(default=None, max_length=240)
    full_bathrooms: int | None = Field(default=None, ge=0, le=100)
    partial_bathrooms: int | None = Field(default=None, ge=0, le=100)
    furnishing: Furnishing | None = None
    parking: Parking | None = None
    other_facilities: list[str] = Field(default_factory=list, max_length=80)

    description: str | None = Field(default=None, max_length=10000)
    local_area: str | None = Field(default=None, max_length=5000)

    price_currency: str = Field(default="USD", min_length=3, max_length=3)
    rent_amount: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    sale_price: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    payment_frequency: PaymentFrequency | None = None
    other_payment_frequency: str | None = Field(default=None, max_length=80)
    deposit_amount: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    deposit_rules: str | None = Field(default=None, max_length=2000)
    tenancy_duration: str | None = Field(default=None, max_length=120)
    available_from: date | None = None
    minimum_tenancy_duration: str | None = Field(default=None, max_length=120)
    utilities_included: list[str] = Field(default_factory=list, max_length=40)
    utilities_paid_separately: list[str] = Field(default_factory=list, max_length=40)
    pet_policy: str | None = Field(default=None, max_length=500)
    smoking_policy: str | None = Field(default=None, max_length=500)
    occupancy_restrictions: str | None = Field(default=None, max_length=1000)
    parking_charge: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    service_charge: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    other_recurring_costs: list[dict[str, Any]] = Field(default_factory=list, max_length=40)

    @field_validator("price_currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("contact_email")
    @classmethod
    def normalize_contact_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("Enter a valid contact email address.")
        return value

    @field_validator("phone_country_code")
    @classmethod
    def validate_phone_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value.startswith("+") or not value[1:].isdigit():
            raise ValueError("Enter a country calling code such as +1, +44, or +91.")
        return value

    def submission_errors(self) -> list[str]:
        errors: list[str] = []
        required = {
            "Owner name": self.owner_full_name,
            "Contact email": self.contact_email,
            "Phone country code": self.phone_country_code,
            "Contact phone": self.contact_phone,
            "Country": self.country,
            "Street address": self.street_address,
            "City": self.city,
            "Property type": self.property_type,
            "Property size": self.property_size,
            "Description": self.description,
        }
        errors.extend(f"{label} is required." for label, value in required.items() if value is None or (isinstance(value, str) and not value.strip()))
        rental_types = {
            ListingType.FOR_RENT,
            ListingType.LONG_TERM_LET,
            ListingType.SHORT_TERM_RENTAL,
            ListingType.ROOM_RENTAL,
            ListingType.COMMERCIAL_LEASE,
        }
        if self.listing_type in rental_types:
            if self.rent_amount is None:
                errors.append("Rent amount is required for rental listings.")
            if self.payment_frequency is None:
                errors.append("Payment frequency is required for rental listings.")
            if self.payment_frequency == PaymentFrequency.OTHER and not (self.other_payment_frequency or "").strip():
                errors.append("Describe the other payment frequency.")
        if self.listing_type == ListingType.FOR_SALE and self.sale_price is None:
            errors.append("Sale price is required for sale listings.")
        return errors


class OwnerListingRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    listing_id: str
    owner_user_id: str
    status: str
    data: OwnerListingDraft
    media: list[ListingMediaRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PublicOwnerListingRecord(BaseModel):
    """Published listing data safe to return to authenticated tenants."""

    listing_id: str
    status: str
    data: OwnerListingDraft
    media: list[ListingMediaRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
