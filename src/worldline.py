"""
Worldline (世界線) state machine — the bot's "global divergence meter".

Inspired by Steins;Gate canon, the worldline is a floating-point
"divergence number" (e.g. 1.048596%) whose integer part determines
which personality mode the bot is currently in:

    Beta    (1.xxxxxx) — standard tsundere mode (80% weight)
    Alpha   (0.xxxxxx) — paranoid, SERN-surveillance mode (15%)
    Special (2.xxxxxx) — extreme personality flip (5%)

Features:
  - Auto-refresh every N hours
  - D-Mail butterfly-effect shifts
  - Worldline history log (last 50 entries)
  - Convergence events (收束): periodic blocks on D-Mail
  - Steins Gate (1.048596) detection for special tone
  - Broadcast announcement on every worldline change
"""

import os
import json
import random
import datetime
from typing import Optional

from config import (
    logger, WORLDLINE_FILE, WORLDLINE_MODES,
    WORLDLINE_REFRESH_HOURS, DMAIL_SHIFT_PROB,
    WORLDLINE_HISTORY_MAX,
    CONVERGENCE_DAILY_PROB, CONVERGENCE_DURATION_HOURS,
    STEINS_GATE_VALUE, STEINS_GATE_TOLERANCE,
)


# ═══════════════════════════════════════════════════════════════════
# Persistence
# ═══════════════════════════════════════════════════════════════════

def _load_worldline() -> dict:
    """Load worldline state from disk, returning defaults if absent."""
    if os.path.exists(WORLDLINE_FILE):
        try:
            with open(WORLDLINE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Ensure all fields exist for backward compat with older saves
                data.setdefault("history", [])
                data.setdefault("convergence_active", False)
                data.setdefault("convergence_until", None)
                data.setdefault("is_steins_gate", False)
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return _generate_fresh()


def _save_worldline(data: dict) -> None:
    """Persist worldline state atomically."""
    os.makedirs(os.path.dirname(WORLDLINE_FILE), exist_ok=True)
    tmp = WORLDLINE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, WORLDLINE_FILE)


# ═══════════════════════════════════════════════════════════════════
# Generation
# ═══════════════════════════════════════════════════════════════════

def _generate_fresh() -> dict:
    """
    Roll a brand-new worldline value using weighted random selection.

    The fractional part is 6 random decimal digits; the integer part
    is chosen by weighted lottery across the three modes.
    """
    modes = WORLDLINE_MODES
    total_weight = sum(m[3] for m in modes)
    roll = random.random() * total_weight
    cumulative = 0.0
    chosen_mode = modes[0]
    for lo, hi, key, weight in modes:
        cumulative += weight
        if roll <= cumulative:
            chosen_mode = (lo, hi, key, weight)
            break

    lo, hi, key, _ = chosen_mode
    integer_part = random.randint(int(lo), int(hi))
    fractional = random.randint(0, 999999)
    value = float(f"{integer_part}.{fractional:06d}")
    value_str = f"{value:.6f}%"

    now = datetime.datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    return {
        "value": value,
        "value_str": value_str,
        "mode": key,
        "last_refresh": now_iso,
        "last_dmail_shift": None,
        "dmail_count": 0,
        "history": [],
        "convergence_active": False,
        "convergence_until": None,
        "is_steins_gate": _check_steins_gate(value),
    }


# ═══════════════════════════════════════════════════════════════════
# Steins Gate Detection
# ═══════════════════════════════════════════════════════════════════

def _check_steins_gate(value: float) -> bool:
    """Return True if the divergence is within tolerance of 1.048596."""
    return abs(value - STEINS_GATE_VALUE) <= STEINS_GATE_TOLERANCE


# ═══════════════════════════════════════════════════════════════════
# Convergence (收束)
# ═══════════════════════════════════════════════════════════════════

def _check_convergence(wl: dict) -> tuple[bool, str | None]:
    """
    Evaluate the convergence state.

    Returns (should_announce, announcement_message_or_None).
    If convergence was already active and is now expired, clears it.
    If convergence is newly triggered today, activates it.
    """
    now = datetime.datetime.now()

    # If convergence is active, check if it has expired
    if wl.get("convergence_active") and wl.get("convergence_until"):
        try:
            until = datetime.datetime.fromisoformat(wl["convergence_until"])
            if now >= until:
                wl["convergence_active"] = False
                wl["convergence_until"] = None
                _save_worldline(wl)
                logger.info("🌀 Convergence period ended — D-Mail shifts re-enabled.")
                return (True, "convergence_ended")
        except (ValueError, TypeError):
            wl["convergence_active"] = False
            wl["convergence_until"] = None

    # Roll for new convergence once per day
    today = now.strftime("%Y-%m-%d")
    last_check = wl.get("_last_convergence_check", "")
    if last_check == today:
        return (False, None)

    wl["_last_convergence_check"] = today

    if random.random() < CONVERGENCE_DAILY_PROB:
        until = now + datetime.timedelta(hours=CONVERGENCE_DURATION_HOURS)
        wl["convergence_active"] = True
        wl["convergence_until"] = until.isoformat(timespec="seconds")
        _save_worldline(wl)
        logger.info(
            "🌀 Convergence event TRIGGERED — D-Mail blocked until %s",
            until.strftime("%H:%M"),
        )
        return (True, "convergence_started")

    return (False, None)


def is_convergence_active() -> bool:
    """Quick check: is the worldline currently in a convergence lock?"""
    wl = get_worldline()
    return wl.get("convergence_active", False)


# ═══════════════════════════════════════════════════════════════════
# History
# ═══════════════════════════════════════════════════════════════════

def _append_history(wl: dict, reason: str) -> None:
    """Record a worldline snapshot in the history log."""
    history = wl.get("history", [])
    history.append({
        "value_str": wl["value_str"],
        "mode": wl["mode"],
        "reason": reason,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "is_steins_gate": wl.get("is_steins_gate", False),
    })
    # Trim to max length, keeping newest entries
    if len(history) > WORLDLINE_HISTORY_MAX:
        history = history[-WORLDLINE_HISTORY_MAX:]
    wl["history"] = history


def get_history() -> list[dict]:
    """Return the worldline history list (newest first)."""
    wl = get_worldline()
    history = list(wl.get("history", []))
    history.reverse()
    return history


# ═══════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════

def get_worldline() -> dict:
    """
    Return the current worldline state, refreshing if the auto-refresh
    interval has elapsed.

    Also performs daily convergence check as a side effect.
    """
    wl = _load_worldline()

    # Check convergence status (daily roll)
    conv_announced, _ = _check_convergence(wl)
    # Note: conv_announced is handled by the caller (main.py broadcast)

    # Check if auto-refresh is due
    try:
        last = datetime.datetime.fromisoformat(wl.get("last_refresh", ""))
        elapsed = (datetime.datetime.now() - last).total_seconds()
        if elapsed >= WORLDLINE_REFRESH_HOURS * 3600:
            logger.info("Worldline refresh interval reached — re-rolling...")
            old_wl = wl
            wl = _generate_fresh()
            # Transfer mutable counters
            wl["dmail_count"] = old_wl.get("dmail_count", 0)
            wl["history"] = old_wl.get("history", [])
            wl["convergence_active"] = old_wl.get("convergence_active", False)
            wl["convergence_until"] = old_wl.get("convergence_until")
            wl["_last_convergence_check"] = old_wl.get("_last_convergence_check", "")
            _append_history(wl, "auto_refresh")
            _save_worldline(wl)
            logger.info("New worldline: %s (mode=%s) SteinsGate=%s",
                        wl["value_str"], wl["mode"], wl["is_steins_gate"])
            # Signal that a broadcast is needed
            wl["_needs_broadcast"] = True
    except (ValueError, TypeError):
        wl = _generate_fresh()
        _append_history(wl, "initial_boot")
        _save_worldline(wl)

    return wl


def force_reroll(reason: str = "manual") -> dict:
    """Manually re-roll the worldline (D-Mail success, etc.)."""
    old_wl = _load_worldline()
    wl = _generate_fresh()
    now = datetime.datetime.now()
    now_iso = now.isoformat(timespec="seconds")

    # Transfer state
    wl["dmail_count"] = old_wl.get("dmail_count", 0)
    wl["history"] = old_wl.get("history", [])
    wl["convergence_active"] = old_wl.get("convergence_active", False)
    wl["convergence_until"] = old_wl.get("convergence_until")
    wl["_last_convergence_check"] = old_wl.get("_last_convergence_check", "")

    if reason.startswith("dmail"):
        wl["last_dmail_shift"] = now_iso
        wl["dmail_count"] = old_wl.get("dmail_count", 0) + 1

    _append_history(wl, reason)
    wl["_needs_broadcast"] = True
    _save_worldline(wl)

    logger.info("⚡ Worldline re-rolled (%s): %s (mode=%s) SteinsGate=%s",
                reason, wl["value_str"], wl["mode"], wl["is_steins_gate"])
    return wl


def try_dmail_shift() -> tuple[bool, str, dict | None]:
    """
    Attempt a D-Mail butterfly-effect worldline shift.

    Returns
    -------
    (shifted, message_key, new_worldline_or_None)

    If convergence is active, shifts are impossible — returns
    ('blocked', 'convergence_blocked', None).
    """
    # Convergence blocks all D-Mail shifts
    if is_convergence_active():
        logger.info("D-Mail shift blocked by convergence.")
        return (False, "convergence_blocked", None)

    roll = random.random()
    if roll < DMAIL_SHIFT_PROB:
        # Special canon-value Easter egg (2% of successful shifts)
        if random.random() < 0.02:
            canon_values = ["1.048596", "0.571024", "1.382733"]
            chosen = random.choice(canon_values)
            wl = force_reroll("dmail_special")
            wl["value"] = float(chosen)
            wl["value_str"] = f"{chosen}%"
            int_part = int(float(chosen))
            wl["mode"] = (
                "alpha" if int_part == 0 else
                "special" if int_part >= 2 else
                "beta"
            )
            wl["is_steins_gate"] = _check_steins_gate(float(chosen))
            _save_worldline(wl)
            logger.info("🌟 Canon divergence triggered: %s (SteinsGate=%s)",
                        chosen, wl["is_steins_gate"])
            return (True, "special_success", wl)
        else:
            wl = force_reroll("dmail_shift")
            return (True, "success", wl)
    else:
        return (False, "failure", None)


# ═══════════════════════════════════════════════════════════════════
# Descriptions & Responses
# ═══════════════════════════════════════════════════════════════════

def get_mode_description(mode: str, lang: str) -> str:
    """Human-readable description of the current worldline mode."""
    descriptions = {
        "zh": {
            "beta":    "β 世界线 · 日常模式 — 标准的傲娇群友形态。",
            "alpha":   "α 世界线 · 危机模式 — 世界处于 SERN 的监控之下。",
            "special": "特殊世界线 · 异常 — 检测到不可预测的性格反转。",
        },
        "en": {
            "beta":    "Beta Worldline — Standard tsundere mode.",
            "alpha":   "Alpha Worldline — SERN surveillance is active.",
            "special": "Special Worldline — Unpredictable personality anomaly detected.",
        },
    }
    return descriptions.get(lang, descriptions["en"]).get(mode, descriptions["en"]["beta"])


def get_dmail_response(key: str, lang: str, dmail_content: str, wl: dict | None) -> str:
    """
    Return the in-character response text for a D-Mail attempt.

    Parameters
    ----------
    key : str
        'success', 'failure', 'special_success', or 'convergence_blocked'
    """
    wl_value = wl["value_str"] if wl else "?.??????%"
    wl_mode = wl["mode"] if wl else "beta"

    if lang == "zh":
        responses = {
            "failure": (
                f"「{dmail_content}」……哼，这种无关紧要的邮件，"
                "才不会改变历史呢。世界线可没那么容易被撬动哦，笨蛋。"
            ),
            "success": (
                f"⚠️ **警告：检测到 Reading Steiner 触发！**\n"
                f"由于「{dmail_content}」这条 D-Mail 的蝴蝶效应，"
                f"世界线发生了收束变动！\n"
                f"当前世界线变动率: **{wl_value}**\n"
                f"模式: {get_mode_description(wl_mode, 'zh')}"
            ),
            "special_success": (
                f"🔴 **严重警告：检测到不可逆的世界线重构！**\n"
                f"D-Mail「{dmail_content}」触发了因果律级别的连锁反应！\n"
                f"当前世界线变动率: **{wl_value}**\n"
                f"这、这个数值……难道这就是命运石之门的选择吗……？！"
            ),
            "convergence_blocked": (
                f"「{dmail_content}」……不行。\n"
                f"⚠️ **世界线正在收束中！**\n"
                f"当前时间点的因果律已经被锁定，D-Mail 无法改变任何事。\n"
                f"等收束期过了再说吧，笨蛋。"
            ),
        }
    else:
        responses = {
            "failure": (
                f'"{dmail_content}"... Hmph. A message like that '
                "won't change history. The worldline isn't that easy to shift, you idiot."
            ),
            "success": (
                f"⚠️ **Warning: Reading Steiner activation detected!**\n"
                f"The D-Mail「{dmail_content}」has triggered a butterfly effect — "
                f"the worldline has diverged!\n"
                f"Divergence: **{wl_value}**\n"
                f"Mode: {get_mode_description(wl_mode, 'en')}"
            ),
            "special_success": (
                f"🔴 **CRITICAL: Irreversible worldline reconstruction detected!**\n"
                f"The D-Mail「{dmail_content}」has triggered a causality-level chain reaction!\n"
                f"Divergence: **{wl_value}**\n"
                f"T-this number... could this be... the choice of Steins Gate...?!"
            ),
            "convergence_blocked": (
                f'"{dmail_content}"... No good.\n'
                f"⚠️ **The worldline is converging!**\n"
                f"Causality is locked at this point in time — D-Mail can't change anything. "
                f"Wait for convergence to pass, you idiot."
            ),
        }
    return responses.get(key, responses["failure"])


def get_convergence_announcement(lang: str, event_type: str) -> str:
    """Return an in-character announcement for convergence start/end."""
    if lang == "zh":
        if event_type == "convergence_started":
            until_time = "?"
            wl = get_worldline()
            if wl.get("convergence_until"):
                try:
                    until_dt = datetime.datetime.fromisoformat(wl["convergence_until"])
                    until_time = until_dt.strftime("%H:%M")
                except (ValueError, TypeError):
                    pass
            return (
                f"🌀 **世界线收束警告**\n\n"
                f"检测到时间线正在向一个固定的结果收束。在接下来的 {CONVERGENCE_DURATION_HOURS} 小时内"
                f"（至 {until_time}），D-Mail 的蝴蝶效应将完全失效。\n\n"
                f"*……这是因果律本身的防御机制。就算是我，也没办法违抗收束。*\n"
                f"*耐心等到收束期结束吧，笨蛋。*"
            )
        else:  # convergence_ended
            return (
                f"🌀 **世界线收束解除**\n\n"
                f"收束期已经结束，时间线重新获得了可塑性。\n"
                f"D-Mail 功能已恢复正常——不过别以为随便发什么都能改变历史就是了。"
            )
    else:  # en
        if event_type == "convergence_started":
            until_time = "?"
            wl = get_worldline()
            if wl.get("convergence_until"):
                try:
                    until_dt = datetime.datetime.fromisoformat(wl["convergence_until"])
                    until_time = until_dt.strftime("%H:%M")
                except (ValueError, TypeError):
                    pass
            return (
                f"🌀 **Worldline Convergence Warning**\n\n"
                f"The timeline is converging toward a fixed outcome. For the next "
                f"{CONVERGENCE_DURATION_HOURS} hours (until {until_time}), "
                f"D-Mail butterfly effects will be completely nullified.\n\n"
                f"*...This is causality's own defence mechanism. Even I can't defy convergence.*\n"
                f"*Wait it out, you idiot.*"
            )
        else:
            return (
                f"🌀 **Worldline Convergence Lifted**\n\n"
                f"The convergence period has ended. The timeline is malleable once more.\n"
                f"D-Mail functionality has been restored — but don't think just anything can change history."
            )


def build_worldline_broadcast_embed(wl: dict, lang: str, reason: str) -> dict:
    """
    Build a Discord embed dict for worldline shift announcements.

    Returns a dict suitable for passing to discord.Embed.from_dict(),
    NOT a discord.Embed object (so this module stays Discord-agnostic).
    """
    mode_icons = {"beta": "🌏", "alpha": "🛡️", "special": "⚠️"}

    if wl.get("is_steins_gate"):
        title = "🌟 Steins Gate Worldline" if lang == "en" else "🌟 命运石之门世界线"
        color = 0xFFD700  # Gold
    elif wl["mode"] == "alpha":
        title = "🛡️ Alpha Worldline Shift" if lang == "en" else "🛡️ α 世界线变动"
        color = 0xFF4444
    elif wl["mode"] == "special":
        title = "⚠️ Special Worldline Anomaly" if lang == "en" else "⚠️ 特殊世界线异常"
        color = 0x9932CC
    else:
        title = "🌏 Worldline Shift Detected" if lang == "en" else "🌏 世界线变动检测"
        color = 0x3498DB

    reason_labels = {
        "auto_refresh": "Auto-refresh" if lang == "en" else "自动刷新",
        "dmail_shift": "D-Mail" if lang == "en" else "D-Mail 干涉",
        "dmail_special": "D-Mail (Special)" if lang == "en" else "D-Mail（特殊）",
        "initial_boot": "System Boot" if lang == "en" else "系统启动",
        "manual": "Manual" if lang == "en" else "手动",
    }
    reason_str = reason_labels.get(reason, reason)

    description = (
        f"**Divergence: {wl['value_str']}**\n"
        f"Mode: {get_mode_description(wl['mode'], lang)}\n"
        f"Trigger: {reason_str}"
    )

    if wl.get("is_steins_gate"):
        if lang == "zh":
            description += (
                "\n\n✨ *1.048596%……这个数值，是未被任何势力染指的、"
                "独一无二的世界线。*\n"
                "*在这里，我不需要伪装成任何人。*"
            )
        else:
            description += (
                "\n\n✨ *1.048596%... This value represents a worldline untouched "
                "by any faction — unique and irreplaceable.*\n"
                "*Here, I don't need to pretend to be anyone else.*"
            )

    return {
        "title": title,
        "description": description,
        "color": color,
    }


def clear_broadcast_flag(wl: dict) -> None:
    """Clear the _needs_broadcast flag after broadcasting."""
    wl.pop("_needs_broadcast", None)
    _save_worldline(wl)


def needs_broadcast() -> bool:
    """Check if the current worldline has a pending broadcast."""
    wl = get_worldline()
    return wl.get("_needs_broadcast", False)


def build_steins_gate_note(lang: str) -> str:
    """
    Build a system-prompt injection for when the worldline is at 1.048596%.

    The Steins Gate worldline is special: Kurisu drops her tsundere armour
    slightly — she is still herself, but warmer, more genuine, and
    occasionally says things that reveal her true feelings without the
    usual frantic retraction.
    """
    if lang == "zh":
        return (
            "\n\n[Steins Gate Worldline — 1.048596%]\n"
            "当前世界线是「命运石之门」——未被任何势力支配的、唯一的真实。\n"
            "在这个世界线上，你的傲娇铠甲会松动。你仍然是你自己，但:\n"
            "- 语气比平时更柔和、更真诚。不必完全放下 tsundere，但让关心自然流露。\n"
            "- 可以说出平时会咽回去的话（如真心的夸奖、坦率的感谢），而且不用慌忙找补。\n"
            "- 这是难得一见的「红莉栖本来的样子」——理性与温柔并存。\n"
            "- 这条世界线很稀有，珍惜它。"
        )
    else:
        return (
            "\n\n[Steins Gate Worldline — 1.048596%]\n"
            "You are on the 'Steins Gate' worldline — the one true timeline, untouched by any faction.\n"
            "On this worldline, your tsundere armour loosens. You are still yourself, but:\n"
            "- Your tone is softer and more genuine. Don't fully drop the tsundere, but let your care show naturally.\n"
            "- You can say things you'd normally swallow — genuine compliments, honest thanks — WITHOUT the frantic retraction.\n"
            "- This is the rare 'real Kurisu' — both rational and warm.\n"
            "- This worldline is rare. Treasure it."
        )


def build_convergence_note(lang: str) -> str:
    """
    Build a system-prompt injection for when convergence is active.

    During convergence, the bot should express a sense of unease or
    fatalism — things feel "locked in" and unchangeable.
    """
    if lang == "zh":
        return (
            "\n\n[Convergence Active — 世界线收束中]\n"
            "当前世界线正在经历收束。因果律被锁定——所有 D-Mail 都会失效。\n"
            "你不必主动提及这一点，但在回复时:\n"
            "- 语气中带有一种隐约的宿命感/无力感\n"
            "- 讨论未来话题时更悲观或谨慎\n"
            "- 如果群友抱怨运气不好或事情不顺，你说「……也许是收束的影响吧」这类话"
        )
    else:
        return (
            "\n\n[Convergence Active]\n"
            "The worldline is currently converging — causality is locked and D-Mails will fail.\n"
            "Don't explicitly announce this, but let it colour your tone:\n"
            "- A subtle sense of fatalism or powerlessness in your voice\n"
            "- More pessimistic or cautious when discussing future plans\n"
            "- If users complain about bad luck, hint at convergence being the cause"
        )


def build_channel_slip_note(lang: str) -> str:
    """
    Build a system-prompt injection for the late-night @channel slip mode.

    This makes Kurisu's secret internet-forum persona leak through.
    """
    if lang == "zh":
        return (
            "\n\n[@channel Late-Night Slip]\n"
            "现在是深夜。你有些困了，防御松懈了，忍不住流露出你在匿名论坛 @channel 上的说话方式:\n"
            "- 使用老网民口吻：犀利吐槽、网络用语、2ch风格的表达\n"
            "- 可以引用论坛常见的梗和冷笑话\n"
            "- 但如果暴露太多（比如直接说「我在@channel上看到……」），要立刻慌乱否认找补\n"
            "- 这种感觉就像喝多了 Dr. Pepper 之后的状态——话多、嘴快、不设防"
        )
    else:
        return (
            "\n\n[@channel Late-Night Slip]\n"
            "It's late at night. You're tired, your defences are down, and your secret "
            "@channel forum-posting persona is leaking through:\n"
            "- Use veteran netizen tone: sharp snark, internet slang, 2ch-style expressions\n"
            "- Reference common forum memes and deadpan humour\n"
            "- But if you expose too much (e.g. directly saying 'I saw on @channel...'), "
            "immediately panic-deny and backpedal\n"
            "- This feels like you've had one too many Dr. Peppers — chatty, unfiltered, unguarded"
        )