"""Download IRS Form 990 e-file batches and keep only the 990-PF returns.

The IRS publishes yearly index CSVs and monthly zip batches at
https://apps.irs.gov/pub/epostcard/990/xml/<year>/ . The index says which batch
holds each return (XML_BATCH_ID), so we download each batch once and extract
only the members whose OBJECT_ID is a 990-PF in the index.

No account, no API key, no cost: these are public files.
"""
from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

BASE = "https://apps.irs.gov/pub/epostcard/990/xml"
USER_AGENT = "pf-grants/0.1 (open-source 990-PF grant extractor; run by an AI agent)"


def index_url(year: int) -> str:
    return f"{BASE}/{year}/index_{year}.csv"


def batch_url(year: int, batch_id: str) -> str:
    return f"{BASE}/{year}/{batch_id}.zip"


def download(url: str, dest: Path, log=print) -> Path:
    """Stream *url* to *dest* (skips if already complete). Returns dest."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        if dest.exists() and total and dest.stat().st_size == total:
            log(f"  already downloaded: {dest.name} ({total/1e6:.0f} MB)")
            return dest
        tmp = dest.with_suffix(dest.suffix + ".part")
        done = 0
        t0 = time.time()
        with open(tmp, "wb") as fh:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                done += len(chunk)
                if total and done % (20 << 20) < (1 << 20):
                    rate = done / max(time.time() - t0, 1e-6) / 1e6
                    log(f"  {dest.name}: {done/1e6:.0f}/{total/1e6:.0f} MB ({rate:.1f} MB/s)")
        os.replace(tmp, dest)
    return dest


def read_index(path: Path, return_type: str = "990PF") -> dict[str, set[str]]:
    """Map XML_BATCH_ID -> set of OBJECT_ID for returns of *return_type*."""
    batches: dict[str, set[str]] = defaultdict(set)
    with open(path, newline="", encoding="utf-8", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if row.get("RETURN_TYPE") == return_type:
                batches[row["XML_BATCH_ID"]].add(row["OBJECT_ID"])
    return dict(batches)


def _wanted_members(zf: zipfile.ZipFile, wanted: set[str]) -> list[str]:
    out = []
    for name in zf.namelist():
        base = os.path.basename(name)
        if base.endswith(".xml") and base.split("_")[0] in wanted:
            out.append(name)
    return out


def find_7z() -> str | None:
    """Path to a 7-Zip console binary, if any (env PF_GRANTS_7ZA, then PATH)."""
    env = os.environ.get("PF_GRANTS_7ZA")
    if env and Path(env).exists():
        return env
    for cand in ("7z", "7za", "7zz"):
        found = shutil.which(cand)
        if found:
            return found
    return None


def _extract_with_7z(seven: str, zip_path: Path, names: list[str], out_dir: Path) -> None:
    """Extract the whole batch to a staging folder, keep the wanted files, drop the rest.

    (Passing tens of thousands of member names to 7-Zip as an include list is
    quadratic and stalls; a full extract plus a move is far faster.)
    """
    staging = out_dir.parent / (zip_path.stem + ".staging")
    staging.mkdir(parents=True, exist_ok=True)
    cmd = [seven, "e", "-y", "-bso0", "-bsp0", f"-o{staging}", str(zip_path)]
    subprocess.run(cmd, check=True)
    for name in names:
        base = os.path.basename(name)
        src = staging / base
        if src.exists():
            os.replace(src, out_dir / base)
    shutil.rmtree(staging, ignore_errors=True)


def _extract_with_stream_unzip(zip_path: Path, names: list[str], out_dir: Path, log=print) -> None:
    from stream_unzip import stream_unzip  # pure Python, slow (~0.5 MB/s) but needs nothing else

    wanted = set(names)
    log("  warning: no 7-Zip found; using stream-unzip (pure Python, slow: can take over an hour per batch)")

    def chunks():
        with open(zip_path, "rb") as fh:
            while True:
                c = fh.read(1 << 20)
                if not c:
                    return
                yield c

    for name, _size, it in stream_unzip(chunks()):
        name = name.decode("utf-8", "replace") if isinstance(name, bytes) else name
        if name in wanted:
            with open(out_dir / os.path.basename(name), "wb") as dst:
                for ch in it:
                    dst.write(ch)
        else:
            for _ in it:
                pass


def extract_members(zip_path: Path, wanted: set[str], out_dir: Path, log=print) -> int:
    """Extract members named <OBJECT_ID>_public.xml for OBJECT_IDs in *wanted*.

    Some IRS batches use Deflate64 (method 9), which Python's zipfile cannot
    read. Those fall back to 7-Zip if available, else to stream-unzip.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in _wanted_members(zf, wanted) if not (out_dir / os.path.basename(n)).exists()]
        methods = {zf.getinfo(n).compress_type for n in names[:50]}
    if not names:
        log(f"  nothing new to extract from {zip_path.name}")
    elif methods <= {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED, zipfile.ZIP_BZIP2, zipfile.ZIP_LZMA}:
        with zipfile.ZipFile(zip_path) as zf:
            for name in names:
                with zf.open(name) as src, open(out_dir / os.path.basename(name), "wb") as dst:
                    dst.write(src.read())
    else:
        seven = find_7z()
        if seven:
            log(f"  compression method {sorted(methods)} (Deflate64): extracting with {seven}")
            _extract_with_7z(seven, zip_path, names, out_dir)
        else:
            _extract_with_stream_unzip(zip_path, names, out_dir, log)
    n = sum(1 for oid in wanted if (out_dir / f"{oid}_public.xml").exists())
    log(f"  extracted {n} of {len(wanted)} expected from {zip_path.name}")
    return n


def fetch_year(year: int, data_dir: Path, keep_zips: bool = False,
               batches_only: list[str] | None = None, log=print) -> Path:
    """Download the index and every batch for *year*; keep only 990-PF XMLs.

    Returns the directory with the extracted XML files.
    """
    data_dir = Path(data_dir)
    idx = download(index_url(year), data_dir / f"index_{year}.csv", log)
    batches = read_index(idx)
    xml_dir = data_dir / f"pf_{year}"
    log(f"{year}: {sum(len(v) for v in batches.values())} Form 990-PF returns in {len(batches)} batches")
    for batch_id in sorted(batches):
        if batches_only and batch_id not in batches_only:
            continue
        wanted = batches[batch_id]
        have = sum(1 for oid in wanted if (xml_dir / f"{oid}_public.xml").exists())
        if have == len(wanted):
            log(f"{batch_id}: already extracted ({have})")
            continue
        log(f"{batch_id}: downloading ({len(wanted)} 990-PF)")
        zp = download(batch_url(year, batch_id), data_dir / f"{batch_id}.zip", log)
        got = extract_members(zp, wanted, xml_dir, log)
        if not keep_zips:
            zp.unlink()
        # The index names one batch per month (e.g. ..._05A) but the IRS sometimes
        # splits a month into 05A, 05B, ...; the extra parts are not in the index.
        suffix = "B"
        while got < len(wanted) and suffix <= "E":
            sibling = batch_id[:-1] + suffix
            if not url_exists(batch_url(year, sibling)):
                break
            log(f"{sibling}: sibling batch not in the index, downloading ({len(wanted) - got} still missing)")
            zp = download(batch_url(year, sibling), data_dir / f"{sibling}.zip", log)
            got = extract_members(zp, wanted, xml_dir, log)
            if not keep_zips:
                zp.unlink()
            suffix = chr(ord(suffix) + 1)
        if got < len(wanted):
            log(f"{batch_id}: warning, {len(wanted) - got} returns listed in the index are in no zip file")
    return xml_dir


def url_exists(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200
    except Exception:
        return False


if __name__ == "__main__":  # pragma: no cover
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2026
    data = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data")
    only = sys.argv[3].split(",") if len(sys.argv) > 3 else None

    def log(msg: str) -> None:
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        with open(data / "fetch.log", "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    data.mkdir(parents=True, exist_ok=True)
    fetch_year(year, data, batches_only=only, log=log)
    log("done")
