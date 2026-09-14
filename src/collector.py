#!/usr/bin/env python3
import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen

ROOT = Path(os.getenv("GITHUB_WORKSPACE", "."))
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "processed.json"
DEFAULT_QUEUE_URL = "https://zhestkz.kzalladinkz.workers.dev/video/jobs?limit=20"


def load_state():
    if not STATE_FILE.exists():
        return {"processed": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"processed": []}
        rows = data.get("processed")
        if not isinstance(rows, list):
            rows = []
        return {"processed": rows}
    except Exception:
        return {"processed": []}


def save_state(state):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    rows = state.get("processed", [])
    rows = sorted(
        [r for r in rows if isinstance(r, dict) and r.get("id")],
        key=lambda r: str(r.get("processedAt", "")),
        reverse=True,
    )[:500]
    STATE_FILE.write_text(
        json.dumps({"processed": rows}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def processed_ids(state):
    return {str(r.get("id")) for r in state.get("processed", []) if r.get("id")}


def write_env(values):
    env_path = os.getenv("GITHUB_ENV")
    if not env_path:
        for k, v in values.items():
            print(f"{k}={v}")
        return

    with open(env_path, "a", encoding="utf-8") as f:
        for key, value in values.items():
            value = "" if value is None else str(value)
            marker = f"EOF_ZHESTKZ_{key}"
            f.write(f"{key}<<{marker}\n{value}\n{marker}\n")


def cache_busted(url):
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["_ts"] = str(int(time.time()))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def first_nonempty(job, *keys):
    for key in keys:
        value = job.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def select_job():
    queue_url = os.getenv("ZHESKZ_QUEUE_URL", DEFAULT_QUEUE_URL)
    request_url = cache_busted(queue_url)
    print(f"Queue: {queue_url}")
    req = Request(
        request_url,
        headers={
            "User-Agent": "ZhestKZ-GitHub-Processor/1.2",
            "Accept": "application/json",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
    )
    with urlopen(req, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))

    jobs = data.get("jobs") or []
    if not isinstance(jobs, list):
        jobs = []

    state = load_state()
    done = processed_ids(state)
    queue_ids = [str(j.get("id")) for j in jobs if isinstance(j, dict) and j.get("id")]
    print(f"Worker version: {data.get('version', 'unknown')}; queue_count={len(jobs)}")
    print(f"Queue IDs: {queue_ids}")
    print(f"Processed IDs: {sorted(done)}")

    pending = [
        j for j in jobs
        if isinstance(j, dict) and j.get("id") and str(j.get("id")) not in done
    ]

    if not pending:
        print("No unprocessed video jobs.")
        write_env({
            "HAS_JOB": "0",
            "MEDIA_URL": "",
            "TITLE": "",
            "TEXT": "",
            "JOB_ID": "",
            "SOURCE_URL": "",
        })
        return 0

    job = sorted(pending, key=lambda j: int(j.get("createdAt") or 0))[0]
    job_id = str(job.get("id"))
    media_url = first_nonempty(job, "mediaUrl", "videoUrl", "image", "imageUrl", "thumbnail")
    title = first_nonempty(job, "title") or "ЖЕСТЬ KZ"
    text = first_nonempty(job, "text", "caption", "description", "summary")
    source_url = first_nonempty(job, "link", "sourceUrl", "url")

    print("Selected job:")
    print(json.dumps(job, ensure_ascii=False, indent=2))
    print(f"Mapped fields: media={'yes' if media_url else 'no'}, text={'yes' if text else 'no'}, source={'yes' if source_url else 'no'}")
    write_env({
        "HAS_JOB": "1",
        "MEDIA_URL": media_url,
        "TITLE": title,
        "TEXT": text,
        "JOB_ID": job_id,
        "SOURCE_URL": source_url,
    })
    return 0


def mark_done(job_id):
    if not job_id:
        raise SystemExit("job id is required")
    state = load_state()
    rows = state.get("processed", [])
    rows = [r for r in rows if str(r.get("id")) != str(job_id)]
    rows.insert(0, {
        "id": str(job_id),
        "processedAt": datetime.now(timezone.utc).isoformat(),
    })
    state["processed"] = rows
    save_state(state)
    print(f"Marked processed: {job_id}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("select")
    mark = sub.add_parser("mark")
    mark.add_argument("--job-id", required=True)
    args = ap.parse_args()
    if args.cmd == "select":
        return select_job()
    return mark_done(args.job_id)


if __name__ == "__main__":
    raise SystemExit(main())
