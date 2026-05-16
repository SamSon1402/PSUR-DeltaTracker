"""
Pydantic request/response models.

Domain conventions:
- ICH E2B(R3) for ICSR seriousness criteria
- ICH E2C(R2) for PSUR structure
- MedDRA Preferred Terms (PT) — production loads the full v27.0 hierarchy.
"""
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ============ Enums ============

class SeriousnessCriterion(str, Enum):
    """ICH E2B(R3) seriousness criteria."""
    DEATH = "death"
    LIFE_THREATENING = "life_threatening"
    HOSPITALISATION = "hospitalisation"
    DISABILITY = "disability"
    CONGENITAL_ANOMALY = "congenital_anomaly"
    MEDICALLY_IMPORTANT = "medically_important"


class CaseOutcome(str, Enum):
    RECOVERED = "recovered"
    RECOVERING = "recovering"
    NOT_RECOVERED = "not_recovered"
    FATAL = "fatal"
    UNKNOWN = "unknown"


class ReporterType(str, Enum):
    PHYSICIAN = "physician"
    CONSUMER = "consumer"
    OTHER_HCP = "other_hcp"
    LITERATURE = "literature"


class SignalStatus(str, Enum):
    OPEN = "open"
    UNDER_REVIEW = "under_review"
    VALIDATED = "validated"
    REFUTED = "refuted"
    CLOSED = "closed"


class SignalSeverity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ============ MedDRA ============

class MedDRATerm(BaseModel):
    pt_code: str = Field(..., examples=["10037087"])
    pt_name: str = Field(..., examples=["Pruritus generalised"])
    soc: str | None = Field(default=None, description="System Organ Class (optional)")


# ============ Products ============

class ProductCreate(BaseModel):
    code: str = Field(..., examples=["BVL-2188"])
    name: str = Field(..., examples=["BVL-2188 (atopic dermatitis)"])
    indication: str = Field(..., examples=["Moderate-to-severe atopic dermatitis"])
    approval_date: date | None = None


class Product(ProductCreate):
    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=_now)


# ============ ICSRs ============

class ICSRCreate(BaseModel):
    case_id: str = Field(
        ...,
        description="External case identifier (e.g., EudraVigilance EV-case-no.)",
        examples=["EU-EC-12345"],
    )
    received_at: datetime
    onset_date: date | None = None
    age_years: int | None = Field(default=None, ge=0, le=120)
    sex: Literal["male", "female", "unknown"] = "unknown"
    country: str = Field(..., examples=["FR"], min_length=2, max_length=2)
    serious: bool
    seriousness_criteria: list[SeriousnessCriterion] = []
    suspected_terms: list[MedDRATerm] = Field(
        ...,
        description="MedDRA PTs reported as suspected adverse events",
        min_length=1,
    )
    outcome: CaseOutcome = CaseOutcome.UNKNOWN
    reporter_type: ReporterType = ReporterType.OTHER_HCP


class ICSR(ICSRCreate):
    id: UUID = Field(default_factory=uuid4)
    product_id: UUID
    ingested_at: datetime = Field(default_factory=_now)


# ============ PSURs ============

class PSURCreate(BaseModel):
    version: str = Field(..., examples=["v5"])
    interval_start: date
    interval_end: date
    dlp: date = Field(..., description="Data Lock Point")


class PSUR(PSURCreate):
    id: UUID = Field(default_factory=uuid4)
    product_id: UUID
    submitted_at: datetime | None = None
    created_at: datetime = Field(default_factory=_now)


# ============ Signals ============

class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    product_id: UUID
    pt_code: str
    pt_name: str
    case_count: int
    ror: float = Field(..., description="Reporting Odds Ratio")
    ror_ci_lower: float
    ror_ci_upper: float
    severity: SignalSeverity
    status: SignalStatus = SignalStatus.OPEN
    detected_at: datetime = Field(default_factory=_now)
    escalated_at: datetime | None = None
    escalated_to: str | None = None
    notes: str | None = None


class SignalEscalate(BaseModel):
    qppv: str = Field(..., description="QPPV identifier (electronic signature)")
    notes: str | None = None


# ============ Delta ============

class MedDRADelta(BaseModel):
    pt_code: str
    pt_name: str
    count_previous: int
    count_current: int
    delta_absolute: int
    delta_percent: float


class DeltaRequest(BaseModel):
    from_psur_id: UUID
    to_psur_id: UUID


class PSURDelta(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    product_id: UUID
    from_psur_id: UUID
    to_psur_id: UUID
    new_icsrs: int
    new_serious_icsrs: int
    sections_changed: list[str] = Field(
        ...,
        description="ICH E2C(R2) PSUR sections impacted by this delta",
        examples=[["Section 6.3 Cumulative Exposure", "Section 12 Signal Evaluation"]],
    )
    meddra_deltas: list[MedDRADelta]
    new_signal_ids: list[UUID]
    generated_at: datetime = Field(default_factory=_now)


# ============ Frequencies ============

class TermFrequency(BaseModel):
    pt_code: str
    pt_name: str
    count: int
    serious_count: int


class FrequencyReport(BaseModel):
    product_id: UUID
    from_date: date | None
    to_date: date | None
    total_cases: int
    frequencies: list[TermFrequency]
