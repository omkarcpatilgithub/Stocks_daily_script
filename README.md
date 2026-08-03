# Stocks Daily Script

Python tooling to pull BSE “Award of Order” announcements, normalize company names, and score order quality against investment thresholds.

## Pipeline overview

```text
BSE Announcements (Selenium)
        │
        ▼
download_annoucement_pdf.py  →  bse_award_orders/YYYY-MM-DD/*.pdf
        │
        ▼
extract_text_to_company.py   →  filename like 500183_HFCL Ltd.pdf
        │
        ▼
tp.ipynb / company_data_gather.py (WIP)  →  Screener.in fundamentals
        │
        ▼
main.py + sample.json         →  PASS/FAIL score + investment verdict
```

| Script | Role | Status |
|--------|------|--------|
| `download_annoucement_pdf.py` | Scrapes BSE corporate announcements and downloads unique Award-of-Order PDFs | Working (browser automation) |
| `extract_text_to_company.py` | Parses company name + 6-digit BSE code from announcement text | Working |
| `main.py` | Scores an order JSON against fixed investment thresholds | Working (demo via `sample.json`) |
| `tp.ipynb` | Prototype: list PDFs + scrape Screener.in (Market Cap, ROCE, ROE) | Experimental |
| `company_data_gather.py` | Intended Screener/data gather module | Empty stub |
| `redmine_sso_login.py` | Redmine SSO helper (not part of the stocks pipeline) | Separate utility |

## Prerequisites

- **Python 3.11+** (project venv uses 3.11.9)
- **Google Chrome** (required for Selenium scripts)
- Internet access for BSE / Screener / Redmine

## Setup

```powershell
cd "c:\Users\omkar.patil\Desktop\Trantor Project\Stocks_daily_script"

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Dependencies (`requirements.txt`):

- `requests`
- `beautifulsoup4`
- `pandas`
- `selenium>=4.17.0`
- `webdriver-manager`

Selenium 4.x can auto-manage ChromeDriver; `webdriver-manager` is listed for compatibility.

## How to run

### 1. Order scoring demo (no browser)

Uses `sample.json` and prints PASS/FAIL criteria plus a verdict:

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

Example output:

```text
Evaluation Results:
- Proportion to Revenue: PASS
...
Score: 10/10
Investment Verdict: STRONG POSITIVE
```

Verdicts:

| Score | Verdict |
|-------|---------|
| ≥ 8 / 10 | STRONG POSITIVE |
| ≥ 6 / 10 | MODERATE POSITIVE |
| &lt; 6 / 10 | WEAK / AVOID |

To score your own order, replace or edit `sample.json` to match this shape:

```json
{
  "company": {
    "name": "ABC Infra Ltd",
    "annual_revenue_cr": 1200,
    "industry": "Infrastructure",
    "market_share_percent": 8.0
  },
  "order": {
    "order_value_cr": 300,
    "profit_margin_percent": 18,
    "contract_duration_years": 3,
    "execution_risk_score": 2,
    "customer_credit_rating": "A",
    "upfront_payment_percent": 45,
    "strategic_fit": true,
    "expected_market_share_increase_percent": 6,
    "expected_cashflow_improvement_percent": 12,
    "competitor_average_order_value_cr": 260
  }
}
```

### 2. Download today’s BSE Award-of-Order PDFs

Opens Chrome, filters BSE announcements to **Company Update → Award of Order / Receipt of Order**, and downloads one PDF per company into `bse_award_orders/YYYY-MM-DD/`.

```powershell
.\.venv\Scripts\Activate.ps1
python download_annoucement_pdf.py
```

Notes:

- Runs at import/script level (not wrapped in `if __name__ == "__main__"`).
- From/To date fields are currently commented out; the site’s default date range is used unless you uncomment `type_date_by_label(...)` in the script.
- On failure, a screenshot is saved under `Failure/failure_YYYYMMDD_HHMMSS.png`.

### 3. Company-name helper (library)

Used by the downloader; you can also call it directly:

```powershell
python -c "import extract_text_to_company as etc; print(etc.extract_company_name_from_bse_text('HFCL Ltd - 500183 - Award'))"
```

### 4. Screener data prototype (notebook)

```powershell
.\.venv\Scripts\Activate.ps1
pip install jupyter
jupyter notebook tp.ipynb
```

Cells cover:

1. Company-name extraction tests  
2. Listing PDFs from `bse_award_orders/<date>/`  
3. Draft `ScreenerAutomation` class against [screener.in](https://www.screener.in/)

`company_data_gather.py` is reserved for moving that logic out of the notebook (currently empty).

### 5. Redmine SSO (optional / unrelated)

```powershell
$env:REDMINE_SSO_USERNAME = "your.user@company.com"
$env:REDMINE_SSO_PASSWORD = "your-password"
$env:HEADLESS = "false"
$env:POST_LOGIN_WAIT = "10"
python redmine_sso_login.py
```

## Project layout

```text
Stocks_daily_script/
├── main.py                      # Order scoring engine
├── sample.json                  # Demo input for main.py
├── download_annoucement_pdf.py  # BSE PDF downloader
├── extract_text_to_company.py   # BSE text → company_code_name
├── company_data_gather.py       # Stub (empty)
├── redmine_sso_login.py         # Redmine SSO helper
├── tp.ipynb                     # Screener / PDF exploration notebook
├── requirements.txt
├── bse_award_orders/            # Downloaded PDFs by date
├── Failure/                     # Selenium failure screenshots
├── BSE_data/                    # Reserved (currently empty)
└── .venv/
```

## Diagnosis (as of 2026-07-31)

| Check | Result |
|-------|--------|
| Python / venv | OK — 3.11.9 in `.venv` |
| `pip install -r requirements.txt` | OK — packages import; `pip check` clean |
| Google Chrome | OK — installed |
| `python main.py` | OK — scores `sample.json` (10/10 STRONG POSITIVE) |
| `extract_text_to_company` | OK — e.g. `500183_HFCL Ltd` |
| End-to-end pipeline | Incomplete — download → score not wired; company data gather not implemented |
| `company_data_gather.py` | Empty file (0 bytes) |
| `BSE_data/` | Empty |
| Unified CLI / scheduler | Missing — each script is run manually |
| Date filter on BSE downloader | Disabled (commented out) |
| Filename typo | `download_annoucement_pdf.py` (“annoucement”) |
| `redmine_sso_login.py` | Unrelated to stocks workflow |

### Suggested next steps

1. Move Screener scraping from `tp.ipynb` into `company_data_gather.py`.
2. Wire PDF/order fields into a JSON that `main.py` can score (today `main.py` only reads `sample.json`).
3. Wrap `download_annoucement_pdf.py` in `if __name__ == "__main__"` and restore From/To date selection for true “daily” runs.
4. Add a single entry script or Task Scheduler job that runs download → enrich → score.
5. Optionally add `.gitignore` for `.venv/`, `__pycache__/`, `Failure/`, and large PDF trees.

## License / notes

Internal Trantor project scripts. Respect BSE and Screener terms of use when automating browsers; prefer reasonable delays and avoid heavy scraping.
