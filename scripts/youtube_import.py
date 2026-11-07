"""Import YouTube channel videos into static/media as content assets.

Lists a channel's videos (views, likes, duration, upload date), downloads the
chosen ones as H.264/AAC mp4 named `content_<video_title>.mp4`, registers them
in the asset library (`assets.json`, display name = video title) and records the
YouTube metadata in `static/media/index.json`.

Usage:
  python scripts/youtube_import.py https://www.youtube.com/@CageFiction --list
  python scripts/youtube_import.py https://www.youtube.com/@CageFiction --top 5
  python scripts/youtube_import.py https://www.youtube.com/@CageFiction --ids ID1,ID2

Requires: pip install yt-dlp imageio-ffmpeg  (ffmpeg on PATH also works)
"""
import argparse
import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yt_dlp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bartv.config import CONFIG_PATH  # noqa: E402
from bartv.library import AssetStore, sanitize_filename  # noqa: E402

MEDIA = ROOT / "static" / "media"
INDEX = MEDIA / "index.json"


def ffmpeg_path():
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        sys.exit("ffmpeg not found: pip install imageio-ffmpeg")


def safe_name(title):
    return sanitize_filename("content_" + re.sub(r"[/\\]", "_", title[:100]))


def channel_videos(channel):
    url = channel.rstrip("/")
    if not url.endswith("/videos"):
        url += "/videos"
    with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    return [e for e in info["entries"] if e.get("id")]


def video_info(video_id):
    with yt_dlp.YoutubeDL({"quiet": True}) as ydl:
        return ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)


def load_index():
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else {"assets": []}


def save_index(index):
    INDEX.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")


def download(info, max_height):
    h = max_height
    name = safe_name(info["title"])
    opts = {
        "format": f"bv*[vcodec^=avc1][height<={h}]+ba[ext=m4a]/b[ext=mp4][height<={h}]/bv*[height<={h}]+ba/b",
        "merge_output_format": "mp4",
        "outtmpl": str(MEDIA / f"{name}.%(ext)s"),
        "ffmpeg_location": ffmpeg_path(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([info["webpage_url"]])
    return f"{name}.mp4"


def index_entry(info, file):
    return {
        "file": file,
        "url": "/media/" + file,
        "kind": "content",
        "title": info["title"],
        "source": "youtube",
        "youtube_id": info["id"],
        "source_url": info["webpage_url"],
        "channel": info.get("channel"),
        "duration_s": info.get("duration"),
        "upload_date": info.get("upload_date"),
        "views": info.get("view_count"),
        "likes": info.get("like_count"),
        "thumbnail": info.get("thumbnail"),
        "size": (MEDIA / file).stat().st_size,
        "imported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("channel", help="channel URL, e.g. https://www.youtube.com/@CageFiction")
    ap.add_argument("--list", action="store_true", help="only print the channel's videos")
    ap.add_argument("--top", type=int, help="download the N most-viewed videos")
    ap.add_argument("--ids", help="comma-separated video IDs to download")
    ap.add_argument("--max-height", type=int, default=1080)
    args = ap.parse_args()

    videos = channel_videos(args.channel)
    videos.sort(key=lambda v: v.get("view_count") or 0, reverse=True)
    if args.list or not (args.top or args.ids):
        for v in videos:
            mins = (v.get("duration") or 0) / 60
            print(f"{v['id']}  {v.get('view_count') or 0:>10,} views  {mins:5.1f} min  {v.get('title')}")
        print(f"{len(videos)} videos")
        return

    ids = args.ids.split(",") if args.ids else [v["id"] for v in videos[: args.top]]
    MEDIA.mkdir(parents=True, exist_ok=True)
    index = load_index()
    library = AssetStore(MEDIA, CONFIG_PATH.parent / "assets.json")
    done = {a.get("youtube_id") for a in index["assets"] if (MEDIA / a["file"]).exists()}
    for vid in ids:
        if vid in done:
            print(f"skip {vid} (already imported)")
            continue
        info = video_info(vid)
        print(f"downloading: {info['title']}")
        file = download(info, args.max_height)
        index["assets"] = [a for a in index["assets"] if a.get("youtube_id") != vid]
        index["assets"].append(index_entry(info, file))
        save_index(index)
        library.update(file, name=info["title"], duration=info.get("duration"))
        print(f"  -> static/media/{file}")
    print(f"index: {INDEX.relative_to(ROOT)} ({len(index['assets'])} assets)")


if __name__ == "__main__":
    main()
