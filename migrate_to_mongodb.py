"""
One-time migration: read all src/bin/users/<user_id>.json files and upsert
them into MongoDB.  Run ONCE before switching to the new bot version.

Usage:
    MONGODB_URI=... MONGODB_DB_NAME=encode_bot python migrate_to_mongodb.py

Reads thumbnails from src/bin/thumbnails/thumb_<user_id>.jpg and stores
them as base64 in the MongoDB document.
"""

import asyncio
import base64
import json
import os
import sys

import motor.motor_asyncio
from dotenv import load_dotenv

load_dotenv()

MONGO_URI = os.environ.get("MONGODB_URI", "")
MONGO_DB  = os.environ.get("MONGODB_DB_NAME", "encode_bot")
USERS_DIR = os.path.join(os.path.dirname(__file__), "src", "bin", "users")
THUMBS_DIR = os.path.join(os.path.dirname(__file__), "src", "bin", "thumbnails")
FONTS_DIR  = os.path.join(os.path.dirname(__file__), "src", "bin", "fonts")


def _b64(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")
    except Exception:
        return ""


async def migrate():
    if not MONGO_URI:
        print("ERROR: MONGODB_URI not set")
        sys.exit(1)

    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=10_000)
    db = client[MONGO_DB]
    col = db["user_settings"]
    await col.create_index("user_id", unique=True)

    if not os.path.isdir(USERS_DIR):
        print(f"No users directory found at {USERS_DIR!r} — nothing to migrate.")
        return

    files = [f for f in os.listdir(USERS_DIR) if f.endswith(".json")]
    print(f"Found {len(files)} user JSON files to migrate.")

    ok = 0
    fail = 0
    for fname in files:
        path = os.path.join(USERS_DIR, fname)
        try:
            with open(path, "r") as fh:
                data = json.load(fh)

            user_id = data.get("user_id")
            if user_id is None:
                # derive from filename
                try:
                    user_id = int(fname.replace(".json", ""))
                    data["user_id"] = user_id
                except ValueError:
                    print(f"  SKIP {fname}: cannot determine user_id")
                    fail += 1
                    continue

            # Embed thumbnail
            thumb_path = os.path.join(THUMBS_DIR, f"thumb_{user_id}.jpg")
            if os.path.exists(thumb_path):
                data["thumbnail_b64"]  = _b64(thumb_path)
                data["thumbnail_path"] = "__mongo__:thumb"
                print(f"  {fname}: thumbnail embedded ({os.path.getsize(thumb_path)//1024} KB)")
            else:
                data.setdefault("thumbnail_b64", "")

            # Embed font (if stored in watermark.font_path)
            wm = data.get("watermark", {})
            font_path_local = wm.get("font_path", "")
            if font_path_local and os.path.exists(font_path_local):
                ext = os.path.splitext(font_path_local)[1].lower()
                font_b64 = _b64(font_path_local)
                if font_b64:
                    wm["font_b64"] = font_b64
                    wm["font_ext"] = ext
                    wm["font_path"] = f"__mongo__:{wm.get('font_name', 'font')}{ext}"
                    data["watermark"] = wm
                    print(f"  {fname}: watermark font embedded")

            await col.replace_one({"user_id": user_id}, data, upsert=True)
            print(f"  {fname}: migrated (user_id={user_id})")
            ok += 1

        except Exception as e:
            print(f"  {fname}: FAILED — {e}")
            fail += 1

    print(f"\nDone. Migrated: {ok}  Failed: {fail}")
    client.close()


if __name__ == "__main__":
    asyncio.run(migrate())
