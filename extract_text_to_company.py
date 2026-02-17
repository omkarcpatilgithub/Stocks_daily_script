import re

DATE_PREFIX_RE = re.compile(
    r"""^\s*
        (?:                                   # common date prefixes
            \d{1,2}\s*[A-Za-z]{3,}\w*\s*\d{2,4}   # 13 febv 26 / 17 Feb 2026
          | \d{1,2}[-/]\d{1,2}[-/]\d{2,4}         # 13-02-2026 / 13/02/26
        )
        \s*[-–—:]*\s*                          # optional separators after date
    """,
    re.IGNORECASE | re.VERBOSE
)

def extract_company_name_from_bse_text(text: str) -> str | None:
    # Find first 6-digit BSE scrip code
    m = re.search(r"\b\d{6}\b", text)
    if not m:
        return None
    company_code = m.group(0)

    # Take only the same-line chunk immediately before the code
    line_start = max(text.rfind("\n", 0, m.start()), text.rfind("\r", 0, m.start()))
    line_start = 0 if line_start == -1 else line_start + 1
    candidate = text[line_start:m.start()].strip()

    # Remove a leading date prefix if present (e.g., "13 febv 26- ")
    candidate = DATE_PREFIX_RE.sub("", candidate).strip()

    # Clean trailing separators / currency artifacts
    candidate = re.sub(r"[-\s]+$", "", candidate)
    candidate = re.sub(r"[-\s]*[₹$]+$", "", candidate)
    candidate = re.sub(r"[-\s]+$", "", candidate).strip()

    if candidate:
        return company_code + "_" + candidate
    elif company_code:
        return company_code + "_"
    else:
        return text.strip()  # fallback to raw text if no code found