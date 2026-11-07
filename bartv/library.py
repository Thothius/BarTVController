"""JSON-backed stores for the asset library and program wrappers."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".m3u8"}
AUDIO_EXTS = {".mp3", ".aac", ".wav", ".m4a"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXTENSIONS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # 1 GB


def kind_of(suffix: str) -> str:
    s = suffix.lower()
    if s in VIDEO_EXTS:
        return "video"
    if s in AUDIO_EXTS:
        return "audio"
    if s in IMAGE_EXTS:
        return "image"
    return "file"


def sanitize_filename(name: str) -> str:
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name or name in (".", ".."):
        raise ValueError("Invalid file name")
    return name


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save(path: Path, data: dict):
    path.write_text(json.dumps(data, indent=2))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class AssetStore:
    """Media files in a flat directory plus a sidecar JSON of display names/thumbnails."""

    def __init__(self, media_dir: Path, meta_path: Path):
        self.dir = Path(media_dir)
        self.meta_path = Path(meta_path)

    def _meta(self) -> dict:
        return _load(self.meta_path)

    def _save_meta(self, meta: dict):
        _save(self.meta_path, meta)

    def list(self) -> list[dict]:
        if not self.dir.exists():
            return []
        meta = self._meta()
        out = []
        for f in sorted(self.dir.iterdir()):
            if not f.is_file() or f.suffix.lower() not in MEDIA_EXTENSIONS:
                continue
            m = meta.get(f.name, {})
            out.append({
                "file": f.name,
                "url": "/media/" + f.name,
                "name": m.get("name") or f.name,
                "thumb": m.get("thumb"),
                "duration": m.get("duration"),
                "size": f.stat().st_size,
                "added": m.get("added"),
                "kind": kind_of(f.suffix),
            })
        return out

    def target_for(self, filename: str, replace: bool = False) -> Path:
        """Validated destination path for an upload. Raises ValueError / FileExistsError."""
        safe = sanitize_filename(filename)
        if Path(safe).suffix.lower() not in MEDIA_EXTENSIONS:
            raise ValueError("Unsupported file type")
        target = self.dir / safe
        if target.exists() and not replace:
            raise FileExistsError(safe)
        self.dir.mkdir(parents=True, exist_ok=True)
        return target

    def register(self, filename: str) -> dict:
        meta = self._meta()
        if filename not in meta:
            meta[filename] = {"name": Path(filename).stem.replace("_", " "), "thumb": None, "added": _now()}
            self._save_meta(meta)
        else:
            meta[filename]["thumb"] = None  # replaced file - regenerate thumbnail
            self._save_meta(meta)
        return next(a for a in self.list() if a["file"] == filename)

    def put(self, filename: str, data: bytes, replace: bool = False) -> dict:
        target = self.target_for(filename, replace)
        target.write_bytes(data)
        return self.register(target.name)

    def update(self, filename: str, name: Optional[str] = None, thumb: Optional[str] = None,
               duration: Optional[float] = None) -> Optional[dict]:
        safe = sanitize_filename(filename)
        if not (self.dir / safe).is_file():
            return None
        meta = self._meta()
        m = meta.setdefault(safe, {"name": safe, "thumb": None, "added": _now()})
        if name is not None:
            m["name"] = name.strip()[:80] or safe
        if thumb is not None:
            m["thumb"] = thumb or None
        if duration is not None:
            m["duration"] = round(duration, 2)
        self._save_meta(meta)
        return next((a for a in self.list() if a["file"] == safe), None)

    def delete(self, filename: str) -> bool:
        safe = sanitize_filename(filename)
        target = self.dir / safe
        if not target.is_file():
            return False
        target.unlink()
        meta = self._meta()
        if safe in meta:
            del meta[safe]
            self._save_meta(meta)
        return True


class ProgramStore:
    """Named wrappers describing what content/commercials to run. Playback engine comes later."""

    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.exists():
            self._data = _load(self.path)
        else:
            # Default program: regular TV feed as the base, commercials on top.
            self._data = {"default": {"name": "Default", "base": "tv", "content": [],
                                      "commercials": [], "created": _now()}}
            self._save()

    def _save(self):
        _save(self.path, self._data)

    @staticmethod
    def _slug(name: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        return slug or "program"

    def list(self) -> list[dict]:
        return [self._out(pid) for pid in self._data]

    def _out(self, pid: str) -> dict:
        p = self._data[pid]
        return {"id": pid, "name": p.get("name", pid), "base": p.get("base", "content"),
                "content": p.get("content", []), "commercials": p.get("commercials", []),
                "created": p.get("created")}

    def create(self, name: str, content: list[str], commercials: list[str], base: str = "content") -> dict:
        slug = self._slug(name)
        pid, i = slug, 2
        while pid in self._data:
            pid = f"{slug}-{i}"
            i += 1
        self._data[pid] = {
            "name": name.strip()[:80],
            "base": base,
            "content": list(content),
            "commercials": list(commercials),
            "created": _now(),
        }
        self._save()
        return self._out(pid)

    def update(self, pid: str, name: Optional[str] = None, content: Optional[list[str]] = None,
               commercials: Optional[list[str]] = None, base: Optional[str] = None) -> Optional[dict]:
        if pid not in self._data:
            return None
        p = self._data[pid]
        if name is not None:
            p["name"] = name.strip()[:80] or p["name"]
        if base is not None:
            p["base"] = base
        if content is not None:
            p["content"] = list(content)
        if commercials is not None:
            p["commercials"] = list(commercials)
        self._save()
        return self._out(pid)

    def delete(self, pid: str) -> bool:
        if pid not in self._data:
            return False
        del self._data[pid]
        self._save()
        return True

    def scrub(self, filename: str):
        """Remove a deleted asset from every program's content/commercials."""
        changed = False
        for p in self._data.values():
            for key in ("content", "commercials"):
                if filename in p.get(key, []):
                    p[key] = [f for f in p[key] if f != filename]
                    changed = True
        if changed:
            self._save()
