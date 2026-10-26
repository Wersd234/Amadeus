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
IMAGE_INDEX_FILE = os.path.join(DATA_DIR, "image_index.json")

# ── Character Image (立绘) ─────────────────────────────────────
# Sprite image directory — organised by outfit subdirectories.
# CSV index file maps each sprite to emotion/outfit metadata.
CHARACTER_IMAGE_DIR = os.path.join(PROJECT_ROOT, "resource", "kurisu")
IMAGE_CSV_PATH = os.path.join(PROJECT_ROOT, "resource", "分类结果清单.csv")

# Map LLM emotion tags → CSV emotion names.
# Each LLM tag can match multiple CSV emotions; one is picked at random
# for variety when the same emotion is triggered repeatedly.
EMOTION_IMAGE_MAP = {
    # LLM emotion → CSV 表情名称 (matched against 分类结果清单.csv)
    # If a CSV emotion does not exist in the index, it is silently skipped.
    "happy":     ["01-平静自然", "02-微笑", "06-开心"],
    "angry":     ["06-愤怒烦躁", "05-苦恼不快"],
    "sad":       ["05-苦恼不快", "07-悲伤"],
    "surprised": ["08-惊讶", "09-慌张"],
    "other":     ["01-平静自然", "10-思考"],
}


# ── Discord ────────────────────────────────────────────────────────
DISCORD_TOKEN = os.getenv("DISCORD_BOT_TOKEN")

# ── LLM Backend ────────────────────────────────────────────────────
# OpenAI-compatible API.  Model name is NOT sent to the server —
# the backend (llama.cpp / vLLM / etc.) uses whatever is loaded.
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://192.168.0.58:1234/v1")
LLM_API_KEY = os.getenv("LLM_API_KEY", "123")
CTX_SIZE = int(os.getenv("LLM_CTX_SIZE", "32768"))

# ── Model Auto-Detection ────────────────────────────────────────
# The bot queries the LLM backend's /models endpoint at startup to
# discover which model is currently loaded — no need to manually set
# LLM_MODEL_NAME when switching between DeepSeek-V4 and Qwen.
# If the endpoint is unreachable or the env var is set, uses that.
def _detect_model():
    try:
        import httpx
        base = LLM_BASE_URL.rstrip("/v1").rstrip("/")
        for path in ("/v1/models", "/models"):
            try:
                resp = httpx.get(f"{base}{path}", timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("data", [])
                    if models and isinstance(models, list):
                        name = models[0].get("id", "")
                        if name:
                            logger.info("Auto-detected model: %s", name)
                            return name
            except Exception:
                continue
    except Exception:
        pass
    return ""

MODEL_NAME = os.getenv("LLM_MODEL_NAME", "") or _detect_model()

# Vision: auto-detected from MODEL_NAME (qwen→on, others→off).
# Set LLM_VISION_ENABLED=true/false to override.
_vision_env = os.getenv("LLM_VISION_ENABLED", "").lower()
if _vision_env == "true":
    LLM_VISION_ENABLED = True
elif _vision_env == "false":
    LLM_VISION_ENABLED = False
else:
    LLM_VISION_ENABLED = "qwen" in MODEL_NAME.lower()

if MODEL_NAME:
    logger.info("Model: %s | Vision: %s", MODEL_NAME, LLM_VISION_ENABLED)


def refresh_model():
    """
    Re-detect the loaded model from the backend.

    Call this periodically (e.g. every 10 minutes) to catch mid-session
    model switches without restarting the bot.

    Returns True if the model or vision state changed.
    """
    global MODEL_NAME, LLM_VISION_ENABLED
    detected = _detect_model()
    if not detected:
        return False  # Backend unreachable -- keep current state

    old_name = MODEL_NAME
    old_vision = LLM_VISION_ENABLED

    # Env var still takes priority
    env_name = os.getenv("LLM_MODEL_NAME", "")
    MODEL_NAME = env_name or detected

    _vision_env = os.getenv("LLM_VISION_ENABLED", "").lower()
    if _vision_env == "true":
        LLM_VISION_ENABLED = True
    elif _vision_env == "false":
        LLM_VISION_ENABLED = False
    else:
        LLM_VISION_ENABLED = "qwen" in MODEL_NAME.lower()

    changed = (MODEL_NAME != old_name or LLM_VISION_ENABLED != old_vision)
    if changed:
        logger.info(
            "Model changed: %s -> %s | Vision: %s -> %s",
            old_name or "(none)", MODEL_NAME,
            old_vision, LLM_VISION_ENABLED,
        )
    return changed


def refresh_model() -> bool:
    """
    Re-detect the loaded model from the backend.

    Call this periodically (e.g. every 10 minutes) to catch mid-session
    model switches without restarting the bot.

    Returns True if the model or vision state changed.
    """
    global MODEL_NAME, LLM_VISION_ENABLED
    detected = _detect_model()
    if not detected:
        return False  # Backend unreachable — keep current state

    old_name = MODEL_NAME
    old_vision = LLM_VISION_ENABLED

    # Env var still takes priority
    env_name = os.getenv("LLM_MODEL_NAME", "")
    MODEL_NAME = env_name or detected

    _vision_env = os.getenv("LLM_VISION_ENABLED", "").lower()
    if _vision_env == "true":
        LLM_VISION_ENABLED = True
    elif _vision_env == "false":
        LLM_VISION_ENABLED = False
    else:
        LLM_VISION_ENABLED = "qwen" in MODEL_NAME.lower()

    changed = (MODEL_NAME != old_name or LLM_VISION_ENABLED != old_vision)
    if changed:
        logger.info(
            "Model changed: %s → %s | Vision: %s → %s",
            old_name or "(none)", MODEL_NAME,
            old_vision, LLM_VISION_ENABLED,
        )
    return changed

# ── Tavily Search API ──────────────────────────────────────────────
# Sign up at https://tavily.com — free tier includes 1,000 searches/month.
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# ── Free-Speaking: Interest-Driven Reply (兴趣驱动回复) ────────
# The bot reads EVERY message and decides whether to reply based on
# content relevance — not arbitrary message counts or cooldowns.
# Like a real person: if the topic is interesting, jump in.
# If it's boring small talk, stay silent.
#
# Categories are checked in order (first match wins).  Each category
# has keyword lists and a reply probability.  A single message can
# only trigger ONE category.
#
# DM channels use stricter rules: only direct questions and science
# topics trigger replies.  Groups are more relaxed.

# Interest categories: (label, keywords, prob_group, prob_dm)
# prob_group = probability in group channels
# prob_dm    = probability in DM channels (usually much lower)
FREE_SPEAK_INTERESTS = [
    # ── Always-engage (high priority) ──
    ("question", [
        # Matches messages that ask the group a question
        "?", "？", "吗", "呢", "what", "how", "why", "who", "when",
        "有人知道", "谁知道", "请问", "anyone", "does anyone",
        "怎么", "哪里", "哪", "是不是", "能不能", "can you", "could you",
    ], 0.40, 0.30),

    # ── Science & tech (core identity) ──
    ("science", [
        "科学", "研究", "论文", "实验", "量子", "物理", "数学",
        "science", "research", "paper", "experiment", "quantum", "physics", "math",
        "AI", "人工智能", "代码", "code", "程序", "编程", "algorithm", "算法",
        "神经", "neural", "brain", "大脑", "psychology", "心理",
        "university", "大学", "uni", "PhD", "博士", "academic", "学术",
    ], 0.30, 0.25),

    # ── Melbourne life (local relevance) ──
    ("melbourne", [
        "melbourne", "墨尔本", "melb", "CBD", "city", "flinders",
        "train", "tram", "bus", "火车", "电车", "公交", "ptv", "myki",
        "晚点", "delay", "delay", "cancel", "取消",
        "clayton", "caulfield", "south yarra", "richmond",
        "weather", "天气", "rain", "下雨", "cold", "冷", "hot", "热",
        "维妈", "Queen Vic", "图书馆", "library",
    ], 0.25, 0.15),

    # ── Food & drink ──
    ("food", [
        "dr pepper", "dr. pepper", "dr.pepper", "胡椒博士",
        "coffee", "咖啡", "latte", "flat white", "cappuccino",
        "bubble tea", "奶茶", "好吃", "yum", "food", "吃", "喝",
        "餐厅", "restaurant", "brunch", "晚饭", "午饭", "dinner", "lunch",
    ], 0.22, 0.10),

    # ── Gaming / anime / otaku culture ──
    ("otaku", [
        "anime", "动漫", "游戏", "game", "gaming", "steam",
        "steins gate", "命运石之门", "石头门", "sg0",
        "genshin", "原神", "崩坏", "honkai", "star rail",
        "league", "lol", "valorant", "apex", "elden ring",
        "switch", "ps5", "nintendo", "manga", "漫画",
        "cosplay", "漫展", "convention",
    ], 0.25, 0.12),

    # ── Strong emotion / venting ──
    ("emotion", [
        "！！", "？！", "...", "……",
        "卧槽", "我靠", "救命", "啊啊啊", "呜呜", "T.T", "T_T", "orz",
        "fuck", "shit", "damn", "omg", "bruh",
        "气死", "烦死", "笑死", "哭了", "崩溃", "无语",
        "好累", "好困", "好烦", "好爽", "太棒了", "绝了",
    ], 0.28, 0.15),
]

# Absolute minimum seconds between any two free-speaking replies.
# Safety net only — the interest model is the real governor.
FREE_SPEAK_COOLDOWN_SEC = 25

# Do NOT reply to consecutive messages from the same user (avoid stalker vibe).
FREE_SPEAK_SAME_USER_COOLDOWN = True

# ── Conversation Continuity (对话线程) ──────────────────────────
# If the bot just replied to someone and that person replies back,
# it is a conversation thread — keep replying even if the content
# does not match any interest category.  This prevents:
#   群友: Amadeus 在吗  →  Bot: 在的
#   群友: 我失眠了      →  Bot: [沉默]  ← BAD
# After FREE_SPEAK_CONTINUATION_WINDOW_SEC of silence, the thread
# expires and the bot goes back to interest-only mode.
FREE_SPEAK_CONTINUATION_WINDOW_SEC = 180
FREE_SPEAK_CONTINUATION_PROB = 0.70

# ── Conversation Continuity (对话线程) ──────────────────────────
# If the bot just replied to someone and that person replies back,
# it is a conversation thread — keep replying even if the content
# does not match any interest category.  This prevents:
#   群友: Amadeus 在吗  →  Bot: 在的
#   群友: 我失眠了      →  Bot: [沉默]  ← BAD
# After FREE_SPEAK_CONTINUATION_WINDOW_SEC of silence, the thread
# expires and the bot goes back to interest-only mode.
FREE_SPEAK_CONTINUATION_WINDOW_SEC = 180
FREE_SPEAK_CONTINUATION_PROB = 0.70

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