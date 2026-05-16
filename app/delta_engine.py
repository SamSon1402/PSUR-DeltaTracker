"""
Delta engine — compares two PSUR reporting intervals.

Output drives the QPPV's PSUR draft:
- which sections of the next PSUR need rewriting
- how MedDRA term frequencies have shifted
- new safety signals detected in the current interval

Pure functions over case data — no I/O.
"""
from collections import Counter
from datetime import date, datetime

from app.schemas import ICSR, MedDRADelta, PSUR


def _cases_in_interval(cases: list[ICSR], start: date, end: date) -> list[ICSR]:
    """Filter cases whose received_at falls within [start, end]."""
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    return [
        c for c in cases
        if start_dt <= c.received_at.replace(tzinfo=None) <= end_dt
    ]


def _pt_counts(cases: list[ICSR]) -> Counter[str]:
    """Per-case PT counter (one increment per case even if PT appears twice)."""
    counts: Counter[str] = Counter()
    for case in cases:
        seen: set[str] = set()
        for term in case.suspected_terms:
            if term.pt_code not in seen:
                counts[term.pt_code] += 1
                seen.add(term.pt_code)
    return counts


def _pt_names(cases: list[ICSR]) -> dict[str, str]:
    """Build a PT-code → PT-name lookup from a case set."""
    names: dict[str, str] = {}
    for case in cases:
        for term in case.suspected_terms:
            names.setdefault(term.pt_code, term.pt_name)
    return names


def compute_meddra_deltas(
    previous_cases: list[ICSR],
    current_cases: list[ICSR],
) -> list[MedDRADelta]:
    """Per-PT frequency comparison between two intervals."""
    prev = _pt_counts(previous_cases)
    curr = _pt_counts(current_cases)
    names = {**_pt_names(previous_cases), **_pt_names(current_cases)}

    all_pts = set(prev) | set(curr)
    deltas: list[MedDRADelta] = []
    for pt in all_pts:
        p, c = prev.get(pt, 0), curr.get(pt, 0)
        delta_abs = c - p
        delta_pct = ((c - p) / p * 100) if p > 0 else (100.0 if c > 0 else 0.0)
        deltas.append(MedDRADelta(
            pt_code=pt,
            pt_name=names.get(pt, pt),
            count_previous=p,
            count_current=c,
            delta_absolute=delta_abs,
            delta_percent=round(delta_pct, 1),
        ))

    # Sort by absolute change descending so reviewers see the biggest shifts first
    deltas.sort(key=lambda d: abs(d.delta_absolute), reverse=True)
    return deltas


def derive_sections_changed(
    new_icsrs: int,
    new_serious_icsrs: int,
    deltas: list[MedDRADelta],
    new_signals_count: int,
) -> list[str]:
    """
    Map detected changes to the ICH E2C(R2) PSUR sections that need rewriting.

    Rules are illustrative — production maps more granularly per the ICH E2C(R2)
    section catalogue and customer-specific PSUR templates.
    """
    sections: list[str] = []
    # Cumulative exposure always updates when new cases arrive
    if new_icsrs > 0:
        sections.append("Section 6.3 — Cumulative Exposure")
        sections.append("Section 9.1 — New ICSRs")

    # ADRs frequency table changes if any PT shifted by ≥20%
    if any(abs(d.delta_percent) >= 20 for d in deltas):
        sections.append("Section 8.2 — Adverse Reactions")

    # Signal evaluation section updates whenever new signals fire
    if new_signals_count > 0:
        sections.append("Section 12 — Signal Evaluation")
        sections.append("Section 13 — Risk-Benefit (signal-driven)")

    # Serious cases always touch the integrated risk summary
    if new_serious_icsrs > 0:
        sections.append("Section 11 — Serious Risks (cumulative)")

    return sections


def split_cases_by_psur(
    previous_psur: PSUR,
    current_psur: PSUR,
    all_cases: list[ICSR],
) -> tuple[list[ICSR], list[ICSR]]:
    """Bucket cases into the two intervals defined by the PSUR DLPs."""
    prev = _cases_in_interval(all_cases, previous_psur.interval_start, previous_psur.interval_end)
    curr = _cases_in_interval(all_cases, current_psur.interval_start, current_psur.interval_end)
    return prev, curr
