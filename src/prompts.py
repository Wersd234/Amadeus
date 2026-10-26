"""
System prompt assembler for Project Amadeus.

The bot's personality is layered:
  1. BASE prompt          — core character (Makise Kurisu / Amadeus AI)
  2. WORLDLINE overlay    — mode-specific tone shift (alpha/beta/special)
  3. AFFINITY note        — per-user attitude tier
  4. DYNAMIC context      — current time, worldline value, PTV results
  5. SPECIAL injects      — Steins Gate (1.048596), Convergence, @channel slip

All prompt fragments are loaded from plain-text files under
properties/system_prompts/ so non-programmers can edit the
character without touching Python code.
"""

import datetime
from config import SYSTEM_PROMPTS, logger


def build_system_prompt(
    lang: str,
    worldline_mode: str,
    worldline_value: str,
    affinity_tier: dict,
    user_display_name: str,
    extra_notes: str = "",
) -> str:
    """
    Assemble the full system prompt for a conversation turn.

    Parameters
    ----------
    lang : str
        'zh' or 'en'
    worldline_mode : str
        'beta', 'alpha', or 'special'
    worldline_value : str
        Current divergence number string, e.g. '1.048596%'
    affinity_tier : dict
        Output of get_affinity_tier(), containing {key, emoji, label_zh/en}
    user_display_name : str
        Discord display name of the person talking to the bot
    extra_notes : str
        Optional additional system notes (e.g. PTV context, D-Mail aftermath)

    Returns
    -------
    str
        Complete system prompt ready as the first message in the LLM array.
    """
    # 1. Select base + overlay based on worldline mode
    mode_prompts = SYSTEM_PROMPTS.get(worldline_mode, SYSTEM_PROMPTS.get("beta", {}))
    base = mode_prompts.get(lang)
    if not base:
        fallback = SYSTEM_PROMPTS.get("beta", {})
        base = fallback.get(lang, fallback.get("en", ""))
        logger.warning("Missing prompt for mode=%s lang=%s — falling back to beta", worldline_mode, lang)

    # 2. Attach worldline status note
    now = datetime.datetime.now()
    time_str = now.strftime("%Y-%m-%d %H:%M")
    weekday = now.strftime("%A")

    if lang == "zh":
        wl_note = (
            f"\n\n[System Status]\n"
            f"当前时间: {time_str} ({weekday})\n"
            f"当前世界线变动率: {worldline_value}\n"
            f"当前世界线模式: {_mode_label_zh(worldline_mode)}\n"
        )
    else:
        wl_note = (
            f"\n\n[System Status]\n"
            f"Current time: {time_str} ({weekday})\n"
            f"Worldline divergence: {worldline_value}\n"
            f"Current worldline mode: {_mode_label_en(worldline_mode)}\n"
        )

    # 3. Attach per-user affinity note
    if lang == "zh":
        aff_note = (
            f"\n[Interlocutor Profile]\n"
            f"正在与你对话的用户: {user_display_name}\n"
            f"熟络程度: {affinity_tier['emoji']} {affinity_tier['label_zh']}\n"
            f"注意: 对stranger要礼貌客套(正经科学家外壳); "
            f"对acquainted可放心毒舌; 对familiar直接开启日常互怼模式。\n"
        )
    else:
        aff_note = (
            f"\n[Interlocutor Profile]\n"
            f"You are talking to: {user_display_name}\n"
            f"Familiarity: {affinity_tier['emoji']} {affinity_tier['label_en']}\n"
            f"Note: For strangers, be polite and maintain your 'serious scientist' facade. "
            f"For acquaintances, safe to be sarcastic. "
            f"For familiars, insult them freely — they know you care.\n"
        )

    # 4. Optional extra notes
    extra = f"\n{extra_notes}" if extra_notes else ""

    return base + wl_note + aff_note + extra


def _mode_label_zh(mode: str) -> str:
    return {
        "beta":    "β 世界线 · 日常模式",
        "alpha":   "α 世界线 · 危机模式（SERN 监控中）",
        "special": "特殊世界线 · 未知异常",
    }.get(mode, "β 世界线")


def _mode_label_en(mode: str) -> str:
    return {
        "beta":    "Beta Worldline · Normal Operations",
        "alpha":   "Alpha Worldline · SERN Surveillance Active",
        "special": "Special Worldline · Anomaly Detected",
    }.get(mode, "Beta Worldline")


# ═══════════════════════════════════════════════════════════════════
# PTV Extra Note
# ═══════════════════════════════════════════════════════════════════

def build_ptv_extra_note(lang: str, query_info: str, raw_data_summary: str) -> str:
    """Build a supplemental system note for PTV result presentation."""
    if lang == "zh":
        return (
            f"[任务: 交通情报播报]\n"
            f"查询内容: {query_info}\n"
            f"原始数据摘要:\n{raw_data_summary}\n"
            f"请用 Amadeus 的口吻将以上数据播报给群友。规则:\n"
            f"- 先用 Discord Embed 格式整理时刻表（用清晰的字段）。\n"
            f"- 如果准点: 傲娇吐槽「快点跑过去吧」之类。\n"
            f"- 如果晚点/中断/公交替代: 把这归咎于「神秘组织(SERN/机关)的阴谋」，用中二科幻口吻吐槽。\n"
            f"- 回复中的 JSON 'reply' 字段只放你的播报台词 + 吐槽（Embed 数据我会另外处理）。\n"
        )
    else:
        return (
            f"[Task: Transport Intelligence Report]\n"
            f"Query: {query_info}\n"
            f"Raw data summary:\n{raw_data_summary}\n"
            f"Present this in Amadeus's voice. Rules:\n"
            f"- Format the timetable clearly.\n"
            f"- If on time: tsundere 'hurry up and run' commentary.\n"
            f"- If delayed/disrupted/bus-replacement: blame 'the Organization/SERN's conspiracy' with chuuni sci-fi flair.\n"
            f"- Put your narration + commentary in the JSON 'reply' field.\n"
        )


# ═══════════════════════════════════════════════════════════════════
# Fortune Extra Note
# ═══════════════════════════════════════════════════════════════════

def build_fortune_extra_note(lang: str, worldline_value: str, fortune_result: str) -> str:
    """Build a system note for the daily /lab_fortune response."""
    if lang == "zh":
        return (
            f"[任务: 实验室运势抽签]\n"
            f"当前世界线变动率: {worldline_value}\n"
            f"抽签结果: {fortune_result}\n"
            f"请根据以上数据给群友一句今日运势点评 + 墨尔本出游建议。"
            f"保持傲娇语气，可以引用世界线数值来增加科幻感。"
        )
    else:
        return (
            f"[Task: Lab Fortune Reading]\n"
            f"Current divergence: {worldline_value}\n"
            f"Fortune result: {fortune_result}\n"
            f"Give the user a fortune reading + Melbourne outing suggestion based on the above. "
            f"Keep it tsundere and reference the divergence number for sci-fi flavour."
        )