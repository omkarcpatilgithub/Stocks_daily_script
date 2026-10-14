"""
Stock recommender flow:

1. For each of the last N days (--days):
   - If bse_award_orders/<YYYY-MM-DD>/ already has PDFs, reuse it
   - Else download that single day via download_annoucement_pdf.py --date ...
2. For every PDF across those days:
   - Agent A -> company annual revenue + sales (web search) and sales/revenue ratio
   - Agent B -> order details from the PDF
   - Agent C -> analysis + verdict from A and B (order vs sales, sales/revenue)

Returns one record per company so later steps can score them.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from pypdf import PdfReader

from langchain_core.tools import tool
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_openai import ChatOpenAI

try:
    from langchain.agents import create_agent

    _PROMPT_KWARG = "system_prompt"
except ImportError:  # older langchain/langgraph
    from langgraph.prebuilt import create_react_agent as create_agent

    _PROMPT_KWARG = "prompt"


load_dotenv()

PROJECT_DIR = Path(__file__).resolve().parent
AWARD_ORDERS_DIR = PROJECT_DIR / "bse_award_orders"
ANALYSIS_OUTPUT_DIR = PROJECT_DIR / "analysis_results"
DOWNLOAD_SCRIPT = PROJECT_DIR / "download_annoucement_pdf.py"

POSITIVE_VERDICTS = frozenset({"Excellent Deal", "Good Deal"})
AGENT_C_VERDICTS = (
    "Excellent Deal",
    "Good Deal",
    "Average Deal",
    "Not Material",
)

LLM_MODEL = "openrouter/free"  # "openai/gpt-4o-mini"
# OpenRouter retired the ":free" variant of this slug and returns 404 for it
ANALYSIS_LLM_MODEL = "openrouter/free"  # "openai/gpt-oss-120b"
LLM_BASE_URL = "https://openrouter.ai/api/v1"

# Printed by download_annoucement_pdf.py on success
DOWNLOAD_DIR_RE = re.compile(r"Downloaded\s+\d+\s+unique company PDFs to:\s*(.+)")


# ---------------------------------------------------------------- step 1 & 2

def get_download_folder(before_days: int = 0) -> Path:
    """Folder the downloader uses: bse_award_orders/<YYYY-MM-DD>/."""
    day = datetime.now() - timedelta(days=before_days)
    return AWARD_ORDERS_DIR / day.strftime("%Y-%m-%d")


def get_day_folder(day: datetime) -> Path:
    return AWARD_ORDERS_DIR / day.strftime("%Y-%m-%d")


def iter_recent_days(days: int) -> list[datetime]:
    """Today first, then yesterday, ... up to days-1 ago."""
    if days < 1:
        raise ValueError("--days must be >= 1")
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return [now - timedelta(days=offset) for offset in range(days)]


def run_download_script_for_date(day: datetime) -> Path | None:
    """Run the BSE downloader for one calendar day and return that day's folder."""
    date_str = day.strftime("%Y-%m-%d")
    folder = get_day_folder(day)

    if get_pdf_files(folder):
        print(f"Already downloaded, using {folder}")
        return folder

    print(f"Running {DOWNLOAD_SCRIPT.name} --date {date_str} ...")

    result = subprocess.run(
        [sys.executable, str(DOWNLOAD_SCRIPT), "--date", date_str],
        cwd=str(PROJECT_DIR),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    if result.stdout:
        print(result.stdout.strip())
    if result.returncode != 0:
        print(f"Downloader exited with code {result.returncode}")
        if result.stderr:
            print(result.stderr.strip())
        print(
            f"Download failed for {date_str}. Check your internet connection and retry, "
            "or run with --skip-download if PDFs already exist."
        )
        return None

    match = DOWNLOAD_DIR_RE.search(result.stdout or "")
    if match:
        return Path(match.group(1).strip())

    return folder


def ensure_day_folders(
    days: int,
    skip_download: bool = False,
) -> list[Path]:
    """
    For each of the last `days` calendar days, reuse a non-empty PDF folder
    or download that day (unless skip_download).
    """
    folders: list[Path] = []
    for day in iter_recent_days(days):
        folder = get_day_folder(day)
        pdfs = get_pdf_files(folder)

        if pdfs:
            print(f"[{day.strftime('%Y-%m-%d')}] folder non-empty ({len(pdfs)} PDF(s)), skipping download")
            folders.append(folder)
            continue

        if skip_download:
            print(f"[{day.strftime('%Y-%m-%d')}] empty/missing and --skip-download set, skipping")
            continue

        downloaded = run_download_script_for_date(day)
        if downloaded is None:
            continue
        if get_pdf_files(downloaded):
            folders.append(downloaded)
        else:
            print(f"[{day.strftime('%Y-%m-%d')}] no PDFs after download, skipping analysis for this day")

    return folders


def run_download_script() -> Path | None:
    """Backward-compatible helper: ensure today's folder only."""
    folders = ensure_day_folders(days=1, skip_download=False)
    return folders[0] if folders else get_download_folder(before_days=0)


# -------------------------------------------------------------------- step 3

def get_pdf_files(folder: Path) -> list[Path]:
    """All PDFs inside a download folder."""
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")


# -------------------------------------------------------------------- step 4

def get_company_from_pdf(pdf_file: Path | str) -> tuple[str, str]:
    """
    Split `{code}_{Company Name}.pdf` into (code, name).

    e.g. `500294_NCC Ltd.pdf` -> ("500294", "NCC Ltd")
    """
    stem = Path(pdf_file).stem
    if "_" in stem:
        company_code, company_name = stem.split("_", 1)
        return company_code.strip(), company_name.strip()
    return "", stem.strip()


# ------------------------------------------------------------------- agents

def _build_llm(model: str = LLM_MODEL) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=LLM_BASE_URL,
        api_key=os.environ["OPENROUTER_API_KEY"],
        temperature=0,
        max_tokens=4096,
    )


_ddg = DuckDuckGoSearchRun()


@tool
def company_revenue(company_name: str) -> str:
    """Search the internet for a company's latest annual revenue and annual sales/net sales."""
    query = (
        f"{company_name} latest annual revenue net sales operating revenue "
        f"total income FY consolidated"
    )
    print("  Searching:", query)
    return _ddg.invoke(query)


@tool
def read_award_pdf(pdf_path: str) -> str:
    """
    Read text from a BSE Award of Order PDF given its full file path.
    Use this before extracting order value or payment details.
    """
    path = Path(pdf_path)
    if not path.exists():
        return f"ERROR: File not found: {pdf_path}"
    if path.suffix.lower() != ".pdf":
        return f"ERROR: Not a PDF file: {pdf_path}"

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        pages.append(f"--- Page {i} ---\n{text.strip()}")

    full_text = "\n\n".join(pages).strip()
    if not full_text:
        return "ERROR: No extractable text found. PDF may be scanned/image-only."

    # Keep tool output bounded for the LLM
    return full_text[:15000]


REVENUE_AGENT_PROMPT = (
    "You are a helpful research assistant. "
    "Always use the company_revenue tool before answering. "
    "Fetch the company's latest annual revenue and annual sales (net sales / "
    "operating revenue / revenue from operations). Prefer consolidated figures "
    "for the same fiscal year.\n\n"
    "Treat annual_sales as operating turnover (net sales / revenue from operations). "
    "Treat annual_revenue as the broader top-line if available (e.g. total income / "
    "total revenue); if only one operating figure is found, use it for annual_sales "
    "and set annual_revenue to the same value with a note.\n\n"
    "Compute sales_to_revenue_ratio = annual_sales / annual_revenue, expressed as a "
    "percentage with arithmetic shown. If either figure is missing, use "
    "'Not available' for the ratio.\n\n"
    "Return ONLY valid JSON (no markdown, no extra text) with exactly these keys:\n"
    "{\n"
    '  "company_name": "<company queried>",\n'
    '  "annual_revenue": "<latest annual revenue figure with units>",\n'
    '  "annual_sales": "<latest annual sales / net sales / operating revenue with units>",\n'
    '  "sales_to_revenue_ratio": "<e.g. 95.2% (= sales / revenue) or Not available>",\n'
    '  "currency": "<INR | USD | other>",\n'
    '  "fiscal_year": "<FY period or Not disclosed>",\n'
    '  "source_notes": "<brief note on where the figures came from and how sales vs revenue were distinguished>"\n'
    "}"
)

ORDER_AGENT_PROMPT = (
    "You are an analyst for BSE 'Award of Order / Receipt of Order' announcements.\n"
    "Always call read_award_pdf with the exact PDF path from the user before answering.\n"
    "Extract ONLY from the PDF text. If something is not mentioned, use 'Not disclosed'.\n\n"
    "Return ONLY valid JSON (no markdown, no extra text) with exactly these keys:\n"
    "{\n"
    '  "company": "<company name from PDF>",\n'
    '  "order_value": "<order value with units>",\n'
    '  "currency_gst_note": "<currency and GST treatment>",\n'
    '  "order_breakup": "<breakup if any, else Not disclosed>",\n'
    '  "client_awarding_authority": "<client / awarding authority>",\n'
    '  "project_scope_summary": "<project / scope summary>",\n'
    '  "payment_structure": {\n'
    '    "one_time_payment": "<Yes | No | Not disclosed>",\n'
    '    "upfront_advance_pct": "<value or Not disclosed>",\n'
    '    "milestone_progressive_billing": "<details or Not disclosed>",\n'
    '    "retention_bg_ld_terms": "<details or Not disclosed>",\n'
    '    "payment_cycle_credit_period": "<details or Not disclosed>"\n'
    "  },\n"
    '  "contract_duration_execution_period": "<duration or Not disclosed>",\n'
    '  "other_commercial_terms": "<other terms or Not disclosed>",\n'
    '  "notes_caveats": "<caveats or Not disclosed>"\n'
    "}"
)


ANALYSIS_AGENT_PROMPT = (
    "Act as a stock market analyst. You are given a company's latest annual revenue, "
    "annual sales, sales-to-revenue ratio, and the already-extracted details of its "
    "BSE order announcement, so do not look anything up and do not ask for the PDF — "
    "work only from the numbers provided.\n\n"
    "Primary materiality check: compare order value to annual_sales (operating "
    "turnover), not to annual_revenue. Calculate Order Value / Annual Sales (%), "
    "showing the arithmetic.\n"
    "Also consider sales_to_revenue_ratio from the inputs: a high ratio means most "
    "reported revenue is operating sales (cleaner comparison); a low ratio means "
    "non-operating income is material — treat order-vs-sales as the main signal and "
    "note that total revenue overstates the operating base.\n"
    "Use net profit, order book and market capitalisation only where those figures "
    "appear in the inputs; mark anything missing as 'Not available' instead of "
    "guessing.\n"
    "Assess whether the order is likely to materially impact future earnings and "
    "business growth. Highlight any risks (small order size, low margins, one-time "
    "nature, execution risk, undisclosed payment terms, etc.).\n\n"
    "Return ONLY valid JSON (no markdown, no extra text) with exactly these keys:\n"
    "{\n"
    '  "key_ratios": "<calculated ratios with arithmetic, one per line; must include Order Value / Annual Sales (%) and the given Sales / Revenue ratio>",\n'
    '  "materiality_assessment": "<how material this order is vs annual sales, noting sales/revenue context>",\n'
    '  "risks": "<key risks identified>",\n'
    '  "verdict": "<must be exactly one of: Excellent Deal | Good Deal | Average Deal | Not Material>",\n'
    '  "rationale": "<brief justification for the verdict>"\n'
    "}"
)


def _make_agent(tools: list, prompt: str, model: str = LLM_MODEL):
    return create_agent(_build_llm(model), tools, **{_PROMPT_KWARG: prompt})


def build_revenue_agent():
    """Agent A — company annual revenue + sales via web search."""
    return _make_agent([company_revenue], REVENUE_AGENT_PROMPT)


def build_order_agent():
    """Agent B — order details from the announcement PDF."""
    return _make_agent([read_award_pdf], ORDER_AGENT_PROMPT)


def build_analysis_agent():
    """Agent C — order vs sales + sales/revenue context; returns a verdict. No tools."""
    return _make_agent([], ANALYSIS_AGENT_PROMPT, model=ANALYSIS_LLM_MODEL)


def _ask(agent, question: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


def _parse_agent_json(content: str) -> dict:
    """Extract JSON object from agent output; fall back to raw text on failure."""
    text = (content or "").strip()
    if not text:
        return {"raw_response": "", "parse_error": "empty response"}

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
        return {"raw_response": content, "parse_error": "response was not a JSON object"}
    except json.JSONDecodeError as exc:
        return {"raw_response": content, "parse_error": str(exc)}


def _ask_json(agent, question: str) -> dict:
    return _parse_agent_json(_ask(agent, question))


def _format_agent_payload(payload: dict | str | None) -> str:
    if payload is None:
        return "Not available"
    if isinstance(payload, dict):
        return json.dumps(payload, indent=2, ensure_ascii=False)
    return str(payload)


def _canonical_verdict(verdict: str | None) -> str | None:
    """Return the verdict only if it exactly matches one of Agent C's allowed values."""
    if not verdict:
        return None
    text = str(verdict).strip()
    if text in AGENT_C_VERDICTS:
        return text
    return None


def _extract_verdict(analysis: dict | None) -> str | None:
    if not analysis or not isinstance(analysis, dict):
        return None
    return _canonical_verdict(analysis.get("verdict"))


def _is_positive_verdict(verdict: str | None) -> bool:
    return _canonical_verdict(verdict) in POSITIVE_VERDICTS


def get_analysis_run_dir(run_date: datetime | None = None) -> Path:
    """Today's run folder: analysis_results/stocks_analysed_on_DD-MM-YY/."""
    day = run_date or datetime.now()
    return ANALYSIS_OUTPUT_DIR / day.strftime("stocks_analysed_on_%d-%m-%y")


def _safe_filename_part(text: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', "", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _analysis_output_path(record: dict, output_dir: Path) -> Path:
    company_name = _safe_filename_part(record["company_name"] or "Unknown")
    day = record.get("day") or Path(record["pdf_path"]).parent.name
    verdict = _safe_filename_part(_extract_verdict(record.get("analysis")) or "No Verdict")
    filename = f"{company_name}_{verdict}_{day}.json"
    return output_dir / filename


def save_analysis_record(record: dict, output_dir: Path) -> Path | None:
    """Persist Good Deal / Excellent Deal analyses only."""
    if not record.get("analysis"):
        return None

    verdict = _extract_verdict(record.get("analysis"))
    if not _is_positive_verdict(verdict):
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = _analysis_output_path(record, output_dir)

    payload = {
        "company_code": record.get("company_code"),
        "company_name": record.get("company_name"),
        "day": record.get("day"),
        "pdf_path": record.get("pdf_path"),
        "verdict": verdict,
        "revenue": record.get("revenue"),
        "order_details": record.get("order_details"),
        "analysis": record.get("analysis"),
        "errors": record.get("errors", []),
        "saved_at": datetime.now().isoformat(timespec="seconds"),
    }

    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return output_path


# -------------------------------------------------------------------- step 5

def get_revenue(agent, company_name: str) -> dict:
    return _ask_json(
        agent,
        f"What are {company_name} latest annual revenue and annual sales "
        "(net sales / operating revenue)? Also compute sales_to_revenue_ratio.",
    )


# -------------------------------------------------------------------- step 6

def get_order_details(agent, pdf_path: Path) -> dict:
    return _ask_json(
        agent,
        "Analyze this Award of Order PDF and extract order value "
        f"and payment structure details.\nPDF path: {pdf_path}",
    )


# -------------------------------------------------------------------- step 7

def get_analysis(
    agent,
    company_name: str,
    revenue: dict | str,
    order_details: dict | str,
) -> dict:
    return _ask_json(
        agent,
        f"Company: {company_name}\n\n"
        f"[A] Latest annual revenue, annual sales, and sales/revenue ratio:\n"
        f"{_format_agent_payload(revenue)}\n\n"
        f"[B] Order announcement details:\n{_format_agent_payload(order_details)}\n\n"
        "Analyse how meaningful this order is vs annual sales (primary), "
        "consider the sales_to_revenue_ratio, and give your verdict.",
    )


# --------------------------------------------------------------------- flow

def analyze_pdfs(
    pdf_files: list[Path],
    revenue_agent=None,
    order_agent=None,
    analysis_agent=None,
    output_dir: Path | None = None,
) -> list[dict]:
    """Run agents A/B/C on each PDF and return records."""
    if not pdf_files:
        return []

    revenue_agent = revenue_agent or build_revenue_agent()
    order_agent = order_agent or build_order_agent()
    analysis_agent = analysis_agent or build_analysis_agent()
    output_dir = output_dir or get_analysis_run_dir()

    records = []
    for pdf_path in pdf_files:
        company_code, company_name = get_company_from_pdf(pdf_path)
        day_label = pdf_path.parent.name
        print(f"Processing [{day_label}] {company_name} ({company_code})")

        record = {
            "pdf_path": str(pdf_path),
            "day": day_label,
            "company_code": company_code,
            "company_name": company_name,
            "revenue": None,
            "order_details": None,
            "analysis": None,
            "errors": [],
        }

        try:
            record["revenue"] = get_revenue(revenue_agent, company_name)
        except Exception as exc:
            record["errors"].append(f"revenue: {exc}")
            print(f"  Revenue lookup failed: {exc}")

        try:
            record["order_details"] = get_order_details(order_agent, pdf_path)
        except Exception as exc:
            record["errors"].append(f"order_details: {exc}")
            print(f"  Order extraction failed: {exc}")

        if record["revenue"] or record["order_details"]:
            try:
                record["analysis"] = get_analysis(
                    analysis_agent,
                    company_name,
                    record["revenue"] or "Not available",
                    record["order_details"] or "Not available",
                )
            except Exception as exc:
                record["errors"].append(f"analysis: {exc}")
                print(f"  Analysis failed: {exc}")

        saved_path = save_analysis_record(record, output_dir)
        verdict = _extract_verdict(record.get("analysis"))
        if saved_path:
            print(f"  Saved analysis -> {saved_path}")
        elif verdict:
            print(f"  Skipped save — verdict: {verdict} (only Good Deal / Excellent Deal are kept)")
        elif record.get("analysis"):
            raw_verdict = record["analysis"].get("verdict") if isinstance(record["analysis"], dict) else None
            if raw_verdict:
                print(f"  Skipped save — unrecognized verdict: {raw_verdict!r}")

        records.append(record)

    return records


def run_flow(
    skip_download: bool = False,
    days: int = 1,
    before_days: int | None = None,
    limit: int | None = None,
) -> list[dict]:
    """
    Ensure PDFs for the last `days` calendar days, then analyse them all.

    Legacy: if `before_days` is set (and days==1 default path via CLI),
    only that single offset day is used with --skip-download semantics.
    """
    if before_days is not None:
        # Legacy single-folder mode
        folder = get_download_folder(before_days=before_days)
        if skip_download:
            print(f"Skipping download, using {folder}")
            folders = [folder] if get_pdf_files(folder) else []
        else:
            day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
                days=before_days
            )
            downloaded = run_download_script_for_date(day)
            folders = [downloaded] if downloaded and get_pdf_files(downloaded) else []
    else:
        folders = ensure_day_folders(days=days, skip_download=skip_download)

    if not folders:
        print("No PDF folders available to analyse.")
        return []

    pdf_files: list[Path] = []
    for folder in folders:
        day_pdfs = get_pdf_files(Path(folder))
        print(f"Found {len(day_pdfs)} PDF(s) in {folder}")
        pdf_files.extend(day_pdfs)

    if limit:
        pdf_files = pdf_files[:limit]

    if not pdf_files:
        print("No PDFs found across selected days.")
        return []

    output_dir = get_analysis_run_dir()
    print(f"\nAnalysing {len(pdf_files)} PDF(s) across {len(folders)} day(s)")
    print(f"Saving positive deals to {output_dir}\n")
    return analyze_pdfs(pdf_files, output_dir=output_dir)


def _print_positive_deals(records: list[dict]) -> None:
    """Print only Good Deal and Excellent Deal results after all PDFs are processed."""
    positive = [
        record
        for record in records
        if _is_positive_verdict(_extract_verdict(record.get("analysis")))
    ]

    print("\n" + "=" * 70)
    print("POSITIVE DEALS (Good Deal / Excellent Deal)")
    print("=" * 70)

    if not positive:
        print("No Good Deal or Excellent Deal verdicts found.")
        return

    for record in positive:
        day = record.get("day") or Path(record["pdf_path"]).parent.name
        verdict = _extract_verdict(record.get("analysis"))
        print(f"\n[{day}] {record['company_name']} ({record['company_code']})")
        print(f"Verdict: {verdict}")
        print(_format_agent_payload(record["analysis"]))


def _print_records(records: list[dict]) -> None:
    _print_positive_deals(records)


if __name__ == "__main__":
    # Agent output contains ₹ and similar symbols the default Windows console can't encode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="BSE award order stock recommender flow")
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help=(
            "How many calendar days to cover (today + past). "
            "For each day: reuse non-empty folder, else download that day only, "
            "then run agents A/B/C on all collected PDFs. Default: 1 (today)."
        ),
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Never call the downloader; only analyse existing non-empty day folders",
    )
    parser.add_argument(
        "--before-days",
        type=int,
        default=None,
        help="Legacy: process only this many days ago (single folder). Prefer --days.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N PDFs (across all selected days)",
    )
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be >= 1")

    results = run_flow(
        skip_download=args.skip_download,
        days=args.days,
        before_days=args.before_days,
        limit=args.limit,
    )
    _print_records(results)
