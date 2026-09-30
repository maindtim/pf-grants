"""Command line: pf-grants fetch | extract | query | summary."""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

from . import fetch as fetch_mod
from .query import (filter_rows, read_csv, rows_from_dir, rows_from_dir_parallel,
                    summarize_by_foundation, write_csv, GRANT_COLUMNS)


def _log(msg: str) -> None:
    print(f"{time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


def cmd_fetch(a: argparse.Namespace) -> int:
    only = a.batches.split(",") if a.batches else None
    out = fetch_mod.fetch_year(a.year, Path(a.data), keep_zips=a.keep_zips, batches_only=only, log=_log)
    _log(f"990-PF XML files in {out}")
    return 0


def cmd_extract(a: argparse.Namespace) -> int:
    errors: list[str] = []
    if a.workers and a.workers > 1:
        rows = rows_from_dir_parallel(a.xml_dir, a.workers, on_error=errors.append)
    else:
        rows = rows_from_dir(a.xml_dir, on_error=lambda p, e: errors.append(f"{p.name}: {e}"))
    n = write_csv(rows, a.out)
    _log(f"{n} grant rows written to {a.out}; {len(errors)} files skipped")
    if errors and a.verbose:
        for err in errors[:20]:
            _log(f"  skipped {err}")
    return 0


def cmd_query(a: argparse.Namespace) -> int:
    rows = read_csv(a.grants_csv)
    kws = [k for k in (a.keyword or [])]
    kept = list(filter_rows(rows, recipient_state=a.recipient_state, foundation_state=a.foundation_state,
                            keywords=kws, min_amount=a.min_amount, kind=a.kind,
                            exclude_preselected=a.exclude_preselected))
    if a.out:
        write_csv(kept, a.out)
        _log(f"{len(kept)} rows -> {a.out}")
    else:
        w = csv.DictWriter(sys.stdout, fieldnames=GRANT_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(kept)
    return 0


def cmd_summary(a: argparse.Namespace) -> int:
    rows = read_csv(a.grants_csv)
    summ = summarize_by_foundation(rows)
    cols = ["foundation_name", "foundation_ein", "foundation_city", "foundation_state", "tax_year",
            "preselected_only", "application_contact", "application_address", "application_deadlines",
            "website", "fmv_assets_eoy", "matching_grants", "matching_amount", "recipients", "source_url"]
    fh = open(a.out, "w", newline="", encoding="utf-8") if a.out else sys.stdout
    w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(summ)
    if a.out:
        fh.close()
        _log(f"{len(summ)} foundations -> {a.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pf-grants",
                                description="Extract the grants list (Supplementary Information, Part XIV) from IRS e-filed Form 990-PF returns.")
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download IRS batches for a year and keep only the 990-PF XML files")
    f.add_argument("--year", type=int, required=True)
    f.add_argument("--data", default="data")
    f.add_argument("--batches", help="comma-separated XML_BATCH_IDs to limit the download, e.g. 2026_TEOS_XML_01A")
    f.add_argument("--keep-zips", action="store_true")
    f.set_defaults(func=cmd_fetch)

    e = sub.add_parser("extract", help="parse every 990-PF XML in a folder into one CSV of grant rows")
    e.add_argument("xml_dir")
    e.add_argument("--out", required=True)
    e.add_argument("--verbose", action="store_true")
    e.add_argument("--workers", type=int, default=1,
                   help="parse files in N processes (reading 80k small files is I/O bound; try 8)")
    e.set_defaults(func=cmd_extract)

    q = sub.add_parser("query", help="filter the grants CSV")
    q.add_argument("grants_csv")
    q.add_argument("--recipient-state")
    q.add_argument("--foundation-state")
    q.add_argument("--keyword", action="append", help="match against recipient name or purpose; repeatable")
    q.add_argument("--min-amount", type=int)
    q.add_argument("--kind", choices=["paid", "future"])
    q.add_argument("--exclude-preselected", action="store_true",
                   help="drop foundations that ticked 'only contributes to preselected organizations'")
    q.add_argument("--out")
    q.set_defaults(func=cmd_query)

    s = sub.add_parser("summary", help="one line per foundation with matching totals")
    s.add_argument("grants_csv")
    s.add_argument("--out")
    s.set_defaults(func=cmd_summary)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
