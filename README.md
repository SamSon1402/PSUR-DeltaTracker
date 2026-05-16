# PSUR-DeltaTracker

> Automated PSUR update generation. Ingest ICSRs from EudraVigilance / FAERS → compare reporting intervals → surface new safety signals via disproportionality analysis → produce the updated PSUR section.

**Status:** prototype · backend API only · designed to plug into Biolevate's knowledge platform (Post-Market use case).

## What this does

A pharmaceutical Marketing Authorisation Holder produces a Periodic Safety Update Report (PSUR) per ICH E2C(R2) at regular intervals (6-monthly, annually, biennially depending on product age). Each PSUR compares the current reporting interval to the previous one, calls out new safety signals, and proposes label / risk-management updates.

The mechanical work — ingesting hundreds of Individual Case Safety Reports (ICSRs), tallying MedDRA term frequencies, computing disproportionality statistics, deciding which PSUR sections need rewriting — is repetitive but high-stakes. This service automates it.

1. **Ingest** ICSRs (`POST /products/{id}/icsrs`)
2. **Register** PSUR versions with their Data Lock Points (DLPs)
3. **Compute the delta** between two PSURs: new cases, MedDRA term shifts, sections impacted
4. **Detect signals** via Reporting Odds Ratio (ROR) with 95% CI
5. **Escalate** open signals to the Qualified Person for Pharmacovigilance (QPPV)

Target outcome: QPPV opens the PSUR draft with signals already triaged and frequency deltas pre-computed, rather than re-deriving them by hand each interval.

## Architecture

```
   EudraVigilance / FAERS         Biolevate API              Postgres
   ┌──────────────────┐           ┌──────────────┐          ┌───────────┐
   │   ICSR stream    │──────────▶│              │          │ icsrs     │
   └──────────────────┘           │  FastAPI     │─────────▶│ psurs     │
                                  │  Delta +     │          │ signals   │
                                  │  Signal eng  │          │ deltas    │
                                  │              │          └───────────┘
                                  └──────┬───────┘
                                         │
                                         ▼
                                  QPPV review UI
```

### Key design decisions

**Period-over-period as the unit of comparison.** A PSUR is fundamentally an interval. We store each PSUR with its Data Lock Point (DLP) defining `[interval_start, interval_end]` and bucket every ICSR into the interval its `received_at` falls in. Delta = current interval stats minus previous interval stats.

**Disproportionality signals via ROR.** For each MedDRA Preferred Term reported in the current interval, we compute the Reporting Odds Ratio against the rest of the product's case base. A signal fires when the lower 95% CI bound exceeds 1.0 — the standard pharmacovigilance threshold.

**MedDRA-aware throughout.** ICSRs carry MedDRA PT codes natively. Frequency rollups happen at the PT level. Real deployments would also support SOC / HLT rollups (out of scope here).

**ICH E2B(R3) seriousness criteria as first-class enum.** Death, life-threatening, hospitalisation, disability, congenital anomaly, medically important — each ICSR carries the flags directly so regulators can audit them.

### What's stubbed

- **MedDRA dictionary**: hardcoded subset of ~10 PTs. Production: load the full MedDRA v27.0 hierarchy (~80k terms).
- **EudraVigilance / FAERS adapter**: ICSRs arrive via `POST /icsrs`. Production: scheduled pull from regulator endpoints + dedup against prior-interval cases.
- **Storage**: in-memory dict. Production: Postgres + a separate signal-detection job runner.
- **Auth**: omitted. Production: per-QPPV electronic signatures.

## API

All endpoints under `/api/v1`.

### Products
- `POST /products` — register a product
- `GET /products` — list products
- `GET /products/{id}`

### ICSRs (Individual Case Safety Reports)
- `POST /products/{id}/icsrs` — ingest a case
- `GET /products/{id}/icsrs?serious&from_date&to_date` — list cases (filterable)
- `GET /icsrs/{id}` — case detail

### PSURs
- `POST /products/{id}/psurs` — register a PSUR version with its DLP
- `GET /products/{id}/psurs` — list PSURs

### Deltas (the headline feature)
- `POST /products/{id}/delta` — compute delta between two PSURs (body: `{from_psur_id, to_psur_id}`)
- `GET /products/{id}/deltas` — list past deltas
- `GET /products/{id}/deltas/{delta_id}`

### Signals
- `GET /products/{id}/signals?status=open` — list detected signals
- `POST /products/{id}/signals/{signal_id}/escalate` — escalate to QPPV
- `GET /products/{id}/meddra-frequencies?from_date&to_date` — PT counts in a window

### Health
- `GET /health`
- `GET /health/ready`

## Example flow

```bash
# 1. Register product
curl -X POST http://localhost:8000/api/v1/products -H "Content-Type: application/json" -d '{
  "code": "BVL-2188",
  "name": "BVL-2188",
  "indication": "Moderate-to-severe atopic dermatitis",
  "approval_date": "2024-03-15"
}'
# → { "id": "<product_uuid>", ... }

# 2. Register two PSUR versions
curl -X POST http://localhost:8000/api/v1/products/<id>/psurs -H "Content-Type: application/json" -d '{
  "version": "v4", "interval_start": "2025-04-01", "interval_end": "2025-09-30", "dlp": "2025-09-30"
}'
curl -X POST http://localhost:8000/api/v1/products/<id>/psurs -H "Content-Type: application/json" -d '{
  "version": "v5", "interval_start": "2025-10-01", "interval_end": "2026-03-31", "dlp": "2026-03-31"
}'

# 3. Ingest some ICSRs (each falls into one interval based on received_at)
curl -X POST http://localhost:8000/api/v1/products/<id>/icsrs -H "Content-Type: application/json" -d '{
  "case_id": "EU-EC-12345",
  "received_at": "2026-01-15T00:00:00Z",
  "country": "FR", "sex": "female", "age_years": 42,
  "serious": false,
  "seriousness_criteria": [],
  "suspected_terms": [{"pt_code": "10037087", "pt_name": "Pruritus generalised"}],
  "outcome": "recovering",
  "reporter_type": "physician"
}'

# 4. Compute delta
curl -X POST http://localhost:8000/api/v1/products/<id>/delta -H "Content-Type: application/json" -d '{
  "from_psur_id": "<v4_uuid>", "to_psur_id": "<v5_uuid>"
}'
# → { sections_changed: [...], meddra_deltas: [...], new_signals: [...] }

# 5. Review signals
curl http://localhost:8000/api/v1/products/<id>/signals
```

## Run

```bash
cp .env.example .env
pip install -r requirements.txt
uvicorn app.main:app --reload
pytest -v
```

## Project layout

```
psur-deltatracker/
├── app/
│   ├── main.py          # FastAPI entry
│   ├── config.py
│   ├── api.py           # all routes
│   ├── schemas.py       # Pydantic + ICH E2B/E2C enums
│   ├── delta_engine.py  # period-over-period comparison
│   ├── signal_engine.py # ROR disproportionality stats
│   └── store.py
└── tests/
    └── test_api.py
```

---

Built as a Solutions Engineering demo. Plugs into Biolevate's PSUR update generation use case.
