"""Shared fakes for the Journal tests: clock, fixed rng, and a fake `media._run` that writes the output files."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

from bridge import config, media

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
HEIC = b"\x00\x00\x00\x18ftypheic" + b"0" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"0" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"0" * 64
TEXT = b"hello, this is not media"
SUB_APPLE = "https://web.push.apple.com/abc123"
SUB_FCM = "https://fcm.googleapis.com/fcm/send/xyz"


def wib(day: int = 26, hh: int = 10, mm: int = 0, ss: int = 0, month: int = 10) -> datetime:
    return datetime(2026, month, day, hh, mm, ss, tzinfo=config.TZ)


class Clock:
    def __init__(self, t: datetime):
        self.t = t

    def __call__(self) -> datetime:
        return self.t


class FixedRng:
    def __init__(self, v: int = 0):
        self.v = v

    def randrange(self, n: int) -> int:
        return min(self.v, n - 1)


def probe_json(duration=10.0, video=True, audio=True, w=1280, h=720, attached_pic=False) -> dict:
    streams = []
    if video:
        streams.append({"codec_type": "video", "width": w, "height": h, "disposition": {"attached_pic": int(attached_pic)}})
    if audio:
        streams.append({"codec_type": "audio"})
    fmt = {} if duration is None else {"duration": str(duration)}
    return {"streams": streams, "format": fmt}


class FakeTools:
    """Stand-in for media._run. Records every args list and writes the files the real tool would."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.sips_size = (1200, 800)
        self.probe_src = probe_json()  # ffprobe of the raw upload
        self.probe_out = None  # ffprobe of the encoded file; defaults to probe_src's streams
        self.fail: set[str] = set()  # binary names that raise MediaError
        self.fail_if = None  # optional predicate on args for finer failures
        self.whisper_raw = (Path(__file__).parent / "fixtures" / "whisper.json").read_bytes()

    def names(self) -> list[str]:
        return [Path(c[0]).name for c in self.calls]

    def __call__(self, args, timeout):
        self.calls.append(list(args))
        name = Path(args[0]).name
        if name in self.fail or (self.fail_if and self.fail_if(args)):
            raise media.MediaError(f"{name} failed (1): boom")
        if name == "sips":
            if "-g" in args:
                w, h = self.sips_size
                out = f"{args[-1]}\n  pixelWidth: {w}\n  pixelHeight: {h}\n"
                return subprocess.CompletedProcess(args, 0, out.encode(), b"")
            Path(args[args.index("--out") + 1]).write_bytes(b"jpegdata")
        elif name == "ffprobe":
            src = str(args[-1]).endswith(".upload")
            info = self.probe_src if src or self.probe_out is None else self.probe_out
            return subprocess.CompletedProcess(args, 0, json.dumps(info).encode(), b"")
        elif name == "ffmpeg":
            Path(args[-1]).write_bytes(b"encoded-" + b"x" * 100)
        elif name == "whisper-cli":
            Path(str(args[args.index("-of") + 1]) + ".json").write_bytes(self.whisper_raw)
        return subprocess.CompletedProcess(args, 0, b"", b"")


def multipart(data: bytes, name: str = "clip.bin", ctype: str = "application/octet-stream", field: str = "file"):
    return {field: (name, data, ctype)}
