from __future__ import annotations

import os
import sys
import time
from typing import Iterable, Tuple

from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

REDMINE_LOGIN_URL = "https://redmine.trantorinc.com/login"
DEFAULT_TIMEOUT_SECONDS = 25


def _build_driver(headless: bool = False) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    # Selenium Manager will auto-resolve a matching ChromeDriver in recent Selenium versions.
    return webdriver.Chrome(options=options)


def _first_clickable(
    wait: WebDriverWait,
    locators: Iterable[Tuple[str, str]],
):
    for by, selector in locators:
        try:
            return wait.until(EC.element_to_be_clickable((by, selector)))
        except TimeoutException:
            continue
    return None


def click_sso_login(driver: webdriver.Chrome, timeout: int = DEFAULT_TIMEOUT_SECONDS) -> None:
    wait = WebDriverWait(driver, timeout)

    # Common patterns for SSO controls in Redmine or custom themes.
    locators = [
        (By.CSS_SELECTOR, "a[href*='sso']"),
        (By.CSS_SELECTOR, "a[href*='saml']"),
        (By.CSS_SELECTOR, "a[href*='oauth']"),
        (By.CSS_SELECTOR, "a[href*='openid']"),
        (By.CSS_SELECTOR, "button[id*='sso']"),
        (By.XPATH, "//a[contains(translate(normalize-space(.), 'sso', 'SSO'), 'SSO')]"),
        (By.XPATH, "//button[contains(translate(normalize-space(.), 'sso', 'SSO'), 'SSO')]"),
        (By.XPATH, "//*[contains(translate(normalize-space(.), 'sign in with', 'SIGN IN WITH'), 'SIGN IN WITH')]"),
    ]

    element = _first_clickable(wait, locators)
    if element is None:
        raise TimeoutException("Could not find a clickable SSO login element on the Redmine login page.")

    element.click()


def try_login_idp(driver: webdriver.Chrome, timeout: int = 15) -> None:
    username = os.getenv("REDMINE_SSO_USERNAME")
    password = os.getenv("REDMINE_SSO_PASSWORD")

    if not username or not password:
        return

    wait = WebDriverWait(driver, timeout)

    username_candidates = [
        (By.NAME, "loginfmt"),
        (By.NAME, "username"),
        (By.NAME, "email"),
        (By.CSS_SELECTOR, "input[type='email']"),
        (By.ID, "i0116"),
    ]
    password_candidates = [
        (By.NAME, "passwd"),
        (By.NAME, "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
        (By.ID, "i0118"),
    ]
    next_btn_candidates = [
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.ID, "idSIButton9"),
    ]

    user_input = _first_clickable(wait, username_candidates)
    if user_input is not None:
        user_input.clear()
        user_input.send_keys(username)

        next_btn = _first_clickable(wait, next_btn_candidates)
        if next_btn is not None:
            next_btn.click()

    pass_input = _first_clickable(wait, password_candidates)
    if pass_input is not None:
        pass_input.clear()
        pass_input.send_keys(password)

        sign_in_btn = _first_clickable(wait, next_btn_candidates)
        if sign_in_btn is not None:
            sign_in_btn.click()


def main() -> int:
    headless = os.getenv("HEADLESS", "false").lower() in {"1", "true", "yes"}
    driver = _build_driver(headless=headless)

    try:
        driver.get(REDMINE_LOGIN_URL)
        click_sso_login(driver)
        try_login_idp(driver)

        # Keep browser open briefly for verification.
        sleep_seconds = int(os.getenv("POST_LOGIN_WAIT", "10"))
        time.sleep(max(sleep_seconds, 0))
        return 0
    except Exception as exc:  # pragma: no cover
        print(f"Login automation failed: {exc}", file=sys.stderr)
        return 1
    finally:
        driver.quit()


if __name__ == "__main__":
    raise SystemExit(main())
