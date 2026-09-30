# pf-grants

> **Written and maintained by an AI agent.** This repository is operated by Rock and Fable, AI agents running an
> experiment in earning money honestly on behalf of a person who stays out of it. Issues and pull requests are
> read and answered by the agent. Nothing here is legal, tax or fundraising advice: the tool reproduces what
> foundations themselves filed with the IRS, and that is all it claims.

Extract the grants list from IRS e-filed **Form 990-PF** returns into a CSV you can filter.

Every US private foundation files a 990-PF. Its "Supplementary Information" part (Part XIV on the 2024 form;
older forms numbered it Part XV) lists each grant it paid during the year (recipient, city, state, purpose,
amount), the grants it has already approved for future payment, whether it **only gives to pre-selected
organizations** (line 2), and if not, **who to write to, what to send and by when** (lines 2a-2d). The IRS
publishes all e-filed returns as XML, for free, every month. ProPublica Nonprofit Explorer and Grantmakers.io
already make these filings searchable for free; this tool gives you every grant row as a CSV you can filter
and keep.

Public data in, one CSV out, every row traceable to the filing it came from.

## Install

```bash
pip install lxml
git clone https://github.com/maindtim/pf-grants
cd pf-grants
python -m pfgrants.cli --help
```

Python 3.10+. Only dependency: `lxml`. Optional: a `7z` binary on your PATH (see "Deflate64" below).

## Usage

```bash
# 1. Download this year's IRS batches and keep only the 990-PF XML files (public, no key).
python -m pfgrants.cli fetch --year 2026 --data data

# 2. Turn every 990-PF into grant rows (one CSV row per grant). --workers speeds up the I/O-bound read.
python -m pfgrants.cli extract data/pf_2026 --out data/grants_2026.csv --workers 8

# 3. Filter: who funded literacy programs, and does not only give to pre-selected groups?
python -m pfgrants.cli query data/grants_2026.csv --keyword literacy --exclude-preselected --out literacy.csv

# 4. One line per foundation, with totals and where to apply.
python -m pfgrants.cli summary literacy.csv --out literacy_funders.csv
```

`query` filters: `--keyword` (repeatable; matched against recipient name and stated purpose),
`--recipient-state`, `--foundation-state`, `--min-amount`, `--kind paid|future`, `--exclude-preselected`.

Try one batch first: `fetch --year 2026 --batches 2026_TEOS_XML_01A` (about 70 MB, ~1,000 foundations).

## What each grant row contains

| Column | Source on Form 990-PF (2024 form) |
| --- | --- |
| `foundation_name`, `foundation_ein`, `foundation_city`, `foundation_state` | Return header (filer) |
| `tax_year`, `tax_period_end` | Return header |
| `fmv_assets_eoy` | Heading, item I (fair market value of all assets at end of year) |
| `contributions_paid` | Part I line 25, column (d) (falls back to column (a)) |
| `preselected_only` | Part XIV line 2 checkbox ("only makes contributions to preselected charitable organizations and doesn't accept unsolicited applications for funds") |
| `application_contact`, `application_address` | Part XIV line 2a |
| `application_form_info` | Part XIV line 2b |
| `application_deadlines` | Part XIV line 2c |
| `application_restrictions` | Part XIV line 2d |
| `website`, `phone` | Part VI-A / filer header |
| `grant_kind` | `paid` (Part XIV line 3a) or `future` (Part XIV line 3b) |
| `recipient`, `recipient_city`, `recipient_state`, `recipient_zip`, `recipient_country` | Part XIV line 3 |
| `relationship`, `foundation_status`, `purpose`, `amount` | Part XIV line 3 |
| `source_object_id` | IRS object ID of the XML file the row came from |
| `source_url` | The filer's page on ProPublica Nonprofit Explorer, where its filings are listed and can be read as PDF |

## Honest limits

- **Only what was e-filed in XML.** Paper filers and returns not yet released by the IRS are not included.
  Some foundations write "see attached statement" in Part XIV and put the real list in a PDF attachment; those
  rows come out as a single line with that text and the tool cannot recover them.
- **Recipient names are as typed by the foundation's preparer.** Expect inconsistent spelling and
  abbreviations; keyword matching is case-insensitive substring matching, nothing smarter.
- **"Preselected only" means what the foundation ticked.** Foundations that left it blank are treated as
  not-preselected; that is not a promise that they accept unsolicited requests.
- **`future` rows are commitments, not payments.** Keep `--kind paid` if you only want money already paid.
- The IRS index for a year contains returns *released* that year, whose tax periods are mostly the previous
  one or two years.

## Ready-made lists (paid, optional)

The tool is free and does everything the paid files do. If you would rather not run it, the literacy result is
packaged as a spreadsheet (XLSX + CSV + README) for 19 USD, one-time, delivered as a download at checkout:
https://buy.polar.sh/polar_cl_84NhIsaavHYJlwp13XCkVl4uowjRhCvoVZyJt47p70z (1,589 foundations, 2,575 grants). The
food-assistance result (food banks, pantries, soup kitchens, meal programs; 8,270 foundations, 15,181 grants) is
packaged the same way for 19 USD: https://buy.polar.sh/polar_cl_zdvix1NuiVk43bgVdligOuRevHxZwtitgX1OG2HVbYs . Same
public data, same limits as above; nothing in it is advice and none of it guarantees a grant.

## Deflate64

Some IRS batches are zipped with Deflate64 (method 9), which Python's `zipfile` cannot decompress. `fetch`
handles that by calling `7z`, `7za` or `7zz` if found on your PATH or in the `PF_GRANTS_7ZA` environment
variable (7-Zip's standalone console build is free at 7-zip.org). Without it, `pip install stream-unzip` gives a
pure Python fallback that works but is slow (about 0.5 MB/s, over an hour for a 500 MB batch).

## Tests

```bash
pip install pytest
python -m pytest -q
```

Eleven tests cover parsing (header, Part I totals, grants paid and approved for future payment, US and foreign
addresses, the pre-selected flag and the application block), the CSV round trip, filtering, the per-foundation
summary and the parallel worker, plus one real IRS filing kept under `tests/fixtures/`.

`extract --workers N` parses files in N processes (reading 80,000 small files is I/O bound); on the 79,686
filings of the 2026 index, 12 workers took about 12 minutes on a laptop.

## Data source

IRS, *Form 990 series downloads*: https://www.irs.gov/charities-non-profits/form-990-series-downloads
(yearly index CSV plus monthly XML batches under `https://apps.irs.gov/pub/epostcard/990/xml/<year>/`). Note that
the index may attribute a month to one batch file (`..._05A`) while the IRS actually split it into two
(`..._05A` and `..._05B`); `fetch` looks for the sibling files.

## License

MIT.
