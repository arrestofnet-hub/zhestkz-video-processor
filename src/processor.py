#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import textwrap
import urllib.parse
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

WORK = Path(os.getenv("GITHUB_WORKSPACE", ".")) / "work"
OUT = Path(os.getenv("GITHUB_WORKSPACE", ".")) / "output"
WORK.mkdir(exist_ok=True)
OUT.mkdir(exist_ok=True)

W, H = 1080, 1920
FPS = 30
DEFAULT_DURATION = 12
MAX_DURATION = 45

def run(cmd, check=True):
    print("+", " ".join(str(x) for x in cmd))
    return subprocess.run([str(x) for x in cmd], check=check)

def capture(cmd):
    return subprocess.check_output([str(x) for x in cmd], text=True).strip()

def ffprobe_json(path):
    raw = capture([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path)
    ])
    return json.loads(raw)

def has_audio(path):
    info = ffprobe_json(path)
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))

def media_kind(path):
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        return "image"
    info = ffprobe_json(path)
    kinds = {s.get("codec_type") for s in info.get("streams", [])}
    if "video" in kinds:
        return "video"
    if "audio" in kinds:
        return "audio"
    return "unknown"

def duration_seconds(path):
    try:
        info = ffprobe_json(path)
        return float(info.get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0

def normalize_media_url(url):
    url = (url or "").strip()
    if not url:
        return ""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return ""
    lowered = url.lower()
    if "telegram.org/img/emoji/" in lowered:
        return ""
    return url

def safe_filename_from_url(url, default="source.bin"):
    p = urllib.parse.urlparse(url)
    name = Path(urllib.parse.unquote(p.path)).name
    if not name or "." not in name:
        return default
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120]

def download(url):
    dest = WORK / safe_filename_from_url(url)
    print(f"Downloading {url} -> {dest}")
    with requests.get(
        url,
        stream=True,
        timeout=120,
        allow_redirects=True,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ZhestKZ-VideoProcessor/1.2)",
            "Accept": "*/*",
        },
    ) as r:
        r.raise_for_status()
        with dest.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    return dest

def get_font(size, bold=False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

def wrap_text(draw, text, font, max_width):
    words = text.strip().split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for word in words[1:]:
        trial = cur + " " + word
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return lines

def make_card(title, body=None):
    img = Image.new("RGB", (W, H), (18, 18, 18))
    draw = ImageDraw.Draw(img)
    title_font = get_font(72, bold=True)
    brand_font = get_font(54, bold=True)
    body_font = get_font(46)

    draw.rounded_rectangle((70, 70, 460, 170), radius=24, fill=(255, 255, 255))
    draw.text((95, 88), "ЖЕСТЬ KZ", font=brand_font, fill=(10, 10, 10))

    y = 310
    for line in wrap_text(draw, title or "ЖЕСТЬ KZ", title_font, 900)[:8]:
        draw.text((90, y), line, font=title_font, fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
        y += 98

    if body:
        y += 40
        for line in wrap_text(draw, body, body_font, 900)[:12]:
            draw.text((90, y), line, font=body_font, fill=(220, 220, 220))
            y += 66

    draw.text((90, 1770), "Новости Казахстана", font=body_font, fill=(190, 190, 190))
    path = WORK / "card.png"
    img.save(path, quality=95)
    return path

def escape_ass(text):
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")

def fmt_ass_time(seconds):
    seconds = max(0.0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def transcribe(source):
    if not has_audio(source):
        return []
    wav = WORK / "audio.wav"
    try:
        run(["ffmpeg", "-y", "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", "-t", str(MAX_DURATION), str(wav)])
        from faster_whisper import WhisperModel
        model_name = os.getenv("WHISPER_MODEL", "base")
        print(f"Loading Whisper model: {model_name}")
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, _ = model.transcribe(str(wav), language="ru", vad_filter=True)
        result = []
        for seg in segments:
            text = (seg.text or "").strip()
            if text:
                result.append({"start": float(seg.start), "end": float(seg.end), "text": text})
        return result
    except Exception as e:
        print("Whisper failed; continuing without subtitles:", repr(e))
        return []

def make_ass(title, segments, total_duration):
    ass = WORK / "overlay.ass"
    total_duration = min(MAX_DURATION, max(3.0, total_duration or DEFAULT_DURATION))
    title = (title or "ЖЕСТЬ KZ").strip()

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,DejaVu Sans,64,&H00FFFFFF,&H000000FF,&H90000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,8,70,70,120,1
Style: Brand,DejaVu Sans,42,&H00FFFFFF,&H000000FF,&H90000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,9,60,60,55,1
Style: Sub,DejaVu Sans,48,&H00FFFFFF,&H000000FF,&H90000000,&H88000000,-1,0,0,0,100,100,0,0,1,4,1,2,70,70,165,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    title_end = min(5.5, total_duration)
    events.append(f"Dialogue: 0,0:00:00.00,{fmt_ass_time(title_end)},Title,,0,0,0,,{escape_ass(title)}")
    events.append(f"Dialogue: 0,0:00:00.00,{fmt_ass_time(total_duration)},Brand,,0,0,0,,ЖЕСТЬ KZ")
    for seg in segments:
        start = max(0.0, min(total_duration, seg["start"]))
        end = max(start + 0.25, min(total_duration, seg["end"]))
        if start >= total_duration:
            continue
        text = seg["text"]
        if len(text) > 100:
            text = "\n".join(textwrap.wrap(text, 50)[:2])
        events.append(f"Dialogue: 0,{fmt_ass_time(start)},{fmt_ass_time(end)},Sub,,0,0,0,,{escape_ass(text)}")

    ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return ass

def render_video(source, title):
    dur = min(MAX_DURATION, duration_seconds(source) or MAX_DURATION)
    segments = transcribe(source)
    ass = make_ass(title, segments, dur)
    base = WORK / "base.mp4"
    out = OUT / "zhestkz_tiktok.mp4"

    fc = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=28[bg2];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease[fg2];"
        "[bg2][fg2]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]"
    )

    cmd = ["ffmpeg", "-y", "-i", str(source), "-filter_complex", fc, "-map", "[v]"]
    if has_audio(source):
        cmd += ["-map", "0:a:0?", "-af", "loudnorm=I=-16:LRA=11:TP=-1.5"]
    cmd += ["-t", str(MAX_DURATION), "-r", str(FPS), "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if has_audio(source):
        cmd += ["-c:a", "aac", "-b:a", "160k"]
    cmd += [str(base)]
    run(cmd)

    run([
        "ffmpeg", "-y", "-i", str(base),
        "-vf", f"ass={ass}",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(out)
    ])
    return out

def render_image(source, title):
    dur = DEFAULT_DURATION
    ass = make_ass(title, [], dur)
    out = OUT / "zhestkz_tiktok.mp4"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(source),
        "-vf",
        "scale=1200:2134:force_original_aspect_ratio=increase,"
        "crop=1200:2134,"
        "zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=360:s=1080x1920:fps=30,"
        f"ass={ass}",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out)
    ])
    return out

def render_text(title, body=None):
    card = make_card(title, body)
    dur = DEFAULT_DURATION
    out = OUT / "zhestkz_tiktok.mp4"
    run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(card),
        "-vf",
        "scale=1200:2134:force_original_aspect_ratio=increase,"
        "crop=1200:2134,"
        "zoompan=z='min(zoom+0.0006,1.08)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=360:s=1080x1920:fps=30",
        "-t", str(dur),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(out)
    ])
    return out

def render_job(media_url, title, body=None):
    normalized = normalize_media_url(media_url)
    if media_url and not normalized:
        print("Ignoring unusable media URL; rendering text card instead:", media_url)
    if not normalized:
        return render_text(title, body)

    try:
        src = download(normalized)
        kind = media_kind(src)
        print("Detected kind:", kind)
        if kind == "video":
            return render_video(src, title)
        if kind == "image":
            return render_image(src, title)
        try:
            Image.open(src).verify()
            return render_image(src, title)
        except Exception:
            print("Downloaded media is unsupported; rendering text card instead.")
            return render_text(title, body)
    except Exception as exc:
        print("Media processing failed; rendering text card instead:", repr(exc))
        return render_text(title, body)

def callback(url, key, payload):
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-Processor-Key"] = key
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=30)
        print("Callback:", r.status_code, r.text[:500])
        r.raise_for_status()
    except Exception as e:
        print("Callback failed:", repr(e))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--media-url", default=os.getenv("MEDIA_URL", ""))
    parser.add_argument("--title", default=os.getenv("TITLE", "ЖЕСТЬ KZ"))
    parser.add_argument("--text", default=os.getenv("TEXT", ""))
    parser.add_argument("--job-id", default=os.getenv("JOB_ID", "manual"))
    parser.add_argument("--callback-url", default=os.getenv("CALLBACK_URL", ""))
    parser.add_argument("--processor-key", default=os.getenv("PROCESSOR_KEY", ""))
    args = parser.parse_args()

    callback(args.callback_url, args.processor_key, {"job_id": args.job_id, "status": "processing"})

    try:
        out = render_job(args.media_url, args.title, args.text)

        info = ffprobe_json(out)
        result = {
            "job_id": args.job_id,
            "status": "ready",
            "output": str(out),
            "duration": duration_seconds(out),
            "size_bytes": out.stat().st_size,
            "streams": [
                {
                    "codec_type": s.get("codec_type"),
                    "codec_name": s.get("codec_name"),
                    "width": s.get("width"),
                    "height": s.get("height"),
                }
                for s in info.get("streams", [])
            ],
        }
        (OUT / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        callback(args.callback_url, args.processor_key, result)
    except Exception as exc:
        err = {"job_id": args.job_id, "status": "error", "error": repr(exc)}
        (OUT / "result.json").write_text(json.dumps(err, ensure_ascii=False, indent=2), encoding="utf-8")
        callback(args.callback_url, args.processor_key, err)
        raise

if __name__ == "__main__":
    main()
