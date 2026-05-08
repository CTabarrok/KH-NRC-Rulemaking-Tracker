# NRC Rulemaking Tracker — KH Nuclear Task Force

A practice-side companion to the official NRC dockets and the Breakthrough
Institute's public tracker. Tracks all ~75 active NRC rulemakings — EO 14300
wholesale-revision rules and the broader active rulemaking pipeline. Each rule
is paired with a plain-language summary; the rules closest to KH's nuclear
advisory, SMR siting, data center power, and operating-fleet work additionally
carry a Task Force practice implication.

**Live:** `https://<your-gh-username>.github.io/<repo-name>/`

Data refreshes automatically every 6 hours from NRC.gov via a GitHub Actions
workflow.

---

## Repository structure

```
.
├── index.html                     # Dashboard (single file, fetches rules.json)
├── data/
│   ├── rules.json                 # Auto-generated; do not hand-edit
│   └── practice_notes.json        # Manually curated KH commentary  ← edit this
├── scripts/
│   ├── scrape.py                  # Dual-source scraper (CSV + HTML)
│   └── requirements.txt
├── .github/workflows/
│   └── update-tracker.yml         # GHA: runs scrape.py every 6 hours
├── .nojekyll                      # Prevents GH Pages from filtering paths
└── README.md
```

## How it works

The scraper pulls from **two NRC sources** and merges them with KH-authored
practice notes:

1. **`rules-export.csv`** ([NRC.gov][csv]) — primary, canonical list of ~68
   active rulemakings with rich metadata: title, docket, RIN, CFR parts,
   abstract, phase, NRC area of responsibility, CPR priority score, NRC
   contact, and all relevant publication dates. The CSV doesn't include
   comment deadlines or rules that have moved to "Complete."
2. **EO 14300 wholesale-revisions HTML page** ([NRC.gov][eo]) — overlay that
   provides comment deadlines, public meeting dates, NRC ruledetails URLs,
   Federal Register links, and rules in "Complete" phase that have rolled off
   the active CSV.
3. **`data/practice_notes.json`** — KH-authored summary and practice
   implication, keyed by NRC docket ID. The scraper merges these onto the
   matching rules on every run, so curated commentary survives across scrapes.
   Entries flagged with `"manual": true` get appended even if not in either
   NRC source (used today for Part 53).

[csv]: https://www.nrc.gov/sites/default/files/doc_library/cdn/data/rules/rules-export.csv
[eo]: https://www.nrc.gov/about-nrc/governing-laws/advance-act/wholesale-revision-regs

Rules that have a curated KH implication get a gold-accented "KH Practice
Implication" block on the dashboard and show as **Curated** in the stats.
Rules without curated commentary fall back to the NRC Area-of-Responsibility
as a default practice tag and the truncated NRC abstract as the summary,
marked with a small "NRC abstract" indicator.

## One-time setup

### 1. Create the repo and push the contents

```bash
gh repo create Nuclear-NRC-Tracker --public --source=. --remote=origin
git add . && git commit -m "Initial commit"
git push -u origin main
```

### 2. Enable GitHub Pages

- **Repo → Settings → Pages**
- **Source:** Deploy from a branch
- **Branch:** `main` / `/ (root)`
- Save. Site is live at `https://<username>.github.io/<repo-name>/` after a minute.

### 3. Enable the scraper workflow

- **Repo → Settings → Actions → General**
- **Workflow permissions:** Read and write permissions ✓
  (so the workflow can commit `data/rules.json` back to `main`)
- **Actions → Update NRC Rulemaking Data → Run workflow** to test it now.

After this, the scraper runs every 6 hours automatically and pushes any
changes.

## Editing practice notes

Open `data/practice_notes.json`. Keys are NRC docket IDs (e.g.
`NRC-2025-0975`). For most rules, only three fields matter:

| Field         | Description                                            |
|---------------|--------------------------------------------------------|
| `practice`    | Short tag for filtering (e.g. "SMR & Advanced Reactor Siting"). Overrides the auto-default derived from NRC's Area of Responsibility. |
| `summary`     | Plain-language description, 1–2 sentences. Overrides the auto-default truncated NRC abstract. |
| `implication` | KH practice-side read, 1–3 sentences. The presence of this field is what marks a rule as **Curated**. |

For manual entries (rules that aren't in either NRC source but you still want
to track):

| Field      | Description                                            |
|------------|--------------------------------------------------------|
| `manual`   | `true` to include this rule even when not on NRC.      |
| `title`, `cfr`, `phase`, `pubDate`, `deadline`, `nrcUrl`, `frUrl`, `docketUrl`, `eo14300`, `cprPriority` | All other metadata. |

A push to `main` that touches `practice_notes.json` triggers the workflow
immediately, so your changes show up within a couple minutes.

### Adding a new practice tag

The dashboard's "Practice" filter is built from whatever values appear in the
data — just use a new string in `practice_notes.json` and it will appear as
an option on next scrape.

## Running the scraper locally

```bash
pip install -r scripts/requirements.txt
python scripts/scrape.py                 # write data/rules.json
python scripts/scrape.py --dry-run       # don't write
python scripts/scrape.py --diff          # show what would change vs current
```

Then open `index.html` in a browser. Note that `fetch('./data/rules.json')`
needs an HTTP server, not `file://`. Quickest:

```bash
python -m http.server 8080
# open http://localhost:8080/
```

## Field reference (data/rules.json)

Each rule in `data/rules.json` has these fields:

| Field             | Source       | Notes |
|-------------------|--------------|-------|
| `id`              | derived      | Slugified docket ID                        |
| `title`           | CSV / HTML   | `[14300]` prefix stripped                  |
| `docket`          | CSV / HTML   | NRC-YYYY-NNNN                              |
| `rin`             | CSV          | Regulatory Identification Number           |
| `cfr`             | CSV          | List of affected 10 CFR Parts              |
| `prm`             | CSV          | Associated petition for rulemaking number  |
| `phase`           | CSV / HTML   | Proposed / Final / Direct Final / Pre-Rule / Complete |
| `ruleType`        | CSV          | Non-discretionary / Industry Requested / etc. |
| `area`            | CSV          | NRC Area of Regulatory Responsibility      |
| `office`          | CSV          | NRC Office that owns the rule              |
| `fundingStatus`   | CSV          | Funded / Unfunded                          |
| `cprPriority`     | CSV          | High / Medium / Low                        |
| `cprScore`        | CSV          | Numeric CPR score                          |
| `abstract`        | CSV          | Full NRC abstract                          |
| `pubDate`         | CSV / HTML   | Publication date for the rule's current phase |
| `frCitation`      | CSV          | Federal Register citation text             |
| `complianceDate`  | CSV          | When licensees must comply (final rules)   |
| `eo14300`         | derived      | True if `[14300]` prefix or on EO page     |
| `nrcContact`      | CSV          | NRC staff contact name and email           |
| `nrcUrl`          | HTML         | NRC ruledetails URL (when available)       |
| `frUrl`           | HTML         | Federal Register notice URL                |
| `docketUrl`       | derived      | regulations.gov docket URL                 |
| `meetingDate`     | HTML         | Public meeting date (when scheduled)       |
| `meetingUrl`      | HTML         | Public meeting details URL                 |
| `deadline`        | HTML         | Comment period close date                  |
| `practice`        | notes / area | KH practice tag (override or area-default) |
| `summary`         | notes / abstract | Plain-language description (override or abstract excerpt) |
| `implication`     | notes        | KH practice-side read; empty if uncurated  |
| `curated`         | derived      | True iff `implication` is non-empty        |

## Troubleshooting

**The CSV scrape returned 0 rules.** NRC moved or restructured the export.
Check `CSV_URL` in `scripts/scrape.py` against the "Download Rule Data"
link at the bottom of [the active rulemakings page][active].

[active]: https://www.nrc.gov/reading-rm/doc-collections/rulemaking-ruleforum/active/ruleindex

**The HTML overlay scrape failed.** Less critical — the scraper logs a
warning and continues with CSV-only data. Check whether NRC restructured
the EO 14300 wholesale-revisions page table.

**My practice note isn't showing up.** Verify the docket ID in
`practice_notes.json` exactly matches one in `data/rules.json`. The
scraper logs how many rules lack curated implications on each run.

**The page shows "Could not load rulemaking data."** You're opening
`index.html` via `file://`. Use `python -m http.server` or push to GitHub
Pages.

**The Actions workflow can't push.** Settings → Actions → General →
Workflow permissions → Read and write permissions.

## Acknowledgement

Inspired by [Adam Stein's NRC Rulemaking Tracker](https://thebreakthrough.org/issues/nuclear-energy-innovation/nrc-rulemaking-tracker)
at the Breakthrough Institute, with practice-side framing added for
engineering and consulting use.

---

Maintained by the Kimley-Horn Nuclear Task Force. Not legal advice. Always
consult primary sources at NRC.gov and Regulations.gov before relying on
this information for licensing or compliance decisions.
