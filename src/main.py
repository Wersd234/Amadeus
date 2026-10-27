"""
Project Amadeus — Discord Bot Entry Point.

A Steins;Gate-themed AI assistant for Melbourne Discord communities.
Combines tsundere scientist roleplay, a worldline divergence state
machine, Melbourne PTV transport integration, D-Mail butterfly-
effect entertainment, commute subscriptions, convergence events,
Tavily web search, and free-speaking group-member mode.

Key behaviours:
  - Responds to @mentions and replies (always)
  - Keyword eavesdropping with probability/cooldown
  - Free-speaking: occasionally jumps into active conversations
    without being called (controlled by per-channel thresholds)
  - Vision: only forwarded to the LLM if the loaded model supports it
    (auto-detected from MODEL_NAME containing "qwen"); otherwise a
    playful "vision module under repair" message is returned
"""

import asyncio
import base64
import datetime
import io
import json
import os
import random
import re
from collections import deque

import discord
from discord.ext import tasks

# Pillow is only needed for image processing (user-uploaded pictures).
# It requires system C libraries (libjpeg, zlib) that may be missing
# in slim Docker images.  If unavailable, the bot still starts —
# image attachments are simply skipped.
try:
    from PIL import Image
    _pillow_available = True
except ImportError:
    _pillow_available = False

from config import (
    logger, DISCORD_TOKEN, refresh_model, HISTORY_MAXLEN,
    KEYWORD_TRIGGERS, KEYWORD_COOLDOWN_SEC, NAME_TRIGGER_KEYWORDS,
    CHANNEL_SLIP_PROB, CHANNEL_SLIP_START_HOUR, CHANNEL_SLIP_END_HOUR,
    PTV_SUB_CHECK_INTERVAL_MIN, PTV_SUB_LOOKAHEAD_MIN,
    FREE_SPEAK_INTERESTS, FREE_SPEAK_COOLDOWN_SEC,
    FREE_SPEAK_SAME_USER_COOLDOWN,
    FREE_SPEAK_CONTINUATION_WINDOW_SEC, FREE_SPEAK_CONTINUATION_PROB,
    LLM_VISION_ENABLED,
    TAVILY_API_KEY,
)
from utils import (
    run_startup_check, get_server_language,
    get_art_channel,
    load_memory, save_memory,
    get_affinity, increment_affinity, get_affinity_tier,
    get_broadcast_channel,
    load_ptv_subscriptions, get_subscriptions_due,
)
from worldline import (
    get_worldline, force_reroll, try_dmail_shift,
    needs_broadcast, clear_broadcast_flag,
    build_worldline_broadcast_embed,
    is_convergence_active, get_convergence_announcement,
    build_steins_gate_note, build_convergence_note,
    build_channel_slip_note,
)
from prompts import build_system_prompt
from llm_api import generate_reply, vision_available, summarize_memory
from image_index import load_index, get_image_for_emotion
from ptv_api import get_departures
from commands import setup_commands


class AmadeusBot(discord.Client):
    def __init__(self, *args, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        kwargs["intents"] = intents
        super().__init__(*args, **kwargs)

        self.tree = discord.app_commands.CommandTree(self)

        # Per-channel: conversation history with the bot (for @mention context)
        self.history: dict[str, deque] = {}

        # Per-channel: recent messages seen by the bot (for free-speaking context)
        # Format: [{"author": "Name", "content": "..."}, ...]
        self.recent_messages: dict[str, deque] = {}

        # Per-channel: total message count (for free-speaking threshold)
        self.msg_seen: dict[str, int] = {}

        # Per-channel: LLM-turn counter → triggers memory summarisation
        self.msg_counter: dict[str, int] = {}

        # Keyword eavesdropping cooldown
        self._last_keyword_interject: dict[str, float] = {}

        # Free-speaking cooldown per channel
        self._last_free_speak: dict[str, float] = {}

        # Track last author the bot replied to (prevent replying to same user twice)
        self._last_free_speak_author: dict[str, str] = {}

        # Conversation continuity: per-user tracking.
        # {channel_id: {user_id: timestamp}} — each user gets their own
        # thread window, so replying to B doesn't kill A's thread.
        self._last_reply_to: dict[str, dict[str, float]] = {}

        # Convergence announcement guards
        self._convergence_announced_start: bool = False
        self._convergence_announced_end: bool = False

    # ── Lifecycle ───────────────────────────────────────────────

    async def setup_hook(self):
        setup_commands(self)
        self.dynamic_presence.start()
        self.worldline_broadcast_loop.start()
        self.ptv_subscription_checker.start()
        await self.tree.sync()
        logger.info("Command tree synced. Vision=%s FreeSpeak interest topics=%d",
                    "ON" if LLM_VISION_ENABLED else "OFF", len(FREE_SPEAK_INTERESTS))

    async def on_ready(self):
        # Pre-load image index (non-fatal if missing)
        try:
            self.image_index = load_index()
        except Exception:
            self.image_index = {}

        wl = get_worldline()
        logger.info(
            "✅ Amadeus '%s' Online | WL: %s (%s) SG=%s Conv=%s",
            self.user.name, wl["value_str"], wl["mode"],
            wl.get("is_steins_gate", False), wl.get("convergence_active", False),
        )

        # Guild-scoped command sync = instant propagation (a few seconds).
        # The global tree.sync() in setup_hook can take up to an hour to
        # refresh EXISTING commands — which is exactly why changes like
        # adding an autocomplete appear to "do nothing" after a deploy.
        # Global sync still runs (for DM usage); guild sync makes it
        # effective now in every server we're in.
        synced = 0
        for guild in self.guilds:
            try:
                await self.tree.sync(guild=guild)
                synced += 1
            except Exception as exc:
                logger.warning("Guild command sync failed for %s: %s", guild.id, exc)
        logger.info("Command tree instantly synced to %d guild(s).", synced)

    # ── Dynamic Presence ────────────────────────────────────────

    @tasks.loop(minutes=10)
    async def dynamic_presence(self):
        refresh_model()  # Re-detect LLM model (mid-session switch)
        wl = get_worldline()
        icons = {"beta": "🌏", "alpha": "🛡️", "special": "⚠️"}
        icon = icons.get(wl["mode"], "🌏")
        extras = []
        if wl.get("is_steins_gate"):
            extras.append("🌟SG")
        if wl.get("convergence_active"):
            extras.append("🌀Conv")
        extra = " | " + " ".join(extras) if extras else ""
        await self.change_presence(activity=discord.Game(
            name=f"{icon} {wl['value_str']}{extra} | /help"))

    @dynamic_presence.before_loop
    async def _before_presence(self):
        await self.wait_until_ready()

    # ── Worldline Broadcast Loop ────────────────────────────────

    @tasks.loop(minutes=2)
    async def worldline_broadcast_loop(self):
        if not needs_broadcast():
            return
        wl = get_worldline()
        clear_broadcast_flag(wl)
        embed_data = build_worldline_broadcast_embed(
            wl, "en", wl.get("_broadcast_reason", "auto_refresh"))
        embed = discord.Embed.from_dict(embed_data)
        for guild in self.guilds:
            gid = str(guild.id)
            cid = get_broadcast_channel(gid)
            if not cid:
                continue
            ch = guild.get_channel(int(cid))
            if ch and ch.permissions_for(guild.me).send_messages:
                try:
                    await ch.send(embed=embed)
                    logger.info("📡 Broadcast → guild %s #%s", gid, cid)
                except discord.HTTPException as e:
                    logger.warning("Broadcast fail: %s", e)

    @worldline_broadcast_loop.before_loop
    async def _before_broadcast(self):
        await self.wait_until_ready()

    # ── Convergence Announcer ───────────────────────────────────

    async def _announce_convergence(self, event_type: str):
        for guild in self.guilds:
            gid = str(guild.id)
            cid = get_broadcast_channel(gid)
            if not cid:
                continue
            ch = guild.get_channel(int(cid))
            if not ch or not ch.permissions_for(guild.me).send_messages:
                continue
            lang = get_server_language(gid)
            text = get_convergence_announcement(lang, event_type)
            try:
                await ch.send(text)
                logger.info("🌀 Convergence %s → guild %s", event_type, gid)
            except discord.HTTPException as e:
                logger.warning("Convergence announce fail: %s", e)

    # ── PTV Subscription Checker ────────────────────────────────

    @tasks.loop(minutes=PTV_SUB_CHECK_INTERVAL_MIN)
    async def ptv_subscription_checker(self):
        now = datetime.datetime.now()
        now_str = now.strftime("%H:%M")
        due = get_subscriptions_due(now_str, PTV_SUB_LOOKAHEAD_MIN)
        if not due:
            return
        logger.info("⏰ PTV check: %d subs due at %s", len(due), now_str)
        for sub in due:
            try:
                deps = await get_departures(sub["route_type"], sub["stop_id"], max_results=3, stop_name=sub["stop_name"])
                if not deps:
                    continue
                issue = None
                for d in deps.get("departures", []):
                    if d.get("is_bus_replacement"):
                        issue = "bus"
                        break
                    if d.get("is_delayed") and d.get("delay_minutes", 0) >= 10:
                        issue = "delay"
                        break
                if not issue:
                    continue
                ch = self.get_channel(int(sub["channel_id"]))
                if not ch:
                    continue
                user = await self.fetch_user(int(sub["user_id"]))
                alert = (
                    f"🚌 <@{user.id}> **通勤警报！** {sub['stop_name']} 站检测到"
                    f"{'公交替代火车' if issue == 'bus' else '严重延误(10+分钟)'}！\n"
                    f"⚠️ 神秘组织正在干扰你的线路。祝你好运了，笨蛋。"
                )
                await ch.send(alert)
            except Exception as e:
                logger.error("PTV sub check fail: %s", e)

    @ptv_subscription_checker.before_loop
    async def _before_ptv(self):
        await self.wait_until_ready()

    # ── Helpers ─────────────────────────────────────────────────

    def _check_keyword_triggers(self, content: str) -> bool:
        content_lower = content.lower()
        for keywords, prob in KEYWORD_TRIGGERS:
            for kw in keywords:
                if kw.lower() in content_lower:
                    return random.random() < prob
        return False

    def _build_dynamic_injects(self, lang: str, wl: dict) -> str:
        parts = []
        if wl.get("is_steins_gate"):
            parts.append(build_steins_gate_note(lang))
        if wl.get("convergence_active"):
            parts.append(build_convergence_note(lang))
        now_h = datetime.datetime.now().hour
        start, end = CHANNEL_SLIP_START_HOUR, CHANNEL_SLIP_END_HOUR
        in_window = start <= now_h < end if start < end else (now_h >= start or now_h < end)
        if in_window and CHANNEL_SLIP_PROB > 0 and random.random() < CHANNEL_SLIP_PROB:
            parts.append(build_channel_slip_note(lang))
        return "\n".join(parts)

    async def _summarise_memory(self, channel_id: str, history_copy: list, lang: str):
        current = load_memory(channel_id)
        new_mem = await summarize_memory(current, history_copy, lang)
        if new_mem and new_mem != current:
            save_memory(channel_id, new_mem)
            logger.info("Memory updated #%s: %s", channel_id, new_mem[:80])

    # ═════════════════════════════════════════════════════════════
    # Message Handler
    # ═════════════════════════════════════════════════════════════

    async def on_message(self, message: discord.Message):
        if message.author == self.user:
            return
        if message.author.bot:
            return

        guild_id = str(message.guild.id) if message.guild else str(message.author.id)
        channel_id = str(message.channel.id)
        user_id = str(message.author.id)
        lang = get_server_language(guild_id)
        content = message.content

        # ── Track all messages for free-speaking context ────────
        if channel_id not in self.recent_messages:
            self.recent_messages[channel_id] = deque(maxlen=30)
        self.recent_messages[channel_id].append({
            "author": message.author.display_name,
            "content": content or "[attachment]",
        })
        self.msg_seen[channel_id] = self.msg_seen.get(channel_id, 0) + 1

        # ── Determine response triggers ─────────────────────────
        is_mention = self.user in message.mentions
        is_reply_to_bot = (
            message.reference
            and getattr(message.reference.resolved, "author", None) == self.user
        )
        is_keyword = False
        is_free_speak = False

        now_ts = datetime.datetime.now().timestamp()

        # ── Reply style ──────────────────────────────────────────
        # Unless the bot was @-mentioned, respond as a plain message
        # instead of a Discord "reply" — no reply reference/mention.
        async def _respond(text: str, file=None):
            if is_mention:
                if file is None:
                    await message.reply(content=text)
                else:
                    await message.reply(content=text, file=file)
            else:
                if file is None:
                    await message.channel.send(content=text)
                else:
                    await message.channel.send(content=text, file=file)

        if not is_mention and not is_reply_to_bot:
            # Called by name anywhere in the message — always answer
            # (equivalent to a ping, but without requiring the ping).
            if any(kw in content.lower() for kw in NAME_TRIGGER_KEYWORDS):
                is_keyword = True
                self._last_keyword_interject[channel_id] = now_ts
            else:
                # Check keyword eavesdropping first (higher priority)
                last_kw = self._last_keyword_interject.get(channel_id, 0)
                if now_ts - last_kw >= KEYWORD_COOLDOWN_SEC and self._check_keyword_triggers(content):
                    is_keyword = True
                    self._last_keyword_interject[channel_id] = now_ts
                else:
                    # Check free-speaking trigger
                    is_free_speak = self._should_free_speak(channel_id, now_ts, content, user_id, not message.guild)

            if not is_keyword and not is_free_speak:
                return  # Nothing triggered — stay silent

        # ── Affinity ────────────────────────────────────────────
        if is_mention or is_reply_to_bot:
            increment_affinity(user_id)

        # ── Conversation history (for @mention context) ─────────
        if channel_id not in self.history:
            self.history[channel_id] = deque(maxlen=HISTORY_MAXLEN)

        async with message.channel.typing():
            user_text = content.replace(f"<@{self.user.id}>", "").strip()

            # ── Image handling ──────────────────────────────────
            image_bytes = None
            for att in message.attachments:
                if att.content_type and att.content_type.startswith("image/"):
                    image_bytes = await att.read()
                    break

            if image_bytes and _pillow_available:
                if not vision_available():
                    # Non-Qwen model — return playful "repair" message immediately
                    if lang == "zh":
                        await _respond(
                            "*(啊……视觉模块正在维护中，暂时没法看图。"
                            "等我切换到 Qwen 模型再说吧，笨蛋。)*"
                        )
                    else:
                        await _respond(
                            "*(Ah... the vision module is under maintenance right now. "
                            "Try again when I'm running on a Qwen model, you idiot.)*"
                        )
                    return

                try:
                    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
                    img.thumbnail((1024, 1024))
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=85)
                    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
                    user_msg_content = [
                        {"type": "text", "text": user_text or ("看看这张图片。" if lang == "zh" else "Look at this image.")},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ]
                except Exception as e:
                    logger.error("Image processing failed: %s", e)
                    user_msg_content = user_text or "..."
            else:
                user_msg_content = user_text or "..."

            # ── Assemble system prompt ──────────────────────────
            wl = get_worldline()
            affinity = get_affinity(user_id)
            tier = get_affinity_tier(affinity)

            # Convergence announce
            if wl.get("convergence_active") and not self._convergence_announced_start:
                self._convergence_announced_start = True
                self._convergence_announced_end = False
                await self._announce_convergence("convergence_started")
            elif not wl.get("convergence_active") and not self._convergence_announced_end:
                if self._convergence_announced_start:
                    self._convergence_announced_end = True
                    self._convergence_announced_start = False
                    await self._announce_convergence("convergence_ended")

            extra = ""

            # Keyword context
            if is_keyword:
                extra = (
                    f"[Context: 群友聊天提到了你，你没被 @ 但忍不住插嘴了。"
                    f"触发消息: 「{content[:100]}」]"
                    if lang == "zh" else
                    f"[Context: Someone mentioned you in chat. You weren't pinged "
                    f"but you couldn't resist. Trigger: '{content[:100]}']"
                )

            # Free-speaking context: inject recent messages
            if is_free_speak:
                recent = list(self.recent_messages[channel_id])[-15:]
                rc = "\n".join(f"  {m['author']}: {m['content'][:80]}" for m in recent)
                if lang == "zh":
                    extra = (
                        f"[Context: 群友们正在聊天，你想自然地插一句嘴——"
                        f"不要生硬地打招呼，当成你本来就是对话的一部分。"
                        f"记住你是个傲娇科学家+偶尔的网瘾少女。"
                        f"最近的消息:\n{rc}]"
                    )
                else:
                    extra = (
                        f"[Context: The group is chatting and you want to "
                        f"naturally join in — don't awkwardly introduce yourself, "
                        f"speak as if you were already part of the conversation. "
                        f"You're a tsundere scientist + occasional net addict. "
                        f"Recent messages:\n{rc}]"
                    )
                self._last_free_speak[channel_id] = now_ts
                self._last_reply_to.setdefault(channel_id, {})[user_id] = now_ts

            # Dynamic injects
            injects = self._build_dynamic_injects(lang, wl)
            if injects:
                extra = extra + "\n" + injects if extra else injects

            system_prompt = build_system_prompt(
                lang=lang, worldline_mode=wl["mode"],
                worldline_value=wl["value_str"],
                affinity_tier=tier,
                user_display_name=message.author.display_name,
                extra_notes=extra,
            )

            # ── LLM call ─────────────────────────────────────────
            messages = [{"role": "system", "content": system_prompt}]
            for entry in self.history[channel_id]:
                messages.append(entry)
            messages.append({"role": "user", "content": user_msg_content})

            llm_resp = await generate_reply(messages)

            # Handle vision-unavailable sentinel
            if llm_resp and llm_resp.get("reply") == "__VISION_UNAVAILABLE__":
                if lang == "zh":
                    await _respond(
                        "*(啊……视觉模块正在维护中，暂时没法看图。"
                        "等我切换到 Qwen 模型再说吧，笨蛋。)*"
                    )
                else:
                    await _respond(
                        "*(Ah... the vision module is under maintenance right now. "
                        "Try again when I'm running on a Qwen model, you idiot.)*"
                    )
                return

            if llm_resp is None:
                fail = ("*(Amadeus 信号中断……)*" if lang == "zh"
                        else "*(Amadeus's signal was lost...)*")
                await _respond(fail)
                return

            emotion = llm_resp.get("emotion", "other")
            reply_text = llm_resp.get("reply", "...")

            # ── Tavily search loop ──────────────────────────────
            # The LLM may request a web search by including "search_query".
            # If so, run Tavily, feed results back, and get a final reply.
            # Max 1 search per turn to prevent loops.
            search_query = (llm_resp.get("search_query") or "").strip()
            if search_query and TAVILY_API_KEY and TAVILY_API_KEY != "your_tavily_api_key_here":
                try:
                    from tavily import TavilyClient
                    client = TavilyClient(api_key=TAVILY_API_KEY)
                    results = client.search(query=search_query, search_depth="basic", max_results=3)
                    answer = results.get("answer", "")
                    sources = results.get("results", [])

                    # Format results as a compact text block for the LLM
                    result_text = f"Tavily search results for '{search_query}':\n"
                    if answer:
                        result_text += f"Summary: {answer[:500]}\n\n"
                    for i, s in enumerate(sources[:3], 1):
                        result_text += f"{i}. {s.get('title','?')}: {s.get('content','')[:300]}\n"

                    # Feed results back to LLM for final reply
                    messages.append({"role": "assistant", "content": json.dumps(llm_resp, ensure_ascii=False)})
                    messages.append({"role": "user", "content": result_text})

                    llm_resp2 = await generate_reply(messages)
                    if llm_resp2 and llm_resp2.get("reply") and llm_resp2["reply"] != "__VISION_UNAVAILABLE__":
                        emotion = llm_resp2.get("emotion", emotion)
                        reply_text = llm_resp2.get("reply", reply_text)
                    logger.info("🔍 Tavily search: '%s' → used in reply", search_query)
                except Exception as e:
                    logger.error("Tavily search in-chat failed: %s", e)

            # ── Update history ──────────────────────────────────
            self.history[channel_id].append({
                "role": "user",
                "content": user_text if user_text else "[sent an image]",
            })
            self.history[channel_id].append({
                "role": "assistant", "content": reply_text,
            })

            # Memory summarisation every 10 turns
            self.msg_counter[channel_id] = self.msg_counter.get(channel_id, 0) + 1
            if self.msg_counter[channel_id] % 10 == 0:
                asyncio.create_task(
                    self._summarise_memory(channel_id, list(self.history[channel_id]), lang)
                )

            # ── Reply ────────────────────────────────────────────
            # Attach character sprite if this is the designated art channel
            image_path = None
            if message.guild and channel_id == get_art_channel(str(message.guild.id)):
                image_path = get_image_for_emotion(emotion, self.image_index)

            if image_path and os.path.exists(image_path):
                await _respond(reply_text, file=discord.File(image_path))
            else:
                await _respond(reply_text)
            # Track for conversation continuity (per-user)
            self._last_reply_to.setdefault(channel_id, {})[user_id] = datetime.datetime.now().timestamp()

    # ── Free-Speaking Logic ─────────────────────────────────────

    def _should_free_speak(self, channel_id: str, now_ts: float, content: str, author_id: str, is_dm: bool) -> bool:
        """
        Interest-driven reply decision with conversation continuity.

        1. Check interest categories (questions, science, Melbourne, etc.)
        2. If no interest match, check if this is a conversation thread:
           the bot just replied to this person and they're talking back.
        3. DM channels use stricter probabilities.

        This prevents the bot from going silent mid-conversation just
        because the follow-up message doesn't contain a keyword.
        """
        # Safety net: absolute minimum seconds between any two messages
        last_any = self._last_free_speak.get(channel_id, 0)
        if now_ts - last_any < FREE_SPEAK_COOLDOWN_SEC:
            return False

        # Don't reply to the same user twice in a row UNLESS it's a
        # conversation thread (continuation overrides this below)
        last_author = self._last_free_speak_author.get(channel_id)
        same_user = (last_author == author_id)

        content_lower = content.lower()

        # ── Step 1: Interest category match ─────────────────────
        # A category match whose dice roll fails does NOT silence the
        # message: it falls through to the conversation-continuity
        # check below (otherwise ordinary follow-ups in a live thread
        # get swallowed by the first common keyword like "?").
        matched = False
        for _label, keywords, prob_group, prob_dm in FREE_SPEAK_INTERESTS:
            for kw in keywords:
                if kw.lower() in content_lower:
                    prob = prob_dm if is_dm else prob_group
                    if random.random() < prob:
                        self._last_free_speak_author[channel_id] = author_id
                        return True
                    matched = True
                    break
            if matched:
                break  # Matched one category but roll failed — don't check others

        # ── Step 2: Conversation continuity ─────────────────────
        # Check per-user: did the bot recently reply to THIS specific user?
        # Each user has an independent thread window, so replying to B
        # does not break A's conversation.
        channel_threads = self._last_reply_to.get(channel_id, {})
        thread_ts = channel_threads.get(author_id, 0)
        if now_ts - thread_ts < FREE_SPEAK_CONTINUATION_WINDOW_SEC:
            if random.random() < FREE_SPEAK_CONTINUATION_PROB:
                self._last_free_speak_author[channel_id] = author_id
                return True
            return False  # Continuation check failed — stay silent

        # ── Step 3: Same-user guard (only if NOT a thread) ──────
        if FREE_SPEAK_SAME_USER_COOLDOWN and same_user:
            return False

        # Periodically clean expired thread entries (once per ~50 messages)
        if self.msg_seen.get(channel_id, 0) % 50 == 0:
            cutoff = now_ts - FREE_SPEAK_CONTINUATION_WINDOW_SEC
            channel_threads = self._last_reply_to.get(channel_id, {})
            expired = [uid for uid, ts in channel_threads.items() if ts < cutoff]
            for uid in expired:
                del channel_threads[uid]

        return False  # Nothing interesting


# ═══════════════════════════════════════════════════════════════════
# Entry Point
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    run_startup_check()
    bot = AmadeusBot()
    bot.run(DISCORD_TOKEN)