import argparse
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import extract_text_to_company as etc
import requests
from selenium import webdriver
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

URL = "https://www.bseindia.com/corporates/ann.html"
NETWORK_ERROR_MARKERS = (
    "ERR_INTERNET_DISCONNECTED",
    "ERR_NETWORK_CHANGED",
    "ERR_NAME_NOT_RESOLVED",
    "ERR_CONNECTION",
    "ERR_TIMED_OUT",
)
PDF_LINK_XPATH = (
    "//a[contains(translate(@href,'PDF','pdf'),'.pdf') "
    "or contains(translate(@onclick,'PDF','pdf'),'pdf')]"
)


def wait_ready(driver, timeout=25):
    WebDriverWait(driver, timeout).until(
        lambda d: d.execute_script("return document.readyState") == "complete"
    )


def safe_click(driver, element):
    try:
        element.click()
    except ElementClickInterceptedException:
        driver.execute_script("arguments[0].click();", element)


def select_by_label_text(driver, label_text: str, option_text: str, timeout=25):
    """
    Select option from a dropdown found near a label text like 'Category' / 'Sub Category'.
    Works for standard <select> dropdowns.
    """
    select_xpath_candidates = [
        f"//*[self::label or self::span or self::div][contains(normalize-space(), '{label_text}')]/following::select[1]",
        f"//*[contains(normalize-space(), '{label_text}')]/following::select[1]",
    ]

    last_err = None
    for sx in select_xpath_candidates:
        try:
            sel_el = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.XPATH, sx))
            )
            WebDriverWait(driver, timeout).until(lambda d: sel_el.is_enabled())
            Select(sel_el).select_by_visible_text(option_text)
            return
        except Exception as e:
            last_err = e

    raise TimeoutException(
        f"Could not find/select dropdown for '{label_text}'. Last error: {last_err}"
    )


def type_date_by_label(driver, label_text: str, value: str, timeout=25):
    xpaths = [
        f"//*[self::label or self::span or self::div][contains(normalize-space(), '{label_text}')]/following::input[1]",
        f"//*[contains(normalize-space(), '{label_text}')]/following::input[1]",
    ]
    last_err = None
    for xp in xpaths:
        try:
            el = WebDriverWait(driver, timeout).until(EC.element_to_be_clickable((By.XPATH, xp)))
            el.clear()
            el.send_keys(value)
            return
        except Exception as e:
            last_err = e
    raise TimeoutException(
        f"Could not find/type date for '{label_text}'. Last error: {last_err}"
    )


def get_page_with_retry(driver, url: str, retries: int = 3, delay_seconds: int = 5) -> None:
    """Retry navigation when Chrome reports a transient network error."""
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            return
        except WebDriverException as exc:
            last_error = exc
            message = str(exc)
            is_network_error = any(marker in message for marker in NETWORK_ERROR_MARKERS)
            if not is_network_error or attempt == retries:
                raise
            print(
                f"Network error while opening BSE ({attempt}/{retries}): {message}. "
                f"Retrying in {delay_seconds}s..."
            )
            time.sleep(delay_seconds)
    raise last_error


def sanitize_filename(name: str) -> str:
    name = (name or "unknown_company").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:120] if len(name) > 120 else name


def extract_pdf_url_from_onclick(onclick: str) -> str | None:
    if not onclick:
        return None
    m = re.search(r"(https?://[^'\" )]+\.pdf[^'\" )]*)", onclick, flags=re.IGNORECASE)
    return m.group(1) if m else None


def download_with_browser_cookies(driver, url: str, out_path: Path) -> None:
    cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
    headers = {"User-Agent": driver.execute_script("return navigator.userAgent;")}
    r = requests.get(url, cookies=cookies, headers=headers, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 64):
            if chunk:
                f.write(chunk)


def parse_target_date(value: str | None) -> datetime:
    """Parse YYYY-MM-DD, or use today when omitted."""
    if not value:
        return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.strptime(value, "%Y-%m-%d")


def download_for_date(target_date: datetime, project_dir: Path | None = None) -> Path:
    """
    Download Award-of-Order PDFs for a single calendar day into
    bse_award_orders/<YYYY-MM-DD>/.
    """
    project_dir = project_dir or Path(os.getcwd())
    day_folder = target_date.strftime("%Y-%m-%d")
    bse_date = target_date.strftime("%d/%m/%Y")
    download_dir = project_dir / "bse_award_orders" / day_folder
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Award of Order PDFs for {day_folder} ...")

    driver = webdriver.Chrome()
    wait = WebDriverWait(driver, 25)
    download_count = 0

    try:
        get_page_with_retry(driver, URL)
        driver.maximize_window()
        wait_ready(driver)

        # One day only: From Date = To Date = target
        type_date_by_label(driver, "From Date", bse_date, timeout=25)
        type_date_by_label(driver, "To Date", bse_date, timeout=25)

        select_by_label_text(driver, "Category", "Company Update")
        select_by_label_text(driver, "Sub Category", "Award of Order / Receipt of Order")
        print(f"Selected filters for {bse_date}.")

        time.sleep(1)

        submit_btn = wait.until(
            EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and @value='Submit']"))
        )
        try:
            submit_btn.click()
        except Exception:
            driver.execute_script("arguments[0].click();", submit_btn)

        try:
            wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[@ng-if=\"loader.CorpAnnState=='loading'\"]")
                )
            )
            wait.until(
                EC.invisibility_of_element_located(
                    (By.XPATH, "//*[@ng-if=\"loader.CorpAnnState=='loading'\"]")
                )
            )
        except TimeoutException:
            pass

        try:
            wait.until(EC.presence_of_element_located((By.XPATH, PDF_LINK_XPATH)))
        except TimeoutException:
            print(f"No Award of Order PDFs found on BSE for {day_folder}.")
            print(f"Downloaded 0 unique company PDFs to: {download_dir}")
            return download_dir

        rows = driver.find_elements(By.XPATH, "//tr[td]")
        seen_companies: set[str] = set()
        seen_urls: set[str] = set()

        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if not tds:
                continue

            company = tds[0].text.strip()
            if not company:
                continue
            if len(tds) > 3:
                print(f"company: '{tds[3].text.strip()}'")

            company_name = etc.extract_company_name_from_bse_text(company)
            company_key = re.sub(r"\s+", " ", company).strip().lower()
            if company_key in seen_companies:
                continue

            links = row.find_elements(By.XPATH, f".{PDF_LINK_XPATH}")
            if not links:
                continue

            link = links[0]
            href = (link.get_attribute("href") or "").strip()
            onclick = (link.get_attribute("onclick") or "").strip()

            if href and ".pdf" in href.lower():
                pdf_url = href
            else:
                pdf_url = extract_pdf_url_from_onclick(onclick)

            if not pdf_url or ".pdf" not in pdf_url.lower():
                continue

            url_key = pdf_url.split("#")[0].strip()
            if url_key in seen_urls:
                continue

            out_file = download_dir / f"{sanitize_filename(company_name)}.pdf"
            print(f"Downloading -> {company_name} : {out_file.name}")
            download_with_browser_cookies(driver, pdf_url, out_file)

            seen_companies.add(company_key)
            seen_urls.add(url_key)
            download_count += 1

        print(f"Downloaded {download_count} unique company PDFs to: {download_dir}")
        return download_dir

    except Exception as e:
        print("Failed:", str(e))
        failure_dir = Path.cwd() / "Failure"
        failure_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        screenshot_path = failure_dir / f"failure_{ts}.png"
        try:
            driver.save_screenshot(str(screenshot_path))
            print("Screenshot saved to:", screenshot_path)
        except Exception:
            pass
        raise
    finally:
        driver.quit()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download BSE Award of Order PDFs for a single calendar day."
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="Target day as YYYY-MM-DD (default: today). From/To on BSE are set to this day only.",
    )
    args = parser.parse_args()
    target = parse_target_date(args.date)
    download_for_date(target, project_dir=Path.cwd())


if __name__ == "__main__":
    main()
