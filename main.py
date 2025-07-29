from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
import os
import time
import base64
import shutil
import datetime
import uuid
import random
import json
import threading
from pathlib import Path
from typing import Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from zoneinfo import ZoneInfo
import calendar
import requests
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import ElementClickInterceptedException

# ── constants ─────────────────────────────────────────────────────────────
EXTENSION_CRX    = "extension"
HEADLESS       = True
DELAY          = 0.5
ROOT_DL        = Path.cwd() / "downloads"
ROOT_DL.mkdir(exist_ok=True)

API_INFO       = "https://cauntkqx43.execute-api.us-east-1.amazonaws.com/prod/mf_fetch_portal_information"
API_PASS       = "https://cauntkqx43.execute-api.us-east-1.amazonaws.com/prod/mf_fetch_portal_email_password"
API_UPLOAD     = "https://cauntkqx43.execute-api.us-east-1.amazonaws.com/prod/mf_fun_invoice_main"
API_STATUS     = "https://cauntkqx43.execute-api.us-east-1.amazonaws.com/prod/mf_fetch_file_statse"

MAX_WORKERS    = 7
RUNS           = {}

# ── auto-clean empty user dirs ────────────────────────────────────────────
def check_user_folder(user_folder: Path, wait_seconds: int = 40):
    """
    تنتظر wait_seconds ثم تمسح user_folder
    إذا لم يحتوي على أي مجلد فرعي رقمي.
    """
    time.sleep(wait_seconds)
    numeric_dirs = [d for d in user_folder.iterdir()
                    if d.is_dir() and d.name.isdigit()]
    if not numeric_dirs:
        shutil.rmtree(user_folder)
        print(f"Deleted empty user folder: {user_folder}")
    else:
        print(f"User folder OK, contains: {[d.name for d in numeric_dirs]}")

def monitor_downloads_folder(root: Path, wait_seconds: int = 40, poll_interval: float = 1.0):
    """
    يطلق ثريد يراقب root every poll_interval ثانية،
    ويشغل check_user_folder على أي مجلد جديد.
    """
    seen = set()
    def _watcher():
        while True:
            for entry in root.iterdir():
                if entry.is_dir() and entry.name not in seen:
                    seen.add(entry.name)
                    threading.Thread(
                        target=check_user_folder,
                        args=(entry, wait_seconds),
                        daemon=True
                    ).start()
            time.sleep(poll_interval)
    threading.Thread(target=_watcher, daemon=True).start()

# start monitoring downloads for empty user folders
monitor_downloads_folder(ROOT_DL, wait_seconds=40)

# ── security‐settings setup ────────────────────────────────────────────────
def setup_security(driver: webdriver.Chrome):
    """
    يفتح صفحة إعدادات الأمان في Chrome ويرسل 6 ضغطات Tab ثم Enter
    لضبط الإعدادات المطلوبة قبل أي عملية تحميل.
    """
    driver.get("chrome://settings/security")
    time.sleep(1)  # انتظر تحميل الصفحة
    actions = ActionChains(driver)
    for _ in range(7):
        actions.send_keys(Keys.TAB)
        time.sleep(0.2)
    actions.send_keys(Keys.ENTER)
    actions.perform()
    time.sleep(1)  # وقت إضافي لتطبيق الإعدادات

# ── webdriver setup ────────────────────────────────────────────────────────
def make_driver() -> webdriver.Chrome:
    prefs = {
        "download.default_directory": str(ROOT_DL.resolve()),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
    }
    opts = webdriver.ChromeOptions()
    opts.add_experimental_option("prefs", prefs)

    if HEADLESS:
        opts.add_argument("--headless=new")
        opts.add_argument("--window-size=1920,1080")

    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument(f"--remote-debugging-port={random.randint(40000,65000)}")
    opts.add_argument(f"--user-data-dir=/tmp/chrome_{uuid.uuid4().hex}")

    # تحميل الإضافة سواء كمجلد unpacked أو كـ .crx
    ext_path = Path(EXTENSION_CRX).resolve()
    if ext_path.is_dir():
        opts.add_argument(f"--load-extension={ext_path}")
    else:
        opts.add_extension(str(ext_path))

    return webdriver.Chrome(service=ChromeService(), options=opts)

def wclick(dr, loc: Tuple[By, str], t=20):
    elem = WebDriverWait(dr, t).until(EC.element_to_be_clickable(loc))
    try:
        elem.click()
    except ElementClickInterceptedException:
        for sel in ['button[aria-label="Close"]', 'button.ms-Dialog-closeButton']:
            try:
                dr.find_element(By.CSS_SELECTOR, sel).click()
                time.sleep(DELAY)
            except:
                pass
        elem.click()

def wfind(dr, loc: Tuple[By, str], t=20):
    return WebDriverWait(dr, t).until(EC.presence_of_element_located(loc))

def wait_done(folder: Path, before: set) -> Path:
    while True:
        files = {f for f in folder.iterdir() if f.is_file() and not f.name.endswith(".crdownload")}
        new = files - before
        if new:
            return new.pop()
        time.sleep(DELAY)

def chrome_set_user_dir(driver, directory: Path):
    driver.execute_cdp_cmd("Page.setDownloadBehavior", {
        "behavior": "allow",
        "downloadPath": str(directory.resolve())
    })

# ── profile fetch ─────────────────────────────────────────────────────────
def fetch_profile(driver, actions, email, password, user_id, run_id):
    driver.get("https://profile.eta.gov.eg/TaxpayerProfile")
    wfind(driver, (By.ID, "email")).send_keys(email)
    wfind(driver, (By.ID, "Password")).send_keys(password)
    wclick(driver, (By.ID, "submit")); time.sleep(1)
    for _ in range(5):
        try:
            sel = driver.find_element(By.XPATH, "//*[text()='Select']")
            if sel.is_displayed():
                sel.click()
                break
        except:
            time.sleep(1)

    creds, stable, prev = {}, -1, -1
    while stable < 2:
        boxes = driver.find_elements(By.CSS_SELECTOR, "div.vertialFlexDiv")
        stable = stable + 1 if len(boxes) == prev and boxes else 0
        prev = len(boxes)
        time.sleep(DELAY)

    for b in boxes:
        try:
            label = b.find_element(By.CSS_SELECTOR, "label.ms-Label").text.strip()
            value = b.find_element(By.CSS_SELECTOR, "input").get_attribute("value").strip()
            creds[label] = value
        except:
            pass

    reg = creds.get("Registration Number", "UNKNOWN")
    #requests.post(API_INFO, json={
    #    "file_name": f"{reg},{user_id}.txt",
    #    "file_content": "\n".join(f"{k}: {v}" for k, v in creds.items())
    #})
    #requests.post(API_PASS, json={
    #    "registration_number": reg,
    #    "email": email,
    #    "password": password,
    #    "userId": user_id
    #})
    requests.post(API_STATUS, json={
        "userId": user_id,
        "statuse": "false"
    })

    creds_folder = Path("credentials")
    creds_folder.mkdir(exist_ok=True)
    with (creds_folder / f"{reg},{user_id}.txt").open("w", encoding="utf-8") as f:
        f.write(f"registration_number: {reg}\nemail: {email}\npassword: {password}\nuserId: {user_id}\n")

    RUNS[run_id] = "profile_ok"
    return reg

# ── date filter: last two months ───────────────────────────────────────────
def open_filter(driver):
    wclick(driver, (By.NAME, "Search All Documents")); time.sleep(DELAY)
    wclick(driver, (By.ID, "showFilterButton"));    time.sleep(DELAY)

def set_range_last_two_month(driver, actions, today):
    open_filter(driver)
    frm = wfind(driver, (By.CSS_SELECTOR, 'input[placeholder="From"]'))
    frm.find_element(By.XPATH, './following-sibling::*[@data-icon-name="Calendar"]').click()
    time.sleep(DELAY)

    # حساب الشهر والسنة الهدفين قبل شهرين
    target_month = today.month - 2
    target_year = today.year
    if target_month <= 0:
        target_month += 12
        target_year -= 1

    # فتح اختيار الشهر في الكالندر
    actions.send_keys(Keys.TAB, Keys.ENTER).perform()
    time.sleep(DELAY)
    # التنقل بالشهور للخلف
    months_back = (today.month - target_month) % 12
    for _ in range(months_back):
        actions.send_keys(Keys.LEFT).perform()
        time.sleep(DELAY)
    actions.send_keys(Keys.ENTER).perform()
    time.sleep(DELAY)

    # تحديد اليوم مع مراعاة فرق عدد الأيام في الشهر الهدف
    days_in_target = calendar.monthrange(target_year, target_month)[1]
    initial_day = min(today.day, days_in_target)
    for _ in range(initial_day - 1):
        actions.send_keys(Keys.LEFT).perform()
        time.sleep(DELAY)
    actions.send_keys(Keys.ENTER).perform()
    time.sleep(DELAY)

    # فتح حقل "To"
    to_in = wfind(driver, (By.CSS_SELECTOR, 'input[placeholder="To"]'))
    to_in.find_element(By.XPATH, './following-sibling::*[@data-icon-name="Calendar"]').click()
    time.sleep(DELAY)

    # نتركه يختار اليوم الحالي في حقل To
    actions.send_keys(Keys.ENTER).perform()
    time.sleep(DELAY)

    # تطبيق الفلتر
    wclick(driver, (By.XPATH, "//button[.//span[text()='Apply']]"))
    time.sleep(DELAY * 2)

# ── download with empty-skip ───────────────────────────────────────────────
def download_all(driver) -> bool:
    for loc in [
        (By.CLASS_NAME, "downloadAllBtnText"),
        (By.ID, "col-version"),
        (By.ID, "option-negative-credit")
    ]:
        wclick(driver, loc)
    txt = wfind(driver, (By.ID, "modalTotalCountText")).text.strip()
    count = int(txt) if txt.isdigit() else 0
    if count == 0 :
        wclick(driver, (By.CSS_SELECTOR, "[data-dismiss='modal']"))
        return False
    wclick(driver, (By.ID, "excelBtn"))
    return True

# ── upload with retries & backoff ─────────────────────────────────────────
def upload_excel(path: Path) -> bool:
    content = base64.b64encode(path.read_bytes()).decode()
    for attempt in range(1, 16):
        try:
            r = requests.post(API_UPLOAD, json={
                "file_name": path.name,
                "file_content": content
            })
            if r.status_code == 200:
                return True
            print(f"Upload returned {r.status_code} on attempt {attempt}, retrying...")
        except Exception as e:
            print(f"Upload exception on attempt {attempt}: {e}, retrying...")
        time.sleep(DELAY * (2 ** (attempt - 1)))
    print(f"Upload failed after 15 attempts: {path.name}")
    return False

def fetch_invoices(driver, actions, reg, run_id):
    today = datetime.date.today()
    session_root = ROOT_DL / run_id
    reg_dir = session_root / reg
    reg_dir.mkdir(parents=True, exist_ok=True)
    chrome_set_user_dir(driver, reg_dir)

    driver.get("https://invoicing.eta.gov.eg/documents")
    time.sleep(DELAY * 2)

    set_range_last_two_month(driver, actions, today)
    before = {*reg_dir.iterdir()}
    if download_all(driver):
        f = wait_done(reg_dir, before).rename(reg_dir / f"{reg} all document invoice two months.xlsx")
        if upload_excel(f):
            print(f"[{run_id}] Uploaded invoice for user {reg}")
    shutil.rmtree(session_root, ignore_errors=True)

# ── orchestrator with retry and per-user Done log ─────────────────────────
def run_downloader(run_id, email, password, user_id):
    for attempt in range(1, 15):
        driver = None
        try:
            RUNS[run_id] = "running"
            session_root = ROOT_DL / run_id
            if session_root.exists():
                shutil.rmtree(session_root)
            session_root.mkdir(exist_ok=True)

            driver = make_driver()
            actions = ActionChains(driver)

            reg = fetch_profile(driver, actions, email, password, user_id, run_id)
            fetch_invoices(driver, actions, reg, run_id)

            RUNS[run_id] = "done"
            print(f"[{run_id}] Done for user {user_id}")
            return
        except Exception as e:
            print(f"[{run_id}] Attempt {attempt}/2 failed for user {user_id}: {e}")
            shutil.rmtree(ROOT_DL / run_id, ignore_errors=True)
            time.sleep(DELAY)
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass
    RUNS[run_id] = "error"
    print(f"[{run_id}] Error for user {user_id} after retries")

# ── entry point and scheduling ────────────────────────────────────────────
def main():
    try:
        resp = requests.post(API_PASS)
        data = resp.json()
        body = data.get('body', data)
        parsed = json.loads(body) if isinstance(body, str) else body
        entries = parsed.get('files', []) if isinstance(parsed, dict) else parsed
    except Exception as e:
        print(f"Failed to fetch credentials: {e}")
        return

    print(f"Found {len(entries)} users; starting up to {MAX_WORKERS} concurrent downloads.")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {}
        for entry in entries:
            try:
                parts       = entry.split(",", 3)
                reg_no      = parts[0]
                email       = parts[1]
                password    = parts[2]
                user_id_ext = parts[3]
                user_id     = user_id_ext[:-4] if user_id_ext.lower().endswith(".txt") else user_id_ext

                run_id = uuid.uuid4().hex
                futures[executor.submit(run_downloader, run_id, email, password, user_id)] = user_id
            except Exception as e:
                print(f"Parsing entry '{entry}' failed: {e}")

        for future in as_completed(futures):
            user = futures[future]
            try:
                future.result()
            except Exception as e:
                print(f"Task for user {user} raised: {e}")

def get_sleep_duration_until_midnight():
    now = datetime.datetime.now(ZoneInfo("Africa/Cairo"))
    tomorrow = (now + datetime.timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return (tomorrow - now).total_seconds()

if __name__ == "__main__":
    def keep_alive():
        while True:
            print(f"💓 Heartbeat at {datetime.datetime.now()}")
            time.sleep(60)

    threading.Thread(target=keep_alive, daemon=True).start()

    while True:
        print(f"Starting run at {datetime.datetime.now(ZoneInfo('Africa/Cairo'))}")
        main()
        secs = get_sleep_duration_until_midnight()
        print(f"Run complete; sleeping {int(secs)}s until next 12 AM Cairo.")
        time.sleep(secs)
