import os
import re
import json
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BotCommand
import asyncio
import time
from urllib.parse import urlparse
from pyrogram import idle

load_dotenv()

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_TELEGRAM_ID = int(os.getenv("ADMIN_TELEGRAM_ID", "0"))
BALE_ADMIN_CHAT_ID = os.getenv("BALE_ADMIN_CHAT_ID", "").strip()

BASE_DIR = Path(__file__).resolve().parent
DOWNLOAD_DIR = BASE_DIR / "downloads"
QUEUE_DIR = BASE_DIR / "queue"
QUEUE_FILE = QUEUE_DIR / "tasks.jsonl"
STATUS_FILE = QUEUE_DIR / "status.jsonl"
SETTINGS_FILE = QUEUE_DIR / "settings.json"
DELETED_FILE = QUEUE_DIR / "deleted.jsonl"
CANCEL_FILE = QUEUE_DIR / "cancelled.jsonl"
APPROVED_USERS_FILE = QUEUE_DIR / "approved_users.json"
ACCESS_REQUESTS_FILE = QUEUE_DIR / "access_requests.json"
PENDING_DEST_FILE = QUEUE_DIR / "pending_destinations.json"
BALE_APPROVED_USERS_FILE = QUEUE_DIR / "bale_approved_users.json"
METRICS_FILE = QUEUE_DIR / "metrics.json"
BALE_MAX_FILE_SIZE = 50 * 1024 * 1024
SPLIT_THRESHOLD_BYTES = int(1.5 * 1024 * 1024 * 1024)
SPLIT_PART_BYTES = 500 * 1024 * 1024

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

if not API_ID or not API_HASH or not BOT_TOKEN or not ADMIN_TELEGRAM_ID:
    raise RuntimeError("Please set API_ID, API_HASH, BOT_TOKEN and ADMIN_TELEGRAM_ID in .env")

app = Client(
    "tel2rub",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
)


def safe_filename(name: Optional[str]) -> str:
    name = (name or "file.bin").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", name)
    name = name.rstrip(". ")
    return name[:200] or "file.bin"


def split_name(filename: str) -> tuple[str, str]:
    path = Path(filename)
    return path.stem, path.suffix


def get_media(message: Message):
    media_types = [
        ("document", message.document),
        ("video", message.video),
        ("audio", message.audio),
        ("voice", message.voice),
        ("photo", message.photo),
        ("animation", message.animation),
        ("video_note", message.video_note),
        ("sticker", message.sticker),
    ]

    for media_type, media in media_types:
        if media:
            return media_type, media

    return None, None


def build_download_filename(message: Message, media_type: str, media) -> str:
    original_name = getattr(media, "file_name", None)

    if not original_name:
        file_unique_id = getattr(media, "file_unique_id", None) or "file"

        default_extensions = {
            "document": ".bin",
            "video": ".mp4",
            "audio": ".mp3",
            "voice": ".ogg",
            "photo": ".jpg",
            "animation": ".mp4",
            "video_note": ".mp4",
            "sticker": ".webp",
        }

        original_name = f"{file_unique_id}{default_extensions.get(media_type, '.bin')}"

    original_name = safe_filename(original_name)
    stem, suffix = split_name(original_name)

    unique_name = f"{stem}_{message.id}{suffix or '.bin'}"
    return safe_filename(unique_name)

waiting_for_zip_password = False
waiting_password_for_user_id: int | None = None


def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📤 ارسال فایل", callback_data="admin_send_file")],
            [InlineKeyboardButton("📝 درخواست‌ها", callback_data="admin_requests")],
            [InlineKeyboardButton("📊 وضعیت ربات", callback_data="admin_status")],
            [InlineKeyboardButton("👥 کاربران تلگرام", callback_data="admin_users")],
            [InlineKeyboardButton("🟨👥 کاربران بله", callback_data="admin_bale_users")],
            [InlineKeyboardButton("⚙️ Safe Mode", callback_data="admin_safemode_help")],
        ]
    )


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


def load_bale_approved_users() -> list[int]:
    result: list[int] = []
    try:
        if BALE_APPROVED_USERS_FILE.exists():
            data = json.loads(BALE_APPROVED_USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    try:
                        result.append(int(item))
                    except Exception:
                        continue
    except Exception:
        pass

    try:
        if BALE_ADMIN_CHAT_ID and BALE_ADMIN_CHAT_ID.isdigit():
            result.append(int(BALE_ADMIN_CHAT_ID))
    except Exception:
        pass
    return sorted(set(result))


def save_bale_approved_users(user_ids: list[int]) -> None:
    BALE_APPROVED_USERS_FILE.write_text(
        json.dumps(sorted(set(int(u) for u in user_ids)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_admin_users_keyboard(user_ids: list[int], prefix: str) -> InlineKeyboardMarkup:
    rows = []
    for uid in user_ids[:20]:
        rows.append([InlineKeyboardButton(f"🗑 حذف {uid}", callback_data=f"{prefix}_del_{uid}")])
    rows.append([InlineKeyboardButton("↩️ بازگشت", callback_data="admin_status")])
    return InlineKeyboardMarkup(rows)


def load_approved_users() -> set[int]:
    users = {ADMIN_TELEGRAM_ID}
    try:
        if APPROVED_USERS_FILE.exists():
            data = json.loads(APPROVED_USERS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    try:
                        users.add(int(item))
                    except Exception:
                        continue
    except Exception:
        pass
    return users


def save_approved_users(users: set[int]) -> None:
    APPROVED_USERS_FILE.write_text(
        json.dumps(sorted(users), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_access_requests() -> dict[str, dict]:
    try:
        if ACCESS_REQUESTS_FILE.exists():
            data = json.loads(ACCESS_REQUESTS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_access_requests(data: dict[str, dict]) -> None:
    ACCESS_REQUESTS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_approved_user(user_id: int) -> bool:
    return user_id in load_approved_users()


async def require_approved_user(message: Message) -> bool:
    user = message.from_user
    if not user:
        return False
    if is_approved_user(user.id):
        return True
    await message.reply_text(
        "شما هنوز تایید نشده‌اید.\n"
        "برای ارسال درخواست دسترسی، روی دکمه زیر بزنید.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ درخواست دسترسی", callback_data="request_access")]]
        ),
    )
    return False


def build_admin_request_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ تایید", callback_data=f"approve_{user_id}"),
                InlineKeyboardButton("❌ رد", callback_data=f"reject_{user_id}"),
            ]
        ]
    )


def safe_user_display_name(user) -> str:
    if not user:
        return "Unknown"
    parts = [user.first_name or "", user.last_name or ""]
    full_name = " ".join(p for p in parts if p).strip()
    if full_name:
        return full_name
    return user.username or str(user.id)


def destination_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🟦📤 ارسال به روبیکا", callback_data=f"dest_rubika_{draft_id}")],
            [InlineKeyboardButton("🟨📤 ارسال به بله", callback_data=f"dest_bale_{draft_id}")],
            [InlineKeyboardButton("🟩📤 ارسال به هر دو", callback_data=f"dest_both_{draft_id}")],
        ]
    )


def force_rubika_keyboard(draft_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔵✅ تغییر مقصد به روبیکا", callback_data=f"dest_rubika_{draft_id}")],
            [InlineKeyboardButton("🟨↩️ برگشت به انتخاب مقصد", callback_data=f"dest_menu_{draft_id}")],
        ]
    )


def bale_target_keyboard(draft_id: str, user_ids: list[int]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton("🟢 ارسال به همه تاییدشده‌های بله", callback_data=f"btarget_all_{draft_id}")]]
    rows.append([InlineKeyboardButton("👤 ارسال به مدیر بله", callback_data=f"btarget_admin_{draft_id}")])
    admin_id = int(BALE_ADMIN_CHAT_ID) if BALE_ADMIN_CHAT_ID.isdigit() else None
    visible_users = [uid for uid in user_ids if uid != admin_id]
    for uid in visible_users[:10]:
        rows.append([InlineKeyboardButton(f"👤 ارسال به {uid}", callback_data=f"btarget_user_{uid}_{draft_id}")])
    rows.append([InlineKeyboardButton("↩️ بازگشت به انتخاب مقصد", callback_data=f"dest_menu_{draft_id}")])
    return InlineKeyboardMarkup(rows)


def load_pending_destinations() -> dict[str, dict]:
    try:
        if PENDING_DEST_FILE.exists():
            data = json.loads(PENDING_DEST_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_pending_destinations(data: dict[str, dict]) -> None:
    PENDING_DEST_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_pending_draft(task: dict, owner_user_id: int) -> str:
    draft_id = str(int(time.time() * 1000))
    data = load_pending_destinations()
    data[draft_id] = {
        "owner_user_id": owner_user_id,
        "task": task,
        "created_at": int(time.time()),
    }
    save_pending_destinations(data)
    return draft_id

class QueueManager:
    def __init__(self):
        self._cache = None
        self._mtime = 0

    def all(self):
        mtime = QUEUE_FILE.stat().st_mtime if QUEUE_FILE.exists() else 0
        if mtime == self._mtime and self._cache is not None:
            return self._cache
        self._cache = []
        if QUEUE_FILE.exists():
            with open(QUEUE_FILE, "r", encoding="utf-8") as f:
                self._cache = [json.loads(l) for l in f if l.strip()]
        self._mtime = mtime
        return self._cache

    def push(self, task):
        task.setdefault("job_id", str(int(time.time() * 1000)))
        with open(QUEUE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(task, ensure_ascii=False) + "\n")
        self._cache = None

    def remove(self, job_id=None, message_id=None):
        tasks = self.all()
        kept, removed = [], None
        for t in tasks:
            if (job_id and str(t.get("job_id")) == str(job_id)) or \
               (message_id and int(t.get("status_message_id", 0)) == message_id):
                removed = t
            else:
                kept.append(t)
        if removed:
            with open(QUEUE_FILE, "w", encoding="utf-8") as f:
                f.writelines(json.dumps(t, ensure_ascii=False) + "\n" for t in kept)
            self._cache = None
        return removed


queue = QueueManager()

def mark_deleted(task: dict):
    with open(DELETED_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(task, ensure_ascii=False) + "\n")

def mark_cancelled(task: dict):
    with open(CANCEL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(task, ensure_ascii=False) + "\n")

def cancel_job(job_id: str):
    with open(CANCEL_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"job_id": str(job_id)}, ensure_ascii=False) + "\n")

def was_deleted(job_id=None, message_id=None) -> bool:
    if not DELETED_FILE.exists():
        return False
    with open(DELETED_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            item = json.loads(line)
            if job_id and str(item.get("job_id")) == str(job_id):
                return True
            if message_id and int(item.get("status_message_id", 0)) == message_id:
                return True
    return False

def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass

    return {"safe_mode": False, "zip_password": "", "user_settings": {}}

def save_settings(data: dict):
    SETTINGS_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def get_user_settings(user_id: int) -> dict:
    settings = load_settings()
    user_settings_map = settings.get("user_settings")
    if not isinstance(user_settings_map, dict):
        user_settings_map = {}

    user_settings = user_settings_map.get(str(user_id))
    if not isinstance(user_settings, dict):
        user_settings = {}

    safe_mode = bool(user_settings.get("safe_mode", settings.get("safe_mode", False)))
    zip_password = str(user_settings.get("zip_password", settings.get("zip_password", "")))
    return {"safe_mode": safe_mode, "zip_password": zip_password}


def update_user_settings(user_id: int, safe_mode: bool, zip_password: str = "") -> None:
    settings = load_settings()
    user_settings_map = settings.get("user_settings")
    if not isinstance(user_settings_map, dict):
        user_settings_map = {}

    user_settings_map[str(user_id)] = {
        "safe_mode": bool(safe_mode),
        "zip_password": zip_password or "",
    }
    settings["user_settings"] = user_settings_map
    save_settings(settings)

def is_direct_url(text: str) -> bool:
    if not text:
        return False

    url = extract_first_url(text)
    if not url:
        return False

    try:
        parsed = urlparse(url)
    except Exception:
        return False

    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def extract_first_url(text: str) -> Optional[str]:
    if not text:
        return None

    match = re.search(r"https?://\S+", text)
    return match.group(0) if match else None


def progress_bar(percent: float, length: int = 12) -> str:
    filled = int(length * percent / 100)
    return "█" * filled + "░" * (length - filled)


def pretty_size(size) -> str:
    size = float(size or 0)
    units = ["B", "KB", "MB", "GB"]

    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1

    return f"{size:.2f} {units[index]}"


def build_size_warnings(task: dict, destination: str) -> list[str]:
    warnings: list[str] = []
    file_size = int(task.get("file_size") or 0)
    if not file_size:
        if destination in ("bale", "both"):
            warnings.append(
                "⚠️ در بله ارسال فایل فقط تا 50MB امکان‌پذیر است."
            )
        return warnings

    if file_size > SPLIT_THRESHOLD_BYTES:
        warnings.append(
            f"فایل بزرگ‌تر از {pretty_size(SPLIT_THRESHOLD_BYTES)} است و به پارت‌های zip "
            f"{pretty_size(SPLIT_PART_BYTES)} تقسیم می‌شود."
        )

    if destination in ("bale", "both") and file_size > BALE_MAX_FILE_SIZE:
        warnings.append(
            f"⚠️ در بله ارسال فایل فقط تا 50MB امکان‌پذیر است.\n"
            f"این فایل "
            f"{pretty_size(file_size)} است. ارسال به بله انجام نخواهد شد."
        )
    return warnings


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


async def download_progress(current, total, status_message, file_name, started_at, state):
    now = time.time()

    if now - state.get("last_update", 0) < 3 and current < total:
        return

    state["last_update"] = now

    percent = current * 100 / total if total else 0
    elapsed = max(now - started_at, 1)
    speed = current / elapsed
    eta = (total - current) / speed if speed else None

    text = (
        f"📥 در حال دریافت فایل از تلگرام\n\n"
        f"فایل: `{file_name}`\n"
        f"حجم: `{pretty_size(total)}`\n"
        f"پیشرفت: `{percent:.1f}%`\n"
        f"`{progress_bar(percent)}`\n"
        f"سرعت: `{pretty_size(speed)}/s`\n"
        f"زمان باقی‌مانده: `{eta_text(eta)}`"
    )

    try:
        await status_message.edit_text(text)
    except Exception:
        pass

async def status_watcher():
    pos = 0
    last_edit: dict[tuple[int, int], float] = {}
    last_text: dict[tuple[int, int], str] = {}
    while True:
        await asyncio.sleep(1)
        if not STATUS_FILE.exists():
            continue
        try:
            with open(STATUS_FILE, "r", encoding="utf-8") as f:
                f.seek(pos)
                lines = f.readlines()
                pos = f.tell()
            for line in lines:
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                chat_id = data.get("chat_id")
                msg_id = data.get("message_id")
                text = data.get("text", "")
                percent = data.get("percent")
                if not chat_id or not msg_id:
                    continue
                key = (int(chat_id), int(msg_id))
                now = time.time()
                if now - last_edit.get(key, 0) < 1.2:
                    continue

                if percent is not None:
                    try:
                        p = float(percent)
                        if 0 <= p <= 100:
                            text += f"\n\n`{progress_bar(p)}` `{p:.1f}%`"
                    except Exception:
                        pass

                if last_text.get(key) == text:
                    continue
                try:
                    await app.edit_message_text(chat_id, msg_id, text)
                    last_edit[key] = now
                    last_text[key] = text
                except Exception:
                    pass
        except Exception:
            pass

@app.on_message(filters.private & filters.command("start"))
async def start_handler(client: Client, message: Message):
    user = message.from_user
    if not user:
        return

    if user.id == ADMIN_TELEGRAM_ID:
        await message.reply_text(
            "پنل مدیریت TG2RUB\n\n"
            "از دکمه‌های زیر برای مدیریت ربات استفاده کنید.",
            reply_markup=admin_panel_keyboard(),
        )
        return

    if is_approved_user(user.id):
        await message.reply_text(
            "✅ دسترسی شما تایید شده است.\n\n"
            "می‌توانید فایل یا لینک مستقیم ارسال کنید."
        )
        return

    await message.reply_text(
        "این ربات خصوصی است و نیاز به تایید مدیر دارد.\n\n"
        "اگر مایلید، درخواست دسترسی ارسال کنید.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ ارسال درخواست دسترسی", callback_data="request_access")]]
        ),
    )


@app.on_callback_query(filters.regex("^request_access$"))
async def request_access_handler(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    if not user:
        await callback_query.answer("خطا در دریافت اطلاعات کاربر.", show_alert=True)
        return

    if is_approved_user(user.id):
        await callback_query.answer("شما قبلا تایید شده‌اید.", show_alert=True)
        return

    requests = load_access_requests()
    requests[str(user.id)] = {
        "user_id": user.id,
        "username": user.username or "",
        "name": safe_user_display_name(user),
        "requested_at": int(time.time()),
        "status": "pending",
    }
    save_access_requests(requests)

    username_text = f"@{user.username}" if user.username else "ندارد"
    request_text = (
        "درخواست دسترسی جدید:\n\n"
        f"نام: {safe_user_display_name(user)}\n"
        f"آیدی عددی: `{user.id}`\n"
        f"یوزرنیم: {username_text}"
    )
    await client.send_message(
        ADMIN_TELEGRAM_ID,
        request_text,
        reply_markup=build_admin_request_keyboard(user.id),
    )
    if callback_query.message:
        await callback_query.message.edit_text(
            "درخواست شما برای مدیر ارسال شد.\n"
            "پس از تایید، امکان استفاده از ربات فعال می‌شود."
        )
    await callback_query.answer("درخواست ارسال شد.")


@app.on_callback_query(filters.regex(r"^(approve|reject)_\d+$"))
async def access_decision_handler(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user is None or callback_query.from_user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط مدیر می‌تواند این عملیات را انجام دهد.", show_alert=True)
        return

    data = callback_query.data or ""
    action, user_id_text = data.split("_", 1)
    user_id = int(user_id_text)

    requests = load_access_requests()
    req = requests.get(str(user_id))
    if not req:
        await callback_query.answer("این درخواست یافت نشد.", show_alert=True)
        return

    if action == "approve":
        approved = load_approved_users()
        approved.add(user_id)
        save_approved_users(approved)
        req["status"] = "approved"
        requests[str(user_id)] = req
        save_access_requests(requests)

        await client.send_message(user_id, "✅ درخواست شما تایید شد. حالا می‌توانید از ربات استفاده کنید.")
        await callback_query.message.edit_text(
            f"درخواست کاربر `{user_id}` تایید شد."
        )
        await callback_query.answer("کاربر تایید شد.")
        return

    req["status"] = "rejected"
    requests[str(user_id)] = req
    save_access_requests(requests)
    await client.send_message(user_id, "❌ درخواست شما توسط مدیر رد شد.")
    await callback_query.message.edit_text(
        f"درخواست کاربر `{user_id}` رد شد."
    )
    await callback_query.answer("درخواست رد شد.")


@app.on_callback_query(filters.regex(r"^admin_(send_file|requests|status|users|bale_users|safemode_help)$"))
async def admin_panel_callbacks(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user is None or callback_query.from_user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط مدیر می‌تواند این عملیات را انجام دهد.", show_alert=True)
        return

    data = callback_query.data or ""

    if data == "admin_send_file":
        await callback_query.message.edit_text(
            "📤 حالت ارسال فایل فعال است.\n\n"
            "یک فایل یا لینک مستقیم به همین چت بفرستید تا وارد صف شود.",
            reply_markup=admin_panel_keyboard(),
        )
        await callback_query.answer()
        return

    if data == "admin_requests":
        requests = load_access_requests()
        pending = [r for r in requests.values() if r.get("status") == "pending"]
        approved_count = len([r for r in requests.values() if r.get("status") == "approved"])
        rejected_count = len([r for r in requests.values() if r.get("status") == "rejected"])
        pending_preview = "\n".join(
            [
                f"- `{r.get('user_id')}` | {r.get('name', 'Unknown')}"
                for r in pending[:10]
            ]
        ) or "- موردی وجود ندارد"
        await callback_query.message.edit_text(
            "📝 وضعیت درخواست‌ها\n\n"
            f"در انتظار: `{len(pending)}`\n"
            f"تایید شده: `{approved_count}`\n"
            f"رد شده: `{rejected_count}`\n\n"
            f"آخرین درخواست‌های در انتظار:\n{pending_preview}",
            reply_markup=admin_panel_keyboard(),
        )
        await callback_query.answer()
        return

    if data == "admin_status":
        tasks = queue.all()
        approved_users = load_approved_users()
        settings = load_settings()
        requests = load_access_requests()
        metrics = load_metrics()
        all_known_users = set(str(k) for k in requests.keys())
        for uid in approved_users:
            all_known_users.add(str(uid))
        total_users = len(all_known_users)

        downloads_count = int(metrics.get("tg_downloads", 0)) + int(metrics.get("url_downloads", 0))
        uploads_count = int(metrics.get("rubika_uploads", 0)) + int(metrics.get("bale_uploads", 0))
        approved_non_admin = sorted([uid for uid in approved_users if uid != ADMIN_TELEGRAM_ID])
        approved_preview = "\n".join(
            [
                f"- `{uid}` | {requests.get(str(uid), {}).get('name', 'Unknown')}"
                for uid in approved_non_admin[:20]
            ]
        ) or "- موردی وجود ندارد"
        users_with_safemode = 0
        user_settings = settings.get("user_settings")
        if isinstance(user_settings, dict):
            for item in user_settings.values():
                if isinstance(item, dict) and item.get("safe_mode"):
                    users_with_safemode += 1

        bale_users = load_bale_approved_users()
        await callback_query.message.edit_text(
            "📊 وضعیت ربات\n\n"
            f"در صف: `{len(tasks)}`\n"
            f"کاربران تاییدشده: `{max(len(approved_users) - 1, 0)}`\n"
            f"کاربران کل (شناخته‌شده): `{total_users}`\n"
            f"کاربران تاییدشده بله: `{len(bale_users)}`\n\n"
            f"دانلودها: `{downloads_count}` (TG: `{metrics.get('tg_downloads',0)}` | URL: `{metrics.get('url_downloads',0)}`)\n"
            f"آپلودها: `{uploads_count}` (Rubika: `{metrics.get('rubika_uploads',0)}` | Bale: `{metrics.get('bale_uploads',0)}`)\n"
            f"ماموریت موفق: `{metrics.get('missions_success',0)}`\n"
            f"ماموریت ناموفق: `{metrics.get('missions_failed',0)}`\n\n"
            f"کاربران با Safe Mode فعال: `{users_with_safemode}`\n\n"
            f"لیست کاربران تاییدشده:\n{approved_preview}",
            reply_markup=admin_panel_keyboard(),
        )
        await callback_query.answer()
        return

    if data == "admin_users":
        approved = load_approved_users()
        reqs = load_access_requests()
        approved_non_admin = sorted([uid for uid in approved if uid != ADMIN_TELEGRAM_ID])
        preview = "\n".join(
            [f"- `{uid}` | {reqs.get(str(uid), {}).get('name', 'Unknown')}" for uid in approved_non_admin[:50]]
        ) or "- موردی وجود ندارد"
        await callback_query.message.edit_text(
            "👥 کاربران تاییدشده تلگرام\n\n"
            f"تعداد: `{len(approved_non_admin)}`\n\n"
            f"{preview}",
            reply_markup=build_admin_users_keyboard(approved_non_admin, "tuser"),
        )
        await callback_query.answer()
        return

    if data == "admin_bale_users":
        bale_users = load_bale_approved_users()
        preview = "\n".join([f"- `{uid}`" for uid in bale_users[:50]]) or "- موردی وجود ندارد"
        await callback_query.message.edit_text(
            "🟨👥 کاربران تاییدشده بله\n\n"
            f"تعداد: `{len(bale_users)}`\n\n"
            f"{preview}",
            reply_markup=build_admin_users_keyboard(bale_users, "buser"),
        )
        await callback_query.answer()
        return

    await callback_query.message.edit_text(
        "⚙️ مدیریت Safe Mode\n\n"
        "هر کاربر تاییدشده می‌تواند Safe Mode شخصی خود را تنظیم کند.\n\n"
        "برای تنظیم Safe Mode از دستورات زیر استفاده کنید:\n"
        "`/safemode on`\n"
        "`/safemode off`",
        reply_markup=admin_panel_keyboard(),
    )
    await callback_query.answer()


@app.on_callback_query(filters.regex(r"^(tuser|buser)_del_\d+$"))
async def admin_delete_user_callback(client: Client, callback_query: CallbackQuery):
    if callback_query.from_user is None or callback_query.from_user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط مدیر می‌تواند این عملیات را انجام دهد.", show_alert=True)
        return

    data = callback_query.data or ""
    prefix, _, user_id_text = data.split("_", 2)
    user_id = int(user_id_text)

    if prefix == "tuser":
        if user_id == ADMIN_TELEGRAM_ID:
            await callback_query.answer("حذف مدیر مجاز نیست.", show_alert=True)
            return
        approved = load_approved_users()
        if user_id not in approved:
            await callback_query.answer("این کاربر در لیست تاییدشده نیست.", show_alert=True)
            return
        approved.remove(user_id)
        save_approved_users(approved)
        await callback_query.answer("کاربر تلگرام حذف شد.")
        await callback_query.message.edit_text(
            "✅ کاربر حذف شد.\n\nبرای مشاهده لیست جدید «👥 کاربران تلگرام» را بزنید.",
            reply_markup=admin_panel_keyboard(),
        )
        return

    bale_users = load_bale_approved_users()
    if BALE_ADMIN_CHAT_ID and BALE_ADMIN_CHAT_ID.isdigit() and int(BALE_ADMIN_CHAT_ID) == user_id:
        await callback_query.answer("حذف مدیر بله مجاز نیست.", show_alert=True)
        return
    if user_id not in bale_users:
        await callback_query.answer("این کاربر در لیست بله نیست.", show_alert=True)
        return
    bale_users = [uid for uid in bale_users if uid != user_id]
    save_bale_approved_users(bale_users)
    await callback_query.answer("کاربر بله حذف شد.")
    await callback_query.message.edit_text(
        "✅ کاربر بله حذف شد.\n\nبرای مشاهده لیست جدید «🟨👥 کاربران بله» را بزنید.",
        reply_markup=admin_panel_keyboard(),
    )


@app.on_callback_query(filters.regex(r"^dest_(rubika|bale|both)_\d+$"))
async def destination_select_handler(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    if not user:
        await callback_query.answer("خطا در دریافت اطلاعات کاربر.", show_alert=True)
        return

    data = callback_query.data or ""
    _, destination, draft_id = data.split("_", 2)
    drafts = load_pending_destinations()
    draft = drafts.get(draft_id)
    if not draft:
        await callback_query.answer("این درخواست منقضی یا حذف شده است.", show_alert=True)
        return

    owner_user_id = int(draft.get("owner_user_id", 0))
    if user.id != owner_user_id and user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط صاحب فایل می‌تواند مقصد را انتخاب کند.", show_alert=True)
        return

    task = draft.get("task", {})
    size_warnings = build_size_warnings(task, destination)
    if destination in ("bale", "both"):
        file_size = int(task.get("file_size") or 0)
        if file_size and file_size > BALE_MAX_FILE_SIZE:
            warning_text = "\n".join(size_warnings) if size_warnings else "اندازه فایل برای بله بیش از حد مجاز است."
            await callback_query.answer(warning_text, show_alert=True)
            await callback_query.message.edit_text(
                "🚫 ارسال به بله برای این فایل ممکن نیست.\n"
                "⚠️ در بله ارسال فایل فقط تا 50MB امکان‌پذیر است.\n\n"
                "برای ادامه، از دکمه آبی زیر مقصد را به روبیکا تغییر بده:",
                reply_markup=force_rubika_keyboard(draft_id),
            )
            return

    task["destination"] = destination

    if destination in ("bale", "both"):
        bale_users = load_bale_approved_users()
        if not bale_users:
            await callback_query.answer("هنوز کاربر تاییدشده‌ای در ربات بله وجود ندارد.", show_alert=True)
            await callback_query.message.edit_text(
                "هیچ کاربر تاییدشده‌ای در بله پیدا نشد.\n"
                "ابتدا کاربران را در ربات بله تایید کنید.",
                reply_markup=force_rubika_keyboard(draft_id),
            )
            return
        task["destination"] = destination
        draft["task"] = task
        drafts[draft_id] = draft
        save_pending_destinations(drafts)
        await callback_query.message.edit_text(
            "لطفا گیرنده فایل در بله را انتخاب کنید:",
            reply_markup=bale_target_keyboard(draft_id, bale_users),
        )
        await callback_query.answer()
        return

    queue.push(task)

    drafts.pop(draft_id, None)
    save_pending_destinations(drafts)

    dest_text_map = {
        "rubika": "روبیکا",
        "bale": "بله",
        "both": "روبیکا + بله",
    }
    warning_block = f"هشدار:\n{chr(10).join(size_warnings)}\n\n" if size_warnings else ""
    await callback_query.message.edit_text(
        "در صف قرار گرفت.\n\n"
        f"مقصد: `{dest_text_map.get(destination, destination)}`\n"
        f"{warning_block}"
        f"شناسه: `{task['job_id']}`\n"
        "برای حذف این مورد از صف:\n"
        f"`/del {task['job_id']}`"
    )
    await callback_query.answer("مقصد ثبت شد.")


@app.on_callback_query(filters.regex(r"^dest_menu_\d+$"))
async def destination_menu_handler(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    if not user:
        await callback_query.answer("خطا در دریافت اطلاعات کاربر.", show_alert=True)
        return

    data = callback_query.data or ""
    _, _, draft_id = data.split("_", 2)
    drafts = load_pending_destinations()
    draft = drafts.get(draft_id)
    if not draft:
        await callback_query.answer("این درخواست منقضی یا حذف شده است.", show_alert=True)
        return

    owner_user_id = int(draft.get("owner_user_id", 0))
    if user.id != owner_user_id and user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط صاحب فایل می‌تواند مقصد را انتخاب کند.", show_alert=True)
        return

    await callback_query.message.edit_text(
        "🎯 مقصد ارسال را انتخاب کنید:",
        reply_markup=destination_keyboard(draft_id),
    )
    await callback_query.answer()


@app.on_callback_query(filters.regex(r"^btarget_(all|admin)_\d+$|^btarget_user_\d+_\d+$"))
async def bale_target_handler(client: Client, callback_query: CallbackQuery):
    user = callback_query.from_user
    if not user:
        await callback_query.answer("خطا در دریافت اطلاعات کاربر.", show_alert=True)
        return

    data = callback_query.data or ""
    parts = data.split("_")
    if parts[1] == "user":
        target_user_id = int(parts[2])
        draft_id = parts[3]
        mode = "user"
    else:
        mode = parts[1]
        target_user_id = None
        draft_id = parts[2]

    drafts = load_pending_destinations()
    draft = drafts.get(draft_id)
    if not draft:
        await callback_query.answer("این درخواست منقضی یا حذف شده است.", show_alert=True)
        return

    owner_user_id = int(draft.get("owner_user_id", 0))
    if user.id != owner_user_id and user.id != ADMIN_TELEGRAM_ID:
        await callback_query.answer("فقط صاحب فایل می‌تواند گیرنده را انتخاب کند.", show_alert=True)
        return

    task = draft.get("task", {})
    if mode == "all":
        task["bale_send_all"] = True
        task["bale_targets"] = []
        target_text = "همه کاربران تاییدشده بله"
    elif mode == "admin":
        from bale import BALE_ADMIN_CHAT_ID
        if not BALE_ADMIN_CHAT_ID:
            await callback_query.answer("BALE_ADMIN_CHAT_ID تنظیم نشده است.", show_alert=True)
            return
        task["bale_send_all"] = False
        task["bale_targets"] = [str(BALE_ADMIN_CHAT_ID)]
        target_text = f"مدیر بله (`{BALE_ADMIN_CHAT_ID}`)"
    else:
        task["bale_send_all"] = False
        task["bale_targets"] = [str(target_user_id)]
        target_text = f"`{target_user_id}`"

    queue.push(task)
    drafts.pop(draft_id, None)
    save_pending_destinations(drafts)

    await callback_query.message.edit_text(
        "در صف قرار گرفت.\n\n"
        f"مقصد: `{task.get('destination')}`\n"
        f"گیرنده بله: {target_text}\n"
        f"شناسه: `{task['job_id']}`\n"
        "برای حذف این مورد از صف:\n"
        f"`/del {task['job_id']}`"
    )
    await callback_query.answer("گیرنده بله ثبت شد.")

@app.on_message(filters.private & filters.command("safemode"))
async def safemode_handler(client: Client, message: Message):
    global waiting_for_zip_password, waiting_password_for_user_id
    if message.from_user is None:
        return
    if not await require_approved_user(message):
        return

    args = message.text.split(maxsplit=1)

    if len(args) < 2:
        await message.reply_text("برای تغییر وضعیت Safe Mode از `/safemode on` یا `/safemode off` استفاده کن.")
        return

    action = args[1].strip().lower()
    user_id = message.from_user.id
    current = get_user_settings(user_id)

    if action == "on":
        update_user_settings(user_id, True, current.get("zip_password", ""))
        waiting_for_zip_password = True
        waiting_password_for_user_id = user_id

        await message.reply_text(
            "Safe Mode فعال شد.\n\n"
            "لطفا رمزی که می‌خواهید روی فایل‌های ZIP قرار بگیرد را ارسال کنید.\n"
            "از این به بعد فایل‌ها قبل از ارسال به روبیکا با همین رمز ZIP می‌شوند."
        )
        return

    if action == "off":
        update_user_settings(user_id, False, "")
        waiting_for_zip_password = False
        if waiting_password_for_user_id == user_id:
            waiting_password_for_user_id = None

        await message.reply_text(
            "Safe Mode غیرفعال شد.\n\n"
            "از این به بعد فایل‌ها به‌صورت عادی ارسال می‌شوند."
        )
        return

    await message.reply_text("دستور نامعتبر است. از `/safemode on` یا `/safemode off` استفاده کن.")


@app.on_message(filters.private & filters.command("delall"))
async def clear_queue_handler(client: Client, message: Message):
    if message.from_user is None or message.from_user.id != ADMIN_TELEGRAM_ID:
        await message.reply_text("فقط مدیر می‌تواند صف را پاک کند.")
        return

    tasks = queue.all()

    if not tasks:
        await message.reply_text("صف خالی است.")
        return

    for task in tasks:
        mark_deleted(task)

        old_path = task.get("path")
        if old_path:
            try:
                path = Path(old_path)
                if path.exists():
                    path.unlink()
            except Exception:
                pass

        try:
            await client.edit_message_text(
                chat_id=task["chat_id"],
                message_id=task["status_message_id"],
                text="این مورد از صف حذف شد."
            )
        except Exception:
            pass

    with open(QUEUE_FILE, "w", encoding="utf-8") as f:
        pass
    queue._cache = None
    queue._mtime = 0
    await message.reply_text("تمام موارد در صف پاک شد.")


@app.on_message(filters.private & filters.command("users"))
async def users_handler(client: Client, message: Message):
    if message.from_user is None or message.from_user.id != ADMIN_TELEGRAM_ID:
        await message.reply_text("فقط مدیر می‌تواند این لیست را ببیند.")
        return

    approved = load_approved_users()
    reqs = load_access_requests()
    approved_non_admin = sorted([uid for uid in approved if uid != ADMIN_TELEGRAM_ID])
    preview = "\n".join(
        [f"- `{uid}` | {reqs.get(str(uid), {}).get('name', 'Unknown')}" for uid in approved_non_admin[:50]]
    ) or "- موردی وجود ندارد"
    await message.reply_text(
        "👥 کاربران تاییدشده تلگرام\n\n"
        f"تعداد: `{len(approved_non_admin)}`\n\n"
        f"{preview}",
        reply_markup=build_admin_users_keyboard(approved_non_admin, "tuser"),
    )


@app.on_message(filters.private & filters.command("deluser"))
async def deluser_handler(client: Client, message: Message):
    if message.from_user is None or message.from_user.id != ADMIN_TELEGRAM_ID:
        await message.reply_text("فقط مدیر می‌تواند کاربر حذف کند.")
        return

    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip().isdigit():
        await message.reply_text("فرمت صحیح: `/deluser USER_ID`")
        return

    uid = int(parts[1].strip())
    if uid == ADMIN_TELEGRAM_ID:
        await message.reply_text("حذف مدیر مجاز نیست.")
        return

    approved = load_approved_users()
    if uid not in approved:
        await message.reply_text("این کاربر در لیست تاییدشده نیست.")
        return
    approved.remove(uid)
    save_approved_users(approved)
    await message.reply_text(f"✅ کاربر `{uid}` حذف شد.")

@app.on_message(filters.private & filters.command("del"))
async def delete_one_handler(client: Client, message: Message):
    if not await require_approved_user(message):
        return

    job_id = None
    reply_message_id = None

    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        job_id = parts[1].strip()

    if message.reply_to_message:
        reply_message_id = message.reply_to_message.id

    tasks = queue.all()

    if not tasks:
        if job_id and was_deleted(job_id=job_id):
            await message.reply_text("این مورد قبلاً از صف حذف شده است.")
            return

        if reply_message_id and was_deleted(message_id=reply_message_id):
            await message.reply_text("این مورد قبلاً از صف حذف شده است.")
            return

        if job_id:
            cancel_job(job_id)
            await message.reply_text(
                "لغو ثبت شد.\n\n"
            )
            return

        await message.reply_text("موردی برای حذف در صف پیدا نشد.")
        return

    removed = queue.remove(job_id=job_id, message_id=reply_message_id)

    if removed:
        mark_deleted(removed)

        old_path = removed.get("path")
        if old_path:
            try:
                path = Path(old_path)
                if path.exists():
                    path.unlink()
            except Exception:
                pass

        try:
            await client.edit_message_text(
                chat_id=removed["chat_id"],
                message_id=removed["status_message_id"],
                text="این مورد از صف حذف شد."
            )
        except Exception:
            pass

        await message.reply_text("از صف حذف شد.")
        return

    if job_id and was_deleted(job_id=job_id):
        await message.reply_text("این مورد قبلاً از صف حذف شده است.")
        return

    if reply_message_id and was_deleted(message_id=reply_message_id):
        await message.reply_text("این مورد قبلاً از صف حذف شده است.")
        return

    if job_id:
        cancel_job(job_id)
        await message.reply_text("دستور لغو ثبت شد.") 
        return


@app.on_message(filters.private & filters.text & ~filters.command(["start", "safemode", "del", "delall", "users", "deluser"]))
async def text_handler(client: Client, message: Message):
    global waiting_for_zip_password, waiting_password_for_user_id

    text = message.text or ""

    if waiting_for_zip_password and message.from_user and message.from_user.id == waiting_password_for_user_id:
        password = text.strip()

        if not password:
            await message.reply_text("رمز نمی‌تواند خالی باشد. لطفاً یک رمز معتبر ارسال کنید.")
            return

        update_user_settings(message.from_user.id, True, password)

        waiting_for_zip_password = False
        waiting_password_for_user_id = None

        await message.reply_text(
            "رمز ذخیره شد.\n\n"
            "از این به بعد فایل‌ها قبل از ارسال به روبیکا به‌صورت ZIP رمزدار آماده می‌شوند."
        )
        return

    if not await require_approved_user(message):
        return

    url = extract_first_url(text)

    if not url or not is_direct_url(url):
        return

    user_settings = get_user_settings(message.from_user.id if message.from_user else 0)

    status = await message.reply_text(
        "لینک دریافت شد.\n\n"
        "وضعیت: در صف دانلود قرار گرفت."
    )

    task = {
        "type": "direct_url",
        "url": url,
        "chat_id": message.chat.id,
        "status_message_id": status.id,
        "safe_mode": user_settings.get("safe_mode", False),
        "zip_password": user_settings.get("zip_password", ""),
        "enqueued_at": float(getattr(message, "date", None).timestamp()) if getattr(message, "date", None) else time.time(),
    }
    draft_id = create_pending_draft(task, message.from_user.id if message.from_user else 0)
    await status.edit_text(
        "لینک دریافت شد.\n\n"
        "مقصد ارسال را انتخاب کنید:",
        reply_markup=destination_keyboard(draft_id),
    )

    
@app.on_message(
    filters.private
    & (
        filters.document
        | filters.video
        | filters.audio
        | filters.voice
        | filters.photo
        | filters.animation
        | filters.video_note
        | filters.sticker
    )
)
async def media_handler(client: Client, message: Message):
    if not await require_approved_user(message):
        return

    media_type, media = get_media(message)
    if not media:
        await message.reply_text("فایل قابل پردازش نیست.")
        return

    download_name = build_download_filename(message, media_type, media)
    download_path = DOWNLOAD_DIR / download_name

    status = await message.reply_text(
        "فایل دریافت شد.\n\n"
        "وضعیت: آماده‌سازی برای دانلود از تلگرام..."
    )

    try:
        started_at = time.time()
        progress_state = {"last_update": 0}

        downloaded = await client.download_media(
            message,
            file_name=str(download_path),
            progress=download_progress,
            progress_args=(status, download_name, started_at, progress_state),
        )

        if not downloaded:
            raise RuntimeError("Download failed.")

        downloaded_path = Path(downloaded)
        if not downloaded_path.exists():
            raise RuntimeError("Downloaded file not found.")

        file_size = downloaded_path.stat().st_size
        inc_metric("tg_downloads", 1)
        user_settings = get_user_settings(message.from_user.id if message.from_user else 0)

        task = {
            "type": "local_file",
            "path": str(downloaded_path),
            "caption": message.caption or "",
            "chat_id": message.chat.id,
            "status_message_id": status.id,
            "file_name": download_name,
            "file_size": file_size,
            "safe_mode": user_settings.get("safe_mode", False),
            "zip_password": user_settings.get("zip_password", ""),
            "enqueued_at": float(getattr(message, "date", None).timestamp()) if getattr(message, "date", None) else time.time(),
        }
        draft_id = create_pending_draft(task, message.from_user.id if message.from_user else 0)
        await status.edit_text(
            f"فایل آماده شد.\n\n"
            f"فایل: `{download_name}`\n"
            f"حجم: `{pretty_size(file_size)}`\n\n"
            "مقصد ارسال را انتخاب کنید:",
            reply_markup=destination_keyboard(draft_id),
        )

    except Exception as e:
        await status.edit_text(f"خطا: {str(e)}")

def clear_old_status():
    try:
        if STATUS_FILE.exists():
            STATUS_FILE.unlink()
    except Exception:
        pass


async def setup_bot_commands():
    commands = [
        BotCommand("start", "شروع ربات"),
        BotCommand("safemode", "مدیریت Safe Mode"),
        BotCommand("del", "حذف یک ماموریت"),
        BotCommand("delall", "حذف کل صف (مدیر)"),
        BotCommand("users", "لیست کاربران تاییدشده (مدیر)"),
        BotCommand("deluser", "حذف کاربر تاییدشده (مدیر)"),
    ]
    try:
        await app.set_bot_commands(commands)
    except Exception:
        pass

if __name__ == "__main__":
    clear_old_status()
    app.start()
    app.loop.run_until_complete(setup_bot_commands())
    app.loop.create_task(status_watcher())
    idle()
    app.stop()
