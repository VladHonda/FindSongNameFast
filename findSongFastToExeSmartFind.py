#!/usr/bin/env python3
"""
ULTRA FAST SONG DETECTOR
"""

import asyncio
import yt_dlp
import subprocess
import os
import tempfile
import re
import uuid
import shutil
import time
from shazamio import Shazam
from urllib.parse import urlparse, parse_qs

# ================= ULTRA FAST CONFIG =================

CLIP_DURATION = 15
MAX_CONCURRENT = 1           # Reduced for stability
RECOGNIZE_TIMEOUT = 10

TIMESTAMP_RANGE = 30
COARSE_STEP = 45             # Big step = much faster on long videos

# ================= HELPERS =================

def detect_platform(url: str) -> str:
    url = url.lower()
    if "youtu" in url: return "youtube"
    if "tiktok.com" in url: return "tiktok"
    if "instagram.com" in url: return "instagram"
    if "facebook.com" in url: return "facebook"
    return "unknown"


def parse_timestamp(url: str) -> int:
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    t = (qs.get("t") or qs.get("start") or ["0"])[0]
    if t.isdigit(): return int(t)
    match = re.match(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if match:
        return int(match.group(1) or 0)*3600 + int(match.group(2) or 0)*60 + int(match.group(3) or 0)
    return 0


def check_ffmpeg():
    if not shutil.which("ffmpeg"):
        print("❌ FFmpeg not found!")
        return False
    return True


def get_video_info(url: str):
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        return info.get("duration") or 0, info.get("title") or "Unknown"


def download_audio(url: str, platform: str) -> str:
    temp_dir = tempfile.gettempdir()
    unique_id = uuid.uuid4().hex
    outtmpl = os.path.join(temp_dir, f"fsong_{unique_id}.%(ext)s")

    print("⬇️ Downloading audio...")
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    }
    if platform == "facebook":
        ydl_opts["cookiesfrombrowser"] = ("firefox", None)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(url, download=True)

    wav_path = outtmpl.replace(".%(ext)s", ".wav")
    print("✅ Audio ready")
    return wav_path


# ================= RECOGNITION =================

def preprocess_clip(input_path, output_path, offset):
    cmd = [
        "ffmpeg", "-y", "-ss", str(offset), "-t", str(CLIP_DURATION), "-i", input_path,
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",   # Removed mid boost for speed
        "-acodec", "pcm_s16le", "-ac", "1", "-ar", "44100", output_path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


async def recognize_worker(audio_path, offset, shazam, sem):
    async with sem:
        clip = None
        try:
            print(f"🔍 {offset:4}s → ", end="", flush=True)
            clip = os.path.join(tempfile.gettempdir(), f"c_{offset}_{uuid.uuid4().hex[:6]}.wav")
            await asyncio.to_thread(preprocess_clip, audio_path, clip, offset)

            result = await asyncio.wait_for(shazam.recognize(clip), timeout=RECOGNIZE_TIMEOUT)

            if result and "track" in result:
                t = result["track"]
                title = t.get("title", "").strip()
                artist = t.get("subtitle", "").strip()
                if title:
                    score = t.get("score", 0) / 100 if "score" in t else 1.0
                    print(f"✅ {title} — {artist}")
                    return offset, title, artist, score
            print("no")
        except asyncio.TimeoutError:
            print("⏰")
        except:
            print("err")
        finally:
            if clip and os.path.exists(clip):
                try: os.remove(clip)
                except: pass
        return offset, None, None, 0


# ================= MAIN SCAN =================

async def ultra_fast_scan(audio_path: str, duration: int, start_offset: int, shazam, sem):
    found = []
    seen = set()

    # 1. Timestamp area
    print("\n🎯 Timestamp area...")
    offsets = sorted({max(0, min(start_offset + d, duration - CLIP_DURATION)) 
                      for d in range(-TIMESTAMP_RANGE, TIMESTAMP_RANGE + 1, 8)})

    for task in asyncio.as_completed([asyncio.create_task(recognize_worker(audio_path, o, shazam, sem)) for o in offsets]):
        off, title, artist, score = await task
        if title and title not in seen:
            seen.add(title)
            found.append((off, title, artist, score))

    # 2. Very coarse broad scan
    print(f"\n🔍 Ultra coarse scan (step {COARSE_STEP}s)...")
    step = COARSE_STEP if duration > 600 else 20   # 10+ minutes → very sparse

    for pos in range(0, duration - CLIP_DURATION + 1, step):
        task = asyncio.create_task(recognize_worker(audio_path, pos, shazam, sem))
        off, title, artist, score = await task

        if title and title not in seen:
            seen.add(title)
            found.append((off, title, artist, score))

    return sorted(found)


# ================= MAIN =================

async def detect():
    overall_start = time.time()
    print("\n🎵 ULTRA FAST SONG DETECTOR\n")

    url = input("📎 Paste video/reel URL:\n> ").strip()
    if not url: return

    platform = detect_platform(url)
    start_offset = parse_timestamp(url)

    try:
        download_start = time.time()
        duration, title = get_video_info(url)
        print(f"📼 {title[:85]}... | {duration//60}m {duration%60}s")

        audio = download_audio(url, platform)
        print(f"   Download took: {time.time() - download_start:.1f}s\n")

        scan_start = time.time()
        shazam = Shazam()
        sem = asyncio.Semaphore(MAX_CONCURRENT)

        songs = await ultra_fast_scan(audio, duration, start_offset, shazam, sem)

        scan_time = time.time() - scan_start
        total_time = time.time() - overall_start

        if songs:
            print(f"\n{'='*70}")
            print(f"🎉 FOUND {len(songs)} SONG(S)")
            print(f"{'='*70}\n")
            for i, (off, title, artist, _) in enumerate(songs, 1):
                print(f"{i:2}. {off:4}s → {title}")
                print(f"     👤 {artist}\n")
        else:
            print("\n❌ No songs found.")

        print(f"⏱️  Timing:")
        print(f"   • Download : {time.time() - download_start:.1f}s")
        print(f"   • Scanning : {scan_time:.1f}s")
        print(f"   • Total    : {total_time:.1f}s")

    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        if 'audio' in locals() and os.path.exists(audio):
            try: os.remove(audio)
            except: pass


if __name__ == "__main__":
    if not check_ffmpeg():
        exit(1)
    asyncio.run(detect())
    input("\nPress ENTER to close...")