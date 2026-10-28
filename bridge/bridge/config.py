import os
import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BRIDGE_DIR = Path(__file__).resolve().parent.parent
ROOT = BRIDGE_DIR.parent
SEED_DIR = BRIDGE_DIR / "seed"
MATERIALS_DIR = ROOT / "materials"
WEB_DIR = ROOT / "web"
DATA_DIR = Path(os.environ.get("BM_DATA_DIR") or BRIDGE_DIR / "data")
DB_PATH = DATA_DIR / "app.sqlite3"
PHOTOS_DIR = DATA_DIR / "photos"
LOGS_DIR = DATA_DIR / "logs"
JOURNAL_DIR = DATA_DIR / "journal"  # served files
JOURNAL_TMP = JOURNAL_DIR / "tmp"  # uploads and intermediates, never served
VAPID_KEY_PATH = DATA_DIR / "vapid_private.pem"
BACKGROUND = os.environ.get("BM_BACKGROUND", "1") != "0"  # scheduler + worker loops

TZ = ZoneInfo("Asia/Jakarta")

load_dotenv(BRIDGE_DIR / ".env")


def now() -> datetime:
    return datetime.now(TZ)


def today() -> str:
    return now().strftime("%Y-%m-%d")


def screen_time_archive() -> Path:
    return Path.home() / "Library/Application Support/ScreenTimeArchive/archive.sqlite"


def screen_time_binary() -> Path:
    return Path.home() / "Library/Application Support/ScreenTimeArchive/bin/screentime-archiver"


def settings() -> SimpleNamespace:
    # launchd has a minimal PATH, so resolve claude to an absolute path.
    want = os.environ.get("CLAUDE_BIN") or "claude"
    claude = shutil.which(want) or shutil.which("claude") or str(Path.home() / ".local/bin/claude")
    public_url = (os.environ.get("PUBLIC_URL") or "http://127.0.0.1:8787").rstrip("/")

    def binary(env: str, name: str) -> str:
        # launchd's PATH has no /opt/homebrew/bin, so fall back to the absolute path.
        return os.environ.get(env) or shutil.which(name) or f"/opt/homebrew/bin/{name}"

    whisper_model = os.environ.get("WHISPER_MODEL")
    if not whisper_model:  # default only when the file is really there, so an unconfigured Mac stays "not configured"
        default = Path("~/.local/share/whisper-cpp/models/ggml-large-v3-turbo-q5_0.bin").expanduser()
        whisper_model = str(default) if default.is_file() else None

    return SimpleNamespace(
        YPT_URL=os.environ.get("YPT_URL") or None,
        PUBLIC_URL=public_url,
        PORT=int(os.environ.get("PORT") or 8787),
        CLAUDE_BIN=claude,
        FFMPEG_BIN=binary("FFMPEG_BIN", "ffmpeg"),
        FFPROBE_BIN=binary("FFPROBE_BIN", "ffprobe"),
        WHISPER_BIN=binary("WHISPER_BIN", "whisper-cli"),
        WHISPER_MODEL=whisper_model,
        VAPID_SUBJECT=os.environ.get("VAPID_SUBJECT")
        or (public_url if public_url.startswith("https://") else "mailto:bridge@boredom-maxxing.invalid"),
    )
