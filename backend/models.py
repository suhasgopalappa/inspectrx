"""Data models for InspectRx."""

from pydantic import BaseModel
from datetime import datetime
from enum import Enum


class SeverityLevel(str, Enum):
    HIGH = "high"        # >20% overcharge or phantom charge
    MEDIUM = "medium"    # 10-20% overcharge
    LOW = "low"          # <10% overcharge or minor discrepancy
    INFO = "info"        # informational note, not necessarily an error


class LineItem(BaseModel):
    """A single line item extracted from the hospital bill."""
    sl_no: int | None = None
    description: str
    category: str  # medication, consumable, procedure, room, doctor_fee, lab, misc
    quantity: int | None = None
    unit_price: float | None = None
    billed_amount: float
    reference_amount: float | None = None  # expected amount from tariff/CGHS
    reference_source: str | None = None    # "CGHS 2024", "Hospital Tariff Card", etc.
    flag: str | None = None                # "overcharge", "double_billing", "phantom", "unbundled", None
    severity: SeverityLevel | None = None
    explanation: str | None = None
    potential_savings: float = 0.0


class BillAnalysis(BaseModel):
    """Complete analysis of a hospital bill."""
    bill_id: str
    analyzed_at: datetime
    hospital_name: str | None = None
    patient_name: str | None = None
    bill_date: str | None = None
    bill_number: str | None = None
    total_billed: float
    total_expected: float | None = None
    total_potential_savings: float
    line_items: list[LineItem]
    flagged_items: list[LineItem]  # only items with flags
    summary: str
    recommendations: list[str]
    confidence_score: float  # 0-1, how confident the AI is in the analysis
    limitations: list[str]   # what the AI couldn't verify


class AuditRequest(BaseModel):
    """Request to audit a bill."""
    bill_image_base64: str | None = None
    notes: str | None = None


class AuditResponse(BaseModel):
    """Response from the audit endpoint."""
    success: bool
    analysis: BillAnalysis | None = None
    error: str | None = None
    processing_time_seconds: float | None = None
