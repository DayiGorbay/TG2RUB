import json
import os
import threading
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BALE_API_BASE = "https://tapi.bale.ai"
BALE_MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_RETRIES = 5
UPLOAD_TIMEOUT = 1800

BASE_DIR = Path(__file__).resolve().parent
QUEUE_DIR = BASE_DIR / "queue"
BALE_APPROVED_USERS_FILE = QUEUE_DIR / "bale_approved_users.json"
BALE_REQUESTS_FILE = QUEUE_DIR / "bale_access_requests.json"
BALE_STATE_FILE = QUEUE_DIR / "bale_bot_state.json"

BALE_BOT_TOKEN = os.getenv("BALE_BOT_TOKEN", "").strip()
BALE_ADMIN_CHAT_ID = os.getenv("BALE_ADMIN_CHAT_ID", "").strip()

QUEUE_DIR.mkdir(parents=True, exist_ok=True)


def bale_api_call(method: str, payload: dict | None = None, timeout: tuple[int, int] = (10, 60)):
    if not BALE_BOT_TOKEN:
        raise RuntimeError("BALE_BOT_TOKEN تنظیم نشده است.")
    url = f"{BALE_API_BASE}/bot{BALE_BOT_TOKEN}/{method}"
    response = requests.post(url, json=payload or {}, timeout=timeout)
    data = response.json()
    if response.status_code != 200 or not data.get("ok", False):
        raise RuntimeError(data.get("description") or f"Bale API error: {response.status_code}")
    return data.get("result")


def send_bale_text(chat_id: str | int, text: str, reply_markup: dict | None = None):
    payload = {"chat_id": str(chat_id), "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    return bale_api_call("sendMessage", payload=payload)


def send_bale_document(file_path: str, chat_id: str | int, caption: str = ""):
    if not BALE_BOT_TOKEN:
        raise RuntimeError("BALE_BOT_TOKEN تنظیم نشده است.")
    if not chat_id:
        raise RuntimeError("Bale chat_id نامعتبر است.")

    url = f"{BALE_API_BASE}/bot{BALE_BOT_TOKEN}/sendDocument"
    data = {"chat_id": str(chat_id)}
    if caption:
        data["caption"] = caption

    with open(file_path, "rb") as file_obj:
        files = {"document": (Path(file_path).name, file_obj)}
        response = requests.post(url, data=data, files=files, timeout=(30, 600))

    payload = response.json()
    if response.status_code != 200 or not payload.get("ok", False):
        desc = payload.get("description") or f"HTTP {response.status_code}"
        raise RuntimeError(f"ارسال به بله ناموفق بود: {desc}")
    return payload.get("result")


def send_bale_with_timeout(file_path: str, chat_id: str | int, caption: str, timeout: float):
    result = {}
    error = {}

    def target():
        try:
            result["data"] = send_bale_document(file_path, chat_id, caption)
        except Exception as e:
            error["err"] = e

    thread = threading.Thread(target=target)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")
    if "err" in error:
        raise error["err"]
    return result.get("data")


def send_bale_with_retry(file_path: str, chat_id: str | int, caption: str = "", task: dict | None = None):
    _ = task
    last_error = None
    start_time = time.time()
    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start_time > UPLOAD_TIMEOUT:
            raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")
        try:
            remaining = UPLOAD_TIMEOUT - (time.time() - start_time)
            if remaining <= 0:
                raise RuntimeError("ارسال به بله بیشتر از حد مجاز طول کشید و لغو شد.")
            return send_bale_with_timeout(file_path, chat_id, caption, remaining)
        except Exception as e:
            last_error = e
            error_text = str(e).lower()
            transient = any(
                key in error_text
                for key in ["502", "503", "bad gateway", "timeout", "cannot connect", "connection reset"]
            )
            if transient and attempt < MAX_RETRIES:
                time.sleep(3)
                continue
    raise last_error if last_error else RuntimeError("Bale upload failed.")


def load_json(path: Path, default):
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, type(default)):
                return data
    except Exception:
        pass
    return default


def save_json(path: Path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_bale_approved_users() -> list[int]:
    items = load_json(BALE_APPROVED_USERS_FILE, [])
    result: list[int] = []
    for item in items:
        try:
            result.append(int(item))
        except Exception:
            continue
    return sorted(set(result))


def add_bale_approved_user(user_id: int) -> None:
    users = set(get_bale_approved_users())
    users.add(int(user_id))
    save_json(BALE_APPROVED_USERS_FILE, sorted(users))


def get_last_update_id() -> int:
    state = load_json(BALE_STATE_FILE, {})
    return int(state.get("last_update_id", 0))


def set_last_update_id(update_id: int) -> None:
    save_json(BALE_STATE_FILE, {"last_update_id": int(update_id)})


def run_bale_dashboard_loop():
    if not BALE_BOT_TOKEN or not BALE_ADMIN_CHAT_ID:
        print("Bale dashboard disabled: BALE_BOT_TOKEN or BALE_ADMIN_CHAT_ID missing.")
        return

    print("Bale dashboard started.")
    while True:
        try:
            offset = get_last_update_id() + 1
            updates = bale_api_call("getUpdates", payload={"offset": offset, "timeout": 25}, timeout=(30, 60)) or []
            for update in updates:
                update_id = int(update.get("update_id", 0))
                if update_id:
                    set_last_update_id(update_id)

                callback = update.get("callback_query") or {}
                message = update.get("message") or {}

                if callback:
                    data = callback.get("data", "")
                    from_user = (callback.get("from") or {})
                    from_id = int(from_user.get("id", 0))
                    if data.startswith("bale_req_"):
                        uid = int(data.split("_")[-1])
                        if uid != from_id:
                            continue
                        requests_data = load_json(BALE_REQUESTS_FILE, {})
                        requests_data[str(uid)] = {"status": "pending", "requested_at": int(time.time())}
                        save_json(BALE_REQUESTS_FILE, requests_data)
                        keyboard = {
                            "inline_keyboard": [[
                                {"text": "✅ تایید", "callback_data": f"bale_approve_{uid}"},
                                {"text": "❌ رد", "callback_data": f"bale_reject_{uid}"}
                            ]]
                        }
                        send_bale_text(BALE_ADMIN_CHAT_ID, f"درخواست جدید بله از کاربر `{uid}`", keyboard)
                        send_bale_text(uid, "درخواست شما برای مدیر ارسال شد.")
                        continue

                    if from_id != int(BALE_ADMIN_CHAT_ID):
                        continue
                    if data.startswith("bale_approve_"):
                        uid = int(data.split("_")[-1])
                        add_bale_approved_user(uid)
                        requests_data = load_json(BALE_REQUESTS_FILE, {})
                        item = requests_data.get(str(uid), {})
                        item["status"] = "approved"
                        requests_data[str(uid)] = item
                        save_json(BALE_REQUESTS_FILE, requests_data)
                        send_bale_text(uid, "✅ درخواست شما در بله تایید شد.")
                        send_bale_text(BALE_ADMIN_CHAT_ID, f"کاربر `{uid}` تایید شد.")
                    elif data.startswith("bale_reject_"):
                        uid = int(data.split("_")[-1])
                        requests_data = load_json(BALE_REQUESTS_FILE, {})
                        item = requests_data.get(str(uid), {})
                        item["status"] = "rejected"
                        requests_data[str(uid)] = item
                        save_json(BALE_REQUESTS_FILE, requests_data)
                        send_bale_text(uid, "❌ درخواست شما در بله رد شد.")
                        send_bale_text(BALE_ADMIN_CHAT_ID, f"کاربر `{uid}` رد شد.")
                    continue

                text = str(message.get("text") or "").strip()
                chat = message.get("chat") or {}
                from_user = message.get("from") or {}
                chat_id = int(chat.get("id", 0))
                user_id = int(from_user.get("id", 0))
                if not chat_id or not user_id:
                    continue

                if text.lower() in ("/start", "start"):
                    if user_id == int(BALE_ADMIN_CHAT_ID):
                        approved = get_bale_approved_users()
                        preview = "\n".join([f"- `{uid}`" for uid in approved[:20]]) or "- موردی وجود ندارد"
                        send_bale_text(
                            BALE_ADMIN_CHAT_ID,
                            "📊 داشبورد مدیر بله\n\n"
                            f"تعداد کاربران تاییدشده: `{len(approved)}`\n\n"
                            f"لیست کاربران:\n{preview}",
                        )
                    elif user_id in get_bale_approved_users():
                        send_bale_text(chat_id, "✅ شما قبلا تایید شده‌اید.")
                    else:
                        keyboard = {
                            "inline_keyboard": [[{"text": "✅ درخواست دسترسی", "callback_data": f"bale_req_{user_id}"}]]
                        }
                        send_bale_text(chat_id, "این ربات خصوصی است. برای دسترسی درخواست ارسال کنید.", keyboard)
                elif text.lower() in ("/request", "request"):
                    requests_data = load_json(BALE_REQUESTS_FILE, {})
                    requests_data[str(user_id)] = {"status": "pending", "requested_at": int(time.time())}
                    save_json(BALE_REQUESTS_FILE, requests_data)
                    keyboard = {
                        "inline_keyboard": [[
                            {"text": "✅ تایید", "callback_data": f"bale_approve_{user_id}"},
                            {"text": "❌ رد", "callback_data": f"bale_reject_{user_id}"}
                        ]]
                    }
                    send_bale_text(BALE_ADMIN_CHAT_ID, f"درخواست جدید بله از کاربر `{user_id}`", keyboard)
                    send_bale_text(chat_id, "درخواست شما برای مدیر ارسال شد.")
        except Exception as e:
            print(f"Bale dashboard error: {e}")
            time.sleep(2)
