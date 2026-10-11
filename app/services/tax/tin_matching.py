"""
IRS Bulk TIN Matching (e-Services, Publication 2108A): build the submission
file from the vendor audit and read the IRS results back.

Submission (.txt, one record per line, ≤ 100,000 per file):
    TIN type;TIN;Name;Account number
    TIN type 1 = EIN, 2 = SSN (incl. ITIN), 3 = unknown · Name ≤ 40 chars,
    letters / digits / hyphen / ampersand only · account number ≤ 20 chars

Results file: the same record with an indicator appended (codes below).

The submission is the ONLY artifact that holds full TINs: written with
owner-only permissions, uploaded by the payer's authorised e-Services user,
and meant to be deleted after upload. The account-number map (account →
vendor, form, last 4) holds no TINs; results are joined back through it.
"""
import csv
import os
import re
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

MAX_RECORDS = 100_000

RESULT_CODES = {
    "0": ("TIN and name match IRS records", "ok"),
    "1": ("TIN missing or not 9 digits", "high"),
    "2": ("TIN not currently issued", "high"),
    "3": ("TIN and name do not match IRS records", "high"),
    "4": ("Invalid TIN matching request", "low"),
    "5": ("Duplicate TIN matching request", "low"),
    "6": ("Matches IRS SSN records (submitted as unknown type)", "ok"),
    "7": ("Matches IRS EIN records (submitted as unknown type)", "ok"),
    "8": ("Matches both SSN and EIN records (submitted as unknown type)", "ok"),
}


def irs_name(name: str) -> str:
    """Pub 2108A: letters, digits, hyphen and ampersand; 40 characters."""
    s = re.sub(r"[^A-Za-z0-9&\- ]", " ", name or "")
    return re.sub(r"\s+", " ", s).strip()[:40]


def build_submission(forms: List[Dict], out_dir: Path, stem: str) -> Tuple[List[Path], Path, int, List[Dict]]:
    """
    forms: parsed W-9s holding "_tin" (digits, in memory only).
    Returns (submission files, account map, records written, skipped[]).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records, mapping, skipped = [], [], []
    for i, f in enumerate(forms):
        tin = f.get("_tin")
        who = f.get("vendor") or f.get("name") or f["file"]
        if not tin:
            skipped.append({"vendor": who, "file": f["file"], "reason": "no TIN on the W-9"})
            continue
        if f.get("tin_error"):
            skipped.append({"vendor": who, "file": f["file"], "reason": f"invalid format ({f['tin_error']}) — re-request W-9"})
            continue
        name = irs_name(f.get("name") or "")                 # line 1 is the name the TIN belongs to
        if not name:
            skipped.append({"vendor": who, "file": f["file"], "reason": "no line 1 name"})
            continue
        acct = f"V{i + 1:06d}"
        ttype = {"EIN": "1", "SSN": "2"}.get(f.get("tin_kind"), "3")
        records.append(f"{ttype};{tin};{name};{acct}")
        mapping.append({"account": acct, "vendor": who, "w9_name": name, "file": f["file"],
                        "tin_type": f.get("tin_kind") or "unknown", "tin_last4": f.get("tin_last4")})
    files = []
    for n, start in enumerate(range(0, len(records), MAX_RECORDS), 1):
        p = out_dir / f"TINMatch_{stem}_{date.today():%Y%m%d}_{n:02d}.txt"
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # owner read/write only
        with os.fdopen(fd, "w", encoding="ascii", errors="replace", newline="\r\n") as fh:
            fh.write("\n".join(records[start:start + MAX_RECORDS]) + "\n")
        files.append(p)
    map_path = out_dir / f"TINMatch_{stem}_{date.today():%Y%m%d}_map.csv"
    with open(map_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["account", "vendor", "w9_name", "file", "tin_type", "tin_last4"])
        w.writeheader()
        w.writerows(mapping)
    return files, map_path, len(records), skipped


def read_results(results: List[Path], map_path: Path) -> List[Dict]:
    """IRS results (TIN type;TIN;Name;Account;Indicator) → per-vendor outcome. TINs are dropped on read."""
    with open(map_path, newline="") as fh:
        mapping = {r["account"]: r for r in csv.DictReader(fh)}
    out = []
    for path in results:
        for line in Path(path).read_text(errors="replace").splitlines():
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 5:
                continue
            acct, code = parts[3], parts[4][:1]
            m = mapping.get(acct, {})
            meaning, sev = RESULT_CODES.get(code, (f"Unknown indicator {code}", "low"))
            out.append({"account": acct, "vendor": m.get("vendor", acct), "w9_name": m.get("w9_name", parts[2]),
                        "file": m.get("file", "—"), "tin_last4": m.get("tin_last4", "—"),
                        "code": code, "result": meaning, "severity": sev})
    return out
