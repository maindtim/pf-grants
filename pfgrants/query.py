"""Build flat grant rows from parsed 990-PFs and filter them.

One row per grant, carrying the foundation's identity, whether it accepts
applications, and the source file so every row can be checked against the
original filing.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Iterable, Iterator

from .extract import Foundation, iter_990pf

GRANT_COLUMNS = [
    "foundation_name", "foundation_ein", "foundation_city", "foundation_state",
    "tax_year", "tax_period_end", "fmv_assets_eoy", "contributions_paid",
    "preselected_only", "application_contact", "application_address",
    "application_form_info", "application_deadlines", "application_restrictions",
    "website", "phone",
    "grant_kind", "recipient", "recipient_city", "recipient_state", "recipient_zip",
    "recipient_country", "purpose", "amount", "relationship", "foundation_status",
    "source_object_id", "source_url",
]

IRS_SEARCH_URL = "https://apps.irs.gov/app/eos/"  # object IDs are not linkable; search by EIN


def source_url(ein: str) -> str:
    """ProPublica Nonprofit Explorer page for the filer (public, no login)."""
    ein = re.sub(r"\D", "", ein)
    return f"https://projects.propublica.org/nonprofits/organizations/{ein}"


def grant_rows(foundations: Iterable[Foundation]) -> Iterator[dict]:
    for f in foundations:
        base = {
            "foundation_name": f.name, "foundation_ein": f.ein,
            "foundation_city": f.city, "foundation_state": f.state,
            "tax_year": f.tax_year, "tax_period_end": f.tax_period_end,
            "fmv_assets_eoy": f.fmv_assets_eoy, "contributions_paid": f.contributions_paid,
            "preselected_only": "yes" if f.preselected_only else "no",
            "application_contact": f.application_contact,
            "application_address": f.application_address,
            "application_form_info": f.application_form_info,
            "application_deadlines": f.application_deadlines,
            "application_restrictions": f.application_restrictions,
            "website": f.website, "phone": f.phone,
            "source_object_id": f.object_id, "source_url": source_url(f.ein),
        }
        for g in f.grants:
            row = dict(base)
            row.update({
                "grant_kind": g.kind, "recipient": g.recipient,
                "recipient_city": g.recipient_city, "recipient_state": g.recipient_state,
                "recipient_zip": g.recipient_zip, "recipient_country": g.recipient_country,
                "purpose": g.purpose, "amount": g.amount,
                "relationship": g.relationship, "foundation_status": g.foundation_status,
            })
            yield row


def _compile(keywords: Iterable[str] | None) -> re.Pattern | None:
    kws = [k.strip() for k in (keywords or []) if k.strip()]
    if not kws:
        return None
    return re.compile("|".join(re.escape(k) for k in kws), re.IGNORECASE)


def filter_rows(rows: Iterable[dict], *, recipient_state: str | None = None,
                foundation_state: str | None = None, keywords: Iterable[str] | None = None,
                min_amount: int | None = None, kind: str | None = None,
                exclude_preselected: bool = False) -> Iterator[dict]:
    """Keep rows matching every given criterion.

    *keywords* are matched (case-insensitive, any of them) against the
    recipient name and the stated purpose.
    """
    pat = _compile(keywords)
    rs = recipient_state.upper() if recipient_state else None
    fs = foundation_state.upper() if foundation_state else None
    for r in rows:
        if rs and r["recipient_state"].upper() != rs:
            continue
        if fs and r["foundation_state"].upper() != fs:
            continue
        if kind and r["grant_kind"] != kind:
            continue
        if min_amount is not None and (r["amount"] is None or r["amount"] < min_amount):
            continue
        if exclude_preselected and r["preselected_only"] == "yes":
            continue
        if pat and not (pat.search(r["recipient"] or "") or pat.search(r["purpose"] or "")):
            continue
        yield r


def write_csv(rows: Iterable[dict], dest: str | Path) -> int:
    n = 0
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=GRANT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
            n += 1
    return n


def read_csv(path: str | Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k in ("amount", "fmv_assets_eoy", "contributions_paid"):
            r[k] = int(r[k]) if r.get(k) not in (None, "") else None
    return rows


def rows_from_dir(xml_dir: str | Path, on_error=None) -> Iterator[dict]:
    return grant_rows(iter_990pf(xml_dir, on_error=on_error))


def _rows_for_file(path: str) -> tuple[list[dict], str | None]:
    """Worker: grant rows for one file, or (,[], error text)."""
    from lxml import etree
    from .extract import parse_990pf, NotA990PF
    try:
        return list(grant_rows([parse_990pf(path)])), None
    except (NotA990PF, etree.XMLSyntaxError, OSError) as exc:
        return [], f"{Path(path).name}: {exc}"


def rows_from_dir_parallel(xml_dir: str | Path, workers: int, on_error=None,
                           chunksize: int = 64) -> Iterator[dict]:
    """Same as rows_from_dir but parses files in *workers* processes.

    Reading tens of thousands of small files is I/O bound on most machines
    (antivirus scanning included); a handful of processes hides that.
    """
    from concurrent.futures import ProcessPoolExecutor
    paths = [str(p) for p in sorted(Path(xml_dir).glob("*.xml"))]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for rows, err in ex.map(_rows_for_file, paths, chunksize=chunksize):
            if err and on_error:
                on_error(err)
            yield from rows


def summarize_by_foundation(rows: Iterable[dict]) -> list[dict]:
    """Collapse grant rows into one line per foundation with totals."""
    acc: dict[str, dict] = {}
    for r in rows:
        key = r["foundation_ein"]
        a = acc.setdefault(key, {
            "foundation_name": r["foundation_name"], "foundation_ein": key,
            "foundation_city": r["foundation_city"], "foundation_state": r["foundation_state"],
            "tax_year": r["tax_year"], "preselected_only": r["preselected_only"],
            "application_contact": r["application_contact"],
            "application_address": r["application_address"],
            "application_deadlines": r["application_deadlines"],
            "website": r["website"], "fmv_assets_eoy": r["fmv_assets_eoy"],
            "matching_grants": 0, "matching_amount": 0, "recipients": set(),
            "source_url": r["source_url"],
        })
        a["matching_grants"] += 1
        a["matching_amount"] += r["amount"] or 0
        a["recipients"].add(r["recipient"])
    out = []
    for a in acc.values():
        a["recipients"] = "; ".join(sorted(a["recipients"]))
        out.append(a)
    out.sort(key=lambda d: (-d["matching_amount"], d["foundation_name"]))
    return out
