"""
HTTP API routes for PSUR-DeltaTracker.

Grouped:
- Health
- Products
- ICSRs
- PSURs
- Deltas (headline endpoint)
- Signals
- Frequencies
"""
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.delta_engine import (
    compute_meddra_deltas,
    derive_sections_changed,
    split_cases_by_psur,
)
from app.schemas import (
    DeltaRequest,
    FrequencyReport,
    ICSR,
    ICSRCreate,
    Product,
    ProductCreate,
    PSUR,
    PSURCreate,
    PSURDelta,
    Signal,
    SignalEscalate,
    SignalStatus,
    TermFrequency,
)
from app.signal_engine import detect_signals
from app.store import store


router = APIRouter()


# ============ Health ============

@router.get("/health", tags=["health"])
async def health():
    return {"status": "ok", "service": "psur-deltatracker"}


@router.get("/health/ready", tags=["health"])
async def ready():
    return {"status": "ready", "products": len(store.list_products())}


# ============ Products ============

@router.post("/products", response_model=Product, status_code=status.HTTP_201_CREATED, tags=["products"])
async def create_product(payload: ProductCreate):
    return store.put_product(Product(**payload.model_dump()))


@router.get("/products", response_model=list[Product], tags=["products"])
async def list_products():
    return store.list_products()


@router.get("/products/{product_id}", response_model=Product, tags=["products"])
async def get_product(product_id: UUID):
    p = store.get_product(product_id)
    if not p:
        raise HTTPException(404, "Product not found")
    return p


# ============ ICSRs ============

@router.post(
    "/products/{product_id}/icsrs",
    response_model=ICSR,
    status_code=status.HTTP_201_CREATED,
    tags=["icsrs"],
)
async def ingest_icsr(product_id: UUID, payload: ICSRCreate):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    icsr = ICSR(**payload.model_dump(), product_id=product_id)
    return store.put_icsr(icsr)


@router.get(
    "/products/{product_id}/icsrs",
    response_model=list[ICSR],
    tags=["icsrs"],
)
async def list_icsrs(
    product_id: UUID,
    serious: bool | None = Query(default=None),
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    cases = store.list_icsrs(product_id)
    if serious is not None:
        cases = [c for c in cases if c.serious == serious]
    if from_date:
        cases = [c for c in cases if c.received_at.date() >= from_date]
    if to_date:
        cases = [c for c in cases if c.received_at.date() <= to_date]
    return cases


@router.get("/icsrs/{icsr_id}", response_model=ICSR, tags=["icsrs"])
async def get_icsr(icsr_id: UUID):
    c = store.get_icsr(icsr_id)
    if not c:
        raise HTTPException(404, "ICSR not found")
    return c


# ============ PSURs ============

@router.post(
    "/products/{product_id}/psurs",
    response_model=PSUR,
    status_code=status.HTTP_201_CREATED,
    tags=["psurs"],
)
async def register_psur(product_id: UUID, payload: PSURCreate):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    if payload.interval_start >= payload.interval_end:
        raise HTTPException(400, "interval_start must be before interval_end")
    return store.put_psur(PSUR(**payload.model_dump(), product_id=product_id))


@router.get("/products/{product_id}/psurs", response_model=list[PSUR], tags=["psurs"])
async def list_psurs(product_id: UUID):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    return store.list_psurs(product_id)


# ============ Delta — headline endpoint ============

@router.post(
    "/products/{product_id}/delta",
    response_model=PSURDelta,
    status_code=status.HTTP_201_CREATED,
    tags=["deltas"],
)
async def compute_delta(product_id: UUID, payload: DeltaRequest):
    """
    Compute the PSUR delta between two intervals.

    Pipeline:
      1. Fetch both PSURs + their intervals
      2. Bucket ICSRs by received_at into previous / current
      3. Compute MedDRA term frequency deltas
      4. Detect signals on the current interval
      5. Map findings to ICH E2C(R2) PSUR sections that need rewriting
      6. Persist
    """
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    prev_psur = store.get_psur(payload.from_psur_id)
    curr_psur = store.get_psur(payload.to_psur_id)
    if not prev_psur or not curr_psur:
        raise HTTPException(404, "One or both PSURs not found")
    if prev_psur.product_id != product_id or curr_psur.product_id != product_id:
        raise HTTPException(400, "PSURs must belong to the given product")

    all_cases = store.list_icsrs(product_id)
    prev_cases, curr_cases = split_cases_by_psur(prev_psur, curr_psur, all_cases)

    deltas = compute_meddra_deltas(prev_cases, curr_cases)

    # Signal detection on the current interval
    new_signals = detect_signals(product_id, curr_cases)
    # Filter to signals not already known (de-dup by pt_code)
    existing_pts = {s.pt_code for s in store.list_signals(product_id)}
    fresh_signals: list[Signal] = [s for s in new_signals if s.pt_code not in existing_pts]
    for sig in fresh_signals:
        store.put_signal(sig)

    sections = derive_sections_changed(
        new_icsrs=len(curr_cases),
        new_serious_icsrs=sum(1 for c in curr_cases if c.serious),
        deltas=deltas,
        new_signals_count=len(fresh_signals),
    )

    delta = PSURDelta(
        product_id=product_id,
        from_psur_id=prev_psur.id,
        to_psur_id=curr_psur.id,
        new_icsrs=len(curr_cases),
        new_serious_icsrs=sum(1 for c in curr_cases if c.serious),
        sections_changed=sections,
        meddra_deltas=deltas,
        new_signal_ids=[s.id for s in fresh_signals],
    )
    return store.put_delta(delta)


@router.get("/products/{product_id}/deltas", response_model=list[PSURDelta], tags=["deltas"])
async def list_deltas(product_id: UUID):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    return store.list_deltas(product_id)


@router.get(
    "/products/{product_id}/deltas/{delta_id}",
    response_model=PSURDelta,
    tags=["deltas"],
)
async def get_delta(product_id: UUID, delta_id: UUID):
    d = store.get_delta(delta_id)
    if not d or d.product_id != product_id:
        raise HTTPException(404, "Delta not found")
    return d


# ============ Signals ============

@router.get("/products/{product_id}/signals", response_model=list[Signal], tags=["signals"])
async def list_signals(product_id: UUID, status: str | None = Query(default=None)):
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    return store.list_signals(product_id, status=status)


@router.post(
    "/products/{product_id}/signals/{signal_id}/escalate",
    response_model=Signal,
    tags=["signals"],
)
async def escalate_signal(product_id: UUID, signal_id: UUID, payload: SignalEscalate):
    sig = store.get_signal(signal_id)
    if not sig or sig.product_id != product_id:
        raise HTTPException(404, "Signal not found")
    if sig.status not in (SignalStatus.OPEN, SignalStatus.UNDER_REVIEW):
        raise HTTPException(409, f"Signal already in terminal state ({sig.status.value})")
    sig.status = SignalStatus.UNDER_REVIEW
    sig.escalated_at = datetime.now(timezone.utc)
    sig.escalated_to = payload.qppv
    sig.notes = payload.notes
    return store.put_signal(sig)


# ============ Frequencies ============

@router.get(
    "/products/{product_id}/meddra-frequencies",
    response_model=FrequencyReport,
    tags=["frequencies"],
)
async def meddra_frequencies(
    product_id: UUID,
    from_date: date | None = Query(default=None),
    to_date: date | None = Query(default=None),
):
    """Aggregate MedDRA PT counts across a date window."""
    if not store.get_product(product_id):
        raise HTTPException(404, "Product not found")
    cases = store.list_icsrs(product_id)
    if from_date:
        cases = [c for c in cases if c.received_at.date() >= from_date]
    if to_date:
        cases = [c for c in cases if c.received_at.date() <= to_date]

    counts: dict[str, dict] = {}
    for case in cases:
        seen: set[str] = set()
        for term in case.suspected_terms:
            if term.pt_code in seen:
                continue
            seen.add(term.pt_code)
            entry = counts.setdefault(term.pt_code, {
                "pt_code": term.pt_code, "pt_name": term.pt_name,
                "count": 0, "serious_count": 0,
            })
            entry["count"] += 1
            if case.serious:
                entry["serious_count"] += 1

    freqs = sorted(
        [TermFrequency(**v) for v in counts.values()],
        key=lambda t: t.count,
        reverse=True,
    )
    return FrequencyReport(
        product_id=product_id,
        from_date=from_date,
        to_date=to_date,
        total_cases=len(cases),
        frequencies=freqs,
    )
