import os
import time
import threading
from pathlib import Path

import requests
from dotenv import load_dotenv


BALE_API_BASE = "https://tapi.bale.ai"
BALE_MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_RETRIES = 5
UPLOAD_TIMEOUT = 1800

load_dotenv()
BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
BALE_ADMIN_CHAT_ID = os.getenv("BALE_ADMIN_CHAT_ID", "").strip()


def send_bale_document(file_path: str, caption: str = ""):
    if not BALE_BOT_TOKEN:
        raise RuntimeError("BALE_BOT_TOKEN تنظیم نشده است.")
    if not BALE_ADMIN_CHAT_ID:
        raise RuntimeError("BALE_ADMIN_CHAT_ID تنظیم نشده است.")

    url = f"{BALE_API_BASE}/bot{BALE_BOT_TOKEN}/sendDocument"
    data = {"chat_id": BALE_ADMIN_CHAT_ID}
    if caption:
        data["caption"] = caption

    with open(file_path, "rb") as file_obj:
        files = {"document": (Path(file_path).name, file_obj)}
        response = requests.post(url, data=data, files=files, timeout=(30, 600))

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if response.status_code != 200 or not payload.get("ok", False):
        desc = payload.get("description") or f"HTTP {response.status_code}"
        raise RuntimeError(f"ارسال به بله ناموفق بود: {desc}")

    return payload.get("result")


def send_bale_with_timeout(file_path: str, caption: str, timeout: float):
    result = {}
    error = {}

    def target():
        try:
            result["data"] = send_bale_document(file_path, caption)
        except Exception as e:
            error["err"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

    if "err" in error:
        raise error["err"]

    return result.get("data")


def send_bale_with_retry(file_path: str, caption: str = "", task: dict | None = None):
    _ = task
    last_error = None
    start_time = time.time()

    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start_time > UPLOAD_TIMEOUT:
            raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

        try:
            elapsed = time.time() - start_time
            remaining = UPLOAD_TIMEOUT - elapsed
            if remaining <= 0:
                raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

            return send_bale_with_timeout(file_path, caption, remaining)

        except Exception as e:
            last_error = e
            error_text = str(e).lower()
            transient = any(
                key in error_text
                for key in [
                    "502", "503", "bad gateway", "timeout",
                    "cannot connect", "connection reset",
                    "temporarily unavailable",
                ]
            )

            if transient and attempt < MAX_RETRIES:
                time.sleep(3)
                continue

    raise last_error if last_error else RuntimeError("Bale upload failed.")
import os
import time
import threading
from pathlib import Path

import requests


BALE_API_BASE = "https://tapi.bale.ai"
BALE_MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_RETRIES = 5
UPLOAD_TIMEOUT = 1800

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
BALE_ADMIN_CHAT_ID = os.getenv("BALE_ADMIN_CHAT_ID", "").strip()


def send_bale_document(file_path: str, caption: str = ""):
    if not BALE_BOT_TOKEN:
        raise RuntimeError("BALE_BOT_TOKEN تنظیم نشده است.")
    if not BALE_ADMIN_CHAT_ID:
        raise RuntimeError("BALE_ADMIN_CHAT_ID تنظیم نشده است.")

    url = f"{BALE_API_BASE}/bot{BALE_BOT_TOKEN}/sendDocument"
    data = {"chat_id": BALE_ADMIN_CHAT_ID}
    if caption:
        data["caption"] = caption

    with open(file_path, "rb") as file_obj:
        files = {"document": (Path(file_path).name, file_obj)}
        response = requests.post(url, data=data, files=files, timeout=(30, 600))

    try:
        payload = response.json()
    except Exception:
        payload = {}

    if response.status_code != 200 or not payload.get("ok", False):
        desc = payload.get("description") or f"HTTP {response.status_code}"
        raise RuntimeError(f"ارسال به بله ناموفق بود: {desc}")

    return payload.get("result")


def send_bale_with_timeout(file_path: str, caption: str, timeout: float):
    result = {}
    error = {}

    def target():
        try:
            result["data"] = send_bale_document(file_path, caption)
        except Exception as e:
            error["err"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

    if "err" in error:
        raise error["err"]

    return result.get("data")


def send_bale_with_retry(file_path: str, caption: str = "", task: dict | None = None):
    # task پارامتر سازگاری برای امضای فعلی است.
    _ = task
    last_error = None
    start_time = time.time()

    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start_time > UPLOAD_TIMEOUT:
            raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

        try:
            elapsed = time.time() - start_time
            remaining = UPLOAD_TIMEOUT - elapsed
            if remaining <= 0:
                raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")

            return send_bale_with_timeout(file_path, caption, remaining)

        except Exception as e:
            last_error = e
            error_text = str(e).lower()
            transient = any(
                key in error_text
                for key in [
                    "502", "503", "bad gateway", "timeout",
                    "cannot connect", "connection reset",
                    "temporarily unavailable",
                ]
            )

            if transient and attempt < MAX_RETRIES:
                time.sleep(3)
                continue

    raise last_error if last_error else RuntimeError("Bale upload failed.")
