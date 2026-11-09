"""
LLM API interface — thin wrapper around OpenAI-compatible backends.

Supports:
  - Standard chat completions (OpenAI / llama.cpp / LM Studio / DeepSeek)
  - Multimodal image input (base64 JPEG) with automatic text-only fallback
  - JSON-mode extraction from model output (brace-matching, not regex)
  - Memory summarisation (low-temperature, fact-extraction only)
"""

import json
import re
from openai import AsyncOpenAI

from config import (
    logger, LLM_BASE_URL, LLM_API_KEY, MODEL_NAME,
    LLM_VISION_ENABLED,
    CHAT_SAMPLING, CHAT_SAMPLING_EXTRA, MEMORY_SAMPLING,
    MAX_REPLY_TOKENS, MAX_MEMORY_TOKENS,
)

# Single shared client — httpx connection pooling is handled internally
_llm_client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)


# ═══════════════════════════════════════════════════════════════════
# JSON Extraction
# ═══════════════════════════════════════════════════════════════════

def _extract_json(text: str) -> dict | None:
    """
    Extract the first valid JSON object from model output.

    Uses brace-depth balancing rather than regex so nested objects
    (e.g. JSON inside a reply string) are handled correctly.
    If the model outputs a chain-of-thought before the JSON, this
    will skip past it and find the first balanced `{...}` block.
    """
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        break  # Not valid JSON — try next `{`
        start = text.find("{", start + 1)
    return None


# ═══════════════════════════════════════════════════════════════════
# Reply Sanitising
# ═══════════════════════════════════════════════════════════════════

# Strip common chain-of-thought / thinking wrappers that models leak
# (DeepSeek <|channel>thought, generic <thinking>…</thinking>, etc.).
_COT_PATTERNS = [
    (r"<\|channel>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<\|assistant>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<\|user>thought.*?(?=\n\n|\Z)", "", re.DOTALL),
    (r"<thinking>.*?</thinking>", "", re.DOTALL),
    (r"<reasoning>.*?</reasoning>", "", re.DOTALL),
    (r"\[thinking\].*?\[/thinking\]", "", re.DOTALL),
]


def _strip_fw_narration(text: str) -> str:
    """
    Strip inner-monologue that models wrap in full-width parentheses
    （...） or frame with orphan full-width close-parens （）lines like:
        ）这个称呼真是够了……
        ）
        （刚在整理实验数据……
        反正也是熟人了……
        ）
    A real group member never posts their internal OS, so all of this
    must be dropped, keeping only the actual spoken line.
    """
    # 1. Remove whole （…） blocks, including multi-line ones.
    text = re.sub(r"（[^（]*?）", "", text, flags=re.DOTALL)
    # 2. Line-by-line: drop pure-paren lines and lines that open with ）.
    kept = []
    for line in text.split("\n"):
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"[（）]+", s):
            continue
        if s.startswith("）"):
            continue
        kept.append(s)
    cleaned = "\n".join(kept)
    # 3. Clean any residue: nested paren blocks, orphan parens on edges.
    cleaned = re.sub(r"（[^（]*?）", "", cleaned)
    cleaned = cleaned.lstrip("（） ").rstrip("（） ")
    return cleaned.strip()


def _clean_punctuation(text: str) -> str:
    """
    Strip overflow/excess punctuation while keeping the meaningful ones.

    - Collapse runs of the SAME punctuation to a single one (！！！→！,
      ？？→？, ，，→，) so the tone keeps its force without the clutter.
    - Keep ellipsis （……） and single meaning-bearing marks（？！，。…）.
    - Remove stray punctuation at the very start of a message.
    """
    if not text:
        return text
    # Normalize any run of dots/… into a single standard ellipsis ……
    text = re.sub(r"\.{3,}", "……", text)
    text = re.sub(r"…+", "……", text)
    # Collapse repeated identical punctuation to one instance
    text = re.sub(r"([！？，。；：、,;:!?])\1{1,}", r"\1", text)
    # Drop stray leading punctuation
    text = re.sub(r"^[！？，。；：、,;:\s…]+", "", text)
    return text.strip()


def clean_reply_text(text: str) -> str:
    """
    Remove leaked chain-of-thought / inner-monologue from a reply
    before it is sent to the channel.

    A real group member never posts their internal OS, so anything the
    model "thinks" must not appear in the bubble text. Also trims stray
    JSON braces the model may have leaked around the reply.
    """
    if not text:
        return "……"
    cleaned = text
    for pat, repl, *flags in _COT_PATTERNS:
        cleaned = re.sub(pat, repl, cleaned, flags=flags[0] if flags else 0)
    # Drop any dangling thinking markers / stray JSON tokens
    cleaned = cleaned.replace("</thinking>", "").replace("[think]", "")
    # Full-width-paren narration (Chinese model leak style)
    cleaned = _strip_fw_narration(cleaned)
    # Overflow / excess punctuation
    cleaned = _clean_punctuation(cleaned)
    # Never return an empty message — fall back to a silent ellipsis so
    # the bot still visibly acknowledges the message.
    return cleaned.strip() or "……"


# ═══════════════════════════════════════════════════════════════════
# Core Chat Call
# ═══════════════════════════════════════════════════════════════════

async def _chat_once(
    messages: list[dict],
    max_tokens: int,
    sampling: dict,
    extra: dict | None = None,
) -> str | None:
    """
    Single-turn call to the LLM.

    If MODEL_NAME is empty, the model field is omitted entirely —
    the backend (llama.cpp, vLLM) uses whatever is loaded.
    Returns the `content` string from the first choice, or None on error.
    """
    kwargs = dict(
        messages=messages,
        max_tokens=max_tokens,
        **sampling,
    )
    # Only include model if explicitly set (llama.cpp ignores it anyway)
    if MODEL_NAME:
        kwargs["model"] = MODEL_NAME
    if extra:
        kwargs["extra_body"] = extra

    response = await _llm_client.chat.completions.create(**kwargs)
    if not getattr(response, "choices", None) or len(response.choices) == 0:
        return None
    return response.choices[0].message.content


def _has_image(messages: list[dict]) -> bool:
    """Check whether any message contains an image_url content block."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def _strip_images(messages: list[dict]) -> list[dict]:
    """
    Convert multimodal messages to text-only.

    Used as a fallback when the LLM backend doesn't support vision
    (common with llama.cpp without mmproj loaded).
    """
    stripped = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "")
                for p in content
                if isinstance(p, dict) and p.get("type") == "text"
            ]
            stripped.append({**m, "content": " ".join(text_parts).strip() or "[sent an image]"})
        else:
            stripped.append(m)
    return stripped


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def vision_available() -> bool:
    """Check whether the currently loaded model supports image inputs."""
    return LLM_VISION_ENABLED


async def generate_reply(messages: list[dict]) -> dict | None:
    """
    Send conversation to LLM and parse structured JSON response.

    If the message contains an image but vision is not available,
    returns a special sentinel: {"emotion": "other", "reply": "__VISION_UNAVAILABLE__"}
    so the caller can handle the user-facing message.

    Returns None on total failure.
    """
    # Pre-flight: if images are present but vision is disabled, bail early
    if _has_image(messages) and not LLM_VISION_ENABLED:
        logger.info("Image received but vision is disabled (non-Qwen model loaded).")
        return {"emotion": "other", "reply": "__VISION_UNAVAILABLE__"}

    try:
        raw = await _chat_once(messages, MAX_REPLY_TOKENS, CHAT_SAMPLING, CHAT_SAMPLING_EXTRA)
    except Exception as exc:
        # Fallback: strip images and retry (covers edge cases where
        # vision-enabled model still chokes on a specific image)
        if _has_image(messages):
            logger.warning("Image request failed (%s) — retrying text-only...", exc)
            try:
                raw = await _chat_once(
                    _strip_images(messages),
                    MAX_REPLY_TOKENS,
                    CHAT_SAMPLING,
                    CHAT_SAMPLING_EXTRA,
                )
            except Exception as exc2:
                logger.error("LLM text-only retry also failed: %s", exc2)
                return None
        else:
            logger.error("LLM API error: %s", exc)
            return None

    if not raw:
        return None

    parsed = _extract_json(raw.strip())
    if parsed is not None:
        if isinstance(parsed.get("reply"), str):
            parsed["reply"] = clean_reply_text(parsed["reply"])
        return parsed

    # Model didn't output JSON — use the raw text as the reply
    return {"emotion": "other", "reply": clean_reply_text(raw.strip())}


async def summarize_memory(current_memory: str, history: list[dict], lang: str) -> str:
    """
    Ask the LLM to extract key facts from recent conversation and merge
    them with existing long-term memory.

    Returns the updated memory string (or the old one on failure).
    """
    if lang == "zh":
        instruction = (
            "请提取以下对话中关于各个用户的关键事实（例如：用户的昵称、喜好、作息、说过的重要事情等）。"
            "每条事实必须注明是属于哪个用户（按昵称区分，不同昵称是不同的用户，不要合并）。"
            "将新事实与[现有记忆]合并，写成一段简短的客观总结。不要输出任何废话、主观感受、"
            "思考过程或对话格式，只输出事实本身。"
        )
    else:
        instruction = (
            "Extract key facts about EACH user from the conversation below "
            "(nickname, preferences, schedule, important things they said). "
            "Attribute every fact to the correct user by their nickname — different "
            "nicknames are different people, never merge them. "
            "Merge new facts with [Existing Memory] into a single concise paragraph "
            "of objective facts. Output only the facts — no thought process, "
            "commentary, or conversational filler."
        )

    # Serialise the history into plain text (with speaker identity)
    chat_text = ""
    for msg in history:
        content = msg["content"]
        if isinstance(content, list):
            content = "[sent an image]"
        name = msg.get("name", "")
        if name:
            chat_text += f"用户 {name}: {content}\n"
        else:
            chat_text += f"{msg['role']}: {content}\n"

    prompt = (
        f"{instruction}\n\n"
        f"[Existing Memory]: {current_memory or '(empty)'}\n\n"
        f"[Recent Conversation]:\n{chat_text}"
    )

    try:
        raw = await _chat_once(
            [{"role": "user", "content": prompt}],
            MAX_MEMORY_TOKENS,
            MEMORY_SAMPLING,
            {"cache_prompt": True},
        )
        if raw:
            raw = raw.strip()
            # Safety: strip any chain-of-thought tokens the model may leak
            if "<|channel>thought" in raw:
                cleaned = re.sub(
                    r"<\|channel>thought.*?(?=\n\n|\Z)",
                    "",
                    raw,
                    flags=re.DOTALL,
                ).strip()
                return cleaned or raw
            return raw
    except Exception as exc:
        logger.error("Memory summarisation failed: %s", exc)

    return current_memory