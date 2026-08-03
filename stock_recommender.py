"""
Stock recommender flow:

1. Run download_annoucement_pdf.py and wait for it to finish
2. Resolve the folder where it stored today's PDFs
3. For each PDF:
   4. Get the company code + name
   5. Agent A -> company revenue (web search)
   6. Agent B -> order details from the PDF
   7. Agent C -> analysis + verdict from A and B

Returns one record per company so later steps can score them.
"""

from __future__ import annotations

import argparse
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
DOWNLOAD_SCRIPT = PROJECT_DIR / "download_annoucement_pdf.py"

LLM_MODEL = "openai/gpt-4o-mini"
# OpenRouter retired the ":free" variant of this slug and returns 404 for it
ANALYSIS_LLM_MODEL = "openai/gpt-oss-120b"
LLM_BASE_URL = "https://openrouter.ai/api/v1"

# Printed by download_annoucement_pdf.py on success
DOWNLOAD_DIR_RE = re.compile(r"Downloaded\s+\d+\s+unique company PDFs to:\s*(.+)")


# ---------------------------------------------------------------- step 1 & 2

def run_download_script() -> Path | None:
    """Run the BSE downloader as a subprocess and return the folder it wrote to."""
    today_folder = get_download_folder(before_days=0)
    if get_pdf_files(today_folder):
        print(f"Already downloaded, using {today_folder}")
        return today_folder

    print(f"Running {DOWNLOAD_SCRIPT.name} ...")

    result = subprocess.run(
        [sys.executable, str(DOWNLOAD_SCRIPT)],
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

    match = DOWNLOAD_DIR_RE.search(result.stdout or "")
    if match:
        return Path(match.group(1).strip())

    # Downloader names the folder after the run date
    return get_download_folder(before_days=0)


def get_download_folder(before_days: int = 0) -> Path:
    """Folder the downloader uses: bse_award_orders/<YYYY-MM-DD>/."""
    day = datetime.now() - timedelta(days=before_days)
    return AWARD_ORDERS_DIR / day.strftime("%Y-%m-%d")


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
    )


_ddg = DuckDuckGoSearchRun()


@tool
def company_revenue(company_name: str) -> str:
    """Search the internet for a company's latest annual revenue."""
    query = f"{company_name} latest annual revenue FY"
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
    "Always use the company_revenue tool before answering revenue questions. "
    "Analyse the revenue number of the given company. "
    "Return only the revenue number."
)

ORDER_AGENT_PROMPT = (
    "You are an analyst for BSE 'Award of Order / Receipt of Order' announcements.\n"
    "Always call read_award_pdf with the exact PDF path from the user before answering.\n"
    "Extract ONLY from the PDF text. If something is not mentioned, say 'Not disclosed'.\n\n"
    "Return the answer in this exact format:\n"
    "Company:\n"
    "Order Value:\n"
    "Currency / GST note:\n"
    "Order breakup (if any):\n"
    "Client / Awarding authority:\n"
    "Project / Scope summary:\n"
    "Payment structure:\n"
    "- One-time payment: Yes/No/Not disclosed\n"
    "- Upfront / advance %: \n"
    "- Milestone / progressive billing: \n"
    "- Retention / BG / LD terms: \n"
    "- Payment cycle / credit period: \n"
    "Contract duration / execution period:\n"
    "Other commercial terms:\n"
    "Notes / caveats:\n"
)


ANALYSIS_AGENT_PROMPT = (
    "Act as a stock market analyst. You are given a company's latest annual revenue "
    "and the already-extracted details of its BSE order announcement, so do not look "
    "anything up and do not ask for the PDF — work only from the numbers provided.\n\n"
    "Evaluate how meaningful the order is for the company. Compare the order value with "
    "the annual revenue given to you, and with net profit, order book and market "
    "capitalisation only where those figures appear in the inputs; mark anything missing "
    "as 'Not available' instead of guessing. Calculate key ratios such as "
    "Order Value / Annual Revenue (%), showing the arithmetic.\n"
    "Assess whether the order is likely to materially impact future earnings and business "
    "growth. Highlight any risks (small order size, low margins, one-time nature, "
    "execution risk, undisclosed payment terms, etc.).\n\n"
    "Return the answer in this format:\n"
    "Key Ratios:\n"
    "Materiality Assessment:\n"
    "Risks:\n"
    "Verdict: <Excellent Deal | Good Deal | Average Deal | Not Material>\n"
    "Rationale:\n"
)


def _make_agent(tools: list, prompt: str, model: str = LLM_MODEL):
    return create_agent(_build_llm(model), tools, **{_PROMPT_KWARG: prompt})


def build_revenue_agent():
    """Agent A — company revenue via web search."""
    return _make_agent([company_revenue], REVENUE_AGENT_PROMPT)


def build_order_agent():
    """Agent B — order details from the announcement PDF."""
    return _make_agent([read_award_pdf], ORDER_AGENT_PROMPT)


def build_analysis_agent():
    """Agent C — analyses A + B and returns a verdict. No tools needed."""
    return _make_agent([], ANALYSIS_AGENT_PROMPT, model=ANALYSIS_LLM_MODEL)


def _ask(agent, question: str) -> str:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    return result["messages"][-1].content


# -------------------------------------------------------------------- step 5

def get_revenue(agent, company_name: str) -> str:
    return _ask(agent, f"What is {company_name} latest annual revenue?")


# -------------------------------------------------------------------- step 6

def get_order_details(agent, pdf_path: Path) -> str:
    return _ask(
        agent,
        "Analyze this Award of Order PDF and extract order value "
        f"and payment structure details.\nPDF path: {pdf_path}",
    )


# -------------------------------------------------------------------- step 7

def get_analysis(agent, company_name: str, revenue: str, order_details: str) -> str:
    return _ask(
        agent,
        f"Company: {company_name}\n\n"
        f"[A] Latest annual revenue:\n{revenue}\n\n"
        f"[B] Order announcement details:\n{order_details}\n\n"
        "Analyse how meaningful this order is and give your verdict.",
    )


# --------------------------------------------------------------------- flow

def run_flow(
    skip_download: bool = False,
    before_days: int = 0,
    limit: int | None = None,
) -> list[dict]:
    """Run the full flow and return one record per company PDF."""
    if skip_download:
        folder = get_download_folder(before_days=before_days)
        print(f"Skipping download, using {folder}")
    else:
        folder = run_download_script()

    if folder is None or not Path(folder).is_dir():
        print(f"No download folder found: {folder}")
        return []

    pdf_files = get_pdf_files(Path(folder))
    if limit:
        pdf_files = pdf_files[:limit]

    if not pdf_files:
        print(f"No PDFs found in {folder}")
        return []

    print(f"Found {len(pdf_files)} PDF(s) in {folder}\n")

    revenue_agent = build_revenue_agent()
    order_agent = build_order_agent()
    analysis_agent = build_analysis_agent()

    records = []
    for pdf_path in pdf_files:
        company_code, company_name = get_company_from_pdf(pdf_path)
        print(f"Processing {company_name} ({company_code})")

        record = {
            "pdf_path": str(pdf_path),
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

        records.append(record)

    return records


def _print_records(records: list[dict]) -> None:
    for record in records:
        print("\n" + "=" * 70)
        print(f"{record['company_name']} ({record['company_code']})")
        print("=" * 70)
        print(f"\n[A] Revenue:\n{record['revenue']}")
        # print(f"\n[B] Order details:\n{record['order_details']}")
        print(f"\n[C] Analysis:\n{record['analysis']}")
        if record["errors"]:
            print(f"\nErrors: {record['errors']}")


if __name__ == "__main__":
    # Agent output contains ₹ and similar symbols the default Windows console can't encode
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="BSE award order stock recommender flow")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Use already downloaded PDFs instead of running the downloader",
    )
    parser.add_argument(
        "--before-days",
        type=int,
        default=0,
        help="With --skip-download, how many days back to read PDFs from",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N PDFs",
    )
    args = parser.parse_args()

    results = run_flow(
        skip_download=args.skip_download,
        before_days=args.before_days,
        limit=args.limit,
    )
    _print_records(results)
