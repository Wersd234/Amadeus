"""
Character sprite image index.

Reads 分类结果清单.csv (a metadata table mapping image filenames to
emotion/outfit/pose) and builds a fast lookup dict serialised to
data/image_index.json.  The JSON cache is regenerated only when the
CSV is newer, so the bot does not re-parse it on every restart.

Format of the built index:
  {
    "01-平静自然": ["resource/kurisu/Makise Kurisu(风衣)/crs_asa01a.png", ...],
    "05-苦恼不快": ["resource/kurisu/Makise Kurisu(风衣)/crs_asa03a.png", ...],
    ...
  }
"""

import csv
import json
import os
import random

from config import (
    logger, IMAGE_CSV_PATH, IMAGE_INDEX_FILE, CHARACTER_IMAGE_DIR,
)


def build_index() -> dict:
    """
    Parse CSV and return {emotion_name: [relative_path, ...]} dict.

    If the CSV does not exist, returns an empty dict gracefully so
    the bot can still start — image features will simply be unavailable.
    """
    if not os.path.exists(IMAGE_CSV_PATH):
        logger.warning("Image CSV not found at %s — image features disabled.", IMAGE_CSV_PATH)
        return {}

    index: dict[str, list[str]] = {}
    try:
        with open(IMAGE_CSV_PATH, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                filename = (row.get("文件名") or "").strip()
                source_dir = (row.get("来源目录") or "").strip()
                emotion_name = (row.get("表情名称") or "").strip()

                if not filename or not emotion_name:
                    continue

                # Build relative path from CHARACTER_IMAGE_DIR
                rel = os.path.join(source_dir, filename) if source_dir else filename

                index.setdefault(emotion_name, []).append(rel)
    except Exception as exc:
        logger.error("Failed to parse image CSV: %s", exc)
        return {}

    logger.info("Image index built: %d emotion categories, %d total sprites",
                len(index), sum(len(v) for v in index.values()))

    # Persist cache
    os.makedirs(os.path.dirname(IMAGE_INDEX_FILE), exist_ok=True)
    tmp = IMAGE_INDEX_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, indent=2, ensure_ascii=False)
    os.replace(tmp, IMAGE_INDEX_FILE)

    return index


def load_index() -> dict:
    """
    Return the image index, rebuilding from CSV if necessary.

    Strategy: if the cached JSON exists and is newer than the CSV,
    load from cache.  Otherwise rebuild.
    """
    if os.path.exists(IMAGE_INDEX_FILE):
        csv_mtime = os.path.getmtime(IMAGE_CSV_PATH) if os.path.exists(IMAGE_CSV_PATH) else 0
        json_mtime = os.path.getmtime(IMAGE_INDEX_FILE)
        if json_mtime >= csv_mtime:
            try:
                with open(IMAGE_INDEX_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass

    return build_index()


def get_image_for_emotion(emotion_tag: str, index: dict | None = None) -> str | None:
    """
    Pick a random sprite path matching an LLM emotion tag.

    Parameters
    ----------
    emotion_tag : str
        e.g. 'happy', 'angry', 'sad', 'surprised', 'other'
    index : dict | None
        Pre-loaded index; if None, loads from cache.

    Returns
    -------
    str | None
        Absolute path to a PNG file, or None if no matching image exists.
    """
    from config import EMOTION_IMAGE_MAP

    if index is None:
        index = load_index()

    if not index:
        return None

    # Get the list of CSV emotion names for this LLM tag
    csv_emotions = EMOTION_IMAGE_MAP.get(emotion_tag.lower(), EMOTION_IMAGE_MAP.get("other", []))

    # Collect all matching sprites
    candidates = []
    for csv_em in csv_emotions:
        paths = index.get(csv_em, [])
        candidates.extend(paths)

    if not candidates:
        return None

    chosen = random.choice(candidates)
    return os.path.join(CHARACTER_IMAGE_DIR, chosen)