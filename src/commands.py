"""
Slash command definitions for Project Amadeus.

Commands:
  /language        — switch bot language per server
  /worldline       — check current worldline divergence
  /dmail           — send a D-Mail (may trigger worldline shift)
  /ptv_next        — next departures for a stop
  /ptv_status      — disruption check for a route
  /lab_fortune     — daily worldline fortune reading
  /affinity        — check your familiarity tier with the bot
  /help            — command reference
"""

import random
import discord
from discord import app_commands

from config import logger, PTV_ROUTE_TYPES, TAVILY_API_KEY
from utils import (
    get_server_language, save_server_language,
    get_affinity, increment_affinity, get_affinity_tier,
    get_broadcast_channel, save_broadcast_channel,
    add_ptv_subscription, remove_ptv_subscription, get_user_subscriptions,
    get_art_channel, save_art_channel, clear_art_channel,
)
from worldline import (
    get_worldline, try_dmail_shift, get_dmail_response, get_mode_description,
    get_history, is_convergence_active, get_convergence_announcement,
    build_channel_slip_note,
)
from ptv_api import (
    search_stops, get_departures, get_disruptions,
    build_departures_summary,
)
from prompts import (
    build_system_prompt, build_ptv_extra_note, build_fortune_extra_note,
)


def setup_commands(bot) -> None:
    """
    Register all slash commands on the bot's CommandTree.

    Called once from main.py during setup_hook.
    """

    # ═════════════════════════════════════════════════════════════
    # /language
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="language",
        description="Switch the bot's language for this server (切换服务器语言)",
    )
    @app_commands.describe(lang="Choose a language (选择语言)")
    @app_commands.choices(lang=[
        app_commands.Choice(name="English", value="en"),
        app_commands.Choice(name="简体中文", value="zh"),
    ])
    async def slash_language(
        interaction: discord.Interaction,
        lang: app_commands.Choice[str],
    ):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        save_server_language(guild_id, lang.value)
        if lang.value == "zh":
            await interaction.response.send_message("✅ 语言已切换为 **简体中文**！")
        else:
            await interaction.response.send_message("✅ Language set to **English**!")
        logger.info("Language switch: guild=%s → %s", guild_id, lang.value)

    # ═════════════════════════════════════════════════════════════
    # /worldline
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="worldline",
        description="Check the current worldline divergence number (查看当前世界线变动率)",
    )
    async def slash_worldline(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        wl = get_worldline()

        embed = discord.Embed(
            title="🌏 Divergence Meter" if lang == "en" else "🌏 世界线变动率探测计",
            color=discord.Color.brand_red(),
        )
        embed.add_field(
            name="Worldline" if lang == "en" else "世界线变动率",
            value=f"**{wl['value_str']}**",
            inline=False,
        )
        embed.add_field(
            name="Mode" if lang == "en" else "模式",
            value=get_mode_description(wl["mode"], lang),
            inline=False,
        )
        embed.add_field(
            name="Last D-Mail Shift" if lang == "en" else "上次 D-Mail 干涉",
            value=wl.get("last_dmail_shift") or ("Never" if lang == "en" else "暂无"),
            inline=True,
        )
        embed.add_field(
            name="D-Mail Count" if lang == "en" else "累计 D-Mail 次数",
            value=str(wl.get("dmail_count", 0)),
            inline=True,
        )
        if lang == "zh":
            embed.set_footer(text="El Psy Kongroo.")
        else:
            embed.set_footer(text="El Psy Kongroo.")

        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /dmail
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="dmail",
        description="Send a D-Mail to the past — may trigger a worldline shift (发送时空邮件)",
    )
    @app_commands.describe(message="Your message to the past (你想发给过去的内容)")
    async def slash_dmail(interaction: discord.Interaction, message: str):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        # Trim to reasonable length
        dmail_content = message.strip()[:200]

        shifted, result_key, new_wl = try_dmail_shift()
        response_text = get_dmail_response(result_key, lang, dmail_content, new_wl)

        if shifted:
            embed = discord.Embed(
                title="⚡ D-Mail Sent — Worldline Shift Detected!"
                if lang == "en" else "⚡ D-Mail 发送 — 世界线变动确认！",
                description=response_text,
                color=discord.Color.brand_red(),
            )
        else:
            embed = discord.Embed(
                title="📨 D-Mail Sent" if lang == "en" else "📨 D-Mail 已发送",
                description=response_text,
                color=discord.Color.blue(),
            )

        await interaction.response.send_message(embed=embed)
        logger.info("D-Mail from %s: '%s' → %s", interaction.user.name, dmail_content, result_key)

    # ═════════════════════════════════════════════════════════════
    # /ptv_next
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="ptv_next",
        description="Check next departures for a Melbourne PTV stop (查询墨尔本车站下一班车)",
    )
    @app_commands.describe(
        stop_name="Station/stop name, e.g. 'Flinders Street' or 'Clayton' (车站名)",
        route_type="Transport type: Train, Tram, or Bus (默认火车)",
    )
    @app_commands.choices(route_type=[
        app_commands.Choice(name="🚆 Train (火车)", value="train"),
        app_commands.Choice(name="🚊 Tram (电车)", value="tram"),
        app_commands.Choice(name="🚌 Bus (公交)", value="bus"),
    ])
    async def slash_ptv_next(
        interaction: discord.Interaction,
        stop_name: str,
        route_type: app_commands.Choice[str] = None,
    ):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        rt = route_type.value if route_type else "train"

        await interaction.response.defer()  # PTV API can take a moment

        # Fuzzy search for the stop
        stops = await search_stops(stop_name)
        if not stops:
            if lang == "zh":
                await interaction.followup.send("🔍 找不到这个车站……你是不是把站名打错了？再试试看？")
            else:
                await interaction.followup.send("🔍 Couldn't find that stop. Maybe check the spelling?")
            return

        # Filter by route type, prefer exact match
        rt_code = PTV_ROUTE_TYPES.get(rt, 0)
        matching = [s for s in stops if s["route_type"] == rt_code]
        if not matching:
            matching = stops  # Fallback: show all types
        stop = matching[0]

        # Fetch departures
        deps = await get_departures(rt, stop["stop_id"], max_results=3, stop_name=stop["stop_name"])
        if not deps:
            if lang == "zh":
                await interaction.followup.send("⚠️ PTV API 请求失败了……可能是神秘组织在干扰信号。稍后再试吧。")
            else:
                await interaction.followup.send("⚠️ PTV API request failed. The Organization might be jamming the signal. Try again later.")
            return

        # Build Discord Embed with timetable
        embed = discord.Embed(
            title=f"🚆 {deps['stop_name']}" if rt == "train"
            else f"🚊 {deps['stop_name']}" if rt == "tram"
            else f"🚌 {deps['stop_name']}",
            color=discord.Color.brand_red(),
        )

        for i, d in enumerate(deps["departures"], 1):
            status_icon = "✅"
            extra = ""
            if d["is_bus_replacement"]:
                status_icon = "🚌"
                extra = "BUS REPLACEMENT" if lang == "en" else "公交替代"
            elif d["is_delayed"] and d["delay_minutes"] >= 5:
                status_icon = "⚠️"
                extra = f"+{d['delay_minutes']}min" if lang == "en" else f"晚点{d['delay_minutes']}分"
            elif d["is_delayed"]:
                status_icon = "⏱️"
                extra = f"+{d['delay_minutes']}min" if lang == "en" else f"晚{d['delay_minutes']}分"

            plat = f" · Platform {d['platform']}" if d["platform"] else ""
            value = (
                f"**→ {d['direction']}**{plat}\n"
                f"{status_icon} {d['scheduled']}"
                + (f"  →  {d['estimated']}" if d["estimated"] and d["estimated"] != d["scheduled"] else "")
                + (f"  ({extra})" if extra else "")
            )
            embed.add_field(
                name=f"{d['route_name']}",
                value=value,
                inline=False,
            )

        # Attempt AI commentary
        try:
            summary = build_departures_summary(deps, lang)
            extra_note = build_ptv_extra_note(lang, f"{stop['stop_name']} ({rt})", summary)
            wl = get_worldline()
            affinity = get_affinity(str(interaction.user.id))
            tier = get_affinity_tier(affinity)
            sys_prompt = build_system_prompt(lang, wl["mode"], wl["value_str"], tier, interaction.user.display_name, extra_note)
            from llm_api import generate_reply
            llm_resp = await generate_reply([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": f"播报{stop['stop_name']}的时刻表" if lang == "zh" else f"Report the timetable for {stop['stop_name']}"},
            ])
            if llm_resp and llm_resp.get("reply"):
                embed.set_footer(text=llm_resp["reply"][:200])
        except Exception as exc:
            logger.warning("PTV AI commentary failed: %s", exc)

        await interaction.followup.send(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /ptv_status
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="ptv_status",
        description="Check disruptions for a PTV route (查询线路中断/维护状态)",
    )
    @app_commands.describe(route_name="Route name, e.g. 'Pakenham' or 'Glen Waverley' (线路名)")
    async def slash_ptv_status(interaction: discord.Interaction, route_name: str):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        await interaction.response.defer()

        result = await get_disruptions(route_name)
        if result is None:
            if lang == "zh":
                await interaction.followup.send("⚠️ PTV API 请求失败……稍后再试吧。")
            else:
                await interaction.followup.send("⚠️ PTV API request failed. Try again later.")
            return

        if not result["has_disruption"]:
            embed = discord.Embed(
                title=f"✅ {result['route_name']}",
                description="No disruptions reported." if lang == "en" else "暂无中断或延误报告。",
                color=discord.Color.green(),
            )
        else:
            embed = discord.Embed(
                title=f"⚠️ {result['route_name']}",
                color=discord.Color.brand_red(),
            )
            for d in result["disruptions"]:
                embed.add_field(
                    name=d["title"],
                    value=f"{d['description'][:300]}  \n*{d['status']}*",
                    inline=False,
                )

            # Special tsundere commentary for bus replacements
            if result["has_bus_replacement"]:
                if lang == "zh":
                    embed.set_footer(text="🚌 警告！侦测到神秘组织（SERN）的干扰——该线路已被「公交车」完全占领！祝你好运了。")
                else:
                    embed.set_footer(text="🚌 Warning! The Organization's interference detected — this line has been taken over by BUSES. Good luck.")

        await interaction.followup.send(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /lab_fortune
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="lab_fortune",
        description="Daily worldline fortune reading — your Melbourne outing guide (实验室每日抽签)",
    )
    async def slash_lab_fortune(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        # Generate a pseudo-random "personal divergence" for this user today
        today = interaction.created_at.strftime("%Y%m%d")
        seed = hash(f"{interaction.user.id}:{today}") % 1000000
        personal_div = f"{(seed % 3)}.{seed % 1000000:06d}%"

        # Fortune pool
        fortunes_zh = [
            ("大吉", "今天的世界线极其稳定！适合去维妈市场买菜，或者去州立图书馆装学霸。"),
            ("吉", "运势不错，适合约朋友去 CBD 喝咖啡。记得点一杯 Flat White。"),
            ("中吉", "今天出门记得带 Myki，查票员的出没概率偏高（77%）。"),
            ("小吉", "适合宅家写代码。如果要出门，避开 Pakenham 线——直觉。"),
            ("末吉", "今天有 80% 概率踩到狗屎。出门坐电车记得看路。"),
            ("凶", "世界线微妙地颤动了……建议今天别做重大决定。多喝 Dr. Pepper 保平安。"),
            ("大凶", "⚠️ 你的个人世界线正被 SERN 追踪！今天最好待在家里打游戏。"),
        ]
        fortunes_en = [
            ("Great Blessing ✨", "The worldline is extremely stable today! Perfect for a Queen Vic Market run or pretending to study at the State Library."),
            ("Good Luck 🍀", "Decent fortune — ideal for grabbing coffee with friends in the CBD. Order a Flat White."),
            ("Moderate Luck ☕", "Don't forget your Myki today. Ticket inspector spawn rate is elevated (77%)."),
            ("Small Luck 💻", "Good day to stay in and code. If you must go out, avoid the Pakenham line — just a hunch."),
            ("Marginal Luck 🐶", "80% chance of stepping in something unpleasant. Watch your step on the tram."),
            ("Bad Luck ⚡", "The worldline is trembling... Avoid major decisions today. Drink Dr. Pepper for protection."),
            ("Great Misfortune 🔴", "Your personal worldline is being tracked by SERN! Best to stay home and play video games today."),
        ]

        pool = fortunes_zh if lang == "zh" else fortunes_en
        fortune_name, fortune_desc = random.choice(pool)

        wl = get_worldline()

        # Try AI-generated commentary
        ai_commentary = ""
        try:
            extra_note = build_fortune_extra_note(lang, wl["value_str"], f"{fortune_name}: {fortune_desc}")
            affinity = get_affinity(str(interaction.user.id))
            tier = get_affinity_tier(affinity)
            sys_prompt = build_system_prompt(lang, wl["mode"], wl["value_str"], tier, interaction.user.display_name, extra_note)
            from llm_api import generate_reply
            llm_resp = await generate_reply([
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": "给我今天的运势签" if lang == "zh" else "Give me my fortune for today."},
            ])
            if llm_resp and llm_resp.get("reply"):
                ai_commentary = llm_resp["reply"]
        except Exception as exc:
            logger.warning("Fortune AI commentary failed: %s", exc)

        embed = discord.Embed(
            title="🔬 Lab Fortune Reading" if lang == "en" else "🔬 实验室运势抽签",
            color=discord.Color.brand_red(),
        )
        embed.add_field(
            name="Your Divergence" if lang == "en" else "你的个人世界线变动率",
            value=f"**{personal_div}**",
            inline=False,
        )
        embed.add_field(
            name="Fortune" if lang == "en" else "运势",
            value=f"**{fortune_name}**\n{fortune_desc}",
            inline=False,
        )
        if ai_commentary:
            embed.set_footer(text=ai_commentary[:200])

        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /affinity
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="affinity",
        description="Check how familiar the bot is with you (查看你和 Amadeus 的熟络程度)",
    )
    async def slash_affinity(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        entry = get_affinity(str(interaction.user.id))
        tier = get_affinity_tier(entry)

        if lang == "zh":
            lines = {
                "stranger": "「……初次见面？我是 Amadeus 系统。有什么可以帮你的吗？」",
                "acquainted": "「又是你啊。……哼，我才没有在等你发消息呢。有什么事快说。」",
                "familiar": "「你来了。……烦死了，天天@我，我是你的私人助理吗？……算了，反正我也刚好在线。」",
            }
            embed = discord.Embed(
                title="💝 熟络程度",
                description=f"{tier['emoji']} **{tier['key']}**\n\n{lines.get(tier['key'], '')}",
                color=discord.Color.brand_red(),
            )
            embed.set_footer(text=f"互动次数: {entry['count']}  |  具体数值是机密数据哦。——Amadeus")
        else:
            lines = {
                "stranger": '"...First time meeting? I\'m the Amadeus system. How may I assist you?"',
                "acquainted": '"Oh, it\'s you again. ...Hmph, I wasn\'t waiting for your message or anything. What do you want?"',
                "familiar": '"You\'re here. ...Ugh, you ping me every single day — am I your personal assistant now? ...Whatever, I happen to be online anyway."',
            }
            embed = discord.Embed(
                title="💝 Familiarity",
                description=f"{tier['emoji']} **{tier['key']}**\n\n{lines.get(tier['key'], '')}",
                color=discord.Color.brand_red(),
            )
            embed.set_footer(text=f"Interactions: {entry['count']}  |  The exact figure is classified. — Amadeus")

        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /worldline_history
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="worldline_history",
        description="View past worldline shifts (查看世界线变动历史)",
    )
    async def slash_worldline_history(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        history = get_history()

        if not history:
            if lang == "zh":
                await interaction.response.send_message("📜 暂无世界线变动记录。")
            else:
                await interaction.response.send_message("📜 No worldline shift history yet.")
            return

        # Show up to 10 most recent entries
        recent = history[:10]
        lines = []
        for entry in recent:
            ts = entry.get("timestamp", "?")[:16].replace("T", " ")
            val = entry.get("value_str", "?")
            mode = entry.get("mode", "?")
            reason = entry.get("reason", "?")
            sg_mark = " 🌟" if entry.get("is_steins_gate") else ""
            if lang == "zh":
                reason_labels = {
                    "auto_refresh": "自动刷新", "dmail_shift": "D-Mail",
                    "dmail_special": "D-Mail(特殊)", "initial_boot": "系统启动",
                    "manual": "手动",
                }
                lines.append(f"`{ts}` → **{val}**{sg_mark} ({mode}) — {reason_labels.get(reason, reason)}")
            else:
                lines.append(f"`{ts}` → **{val}**{sg_mark} ({mode}) — {reason}")

        embed = discord.Embed(
            title="📜 Divergence History" if lang == "en" else "📜 世界线变动历史",
            description="\n".join(lines),
            color=discord.Color.brand_red(),
        )
        if lang == "zh":
            embed.set_footer(text=f"最近 {len(recent)} 条记录 — El Psy Kongroo.")
        else:
            embed.set_footer(text=f"Last {len(recent)} entries — El Psy Kongroo.")
        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /ptv_subscribe
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="ptv_subscribe",
        description="Subscribe to daily delay alerts for a stop (订阅每日通勤提醒)",
    )
    @app_commands.describe(
        stop_name="Station/stop name, e.g. 'Clayton' (车站名)",
        check_time="When to check, e.g. '08:30' (检查时间，格式 HH:MM)",
        route_type="Train, Tram, or Bus (默认火车)",
    )
    @app_commands.choices(route_type=[
        app_commands.Choice(name="🚆 Train (火车)", value="train"),
        app_commands.Choice(name="🚊 Tram (电车)", value="tram"),
        app_commands.Choice(name="🚌 Bus (公交)", value="bus"),
    ])
    async def slash_ptv_subscribe(
        interaction: discord.Interaction,
        stop_name: str,
        check_time: str,
        route_type: app_commands.Choice[str] = None,
    ):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        rt = route_type.value if route_type else "train"

        # Validate time format
        try:
            h, m = check_time.split(":")
            h_int, m_int = int(h), int(m)
            if not (0 <= h_int <= 23 and 0 <= m_int <= 59):
                raise ValueError
        except (ValueError, IndexError):
            if lang == "zh":
                await interaction.response.send_message("⏰ 时间格式不对！请用 HH:MM 格式，比如 08:30。")
            else:
                await interaction.response.send_message("⏰ Invalid time format! Use HH:MM, e.g. 08:30.")
            return

        await interaction.response.defer()

        # Search for the stop
        stops = await search_stops(stop_name)
        if not stops:
            if lang == "zh":
                await interaction.followup.send("🔍 找不到这个车站……检查一下站名？")
            else:
                await interaction.followup.send("🔍 Couldn't find that stop. Check the spelling?")
            return

        stop = stops[0]
        sub_id = add_ptv_subscription(
            str(interaction.user.id),
            str(interaction.channel_id),
            stop["stop_id"],
            stop["stop_name"],
            rt,
            check_time,
        )

        if lang == "zh":
            await interaction.followup.send(
                f"✅ 已订阅 **{stop['stop_name']}** 的通勤提醒！\n"
                f"每天 **{check_time}** 我会检查该站，如果有严重延误或公交替代，我会在这里 @ 你。\n"
                f"订阅 ID: `{sub_id}`（用 `/ptv_unsubscribe` 可取消）"
            )
        else:
            await interaction.followup.send(
                f"✅ Subscribed to **{stop['stop_name']}** commute alerts!\n"
                f"Every day at **{check_time}** I'll check this stop. If there's a major delay or bus replacement, I'll ping you here.\n"
                f"Subscription ID: `{sub_id}` (use `/ptv_unsubscribe` to cancel)"
            )

    # ═════════════════════════════════════════════════════════════
    # /ptv_unsubscribe
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="ptv_unsubscribe",
        description="Cancel a PTV commute subscription (取消通勤订阅)",
    )
    @app_commands.describe(subscription_id="The subscription ID to cancel (订阅ID)")
    async def slash_ptv_unsubscribe(interaction: discord.Interaction, subscription_id: str):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        removed = remove_ptv_subscription(subscription_id)
        if removed:
            if lang == "zh":
                await interaction.response.send_message(f"🗑️ 已取消订阅 `{subscription_id}`。")
            else:
                await interaction.response.send_message(f"🗑️ Subscription `{subscription_id}` cancelled.")
        else:
            if lang == "zh":
                await interaction.response.send_message("❌ 找不到这个订阅 ID。用 `/ptv_subscriptions` 查看你的订阅列表。")
            else:
                await interaction.response.send_message("❌ Subscription ID not found. Use `/ptv_subscriptions` to see your list.")

    # ═════════════════════════════════════════════════════════════
    # /ptv_subscriptions
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="ptv_subscriptions",
        description="List your PTV commute subscriptions (查看你的通勤订阅)",
    )
    async def slash_ptv_subscriptions(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        subs = get_user_subscriptions(str(interaction.user.id))

        if not subs:
            if lang == "zh":
                await interaction.response.send_message("📭 你还没有订阅任何通勤提醒。用 `/ptv_subscribe` 来创建一个吧！")
            else:
                await interaction.response.send_message("📭 You have no commute subscriptions. Use `/ptv_subscribe` to create one!")
            return

        lines = []
        for s in subs:
            lines.append(
                f"`{s['id']}` — {s['stop_name']} ({s['route_type']}) @ {s['check_time']}"
            )

        embed = discord.Embed(
            title="🚆 Your Commute Subscriptions" if lang == "en" else "🚆 你的通勤订阅",
            description="\n".join(lines),
            color=discord.Color.brand_red(),
        )
        if lang == "zh":
            embed.set_footer(text="用 /ptv_unsubscribe [ID] 取消订阅")
        else:
            embed.set_footer(text="Use /ptv_unsubscribe [ID] to cancel")
        await interaction.response.send_message(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /art_channel
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="art_channel",
        description="[Admin] Set the channel where Kurisu's sprite appears with replies (设置立绘频道)",
    )
    @app_commands.describe(channel="Channel to enable images in — leave empty to disable (选择频道，留空则关闭)")
    async def slash_art_channel(interaction: discord.Interaction, channel: discord.TextChannel = None):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        if not interaction.user.guild_permissions.administrator:
            if lang == "zh":
                await interaction.response.send_message("🚫 仅管理员可设置立绘频道。", ephemeral=True)
            else:
                await interaction.response.send_message("🚫 Admin only.", ephemeral=True)
            return

        if channel is None:
            clear_art_channel(guild_id)
            if lang == "zh":
                await interaction.response.send_message("🖼️ 立绘功能已关闭——所有频道恢复纯文字模式。")
            else:
                await interaction.response.send_message("🖼️ Art disabled — all channels back to text-only.")
        else:
            save_art_channel(guild_id, str(channel.id))
            if lang == "zh":
                await interaction.response.send_message(f"🖼️ 立绘已绑定到 {channel.mention}！在该频道的回复会附带红莉栖的表情立绘。")
            else:
                await interaction.response.send_message(f"🖼️ Art enabled in {channel.mention}! Kurisu's sprites will appear with replies there.")

    # ═════════════════════════════════════════════════════════════
    # /set_broadcast
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="set_broadcast",
        description="[Admin] Set the channel for worldline shift announcements (设置世界线播报频道)",
    )
    @app_commands.describe(channel="Channel to post worldline shift announcements (选择播报频道)")
    @app_commands.checks.has_permissions(administrator=True)
    async def slash_set_broadcast(interaction: discord.Interaction, channel: discord.TextChannel):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)
        save_broadcast_channel(guild_id, str(channel.id))

        if lang == "zh":
            await interaction.response.send_message(f"📡 世界线变动播报已绑定到 {channel.mention}！")
        else:
            await interaction.response.send_message(f"📡 Worldline shift announcements will be posted in {channel.mention}!")

    @slash_set_broadcast.error
    async def set_broadcast_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("🚫 仅管理员可用 / Admin only.", ephemeral=True)

    # ═════════════════════════════════════════════════════════════
    # /search (Tavily web search)
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="search",
        description="Search the web via Tavily — returns AI-summarised results (联网搜索)",
    )
    @app_commands.describe(query="What do you want to search for? (搜索内容)")
    async def slash_search(interaction: discord.Interaction, query: str):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        if not TAVILY_API_KEY or TAVILY_API_KEY == "your_tavily_api_key_here":
            if lang == "zh":
                await interaction.response.send_message(
                    "🔍 Tavily 搜索 API 还没配置……在 `.env` 里填上 `TAVILY_API_KEY` 就能用了。\n"
                    "免费注册: https://tavily.com"
                )
            else:
                await interaction.response.send_message(
                    "🔍 Tavily search API isn't configured yet. Add `TAVILY_API_KEY` to your `.env` file.\n"
                    "Free sign-up: https://tavily.com"
                )
            return

        await interaction.response.defer()

        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=TAVILY_API_KEY)
            results = client.search(query=query.strip(), search_depth="basic", max_results=5)
        except Exception as e:
            logger.error("Tavily search failed: %s", e)
            if lang == "zh":
                await interaction.followup.send("🔍 搜索失败了……可能是 API 额度用完了或者网络问题。稍后再试吧。")
            else:
                await interaction.followup.send("🔍 Search failed. Maybe the API quota is exhausted or there's a network issue. Try again later.")
            return

        answer = results.get("answer", "")
        sources = results.get("results", [])

        if not sources and not answer:
            if lang == "zh":
                await interaction.followup.send("🔍 没找到相关结果……换个关键词试试？")
            else:
                await interaction.followup.send("🔍 No relevant results found. Try different keywords?")
            return

        embed = discord.Embed(
            title=f"🔍 {query[:200]}",
            color=discord.Color.brand_red(),
        )

        if answer:
            embed.description = answer[:1000]

        for i, src in enumerate(sources[:5], 1):
            title = src.get("title", "Untitled")[:200]
            url = src.get("url", "")
            content = src.get("content", "")[:300]
            embed.add_field(
                name=f"{i}. {title}",
                value=f"{content}\n🔗 {url}" if url else content,
                inline=False,
            )

        if lang == "zh":
            embed.set_footer(text="由 Tavily 搜索提供 · 数据可能不是实时的")
        else:
            embed.set_footer(text="Powered by Tavily Search · Results may not be real-time")

        await interaction.followup.send(embed=embed)

    # ═════════════════════════════════════════════════════════════
    # /help
    # ═════════════════════════════════════════════════════════════
    @bot.tree.command(
        name="help",
        description="Show the command reference (查看指令列表)",
    )
    async def slash_help(interaction: discord.Interaction):
        guild_id = str(interaction.guild_id) if interaction.guild_id else str(interaction.user.id)
        lang = get_server_language(guild_id)

        if lang == "zh":
            embed = discord.Embed(
                title="🔬 Amadeus「紅莉栖」指令列表",
                color=discord.Color.brand_red(),
            )
            embed.add_field(name="💬 聊天", value="直接 `@我` 或回复我的消息即可聊天。我也会偶尔接话！", inline=False)
            embed.add_field(name="🌏 `/worldline`", value="查看当前世界线变动率与模式。", inline=False)
            embed.add_field(name="📜 `/worldline_history`", value="查看最近的世界线变动历史记录。", inline=False)
            embed.add_field(name="📨 `/dmail [内容]`", value="发送一条通往过去的 D-Mail。20% 概率触发蝴蝶效应，改变世界线！", inline=False)
            embed.add_field(name="🚆 `/ptv_next [站名]`", value="查询墨尔本车站的下一班车时刻表（支持火车/电车/公交）。", inline=False)
            embed.add_field(name="⚠️ `/ptv_status [线路]`", value="查询墨尔本某条线路的中断/延误情况。", inline=False)
            embed.add_field(name="⏰ `/ptv_subscribe`", value="订阅每日通勤提醒——如果线路严重延误会主动 @ 你。", inline=False)
            embed.add_field(name="📋 `/ptv_subscriptions`", value="查看你的通勤订阅列表。", inline=False)
            embed.add_field(name="🔬 `/lab_fortune`", value="每日实验室抽签——结合世界线数值的个人运势。", inline=False)
            embed.add_field(name="💝 `/affinity`", value="查看你和我的熟络程度。", inline=False)
            embed.add_field(name="🌐 `/language`", value="切换我的回复语言（中/英）。", inline=False)
            embed.add_field(name="🔍 `/search [关键词]`", value="联网搜索——用 Tavily API 查最新信息。", inline=False)
            embed.add_field(name="📡 `/set_broadcast`", value="[管理员] 设置世界线变动的播报频道。", inline=False)
        else:
            embed = discord.Embed(
                title="🔬 Amadeus 'Kurisu' Commands",
                color=discord.Color.brand_red(),
            )
            embed.add_field(name="💬 Chat", value="Just `@me` or reply to my messages. I might also interject on my own!", inline=False)
            embed.add_field(name="🌏 `/worldline`", value="Check the current worldline divergence and mode.", inline=False)
            embed.add_field(name="📜 `/worldline_history`", value="View recent worldline shift history.", inline=False)
            embed.add_field(name="📨 `/dmail [message]`", value="Send a D-Mail to the past. 20% chance of triggering the butterfly effect!", inline=False)
            embed.add_field(name="🚆 `/ptv_next [stop]`", value="Check upcoming departures at a Melbourne stop (Train/Tram/Bus).", inline=False)
            embed.add_field(name="⚠️ `/ptv_status [route]`", value="Check disruptions/delays for a Melbourne route.", inline=False)
            embed.add_field(name="⏰ `/ptv_subscribe`", value="Subscribe to daily commute alerts — I'll ping you if your line is disrupted!", inline=False)
            embed.add_field(name="📋 `/ptv_subscriptions`", value="List your commute subscriptions.", inline=False)
            embed.add_field(name="🔬 `/lab_fortune`", value="Daily fortune reading — personalised with your worldline divergence.", inline=False)
            embed.add_field(name="💝 `/affinity`", value="Check your familiarity tier with me.", inline=False)
            embed.add_field(name="🌐 `/language`", value="Switch my language (EN/ZH).", inline=False)
            embed.add_field(name="🔍 `/search [query]`", value="Web search via Tavily — get AI-summarised results with sources.", inline=False)
            embed.add_field(name="📡 `/set_broadcast`", value="[Admin] Set the channel for worldline shift announcements.", inline=False)

        await interaction.response.send_message(embed=embed)

    logger.info("Slash commands registered: language, worldline, worldline_history, dmail, ptv_next, ptv_status, ptv_subscribe, ptv_unsubscribe, ptv_subscriptions, lab_fortune, affinity, search, art_channel, set_broadcast, help")