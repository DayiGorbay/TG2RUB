import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent

telegram_file = BASE_DIR / "telebot.py"
rubika_file = BASE_DIR / "rub.py"
bale_file = BASE_DIR / "bale_dashboard.py"

telegram_proc = None
rubika_proc = None
bale_proc = None

try:
    rubika_proc = subprocess.Popen([sys.executable, str(rubika_file)])
    telegram_proc = subprocess.Popen([sys.executable, str(telegram_file)])
    if bale_file.exists():
        bale_proc = subprocess.Popen([sys.executable, str(bale_file)])

    rubika_proc.wait()
    telegram_proc.wait()
    if bale_proc:
        bale_proc.wait()

except KeyboardInterrupt:
    pass
finally:
    for proc in [telegram_proc, rubika_proc, bale_proc]:
        if proc and proc.poll() is None:
            proc.terminate()
