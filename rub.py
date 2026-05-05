import os
import re
import json
import time
import zipfile
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from rubpy import Client as RubikaClient
import requests
import pyzipper
from urllib.parse import urlparse
import threading
from bale import send_bale_with_retry, BALE_MAX_FILE_SIZE, BALE_ADMIN_CHAT_ID, get_bale_approved_users

load_dotenv()

SESSION = os.getenv("RUBIKA_SESSION", "rubika_session").strip()

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
QUEUE_DIR = BASE_DIR / "queue"
QUEUE_FILE = QUEUE_DIR / "tasks.jsonl"
PROCESSING_FILE = QUEUE_DIR / "processing.json"
FAILED_FILE = QUEUE_DIR / "failed.jsonl"
STATUS_FILE = QUEUE_DIR / "status.jsonl"
URL_DIR = DOWNLOAD_DIR / "url"
CANCEL_FILE = QUEUE_DIR / "cancelled.jsonl"
METRICS_FILE = QUEUE_DIR / "metrics.json"

MAX_RETRIES = 5
UPLOAD_TIMEOUT = 1800
TARGET = "me"
SPLIT_THRESHOLD_BYTES = int(1.5 * 1024 * 1024 * 1024)
SPLIT_PART_BYTES = 500 * 1024 * 1024

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)
URL_DIR.mkdir(parents=True, exist_ok=True)


def load_metrics() -> dict:
    try:
        if METRICS_FILE.exists():
            data = json.loads(METRICS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {
        "tg_downloads": 0,
        "url_downloads": 0,
        "rubika_uploads": 0,
        "bale_uploads": 0,
        "missions_success": 0,
        "missions_failed": 0,
    }


def save_metrics(data: dict) -> None:
    METRICS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def inc_metric(key: str, amount: int = 1) -> None:
    data = load_metrics()
    try:
        data[key] = int(data.get(key, 0)) + int(amount)
    except Exception:
        data[key] = int(amount)
    save_metrics(data)


def safe_filename(name: Optional[str]) -> str:
    name = (name or "file").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = name.rstrip(". ")
    return name[:200] or "file"

def pretty_size(size) -> str:
    size = float(size or 0)
    units = ["B", "KB", "MB", "GB"]

    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    return f"{size:.2f} {units[index]}"

def get_per_attempt_timeout(file_path: str) -> int:
    size_mb = Path(file_path).stat().st_size / (1024 * 1024)

    if size_mb < 100:
        return 180
    elif size_mb < 500:
        return 420
    elif size_mb < 1000:
        return 720
    else:
        return 1200
    
def eta_text(seconds) -> str:
    if not seconds or seconds <= 0:
        return "نامشخص"

    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60

    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def push_status(task: dict, text: str, status: str = "working", percent: float | None = None):
    payload = {
        "job_id": task.get("job_id"),
        "chat_id": task.get("chat_id"),
        "message_id": task.get("status_message_id"),
        "status": status,
        "text": text,
        "percent": percent,
        "time": time.time(),
    }

    with open(STATUS_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")

def is_cancelled(task: dict) -> bool:
    job_id = str(task.get("job_id", ""))

    if not job_id or not CANCEL_FILE.exists():
        return False

    with open(CANCEL_FILE, "r", encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue

            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue

            if str(item.get("job_id")) == job_id:
                return True

    return False

def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    stem = path.stem
    suffix = path.suffix
    index = 1

    while True:
        candidate = path.with_name(f"{stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def has_session(session_name: str) -> bool:
    candidates = [
        Path(session_name),
        Path(f"{session_name}.session"),
        Path(f"{session_name}.sqlite"),
    ]
    return any(path.exists() for path in candidates)


def ensure_session():
    if has_session(SESSION):
        return

    client = RubikaClient(name=SESSION)

    try:
        client.start()
        print("Login successful.")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


def send_document(file_path: str, caption: str = ""):
    client = RubikaClient(name=SESSION)

    try:
        client.start()
        return client.send_document(
            TARGET,
            file_path,
            caption=caption
        )
    finally:
        try:
            client.disconnect()
        except Exception:
            pass



def send_with_timeout(file_path, caption, timeout):
    result = {}
    error = {}

    def target():
        try:
            result["data"] = send_document(file_path, caption)
        except Exception as e:
            error["err"] = e

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout)

    if t.is_alive():
        raise RuntimeError("آپلود بیشتر از حد مجاز طول کشید و لغو شد.")

    if "err" in error:
        raise error["err"]

    return result.get("data")



def send_with_retry(file_path: str, caption: str = "", task: dict | None = None):
    last_error = None
    start_time = time.time()

    for attempt in range(1, MAX_RETRIES + 1):

        if time.time() - start_time > UPLOAD_TIMEOUT:
            raise RuntimeError("آپلود بیشتر از حد مجاز طول کشید و لغو شد.")

        if task and is_cancelled(task):
            raise RuntimeError("ارسال لغو شد.")

        try:
            if task:
                phase = (task.get("phase") or "").strip()
                phase_title = {
                    "rubika": "🟦 روبیکا",
                    "bale": "🟨 بله",
                }.get(phase, "🔼 آپلود")
                push_status(
                    task,
                    f"{phase_title}: در حال ارسال...\n\n"
                    f"🔴 تلاش {attempt} از {MAX_RETRIES}\n\n"
                    f"⏱ محدودیت تلاش: {get_per_attempt_timeout(file_path)}s",
                    "uploading"
                )

            elapsed = time.time() - start_time
            remaining = UPLOAD_TIMEOUT - elapsed

            if remaining <= 0:
                raise RuntimeError("آپلود بیشتر از ۳۰ دقیقه طول کشید و لغو شد.")

            per_attempt = min(get_per_attempt_timeout(file_path), remaining)

            return send_with_timeout(file_path, caption, per_attempt)

        except Exception as e:
            last_error = e
            error_text = str(e).lower()

            transient = any(
                key in error_text
                for key in [
                    "502", "503", "bad gateway", "timeout",
                    "cannot connect", "connection reset",
                    "temporarily unavailable",
                    "error uploading chunk",
                    "unexpected mimetype",
                ]
            )

            if transient and attempt < MAX_RETRIES:

                if task and is_cancelled(task):
                    raise RuntimeError("ارسال لغو شد.")

                if task:
                    push_status(
                        task,
                        f"ارتباط با روبیکا ناپایدار بود...\n"
                        f"دوباره تلاش می‌کنم ({attempt + 1})",
                        "uploading"
                    )

                time.sleep(3)
                continue

    raise last_error if last_error else RuntimeError("Upload failed.")


def make_zip_part(part_path: Path, password: str = "") -> Path:
    zip_path = unique_path(part_path.with_suffix(part_path.suffix + ".zip"))
    if password:
        with pyzipper.AESZipFile(
            zip_path,
            "w",
            compression=pyzipper.ZIP_STORED,
            encryption=pyzipper.WZ_AES,
        ) as zip_file:
            zip_file.setpassword(password.encode("utf-8"))
            zip_file.write(part_path, arcname=part_path.name)
    else:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as zip_file:
            zip_file.write(part_path, arcname=part_path.name)
    return zip_path


def split_to_zip_parts(file_path: Path, part_size: int, password: str = "") -> list[Path]:
    part_files: list[Path] = []
    zip_parts: list[Path] = []
    base_name = safe_filename(file_path.name)
    index = 1

    with open(file_path, "rb") as source:
        while True:
            chunk = source.read(part_size)
            if not chunk:
                break
            part_path = unique_path(file_path.with_name(f"{base_name}.part{index:03d}"))
            with open(part_path, "wb") as part_file:
                part_file.write(chunk)
            part_files.append(part_path)
            index += 1

    try:
        for part_path in part_files:
            zip_parts.append(make_zip_part(part_path, password))
    finally:
        for part_path in part_files:
            try:
                if part_path.exists():
                    part_path.unlink()
            except Exception:
                pass

    return zip_parts

def download_url(task: dict) -> Path:
    url = task.get("url", "").strip()
    if not url:
        raise RuntimeError("URL خالیه")

    push_status(task, "در حال دانلود ...", "downloading", 0)

    try:
        resp = requests.get(url, stream=True, timeout=(10, 60), allow_redirects=True)
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError("لینک جواب نداد")
    except requests.exceptions.ConnectionError:
        raise RuntimeError("مشکل شبکه")
    except requests.exceptions.HTTPError as e:
        code = e.response.status_code if e.response else "نامشخص"
        raise RuntimeError(f"دانلود انجام نشد. کد خطا: {code}")
    
    cd = resp.headers.get("content-disposition", "")
    match = re.findall(r'filename="(.+?)"', cd)
    name = match[0] if match else Path(urlparse(url).path).name
    name = safe_filename(name or f"file_{int(time.time())}")
    if "." not in name:
        name += ".bin"

    target = unique_path(URL_DIR / name)
    total = int(resp.headers.get("content-length") or 0)
    downloaded, last_update, started = 0, 0, time.time()

    with open(target, "wb") as f:
        for chunk in resp.iter_content(1024 * 1024):
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)

            now = time.time()
            if now - last_update < 3 and downloaded < total:
                continue
            last_update = now

            speed = downloaded / max(now - started, 1)
            eta = (total - downloaded) / speed if total and speed else None
            percent = downloaded * 100 / total if total else None

            text = f"داره دانلود میکنه...\n\n{pretty_size(downloaded)}"
            if total:
                text += f" از {pretty_size(total)}"
            text += f"\nسرعت: {pretty_size(speed)}/s"
            if eta:
                text += f"\nمونده: {eta_text(eta)}"

            push_status(task, text, "downloading", percent)

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError("فایل دانلود نشد")

    task["file_name"] = target.name
    task["file_size"] = target.stat().st_size
    inc_metric("url_downloads", 1)
    return target

def make_zip_with_password(file_path: Path, password: str) -> Path:
    zip_path = unique_path(file_path.with_suffix(file_path.suffix + ".zip"))

    with pyzipper.AESZipFile(
        zip_path,
        "w",
        compression=pyzipper.ZIP_STORED,
        encryption=pyzipper.WZ_AES,
    ) as zip_file:
        zip_file.setpassword(password.encode("utf-8"))
        zip_file.write(file_path, arcname=file_path.name)

    return zip_path

def pop_first_task():
    if not QUEUE_FILE.exists():
        return None

    with open(QUEUE_FILE, "r", encoding="utf-8") as file:
        lines = [line for line in file if line.strip()]

    if not lines:
        return None

    selected_task = None
    selected_index = None
    selected_priority = None
    for index, line in enumerate(lines):
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        enqueue_time = float(item.get("enqueued_at", 0) or 0)
        # اولویت با زمان enqueue کمتر (قدیمی‌تر) است؛ در تساوی ترتیب خط حفظ می‌شود.
        priority = (enqueue_time if enqueue_time > 0 else float("inf"), index)
        if selected_priority is None or priority < selected_priority:
            selected_priority = priority
            selected_task = item
            selected_index = index

    if selected_task is None:
        with open(QUEUE_FILE, "w", encoding="utf-8") as file:
            file.write("")
        return None

    remaining = lines[:selected_index] + lines[selected_index + 1:]

    with open(QUEUE_FILE, "w", encoding="utf-8") as file:
        file.writelines(remaining)

    return selected_task


def save_processing(task: dict) -> None:
    with open(PROCESSING_FILE, "w", encoding="utf-8") as file:
        json.dump(task, file, ensure_ascii=False, indent=2)


def clear_processing() -> None:
    if PROCESSING_FILE.exists():
        PROCESSING_FILE.unlink()


def append_failed(task: dict, error: str) -> None:
    payload = {"task": task, "error": error}
    with open(FAILED_FILE, "a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")

def process_task(task: dict):
    task_type = task.get("type")
    caption = (task.get("caption") or "").strip()[:512]
    if not caption:
        caption = None
    safe_mode = task.get("safe_mode", False)
    zip_password = task.get("zip_password", "")
    destination = (task.get("destination") or "rubika").lower()

    local_path: Path | None = None
    cleanup_paths: list[Path] = []

    if task_type == "local_file":
        local_path = Path(task.get("path", ""))

        if not local_path.exists():
            raise RuntimeError("Local file not found.")

    elif task_type == "direct_url":
        local_path = download_url(task)
        cleanup_paths.append(local_path)

    else:
        raise RuntimeError("Unknown task type.")

    if safe_mode and zip_password:
        push_status(task, "در حال تبدیل به فایل zip ...", "processing")
        zipped = make_zip_with_password(local_path, zip_password)
        cleanup_paths.append(zipped)

        send_path = zipped

    else:
        send_path = local_path

    try:
        if is_cancelled(task):
            raise RuntimeError("ارسال لغو شد.")

        send_targets: list[Path] = [send_path]
        split_password = zip_password if (safe_mode and zip_password) else ""
        if send_path.stat().st_size > SPLIT_THRESHOLD_BYTES:
            push_status(
                task,
                "⚠️ فایل بزرگ‌تر از 1.5GB است.\n"
                "در حال تقسیم به پارت‌های zip 500MB برای ارسال...",
                "processing",
            )
            send_targets = split_to_zip_parts(send_path, SPLIT_PART_BYTES, split_password)
            cleanup_paths.extend(send_targets)

        for index, target_path in enumerate(send_targets, start=1):
            part_caption = caption
            if len(send_targets) > 1:
                part_caption = f"{caption or ''}\nPart {index}/{len(send_targets)}".strip()

            if destination in ("rubika", "both"):
                task["phase"] = "rubika"
                push_status(
                    task,
                    f"🟦 شروع ارسال به روبیکا...\n\n"
                    f"فایل: `{target_path.name}`\n"
                    f"حجم: `{pretty_size(target_path.stat().st_size)}`"
                    + (f"\nپارت: `{index}/{len(send_targets)}`" if len(send_targets) > 1 else ""),
                    "uploading",
                )
                send_with_retry(str(target_path), part_caption, task)
                inc_metric("rubika_uploads", 1)
                push_status(
                    task,
                    f"✅ ارسال روبیکا انجام شد."
                    + (f"\nپارت: `{index}/{len(send_targets)}`" if len(send_targets) > 1 else ""),
                    "uploading",
                )

            if destination in ("bale", "both"):
                size_bytes = target_path.stat().st_size
                if size_bytes > BALE_MAX_FILE_SIZE:
                    raise RuntimeError(
                        f"اندازه فایل برای بله بیش از حد مجاز است "
                        f"({pretty_size(size_bytes)} > {pretty_size(BALE_MAX_FILE_SIZE)})."
                    )
                bale_targets = task.get("bale_targets")
                target_ids: list[str] = []
                if isinstance(bale_targets, list) and bale_targets:
                    target_ids = [str(item) for item in bale_targets if str(item).strip()]
                elif task.get("bale_send_all"):
                    target_ids = [str(uid) for uid in get_bale_approved_users()]
                elif BALE_ADMIN_CHAT_ID:
                    target_ids = [str(BALE_ADMIN_CHAT_ID)]

                if not target_ids:
                    raise RuntimeError("گیرنده‌ای برای ارسال به بله تعیین نشده است.")

                for bale_chat_id in target_ids:
                    task["phase"] = "bale"
                    push_status(
                        task,
                        f"🟨 شروع ارسال به بله...\n\n"
                        f"گیرنده: `{bale_chat_id}`\n"
                        f"فایل: `{target_path.name}`\n"
                        f"حجم: `{pretty_size(size_bytes)}`"
                        + (f"\nپارت: `{index}/{len(send_targets)}`" if len(send_targets) > 1 else ""),
                        "uploading",
                    )
                    try:
                        send_bale_with_retry(str(target_path), bale_chat_id, part_caption, task)
                    except Exception as e:
                        push_status(
                            task,
                            f"❌ ارسال به بله ناموفق شد.\n\n"
                            f"گیرنده: `{bale_chat_id}`\n"
                            f"علت: `{str(e)}`",
                            "failed",
                        )
                        raise
                    inc_metric("bale_uploads", 1)
                    push_status(
                        task,
                        f"✅ ارسال بله انجام شد.\n\n"
                        f"گیرنده: `{bale_chat_id}`\n"
                        + (f"پارت: `{index}/{len(send_targets)}`" if len(send_targets) > 1 else ""),
                        "uploading",
                    )

        inc_metric("missions_success", 1)
        if destination == "both":
            final_text = "✅ فایل به روبیکا و بله ارسال شد."
        elif destination == "rubika":
            final_text = "✅ فایل به روبیکا ارسال شد."
        elif destination == "bale":
            final_text = "✅ فایل به بله ارسال شد."
        else:
            final_text = "✅ فایل ارسال شد."
        push_status(task, final_text, "done")

    finally:
        for path in cleanup_paths:
            try:
                if path and path.exists():
                    path.unlink()
            except Exception:
                pass

def worker_loop():
    ensure_session()
    print("Rubika worker started.")

    while True:
        task = pop_first_task()

        if not task:
            time.sleep(0.2)
            continue

        save_processing(task)

        try:
            process_task(task)
        except Exception as e:
            inc_metric("missions_failed", 1)
            append_failed(task, str(e))
            push_status(task, f"خطا: {str(e)}", "failed")
        finally:
            clear_processing()

if __name__ == "__main__":
    if "--init-session" in sys.argv:
        ensure_session()
        print("Rubika session initialization completed.")
    else:
        worker_loop()
