"""Smoke tests for PSUR-DeltaTracker API."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.store import store


@pytest_asyncio.fixture
async def client():
    store.products.clear()
    store.icsrs.clear()
    store.icsrs_by_product.clear()
    store.psurs.clear()
    store.psurs_by_product.clear()
    store.deltas.clear()
    store.deltas_by_product.clear()
    store.signals.clear()
    store.signals_by_product.clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_health(client: AsyncClient):
    r = await client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def _seed_product_and_psurs(client: AsyncClient):
    """Helper — register a product and two PSURs covering consecutive intervals."""
    r = await client.post("/api/v1/products", json={
        "code": "BVL-2188",
        "name": "BVL-2188",
        "indication": "Moderate-to-severe atopic dermatitis",
        "approval_date": "2024-03-15",
    })
    assert r.status_code == 201, r.text
    product_id = r.json()["id"]

    r = await client.post(f"/api/v1/products/{product_id}/psurs", json={
        "version": "v4",
        "interval_start": "2025-04-01",
        "interval_end": "2025-09-30",
        "dlp": "2025-09-30",
    })
    assert r.status_code == 201
    v4_id = r.json()["id"]

    r = await client.post(f"/api/v1/products/{product_id}/psurs", json={
        "version": "v5",
        "interval_start": "2025-10-01",
        "interval_end": "2026-03-31",
        "dlp": "2026-03-31",
    })
    assert r.status_code == 201
    v5_id = r.json()["id"]
    return product_id, v4_id, v5_id


async def _ingest_icsr(client, product_id, case_id, received_at, pt_code, pt_name, serious=False):
    r = await client.post(f"/api/v1/products/{product_id}/icsrs", json={
        "case_id": case_id,
        "received_at": received_at,
        "country": "FR",
        "sex": "female",
        "age_years": 42,
        "serious": serious,
        "seriousness_criteria": ["hospitalisation"] if serious else [],
        "suspected_terms": [{"pt_code": pt_code, "pt_name": pt_name}],
        "outcome": "recovering",
        "reporter_type": "physician",
    })
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_full_delta_flow_with_signal_detection(client: AsyncClient):
    product_id, v4_id, v5_id = await _seed_product_and_psurs(client)

    # Previous interval (v4): 2 cases of headache, 1 of nausea
    for i, (code, name) in enumerate([
        ("10019211", "Headache"), ("10019211", "Headache"), ("10028813", "Nausea"),
    ]):
        await _ingest_icsr(client, product_id, f"PREV-{i}", "2025-06-15T00:00:00Z", code, name)

    # Current interval (v5): emerging pruritus signal — 5 cases all reporting it
    for i in range(5):
        await _ingest_icsr(
            client, product_id, f"CURR-{i}", "2026-01-15T00:00:00Z",
            "10037087", "Pruritus generalised",
            serious=(i == 0),
        )
    # Plus background headache continuing
    await _ingest_icsr(client, product_id, "CURR-X", "2026-02-15T00:00:00Z", "10019211", "Headache")

    # Compute the delta
    r = await client.post(f"/api/v1/products/{product_id}/delta", json={
        "from_psur_id": v4_id, "to_psur_id": v5_id,
    })
    assert r.status_code == 201, r.text
    delta = r.json()

    assert delta["new_icsrs"] == 6
    assert delta["new_serious_icsrs"] == 1
    # MedDRA deltas should show pruritus jumping from 0 to 5
    pruritus = next(d for d in delta["meddra_deltas"] if d["pt_code"] == "10037087")
    assert pruritus["count_previous"] == 0
    assert pruritus["count_current"] == 5
    assert pruritus["delta_absolute"] == 5

    # Sections triggered
    assert "Section 6.3 — Cumulative Exposure" in delta["sections_changed"]
    assert "Section 12 — Signal Evaluation" in delta["sections_changed"]
    assert "Section 11 — Serious Risks (cumulative)" in delta["sections_changed"]

    # Signal detected for pruritus
    assert len(delta["new_signal_ids"]) >= 1
    r = await client.get(f"/api/v1/products/{product_id}/signals")
    signals = r.json()
    pruritus_signal = next((s for s in signals if s["pt_code"] == "10037087"), None)
    assert pruritus_signal is not None
    assert pruritus_signal["ror_ci_lower"] > 1.0
    assert pruritus_signal["status"] == "open"


@pytest.mark.asyncio
async def test_signal_escalation(client: AsyncClient):
    product_id, v4_id, v5_id = await _seed_product_and_psurs(client)
    # 5 cases all reporting the same PT → guaranteed signal
    for i in range(5):
        await _ingest_icsr(
            client, product_id, f"S-{i}", "2026-01-15T00:00:00Z",
            "10037087", "Pruritus generalised",
        )
    r = await client.post(f"/api/v1/products/{product_id}/delta", json={
        "from_psur_id": v4_id, "to_psur_id": v5_id,
    })
    signal_id = r.json()["new_signal_ids"][0]

    # Escalate to QPPV
    r = await client.post(
        f"/api/v1/products/{product_id}/signals/{signal_id}/escalate",
        json={"qppv": "qppv@biolevate.test", "notes": "Cluster needs medical review"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "under_review"
    assert r.json()["escalated_to"] == "qppv@biolevate.test"


@pytest.mark.asyncio
async def test_meddra_frequencies_endpoint(client: AsyncClient):
    product_id, _, _ = await _seed_product_and_psurs(client)
    await _ingest_icsr(client, product_id, "F1", "2026-02-01T00:00:00Z", "10019211", "Headache")
    await _ingest_icsr(client, product_id, "F2", "2026-02-15T00:00:00Z", "10019211", "Headache", serious=True)
    await _ingest_icsr(client, product_id, "F3", "2026-03-01T00:00:00Z", "10028813", "Nausea")

    r = await client.get(f"/api/v1/products/{product_id}/meddra-frequencies?from_date=2026-01-01&to_date=2026-03-31")
    assert r.status_code == 200
    report = r.json()
    assert report["total_cases"] == 3
    headache = next(f for f in report["frequencies"] if f["pt_code"] == "10019211")
    assert headache["count"] == 2
    assert headache["serious_count"] == 1


@pytest.mark.asyncio
async def test_psur_must_belong_to_product(client: AsyncClient):
    p1_id, v4_id, v5_id = await _seed_product_and_psurs(client)
    # Create a second product
    r = await client.post("/api/v1/products", json={
        "code": "OTHER-001", "name": "Other product", "indication": "Test",
    })
    p2_id = r.json()["id"]
    # Try to compute delta on p2 using p1's PSURs
    r = await client.post(f"/api/v1/products/{p2_id}/delta", json={
        "from_psur_id": v4_id, "to_psur_id": v5_id,
    })
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_invalid_psur_interval_rejected(client: AsyncClient):
    r = await client.post("/api/v1/products", json={
        "code": "X", "name": "X", "indication": "Y",
    })
    pid = r.json()["id"]
    r = await client.post(f"/api/v1/products/{pid}/psurs", json={
        "version": "bad",
        "interval_start": "2026-06-30",
        "interval_end": "2026-01-01",  # before start
        "dlp": "2026-01-01",
    })
    assert r.status_code == 400
