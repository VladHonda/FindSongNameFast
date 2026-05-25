#!/usr/bin/env python3

"""
SMART SONG DETECTOR
Executable-safe edition

Features:
  • Interactive URL input
  • YouTube / TikTok / Instagram / Facebook support
  • Timestamp parsing
  • Better temp-file handling
  • Safer Facebook cookie handling
  • PyInstaller compatible
  • ffmpeg auto-detection
"""

import asyncio
import yt_dlp
import subprocess
import os
import tempfile
import re
import uuid
import shutil
import multiprocessing

from shazamio import Shazam
from urllib.parse import urlparse, parse_qs

# ================= CONFIG =================

CLIP_DURATION = 20
SCAN_WINDOW = 30
MAX_CONCURRENT = 2

NORMALIZE = True
BOOST_MIDS = True

# ================= HELPERS =================

def detect_platform(url: str) -> str:
    url = url.lower()

    if "youtu" in url:
        return "youtube"

    if "tiktok.com" in url:
        return "tiktok"

    if "instagram.com" in url:
        return "instagram"

    if "facebook.com" in url:
        return "facebook"

    return "unknown"


def parse_timestamp(url: str) -> int:
    """
    Extract timestamps like:
      ?t=120
      ?t=1m20s
      &start=45
    """

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    raw = qs.get("t") or qs.get("start") or ["0"]
    t = raw[0]

    if t.isdigit():
        return int(t)

    match = re.match(
        r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?",
        t
    )

    if match:
        h = int(match.group(1) or 0)
        m = int(match.group(2) or 0)
        s = int(match.group(3) or 0)

        return h * 3600 + m * 60 + s

    return 0


def check_ffmpeg():
    """
    Ensure ffmpeg exists.
    """

    ffmpeg = shutil.which("ffmpeg")

    if not ffmpeg:
        print("\n❌ FFmpeg not found.")
        print("Install FFmpeg and add it to PATH.")
        print("https://ffmpeg.org/download.html\n")
        return False

    return True


# ================= DOWNLOAD =================

def download_audio(url: str, platform: str) -> str:

    temp_dir = tempfile.gettempdir()

    unique_id = uuid.uuid4().hex

    outtmpl = os.path.join(
        temp_dir,
        f"findsong_{unique_id}.%(ext)s"
    )

    print(f"\n⬇️ Downloading from {platform}...")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "http_headers": {
            "User-Agent": "Mozilla/5.0"
        },
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
            "preferredquality": "192",
        }],
    }

    # Facebook sometimes needs cookies
    if platform == "facebook":

        try:
            ydl_opts["cookiesfrombrowser"] = ("firefox",)
        except:
            pass

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    base = outtmpl.replace(".%(ext)s", "")
    wav_path = f"{base}.wav"

    if os.path.exists(wav_path):
        print(f"✅ Downloaded")
        return wav_path

    raise Exception("Download failed")


# ================= AUDIO =================

def preprocess_clip(
    input_path: str,
    output_path: str,
    offset: int
):

    filters = []

    if NORMALIZE:
        filters.append(
            "loudnorm=I=-14:TP=-1.5:LRA=11"
        )

    if BOOST_MIDS:
        filters.append(
            "equalizer=f=2000:width_type=h:width=3000:g=6"
        )

    af = ",".join(filters)

    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(offset),
        "-t",
        str(CLIP_DURATION),
        "-i",
        input_path,
        "-af",
        af,
        "-acodec",
        "pcm_s16le",
        "-ac",
        "1",
        "-ar",
        "44100",
        output_path
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

    if result.returncode != 0:
        raise Exception("FFmpeg failed")

    if not os.path.exists(output_path):
        raise Exception("Clip creation failed")


async def extract_clip(audio_path: str, offset: int):

    output = os.path.join(
        tempfile.gettempdir(),
        f"clip_{offset}_{uuid.uuid4().hex}.wav"
    )

    await asyncio.to_thread(
        preprocess_clip,
        audio_path,
        output,
        offset
    )

    return output


# ================= SHAZAM =================

async def recognize_worker(
    audio_path,
    offset,
    shazam,
    sem
):

    async with sem:

        try:

            print(f"🔍 Scanning {offset}s...")

            clip = await extract_clip(
                audio_path,
                offset
            )

            result = await shazam.recognize(clip)

            # cleanup clip immediately
            try:
                os.remove(clip)
            except:
                pass

            if result and "track" in result:

                track = result["track"]

                print(
                    f"   ✅ MATCH: "
                    f"{track.get('title')} — "
                    f"{track.get('subtitle')}"
                )

                return offset, track

        except Exception as e:

            print(
                f"   ⚠️ {offset}s error: "
                f"{str(e)[:60]}"
            )

        return offset, None


# ================= SMART SCAN =================

async def scan_at_timestamp(
    audio_path,
    base_offset,
    shazam,
    sem
):

    offsets = [
        base_offset + d
        for d in [
            0,
            -5,
            5,
            -10,
            10,
            -15,
            15,
            -20,
            20,
            -25,
            25
        ]
    ]

    offsets = [
        o for o in offsets
        if o >= 0
    ]

    print(
        f"\n🎯 Scanning around "
        f"{base_offset}s..."
    )

    tasks = [
        asyncio.create_task(
            recognize_worker(
                audio_path,
                o,
                shazam,
                sem
            )
        )
        for o in offsets
    ]

    for task in asyncio.as_completed(tasks):

        offset, track = await task

        if track:

            for t in tasks:
                t.cancel()

            return offset, track

    return None, None


# ================= MAIN =================

async def detect():

    print("\n🎵 SMART SONG DETECTOR\n")

    url = input(
        "📎 Paste video/reel URL:\n> "
    ).strip()

    if not url:
        print("❌ No URL entered")
        return

    platform = detect_platform(url)

    if platform == "unknown":
        print("❌ Unsupported platform")
        return

    start_offset = parse_timestamp(url)

    print(f"\n🌐 Platform:  {platform}")
    print(f"📍 Timestamp: {start_offset}s")

    audio = None

    try:

        audio = download_audio(
            url,
            platform
        )

        shazam = Shazam()

        sem = asyncio.Semaphore(
            MAX_CONCURRENT
        )

        # Main timestamp scan
        offset, track = await scan_at_timestamp(
            audio,
            start_offset,
            shazam,
            sem
        )

        if track:

            print(f"\n{'=' * 50}")
            print(f"✅ FOUND")

            print(
                f"🎶 Title: "
                f"{track.get('title', 'Unknown')}"
            )

            print(
                f"👤 Artist: "
                f"{track.get('subtitle', 'Unknown')}"
            )

            print(
                f"📍 Offset: "
                f"{offset}s"
            )

            print(f"{'=' * 50}")

            return

        # Fallback broader scan
        print(
            "\n⚠️ No match near timestamp."
        )

        print(
            "🔬 Trying broader scan..."
        )

        for wide_range in [
            (0, 120),
            (120, 300),
            (300, 600)
        ]:

            print(
                f"\n🔍 "
                f"{wide_range[0]}–"
                f"{wide_range[1]}s"
            )

            offsets = list(
                range(
                    wide_range[0],
                    wide_range[1],
                    10
                )
            )

            tasks = [
                asyncio.create_task(
                    recognize_worker(
                        audio,
                        o,
                        shazam,
                        sem
                    )
                )
                for o in offsets
            ]

            for task in asyncio.as_completed(tasks):

                off, trk = await task

                if trk:

                    for t in tasks:
                        t.cancel()

                    print(f"\n{'=' * 50}")

                    print(
                        f"✅ FOUND at {off}s"
                    )

                    print(
                        f"🎶 "
                        f"{trk.get('title')} — "
                        f"{trk.get('subtitle')}"
                    )

                    print(f"{'=' * 50}")

                    return

        print("\n❌ No song found")

    except KeyboardInterrupt:

        print("\n\n🛑 Cancelled by user")

    except Exception as e:

        print(f"\n❌ ERROR: {e}")

    finally:

        # Cleanup
        try:

            if audio and os.path.exists(audio):
                os.remove(audio)

        except:
            pass


# ================= ENTRY =================

if __name__ == "__main__":

    multiprocessing.freeze_support()

    if not check_ffmpeg():
        input("\nPress ENTER to exit...")
        raise SystemExit

    asyncio.run(detect())

    input("\nPress ENTER to close...")