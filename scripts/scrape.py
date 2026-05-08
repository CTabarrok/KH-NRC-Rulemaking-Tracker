#!/usr/bin/env python3
"""
NRC Rulemaking Tracker scraper.

Pulls active rulemakings from two NRC sources and merges with manually-curated
KH practice notes. Writes data/rules.json that the dashboard consumes.

Sources:
    1. NRC rules-export.csv  -> primary, canonical list of ~68 active rules
       https://www.nrc.gov/sites/default/files/doc_library/cdn/data/rules/rules-export.csv
    2. NRC EO 14300 HTML page -> comment deadlines, meeting dates, completed rules
       https://www.nrc.gov/about-nrc/governing-laws/advance-act/wholesale-revision-regs
    3. data/practice_notes.json -> KH practice commentary, keyed by docket ID

The CSV is the structured truth for everything that's "active." The HTML page
provides comment deadlines (not in the CSV) and rules that have moved to
"Complete" (no longer in the CSV). practice_notes.json carries the
KH-authored summary and implication that the front-end displays.

Usage:
    python3 scripts/scrape.py
    python3 scripts/scrape.py --dry-run    # don't write file
    python3 scripts/scrape.py --diff       # show what changed vs current rules.json
"""

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError as exc:
    sys.stderr.write(
        f"Missing dependency: {exc.name}. "
        "Run: pip install -r scripts/requirements.txt\n"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_URL = (
    "https://www.nrc.gov/sites/default/files/"
    "doc_library/cdn/data/rules/rules-export.csv"
)
EO14300_URL = (
    "https://www.nrc.gov/about-nrc/governing-laws/advance-act/wholesale-revision-regs"
)
USER_AGENT = (
    "KH-Nuclear-Tracker/1.0 (+https://github.com/; "
    "Kimley-Horn Nuclear Task Force)"
)
TIMEOUT = 30

ROOT = Path(__file__).resolve().parent.parent
RULES_OUT = ROOT / "data" / "rules.json"
PRACTICE_NOTES = ROOT / "data" / "practice_notes.json"


# Phase normalization
PHASE_MAP = {
    "proposed rule": "Proposed",
    "final rule": "Final",
    "pre-rule": "Pre-Rule",
}
PHASE_HTML_MAP = {
    "complete": "Complete",
    "completed": "Complete",
    "proposed": "Proposed",
    "final": "Final",
    "direct final": "Direct Final",
    "direct-final": "Direct Final",
    "pre-rule": "Pre-Rule",
}

# Default practice tag from NRC Area_of_Regulatory_Responsibility.
# practice_notes.json overrides per-rule when curated.
AREA_TO_PRACTICE = {
    "New Reactors": "SMR & Advanced Reactor Siting",
    "Advanced Reactors": "SMR & Advanced Reactor Siting",
    "Operating Reactors": "Operating Fleet",
    "Nuclear Materials Users": "Materials & Medical",
    "Decommissioning and Low-Level Waste": "Fuel Cycle & Decommissioning",
    "Spent Fuel Storage and Transportation": "Transportation",
    "Fuel Facilities": "Fuel Cycle & Decommissioning",
    "Corporate Support": "Administrative",
}

DOCKET_RE = re.compile(r"NRC-\d{4}-\d{3,5}")
DATE_RE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")
DEADLINE_RE = re.compile(r"deadline\s*[:\-]?\s*(\d{1,2}/\d{1,2}/\d{4})", re.IGNORECASE)
EO_PREFIX_RE = re.compile(r"^\s*\[14300\]\s*")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return s[:60] if s else "unknown"


def parse_slash_date(text: str | None) -> str | None:
    if not text:
        return None
    m = DATE_RE_SLASH.search(text)
    if not m:
        return None
    mo, da, yr = m.groups()
    try:
        return datetime(int(yr), int(mo), int(da)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_cfr_parts(text: str) -> list[str]:
    if not text:
        return []
    parts = re.split(r"[,;]\s*", text.strip())
    out = []
    for p in parts:
        p = p.strip()
        if re.fullmatch(r"\d+[A-Za-z]?", p):
            out.append(p)
    return out


def truncate(text: str, n: int = 320) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n].rsplit(" ", 1)[0]
    return cut + "\u2026"


def fetch(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
    r.raise_for_status()
    if "rules-export.csv" in url:
        r.encoding = "utf-8-sig"     # CSV has BOM
    return r.text


# ---------------------------------------------------------------------------
# Source 1: CSV
# ---------------------------------------------------------------------------


def derive_phase(row: dict) -> str:
    if (row.get("Direct_Final_Rule") or "").strip().lower() == "yes":
        return "Direct Final"
    rule_phase = (row.get("Rule_Phase") or "").strip().lower()
    return PHASE_MAP.get(rule_phase, rule_phase.title() or "Unknown")


def derive_pub_and_fr(row: dict, phase: str) -> tuple[str | None, str | None]:
    """Pick the publication date and FR citation for the rule's current phase."""
    if phase == "Proposed":
        candidates = [(row.get("Proposed_Rule_Publication_Date"),
                       row.get("FR_Citation_for_Proposed_Rule"))]
    elif phase == "Final":
        candidates = [(row.get("Final_Rule_Publication_Date"),
                       row.get("FR_Citation_for_Final_Rule"))]
    elif phase == "Direct Final":
        candidates = [(row.get("Publication_Date"),
                       row.get("FR_Citation_for_Direct_Final_Rule"))]
    elif phase == "Pre-Rule":
        candidates = [
            (row.get("ANPR_Publication_Date"), row.get("FR_Citation_for_ANPR")),
            (row.get("Regulatory_Basis_Publication_Date"),
             row.get("FR_Citation_for_Regulatory_Basis")),
            (row.get("Rulemaking_Initiation_Date"), None),
        ]
    else:
        candidates = []

    for pub_text, fr_text in candidates:
        d = parse_slash_date(pub_text)
        if d:
            return d, (fr_text or None)

    # Fallback: most recent date in any field
    for f in ("Final_Rule_Publication_Date", "Proposed_Rule_Publication_Date",
              "Publication_Date", "Regulatory_Basis_Publication_Date",
              "ANPR_Publication_Date", "Rulemaking_Initiation_Date"):
        d = parse_slash_date(row.get(f))
        if d:
            return d, None
    return None, None


def scrape_csv() -> list[dict]:
    print(f"[scrape] GET {CSV_URL}", flush=True)
    text = fetch(CSV_URL)
    reader = csv.DictReader(io.StringIO(text))

    rules: list[dict] = []
    for row in reader:
        title = (row.get("Title") or "").strip()
        if not title:
            continue

        eo14300 = bool(EO_PREFIX_RE.match(title))
        title = EO_PREFIX_RE.sub("", title).strip()

        docket = (row.get("Docket_ID") or "").strip() or None
        phase = derive_phase(row)
        pub_date, fr_citation = derive_pub_and_fr(row, phase)

        cpr_priority = (row.get("CPR_Priority") or "").strip() or None
        cpr_score_raw = (row.get("CPR_Score") or "").strip()
        cpr_score = int(cpr_score_raw) if cpr_score_raw.isdigit() else None

        rules.append({
            "id": slugify(docket or title),
            "title": title,
            "docket": docket,
            "rin": (row.get("RIN") or "").strip() or None,
            "cfr": parse_cfr_parts(row.get("Affected_CFR_Parts", "")),
            "prm": (row.get("Associated_PRM_Numbers") or "").strip() or None,
            "phase": phase,
            "ruleType": (row.get("Rule_Type") or "").strip() or None,
            "area": (row.get("Area_of_Regulatory_Responsibility") or "").strip() or None,
            "office": (row.get("NRC_Office") or "").strip() or None,
            "fundingStatus": (row.get("Status") or "").strip() or None,
            "cprPriority": cpr_priority,
            "cprScore": cpr_score,
            "abstract": (row.get("Abstract") or "").strip(),
            "pubDate": pub_date,
            "frCitation": (fr_citation or "").strip() or None,
            "complianceDate": parse_slash_date(row.get("Final_Rule_Compliance_Date")),
            "eo14300": eo14300,
            "nrcContact": (row.get("NRCContact") or "").strip() or None,
            "nrcUrl": None,
            "frUrl": None,
            "docketUrl": (
                f"https://www.regulations.gov/docket/{docket}" if docket else None
            ),
            "meetingDate": None,
            "meetingUrl": None,
            "deadline": None,
        })

    print(f"[scrape] CSV: {len(rules)} rules", flush=True)
    return rules


# ---------------------------------------------------------------------------
# Source 2: EO 14300 HTML overlay
# ---------------------------------------------------------------------------


def find_rulemaking_table(soup: BeautifulSoup):
    for t in soup.find_all("table"):
        headers = [th.get_text(strip=True).lower() for th in t.find_all("th")]
        if "title" in headers and any("rule phase" in h for h in headers):
            return t
    return None


def extract_link(cell, base: str = EO14300_URL) -> str | None:
    a = cell.find("a")
    if a and a.get("href"):
        return urljoin(base, a["href"])
    return None


def parse_html_row(row) -> dict | None:
    cells = row.find_all(["td", "th"])
    if len(cells) < 6:
        return None
    title_cell, _, phase_cell, pub_cell, meeting_cell, comment_cell = cells[:6]

    title = title_cell.get_text(" ", strip=True)
    if not title:
        return None
    nrc_url = extract_link(title_cell)
    phase_raw = phase_cell.get_text(" ", strip=True).strip().lower()
    phase = PHASE_HTML_MAP.get(phase_raw, phase_raw.title())
    pub_date = parse_slash_date(pub_cell.get_text(" ", strip=True))
    fr_url = extract_link(pub_cell)
    meeting_date = parse_slash_date(meeting_cell.get_text(" ", strip=True))
    meeting_url = extract_link(meeting_cell)
    comment_text = comment_cell.get_text(" ", strip=True)
    docket_match = DOCKET_RE.search(comment_text)
    docket = docket_match.group(0) if docket_match else None
    deadline_match = DEADLINE_RE.search(comment_text)
    deadline = parse_slash_date(deadline_match.group(1)) if deadline_match else None
    docket_url = extract_link(comment_cell)

    return {
        "title": title,
        "phase": phase,
        "pubDate": pub_date,
        "frUrl": fr_url,
        "meetingDate": meeting_date,
        "meetingUrl": meeting_url,
        "deadline": deadline,
        "docket": docket,
        "docketUrl": docket_url,
        "nrcUrl": nrc_url,
    }


def scrape_eo14300_html() -> list[dict]:
    print(f"[scrape] GET {EO14300_URL}", flush=True)
    html = fetch(EO14300_URL)
    soup = BeautifulSoup(html, "html.parser")
    table = find_rulemaking_table(soup)
    if not table:
        print("[scrape] WARNING: EO 14300 table not found; skipping HTML overlay",
              file=sys.stderr)
        return []
    rows = []
    for tr in table.find_all("tr"):
        if not tr.find("td"):
            continue
        parsed = parse_html_row(tr)
        if parsed:
            rows.append(parsed)
    print(f"[scrape] HTML: {len(rows)} rows from EO 14300 page", flush=True)
    return rows


# ---------------------------------------------------------------------------
# Merge: HTML overlay onto CSV; append HTML-only rules
# ---------------------------------------------------------------------------


def merge_sources(csv_rules: list[dict], html_rows: list[dict]) -> list[dict]:
    by_docket = {r["docket"]: r for r in csv_rules if r.get("docket")}
    overlaid = 0
    appended = 0

    for hr in html_rows:
        dk = hr.get("docket")
        if not dk:
            continue
        if dk in by_docket:
            cr = by_docket[dk]
            if hr.get("deadline"):
                cr["deadline"] = hr["deadline"]
            if hr.get("meetingDate"):
                cr["meetingDate"] = hr["meetingDate"]
            if hr.get("meetingUrl"):
                cr["meetingUrl"] = hr["meetingUrl"]
            if hr.get("nrcUrl") and not cr.get("nrcUrl"):
                cr["nrcUrl"] = hr["nrcUrl"]
            if hr.get("frUrl") and not cr.get("frUrl"):
                cr["frUrl"] = hr["frUrl"]
            if hr.get("phase") == "Complete":
                cr["phase"] = "Complete"
            cr["eo14300"] = True
            overlaid += 1
        else:
            csv_rules.append({
                "id": slugify(dk),
                "title": hr["title"],
                "docket": dk,
                "rin": None,
                "cfr": [],
                "prm": None,
                "phase": hr.get("phase") or "Complete",
                "ruleType": None,
                "area": None,
                "office": None,
                "fundingStatus": None,
                "cprPriority": None,
                "cprScore": None,
                "abstract": "",
                "pubDate": hr.get("pubDate"),
                "frCitation": None,
                "complianceDate": None,
                "eo14300": True,
                "nrcContact": None,
                "nrcUrl": hr.get("nrcUrl"),
                "frUrl": hr.get("frUrl"),
                "docketUrl": hr.get("docketUrl") or f"https://www.regulations.gov/docket/{dk}",
                "meetingDate": hr.get("meetingDate"),
                "meetingUrl": hr.get("meetingUrl"),
                "deadline": hr.get("deadline"),
            })
            appended += 1

    print(f"[scrape] Overlaid {overlaid}; appended {appended} HTML-only rules",
          flush=True)
    return csv_rules


# ---------------------------------------------------------------------------
# Practice notes overlay
# ---------------------------------------------------------------------------


def load_practice_notes() -> dict:
    if not PRACTICE_NOTES.exists():
        print(f"[scrape] WARNING: {PRACTICE_NOTES} not found", file=sys.stderr)
        return {}
    with PRACTICE_NOTES.open() as f:
        notes = json.load(f)
    return {k: v for k, v in notes.items() if not k.startswith("_")}


def default_practice(area: str | None) -> str:
    return AREA_TO_PRACTICE.get(area or "", area or "Uncategorized")


def default_summary(rule: dict) -> str:
    abstract = rule.get("abstract") or ""
    if abstract:
        return truncate(abstract, 320)
    cfr = ", ".join(f"10 CFR {p}" for p in rule.get("cfr", [])[:4])
    if len(rule.get("cfr", [])) > 4:
        cfr += f" (+{len(rule['cfr']) - 4} more)"
    return f"Affects {cfr}." if cfr else "See NRC docket for full description."


def overlay_practice_notes(rules: list[dict], notes: dict) -> list[dict]:
    by_docket = {r["docket"]: r for r in rules if r.get("docket")}

    for rule in rules:
        key = rule.get("docket") or rule["id"]
        note = notes.get(key, {})
        rule["practice"] = note.get("practice") or default_practice(rule.get("area"))
        rule["summary"] = note.get("summary") or default_summary(rule)
        rule["implication"] = note.get("implication", "")
        rule["curated"] = bool(note.get("implication"))

    appended = 0
    for key, note in notes.items():
        if not note.get("manual") or key in by_docket:
            continue
        rules.append({
            "id": slugify(key),
            "title": note.get("title", key),
            "docket": key,
            "rin": note.get("rin"),
            "cfr": note.get("cfr", []),
            "prm": note.get("prm"),
            "phase": note.get("phase"),
            "ruleType": note.get("ruleType"),
            "area": note.get("area"),
            "office": note.get("office"),
            "fundingStatus": note.get("fundingStatus"),
            "cprPriority": note.get("cprPriority"),
            "cprScore": note.get("cprScore"),
            "abstract": note.get("abstract", ""),
            "pubDate": note.get("pubDate"),
            "frCitation": note.get("frCitation"),
            "complianceDate": note.get("complianceDate"),
            "eo14300": note.get("eo14300", False),
            "nrcContact": note.get("nrcContact"),
            "nrcUrl": note.get("nrcUrl"),
            "frUrl": note.get("frUrl"),
            "docketUrl": note.get("docketUrl") or f"https://www.regulations.gov/docket/{key}",
            "meetingDate": note.get("meetingDate"),
            "meetingUrl": note.get("meetingUrl"),
            "deadline": note.get("deadline"),
            "practice": note.get("practice", "Uncategorized"),
            "summary": note.get("summary", ""),
            "implication": note.get("implication", ""),
            "curated": bool(note.get("implication")),
        })
        appended += 1

    if appended:
        print(f"[scrape] Appended {appended} manual entries from practice_notes.json",
              flush=True)

    uncurated = sum(1 for r in rules if not r.get("curated"))
    print(f"[scrape] {uncurated} rule(s) lack a curated KH practice implication",
          flush=True)
    return rules


# ---------------------------------------------------------------------------
# Sort and write
# ---------------------------------------------------------------------------


PHASE_SORT_ORDER = {
    "Proposed": 0,
    "Final": 1,
    "Direct Final": 2,
    "Pre-Rule": 3,
    "Complete": 4,
}


def sort_rules(rules: list[dict]) -> list[dict]:
    def key(r):
        return (
            PHASE_SORT_ORDER.get(r.get("phase", ""), 9),
            -(int(r["pubDate"].replace("-", "")) if r.get("pubDate") else 0),
            r.get("title", ""),
        )
    return sorted(rules, key=key)


def write_output(rules: list[dict]) -> None:
    RULES_OUT.parent.mkdir(parents=True, exist_ok=True)
    eo_count = sum(1 for r in rules if r.get("eo14300"))
    payload = {
        "_meta": {
            "sources": {"csv": CSV_URL, "html": EO14300_URL},
            "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "count": len(rules),
            "eo14300_count": eo_count,
            "scraper": "scripts/scrape.py",
            "notice": (
                "Generated file. Edit data/practice_notes.json instead — "
                "this file is rewritten on every scraper run."
            ),
        },
        "rules": rules,
    }
    RULES_OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"[scrape] Wrote {len(rules)} rules ({eo_count} EO 14300) -> {RULES_OUT}",
          flush=True)


def show_diff(new_rules: list[dict]) -> None:
    if not RULES_OUT.exists():
        print("[diff] No existing rules.json to compare against.")
        return
    try:
        with RULES_OUT.open() as f:
            current = json.load(f).get("rules", [])
    except Exception as exc:
        print(f"[diff] Could not read existing rules.json: {exc}")
        return

    cur_by = {r.get("docket") or r.get("id"): r for r in current}
    new_by = {r.get("docket") or r.get("id"): r for r in new_rules}
    added = sorted(set(new_by) - set(cur_by))
    removed = sorted(set(cur_by) - set(new_by))
    changed = []
    for k in sorted(set(cur_by) & set(new_by)):
        diffs = []
        for f in ("title", "phase", "pubDate", "deadline", "cfr", "cprPriority"):
            if cur_by[k].get(f) != new_by[k].get(f):
                diffs.append(f"{f}: {cur_by[k].get(f)!r} -> {new_by[k].get(f)!r}")
        if diffs:
            changed.append((k, diffs))
    print(f"[diff] {len(added)} added, {len(removed)} removed, {len(changed)} changed")
    for k in added:
        print(f"  + {k}: {new_by[k].get('title', '')[:80]}")
    for k in removed:
        print(f"  - {k}: {cur_by[k].get('title', '')[:80]}")
    for k, diffs in changed:
        print(f"  ~ {k}")
        for d in diffs:
            print(f"      {d}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="NRC Rulemaking Tracker scraper")
    parser.add_argument("--dry-run", action="store_true", help="Don't write file")
    parser.add_argument("--diff", action="store_true",
                        help="Show diff vs existing rules.json")
    args = parser.parse_args(argv)

    try:
        csv_rules = scrape_csv()
    except Exception as exc:
        print(f"[scrape] ERROR fetching CSV: {exc}", file=sys.stderr)
        return 1

    if not csv_rules:
        print("[scrape] ERROR: parsed 0 rules from CSV. URL may have changed.",
              file=sys.stderr)
        return 2

    try:
        html_rows = scrape_eo14300_html()
    except Exception as exc:
        print(f"[scrape] WARN: HTML overlay failed ({exc}); continuing.",
              file=sys.stderr)
        html_rows = []

    merged = merge_sources(csv_rules, html_rows)
    notes = load_practice_notes()
    rules = overlay_practice_notes(merged, notes)
    rules = sort_rules(rules)

    if args.diff:
        show_diff(rules)

    if args.dry_run:
        print("[scrape] --dry-run: not writing output")
        return 0

    write_output(rules)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
