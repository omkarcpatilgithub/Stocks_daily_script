from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
import time
import os, re, requests 
from pathlib import Path
from datetime import datetime

URL = "https://www.bseindia.com/corporates/ann.html"

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

    # Find the <select> that comes after the label text
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
            # Sometimes element is present but not interactable yet
            WebDriverWait(driver, timeout).until(lambda d: sel_el.is_enabled())
            Select(sel_el).select_by_visible_text(option_text)
            return
        except Exception as e:
            last_err = e

    raise TimeoutException(f"Could not find/select dropdown for '{label_text}'. Last error: {last_err}")

# ---- From Date / To Date = Today (label-based, robust) ----
from datetime import datetime
today_str = datetime.today().strftime("%d/%m/%Y")  # if site expects different format, we’ll tweak

def type_date_by_label(driver, label_text: str, value: str, timeout=25):
    # find the first input after the label (works for most forms)
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
    raise TimeoutException(f"Could not find/type date for '{label_text}'. Last error: {last_err}")



driver = webdriver.Chrome()
wait = WebDriverWait(driver, 25)

try:
    driver.get(URL)
    driver.maximize_window()
    wait_ready(driver)

    today_str = datetime.today().strftime("%d/%m/%Y")  # change format if your UI expects differently

    # type_date_by_label(driver, "From Date", today_str, timeout=25)
    # type_date_by_label(driver, "To Date", today_str, timeout=25)

    time.sleep(10)  # small pause to ensure any datepicker scripts react to input    

    # ---- Category = Company Updates ----
    select_by_label_text(driver, "Category", "Company Update")

    # ---- Sub Category = Award of order ----
    select_by_label_text(driver, "Sub Category", "Award of Order / Receipt of Order")

    print("✅ Selected Category and Sub Category successfully.")

    time.sleep(1)

    # --------- CLICK SUBMIT ----------
    submit_btn = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//input[@type='submit' and @value='Submit']"))
    )
    try:
        submit_btn.click()
    except Exception:
        driver.execute_script("arguments[0].click();", submit_btn)

    # --------- WAIT FOR RESULTS TO LOAD ----------
    # Wait until the loader disappears (if present)
    try:
        wait.until(EC.presence_of_element_located(
            (By.XPATH, "//*[@ng-if=\"loader.CorpAnnState=='loading'\"]")
        ))
        wait.until(EC.invisibility_of_element_located(
            (By.XPATH, "//*[@ng-if=\"loader.CorpAnnState=='loading'\"]")
        ))
    except TimeoutException:
        # Loader may not appear every time; continue
        pass

    # Wait until at least one PDF link is present in results
    wait.until(
        EC.presence_of_element_located(
            (By.XPATH, "//a[contains(translate(@href,'PDF','pdf'),'.pdf') or contains(translate(@onclick,'PDF','pdf'),'pdf')]")
        )
    )

    # --------- DOWNLOAD ONLY 1 PDF PER COMPANY (DEDUPED) ----------
    import os, re, requests
    from pathlib import Path

    download_dir = Path(os.getcwd()) / "bse_award_orders" / time.strftime("%Y-%m-%d")
    download_dir.mkdir(parents=True, exist_ok=True)

    def sanitize_filename(name: str) -> str:
        name = (name or "unknown_company").strip()
        name = re.sub(r"[\\/:*?\"<>|]+", "_", name)
        name = re.sub(r"\s+", " ", name).strip()
        return name[:120] if len(name) > 120 else name

    def extract_pdf_url_from_onclick(onclick: str) -> str | None:
        if not onclick:
            return None
        m = re.search(r"(https?://[^'\" )]+\.pdf[^'\" )]*)", onclick, flags=re.IGNORECASE)
        return m.group(1) if m else None

    def download_with_browser_cookies(url: str, out_path: Path):
        cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
        headers = {"User-Agent": driver.execute_script("return navigator.userAgent;")}
        r = requests.get(url, cookies=cookies, headers=headers, stream=True, timeout=60)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 64):
                if chunk:
                    f.write(chunk)

    # Rows that look like announcement results
    rows = driver.find_elements(By.XPATH, "//tr[td]")

    seen_companies = set()
    seen_urls = set()
    download_count = 0

    for row in rows:
        tds = row.find_elements(By.TAG_NAME, "td")
        if not tds:
            continue

        company = tds[0].text.strip()
        
        if not company:
            continue
        if len(tds) > 3:
            print(f"company: '{tds[3].text.strip()}'")
        company_key = re.sub(r"\s+", " ", company).strip().lower()
        if company_key in seen_companies:
            # already downloaded for this company (prevents duplicates)
            continue

        # pick ONLY the first PDF link in this row
        links = row.find_elements(
            By.XPATH,
            ".//a[contains(translate(@href,'PDF','pdf'),'.pdf') or contains(translate(@onclick,'PDF','pdf'),'pdf')]"
        )
        if not links:
            continue

        link = links[0]
        href = (link.get_attribute("href") or "").strip()
        onclick = (link.get_attribute("onclick") or "").strip()

        pdf_url = None
        if href and ".pdf" in href.lower():
            pdf_url = href
        else:
            pdf_url = extract_pdf_url_from_onclick(onclick)

        if not pdf_url or ".pdf" not in pdf_url.lower():
            continue

        # URL-level dedupe too (in case 2 companies point to same PDF)
        url_key = pdf_url.split("#")[0].strip()
        if url_key in seen_urls:
            continue

        out_file = download_dir / f"{sanitize_filename(company)}.pdf"
        print(f"Downloading -> {company} : {out_file.name}")

        download_with_browser_cookies(pdf_url, out_file)

        seen_companies.add(company_key)
        seen_urls.add(url_key)
        download_count += 1

    print(f"✅ Downloaded {download_count} unique company PDFs to: {download_dir}")
except Exception as e:
    print("❌ Failed:", str(e))

    # Create "Failure" folder in current directory (if not exists)
    failure_dir = Path.cwd() / "Failure"
    failure_dir.mkdir(parents=True, exist_ok=True)

    # Filename with today's date & time (safe for Windows/macOS/Linux)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot_path = failure_dir / f"failure_{ts}.png"

    driver.save_screenshot(str(screenshot_path))
    print("📸 Screenshot saved to:", screenshot_path)

    raise

finally:
    driver.quit()