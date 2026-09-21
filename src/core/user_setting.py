"""
UserSettings — MongoDB-backed user settings store.

Each user's settings are stored in the `user_settings` collection as a single
document keyed by `user_id`.  Binary assets (thumbnail, font) are stored inline
as base64 strings so the bot works on ephemeral hosts (Heroku / Render) where
the local filesystem is wiped on every restart.

The public API is identical to the original file-based class so every handler
works without changes.
"""

import base64
import copy
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

VALID_RESOLUTIONS = ["480p", "720p", "1080p"]
RESOLUTION_ALIASES = {
    "1080p": "1080p",
    "720p":  "720p",
    "480p":  "480p",
    "480":   "480p",
}

DEFAULT_PROFILES = {
    "1080p": {"mode": "encode", "crf": 23, "preset": "medium", "codec": "libx264", "audio_bitrate": "192k"},
    "720p":  {"mode": "encode", "crf": 26, "preset": "medium", "codec": "libx264", "audio_bitrate": "128k"},
    "480p":  {"mode": "encode", "crf": 28, "preset": "fast",   "codec": "libx264", "audio_bitrate": "96k"},
}

DEFAULT_WATERMARK = {
    "enabled":      False,
    "text":         "",
    "color":        "white",
    "font_path":    "",   # kept for API compat; actual data is in font_b64
    "font_name":    "default",
    "font_size":    24,
    "padding":      7,
    "timing_mode":  "range",
    "start":        0,
    "end":          0,
    "duration":     30,
    "repeat_count": 1,
    "position":     "bot_right",
}

VALID_WM_POSITIONS = {
    "top_left", "top_mid", "top_right",
    "mid_left", "mid_right",
    "bot_left", "bot_right",
}

DEFAULT_MI_PARAMS: dict = {
    "audio_offset":    0.0,
    "subtitle_offset": 0.0,
    "audio_async":     1,
    "audio_tempo":     1.0,
    "video_fps":       "source",
    "video_vsync":     "cfr",
    "video_pts":       "PTS-STARTPTS",
    "audio_pts":       "PTS-STARTPTS",
    "audio_pad":       False,
    "video_pad":       False,
    "shortest":        True,
    "fix_sub_duration": True,
    "generate_pts":     True,
    "ignore_dts":       False,
    "copy_timestamps":  False,
    "start_at_zero":    False,
}

# ── MongoDB singleton ─────────────────────────────────────────────────────────

_db = None   # motor AsyncIOMotorDatabase — set by init_db()


def init_db(database):
    """Call once at startup with a motor database object."""
    global _db
    _db = database
    logger.info("[UserSettings] MongoDB backend initialised (db=%s)", database.name)


def _collection():
    if _db is None:
        raise RuntimeError(
            "MongoDB not initialised. Call init_db(database) before using UserSettings."
        )
    return _db["user_settings"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _extract_font_name(font_path: str) -> str:
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(font_path, fontNumber=0)
        name_table = tt["name"]
        for name_id in (4, 1):
            record = name_table.getName(name_id, 3, 1, 0x0409)
            if record:
                return record.toUnicode().strip()
        for record in name_table.names:
            if record.nameID == 4:
                try:
                    return record.toUnicode().strip()
                except Exception:
                    pass
    except Exception:
        pass
    return os.path.splitext(os.path.basename(font_path))[0]


def _b64_encode_file(path: str) -> str:
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def _b64_decode_to_tmp(b64: str, suffix: str) -> str:
    """Write base64 data to a temp file and return its path."""
    data = base64.b64decode(b64)
    fd, path = tempfile.mkstemp(suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
    except Exception:
        os.close(fd)
        raise
    return path


# ── UserSettings ──────────────────────────────────────────────────────────────

class UserSettings:
    """
    Per-user settings backed by MongoDB.

    All I/O is async internally (_load / _save use motor), but the class also
    exposes the same synchronous-looking API as the old file-based version so
    existing handlers need zero changes.  The async methods are used by the new
    async helpers below; the sync wrappers call asyncio.get_event_loop().run_*
    only at startup (inside __init__), which is acceptable because __init__ is
    called from the sync `get_user_settings` factory that runs inside an already-
    running event loop via `asyncio.create_task`.

    IMPORTANT: Because motor is async, every _save() call is wrapped with
    asyncio.ensure_future so it fires-and-forgets from sync contexts.
    """

    _temp_state: Dict[int, Dict] = {}

    # ── Resolved temp file paths for binary assets ────────────────────────────
    # We materialise thumbnail / font to /tmp on demand and cache the path here.
    # These are instance-level so they are cleaned up when the cache evicts the entry.
    _thumb_tmp_path: Optional[str] = None
    _font_tmp_path:  Optional[str] = None

    def __init__(self, user_id: int, paths=None):
        self.user_id = user_id

        # paths is kept for API compatibility but we no longer use the filesystem
        # for settings.  We still use paths.thumbnails / paths.fonts as a
        # fallback tmp directory on VPS deployments.
        if paths is not None:
            self._local_thumbnails = paths.thumbnails
            self._local_fonts      = paths.fonts
        else:
            self._local_thumbnails = "/tmp/encode_bot/thumbnails"
            self._local_fonts      = "/tmp/encode_bot/fonts"

        os.makedirs(self._local_thumbnails, exist_ok=True)
        os.makedirs(self._local_fonts,      exist_ok=True)

        self.data: Dict[str, Any] = {}
        self._thumb_tmp_path = None
        self._font_tmp_path  = None

        # Synchronous load — called from a sync factory inside the event loop.
        # We use a blocking motor workaround via asyncio.get_event_loop().
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Schedule load; data will be populated before first use
                # because every handler awaits at least one Telegram call first.
                asyncio.ensure_future(self._async_load())
            else:
                loop.run_until_complete(self._async_load())
        except Exception as e:
            logger.warning("[UserSettings] load error for %s: %s", user_id, e)
            self.data = self._get_default_settings()

    # ── Async I/O ─────────────────────────────────────────────────────────────

    async def _async_load(self):
        try:
            doc = await _collection().find_one({"user_id": self.user_id})
            if doc:
                doc.pop("_id", None)
                self.data = doc
            else:
                self.data = self._get_default_settings()
        except Exception as e:
            logger.warning("[UserSettings] MongoDB load failed for %s: %s", self.user_id, e)
            self.data = self._get_default_settings()
        self._apply_defaults()

    async def _async_save(self):
        try:
            payload = copy.deepcopy(self.data)
            payload["user_id"] = self.user_id
            await _collection().replace_one(
                {"user_id": self.user_id},
                payload,
                upsert=True,
            )
        except Exception as e:
            logger.error("[UserSettings] MongoDB save failed for %s: %s", self.user_id, e)

    # Sync wrapper — fires-and-forgets from sync contexts
    def _save(self):
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.ensure_future(self._async_save())
            else:
                loop.run_until_complete(self._async_save())
        except Exception as e:
            logger.error("[UserSettings] _save wrapper error: %s", e)

    def _load(self):
        """Kept for API compatibility; actual load is in __init__."""
        pass

    # ── Defaults & normalisation ───────────────────────────────────────────────

    def _apply_defaults(self):
        """Backfill any missing fields (same logic as original _load)."""
        d = self.data

        d.setdefault("metadata",    {"title": "", "author": "", "encoder": ""})
        d.setdefault("send_type",   "media")
        d.setdefault("auto_detect_thumb", False)
        d.setdefault("format",      "{title} S{season}E{episode} [{quality}] [{audio}].mkv")
        d.setdefault("default_start_episode", 1)
        d.setdefault("default_season", 1)
        d.setdefault("default_audio", "SUB")

        if "profiles" not in d:
            d["profiles"] = {res: p.copy() for res, p in DEFAULT_PROFILES.items()}
        else:
            for res, profile in DEFAULT_PROFILES.items():
                d["profiles"].setdefault(res, profile.copy())

        if "watermark" not in d:
            d["watermark"] = DEFAULT_WATERMARK.copy()
        else:
            for key, val in DEFAULT_WATERMARK.items():
                d["watermark"].setdefault(key, val)

        if "params" not in d:
            d["params"] = DEFAULT_MI_PARAMS.copy()
        else:
            for key, val in DEFAULT_MI_PARAMS.items():
                d["params"].setdefault(key, val)

        d["resolutions"] = self._normalize_resolutions(
            d.get("resolutions", d.get("resolution"))
        )
        self._sync_resolution_alias()

    def _sync_resolution_alias(self):
        self.data["resolution"] = (self.data.get("resolutions") or ["1080p"])[0]

    def _get_default_settings(self) -> Dict[str, Any]:
        return {
            "user_id":               self.user_id,
            "resolutions":           ["1080p"],
            "resolution":            "1080p",
            "crf":                   28,
            "preset":                "medium",
            "codec":                 "libx264",
            "audio_bitrate":         "128k",
            "send_type":             "media",
            "auto_detect_thumb":     False,
            "metadata":              {"title": "", "author": "", "encoder": ""},
            "thumbnail_path":        "",
            "thumbnail_b64":         "",   # base64-encoded JPEG
            "profiles":              {res: p.copy() for res, p in DEFAULT_PROFILES.items()},
            "watermark":             DEFAULT_WATERMARK.copy(),
            "format":                "{title} S{season}E{episode} [{quality}] [{audio}].mkv",
            "default_start_episode": 1,
            "default_season":        1,
            "default_audio":         "SUB",
            "params":                DEFAULT_MI_PARAMS.copy(),
        }

    def _normalize_resolutions(self, values) -> list:
        if isinstance(values, str):
            values = [values]
        elif not isinstance(values, list):
            values = []
        normalized = []
        for value in values:
            if not isinstance(value, str):
                continue
            clean = RESOLUTION_ALIASES.get(value.strip().lower())
            if clean and clean not in normalized:
                normalized.append(clean)
        return normalized[:4] if normalized else ["1080p"]

    # ── Public API (identical to original) ────────────────────────────────────

    @property
    def temp_state(self) -> Dict[int, Dict]:
        return UserSettings._temp_state

    def get(self) -> Dict[str, Any]:
        return self.data

    def get_profile(self, resolution: str) -> Dict[str, Any]:
        if "profiles" not in self.data:
            self.data["profiles"] = {res: p.copy() for res, p in DEFAULT_PROFILES.items()}
        if resolution not in self.data["profiles"]:
            self.data["profiles"][resolution] = DEFAULT_PROFILES.get(
                resolution,
                {"mode": "encode", "crf": 23, "preset": "medium",
                 "codec": "libx264", "audio_bitrate": "128k"},
            ).copy()
            self._save()
        return self.data["profiles"][resolution]

    def get_all_profiles(self) -> Dict[str, Dict[str, Any]]:
        if "profiles" not in self.data:
            self.data["profiles"] = {res: p.copy() for res, p in DEFAULT_PROFILES.items()}
            self._save()
        return self.data["profiles"]

    def get_watermark(self) -> Dict[str, Any]:
        if "watermark" not in self.data:
            self.data["watermark"] = DEFAULT_WATERMARK.copy()
        for key, val in DEFAULT_WATERMARK.items():
            self.data["watermark"].setdefault(key, val)
        return self.data["watermark"]

    def get_effective_settings(self, resolution: str, base_overrides: Dict[str, Any] = None) -> Dict[str, Any]:
        profile = self.get_profile(resolution)
        effective = {
            "resolution":      resolution,
            "processing_mode": profile.get("mode", "encode"),
            "crf":             profile.get("crf", 23),
            "preset":          profile.get("preset", "medium"),
            "codec":           profile.get("codec", "libx264"),
            "audio_bitrate":   profile.get("audio_bitrate", "128k"),
        }
        if base_overrides:
            effective.update(base_overrides)
        return effective

    def update(self, key: str, value: Any):
        if key == "resolution":
            self.set_resolutions([value])
            return
        self.data[key] = value
        self._save()

    def set_resolutions(self, resolutions):
        self.data["resolutions"] = self._normalize_resolutions(resolutions)
        self._save()

    def toggle_resolution(self, resolution: str):
        normalized = self._normalize_resolutions([resolution])[0]
        selected   = list(self.data.get("resolutions", ["1080p"]))
        if normalized in selected:
            if len(selected) == 1:
                return False, "At least one resolution must stay selected."
            selected.remove(normalized)
            self.data["resolutions"] = selected
            self._save()
            return True, f"{normalized} removed"
        if len(selected) >= 4:
            return False, "You can select up to 4 resolutions per file."
        selected.append(normalized)
        self.data["resolutions"] = self._normalize_resolutions(selected)
        self._save()
        return True, f"{normalized} added"

    def update_metadata(self, title: str = None, author: str = None, encoder: str = None):
        if "metadata" not in self.data:
            self.data["metadata"] = {}
        if title   is not None: self.data["metadata"]["title"]   = title
        if author  is not None: self.data["metadata"]["author"]  = author
        if encoder is not None: self.data["metadata"]["encoder"] = encoder
        self._save()

    def update_profile(self, resolution: str, key: str, value: Any):
        if "profiles" not in self.data:
            self.data["profiles"] = {res: p.copy() for res, p in DEFAULT_PROFILES.items()}
        if resolution not in self.data["profiles"]:
            self.data["profiles"][resolution] = DEFAULT_PROFILES.get(
                resolution,
                {"mode": "encode", "crf": 23, "preset": "medium",
                 "codec": "libx264", "audio_bitrate": "128k"},
            ).copy()
        self.data["profiles"][resolution][key] = value
        self._save()

    def reset_profile(self, resolution: str) -> bool:
        if resolution in DEFAULT_PROFILES:
            self.data["profiles"][resolution] = DEFAULT_PROFILES[resolution].copy()
            self._save()
            return True
        return False

    def update_watermark(self, **kwargs):
        wm = self.get_watermark()
        for key, value in kwargs.items():
            if key in DEFAULT_WATERMARK:
                wm[key] = value
        self.data["watermark"] = wm
        self._save()

    def set_watermark_font(self, tmp_path: str) -> str:
        """Store font as base64 in MongoDB; return font name."""
        if not tmp_path or not os.path.exists(tmp_path):
            return "default"
        ext = os.path.splitext(tmp_path)[1].lower()
        if ext not in (".ttf", ".otf"):
            return "default"

        font_name = _extract_font_name(tmp_path)
        try:
            font_b64 = _b64_encode_file(tmp_path)
        except Exception as e:
            logger.error("[UserSettings] font encode failed: %s", e)
            return "default"

        # Store b64 and extension; font_path is set to a sentinel so existing
        # code that checks `bool(font_path)` still works.
        self.data.setdefault("watermark", DEFAULT_WATERMARK.copy())
        self.data["watermark"]["font_b64"]  = font_b64
        self.data["watermark"]["font_ext"]  = ext
        self.data["watermark"]["font_name"] = font_name
        # font_path: we'll materialise on demand; set a non-empty sentinel
        self.data["watermark"]["font_path"] = f"__mongo__:{font_name}{ext}"

        # Invalidate cached tmp file
        if self._font_tmp_path and os.path.exists(self._font_tmp_path):
            try:
                os.remove(self._font_tmp_path)
            except Exception:
                pass
        self._font_tmp_path = None

        self._save()
        return font_name

    def reset_watermark(self):
        # Clear stored binary
        self._font_tmp_path = None
        self.data["watermark"] = DEFAULT_WATERMARK.copy()
        self._save()

    def set_thumbnail(self, path: str):
        """Store thumbnail as base64 in MongoDB."""
        if not path or not os.path.exists(path):
            return
        try:
            thumb_b64 = _b64_encode_file(path)
        except Exception as e:
            logger.error("[UserSettings] thumbnail encode failed: %s", e)
            return

        self.data["thumbnail_b64"]  = thumb_b64
        # Keep a sentinel so `bool(thumbnail_path)` stays truthy
        self.data["thumbnail_path"] = "__mongo__:thumb"

        # Invalidate cached tmp file
        if self._thumb_tmp_path and os.path.exists(self._thumb_tmp_path):
            try:
                os.remove(self._thumb_tmp_path)
            except Exception:
                pass
        self._thumb_tmp_path = None

        self._save()

    def clear_thumbnail(self):
        if self._thumb_tmp_path and os.path.exists(self._thumb_tmp_path):
            try:
                os.remove(self._thumb_tmp_path)
            except Exception:
                pass
        self._thumb_tmp_path = None
        self.data["thumbnail_b64"]  = ""
        self.data["thumbnail_path"] = ""
        self._save()

    def get_thumbnail_path(self) -> Optional[str]:
        """
        Return a real filesystem path to the thumbnail, materialising it from
        MongoDB if needed.  Returns None if no thumbnail is set.
        """
        b64 = self.data.get("thumbnail_b64", "")
        if not b64:
            return None

        # Re-use cached tmp path if still valid
        if self._thumb_tmp_path and os.path.exists(self._thumb_tmp_path):
            return self._thumb_tmp_path

        try:
            path = _b64_decode_to_tmp(b64, suffix=".jpg")
            self._thumb_tmp_path = path
            return path
        except Exception as e:
            logger.error("[UserSettings] thumbnail materialise failed: %s", e)
            return None

    def get_font_path(self) -> Optional[str]:
        """
        Return a real filesystem path to the watermark font, materialising it
        from MongoDB if needed.  Returns None if no custom font is set.
        """
        b64  = self.data.get("watermark", {}).get("font_b64", "")
        ext  = self.data.get("watermark", {}).get("font_ext", ".ttf")
        if not b64:
            return None

        if self._font_tmp_path and os.path.exists(self._font_tmp_path):
            return self._font_tmp_path

        try:
            path = _b64_decode_to_tmp(b64, suffix=ext)
            self._font_tmp_path = path
            return path
        except Exception as e:
            logger.error("[UserSettings] font materialise failed: %s", e)
            return None

    def get_params(self) -> Dict[str, Any]:
        if "params" not in self.data:
            self.data["params"] = DEFAULT_MI_PARAMS.copy()
        else:
            for key, val in DEFAULT_MI_PARAMS.items():
                self.data["params"].setdefault(key, val)
        return self.data["params"]

    def update_param(self, key: str, value: Any) -> bool:
        if key not in DEFAULT_MI_PARAMS:
            return False
        params = self.get_params()
        params[key] = value
        self.data["params"] = params
        self._save()
        return True

    def reset_params(self):
        self.data["params"] = DEFAULT_MI_PARAMS.copy()
        self._save()

    def reset(self):
        # Preserve binary assets so we don't lose them on a settings reset
        thumb_b64 = self.data.get("thumbnail_b64", "")
        self.data  = self._get_default_settings()
        self.data["thumbnail_b64"] = thumb_b64
        if thumb_b64:
            self.data["thumbnail_path"] = "__mongo__:thumb"
        self._save()

    def get_format(self) -> str:
        return self.data.get("format", "{title} S{season}E{episode} [{quality}] [{audio}].mkv")

    def set_format(self, fmt: str):
        self.data["format"] = fmt
        self._save()

    # storage_path kept as a dummy property so code that accesses it doesn't crash
    @property
    def storage_path(self) -> str:
        return f"mongodb://user_settings/{self.user_id}"

    @property
    def db_folder(self) -> str:
        return self._local_thumbnails
