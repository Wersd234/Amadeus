"""
Central configuration for Project Amadeus Discord Bot.

All tunable parameters live here so they can be adjusted without
touching business logic. Environment variables are loaded once at
import time via python-dotenv.
"""

import os
import logging
from dotenv import load_dotenv

load_dotenv()

# Structured logging with timestamps for Docker log aggregation
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("amadeus")

# ── Paths ──────────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
PROMPTS_DIR = os.path.join(PROJECT_ROOT, "properties", "system_prompts")

# Runtime JSON files (stored in data/ for Docker volume mount safety)
WORLDLINE_FILE = os.path.join(DATA_DIR, "worldline.json")
AFFINITY_FILE = os.path.join(DATA_DIR, "affinity.json")
MEMORY_FILE = os.path.join(DATA_DIR, "memory.json")
SETTINGS_FILE = os.path.join(DATA_DIR, "server_settings.json")
PTV_SUBSCRIPTIONS_FILE = os.path.join(DATA_DIR, "ptv_subscriptions.json")

# ── Discord ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# ── LLM Backend ────────────────────────────────────────────────────
# OpenAI-compatible API.  Model name is NOT sent to the server —
# the backend (llama.cpp / vLLM / etc.) uses whatever is loaded.
# We keep it for logging only; pass MODEL_NAME="" to suppress entirely.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.0.58:1234/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "123")
MODEL_NAME = os.getenv("LLM_MODEL_NAME", "")
CTX_SIZE = int(os.getenv("LLM_CTX_SIZE", "32768"))

# Vision support — auto-detected from MODEL_NAME if not explicitly set.
# Only Qwen models accept image inputs; DeepSeek-V4 and others don't.
# Set LLM_VISION_ENABLED=true to force on, false to force off.
# Leave unset and we'll check if "qwen" appears in MODEL_NAME.
_vision_env = os.getenv("LLM_VISION_ENABLED", "").lower()
if _vision_env == "true":
    LLM_VISION_ENABLED = True
elif _vision_env == "false":
    LLM_VISION_ENABLED = False
else:
    LLM_VISION_ENABLED = "qwen" in MODEL_NAME.lower()

# ── Tavily Search API ──────────────────────────────────────────────
# Sign up at https://tavily.com — free tier includes 1,000 searches/month.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Free-Speaking Mode (群友自由发言) ──────────────────────────────
# The bot monitors all messages in channels it can see and occasionally
# jumps into conversations naturally, like a real group member.
# These parameters control the frequency to avoid spam.

# After this many messages accumulate in a channel, the bot considers chiming in
FREE_SPEAK_MSG_THRESHOLD = 12
# Minimum minutes between two free-speaking interjections (per channel)
FREE_SPEAK_COOLDOWN_MIN = 8
# Probability (0.0–1.0) of chiming in when the threshold is hit
FREE_SPEAK_PROB = 0.06
# Minimum messages the bot must have seen before its first interjection
# in a channel (prevents jumping in immediately after restart)
FREE_SPEAK_WARMUP_MSGS = 20

HISTORY_MAXLEN = 20
MAX_REPLY_TOKENS = 1024
MAX_MEMORY_TOKENS = 512

CHAT_SAMPLING = {
    "temperature": 0.85,
    "top_p": 0.95,
    "presence_penalty": 0.15,
    "frequency_penalty": 0.0,
}
CHAT_SAMPLING_EXTRA = {
    "top_k": 64,
    "min_p": 0.05,
    "repeat_penalty": 1.05,
    "cache_prompt": True,
}
MEMORY_SAMPLING = {
    "temperature": 0.3,
    "top_p": 0.9,
}

# ── PTV Timetable API v3 ───────────────────────────────────────────
PTV_DEVELOPER_ID = os.getenv("PTV_DEVELOPER_ID", "")
PTV_API_KEY = os.getenv("PTV_API_KEY", "")
PTV_BASE_URL = "https://timetableapi.ptv.vic.gov.au"

PTV_ROUTE_TYPES = {
    "train": 0,
    "tram": 1,
    "bus": 2,
}

# ── Worldline System ───────────────────────────────────────────────
WORLDLINE_REFRESH_HOURS = int(os.getenv("WORLDLINE_REFRESH_HOURS", "6"))
DMAIL_SHIFT_PROB = float(os.getenv("DMAIL_SHIFT_PROB", "0.2"))

WORLDLINE_MODES = [
    (0.000000, 0.999999, "alpha",   0.15),
    (1.000000, 1.999999, "beta",    0.80),
    (2.000000, 2.999999, "special", 0.05),
]

# Maximum entries kept in the worldline history log
WORLDLINE_HISTORY_MAX = 50

# ── Worldline Convergence (收束) ────────────────────────────────────
# Once per day, the bot rolls for a convergence event.  During convergence,
# D-Mail shifts are blocked (0% probability) because "time is converging."
# This lasts CONVERGENCE_DURATION_HOURS and then clears.
CONVERGENCE_DAILY_PROB = 0.25        # 25% chance per day
CONVERGENCE_DURATION_HOURS = 2       # How long convergence lasts

# ── 1.048596 Steins Gate Worldline ─────────────────────────────────
# When the divergence value is within this tolerance of the canon
# "Steins Gate" value, the bot enters a subtly warmer, more genuine state.
STEINS_GATE_VALUE = 1.048596
STEINS_GATE_TOLERANCE = 0.000001     # ±0.000001 → effectively exact match needed

# ── @channel Late-Night Slip Mode ──────────────────────────────────
# During these hours (local time), each reply has a chance to leak
# Kurisu's secret @channel forum-posting persona.
CHANNEL_SLIP_PROB = 0.15
CHANNEL_SLIP_START_HOUR = 0          # Midnight
CHANNEL_SLIP_END_HOUR = 3            # 3 AM

# ── PTV Commute Subscription ───────────────────────────────────────
# How often the background task wakes up to check subscriptions (minutes)
PTV_SUB_CHECK_INTERVAL_MIN = 10
# How many minutes before the scheduled time to start checking
PTV_SUB_LOOKAHEAD_MIN = 30

# ── Affinity System ────────────────────────────────────────────────
AFFINITY_DEFAULT = 0

AFFINITY_TIERS = [
    (0,   4,  "stranger",    "🙂"),
    (5,   19, "acquainted",  "😤"),
    (20,  999,"familiar",    "💢"),
]

# ── Keyword Eavesdropping ──────────────────────────────────────────
KEYWORD_TRIGGERS = [
    (["kurisu", "kristina", "kurisutina"], 0.60),
    (["christina", "克里斯蒂娜"], 0.60),
    (["assistant", "助手"], 0.60),
    (["zombie", "僵尸"], 0.50),
    (["dr pepper", "dr. pepper", "dr.pepper"], 0.35),
    (["train", "火车", "metro", "metrotrain"], 0.20),
    (["tram", "电车"], 0.20),
    (["ptv", "myki"], 0.25),
    (["weather", "天气", "墨尔本天气"], 0.15),
    (["steins gate", "stein's gate", "命运石之门", "石头门"], 0.40),
]

KEYWORD_COOLDOWN_SEC = 120

# ── Tavily Search API ────────────────────────────────────────────
# Free tier: 1000 searches/month.  Sign up at https://tavily.com
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Free Speech (主动发言) ─────────────────────────────────────────
# The bot occasionally speaks unprompted in active channels, like a
# real group member who chimes in when something interesting happens.
# Check interval: how often the bot scans for opportunities to speak.
FREE_SPEECH_CHECK_MIN = 15
# Cooldown per channel: minimum minutes between unprompted messages.
FREE_SPEECH_COOLDOWN_MIN = 45
# Probability that, during a check cycle, the bot will speak in an
# eligible channel (provided cooldown has elapsed and there is recent
# human activity).  Keep this low to avoid being spammy.
FREE_SPEECH_PROB = 0.12
# A channel must have at least this many human messages in the last
# N minutes to be considered "active".
FREE_SPEECH_MIN_RECENT_MSGS = 3
FREE_SPEECH_ACTIVE_WINDOW_MIN = 30

# ── Emoji Reactions ────────────────────────────────────────────────
EMOTION_EMOJIS = {
    "happy":     "✨",
    "angry":     "💢",
    "sad":       "💧",
    "surprised": "❗",
    "other":     "🍡",
}

# ── System Prompt Loading ──────────────────────────────────────────
def _load_prompt(filename: str) -> str:
    path = os.path.join(PROMPTS_DIR, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logger.warning("Prompt file not found: %s", path)
        return ""

SYSTEM_PROMPTS = {
    "beta": {
        "zh": _load_prompt("base_zh.txt"),
        "en": _load_prompt("base_en.txt"),
    },
    "alpha": {
        "zh": _load_prompt("alpha_zh.txt"),
        "en": _load_prompt("alpha_en.txt"),
    },
    "special": {
        "zh": _load_prompt("special_zh.txt"),
        "en": _load_prompt("special_en.txt"),
    },
}