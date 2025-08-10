# ifttt.py
#
# Red-DiscordBot cog that forwards Discord messages to an IFTTT Webhooks
# endpoint, formatted as:
#   {
#       "value1": "<server-name>",
#       "value2": "<author-name>",
#       "value3": "<message>"
#   }
#
# Commands (prefix [p]):
#   ifttt url <webhook>
#   ifttt ratelimit <seconds>
#   ifttt allowbot @bot
#   ifttt disablebot
#   ifttt toggle
#   ifttt send <message>
#
# ---------------------------------------------------------------------------

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Dict, Optional

import aiohttp
import discord
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import error, info, warning


class IFTTT(commands.Cog):
    """Send Discord messages to an IFTTT Webhooks URL."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        self.last_sent: Dict[int, datetime] = {}  # {channel_id: last_sent_time}

        # Per-guild configuration
        self.config = Config.get_conf(self, identifier=5849321)
        self.config.register_guild(
            ifttt_url="",
            rate_limit=30,       # seconds
            allowed_bot=None,    # optional bot id
            enabled=True,
        )

    # ------------------------------------------------------------------ #
    # Helpers                                                             #
    # ------------------------------------------------------------------ #
    async def _sanitise(self, content: str) -> Optional[str]:
        """Remove HTML tags/extra spaces and cap at 2 000 chars."""
        if not content:
            return None
        cleaned = re.sub(r"<[^>]+>", "", content)
        cleaned = " ".join(cleaned.split())
        cleaned = cleaned[:2000]
        return cleaned or None

    async def _post_to_ifttt(
        self,
        guild: discord.Guild,
        author_name: str,
        channel_id: int,
        content: str,
    ):
        """Send JSON payload to the guild’s configured IFTTT URL."""
        rate_limit = await self.config.guild(guild).rate_limit()
        last_sent = self.last_sent.get(channel_id)
        if last_sent and (datetime.now() - last_sent) < timedelta(seconds=rate_limit):
            return

        url = await self.config.guild(guild).ifttt_url()
        if not url:
            return

        payload = {
            "value1": guild.name,
            "value2": author_name,
            "value3": content,
        }

        try:
            async with self.session.post(url, json=payload, timeout=15) as resp:
                if resp.status >= 400:
                    self.bot.log.warning(
                        "IFTTT POST failed (%s): %s", resp.status, await resp.text()
                    )
                else:
                    self.last_sent[channel_id] = datetime.now()
        except Exception as exc:  # noqa: BLE001
            self.bot.log.exception("Error posting to IFTTT: %s", exc)

    # ------------------------------------------------------------------ #
    # Commands                                                            #
    # ------------------------------------------------------------------ #
    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def ifttt(self, ctx: commands.Context):
        """IFTTT configuration commands."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help()

    @ifttt.command(name="url")
    async def _cmd_url(self, ctx: commands.Context, url: str):
        """Set the IFTTT Webhooks URL."""
        if not url.startswith("https://"):
            await ctx.send(error("URL must start with https://"))
            return
        await self.config.guild(ctx.guild).ifttt_url.set(url)
        await ctx.send(info(f"IFTTT URL set to:\n{url}"))

    @ifttt.command(name="ratelimit")
    async def _cmd_ratelimit(self, ctx: commands.Context, seconds: int):
        """Set minimum seconds between posts (≥ 30)."""
        if seconds < 30:
            await ctx.send(
                warning("Rate limit cannot be less than 30 seconds. Using 30.")
            )
            seconds = 30
        await self.config.guild(ctx.guild).rate_limit.set(seconds)
        await ctx.send(info(f"Rate limit set to {seconds} seconds."))

    @ifttt.command(name="allowbot")
    async def _cmd_allowbot(self, ctx: commands.Context, bot: discord.User):
        """Only forward messages from a specific bot."""
        if not bot.bot:
            await ctx.send(error("That user is not a bot."))
            return
        await self.config.guild(ctx.guild).allowed_bot.set(bot.id)
        await ctx.send(info(f"Now listening only to messages from **{bot.name}**."))

    @ifttt.command(name="disablebot")
    async def _cmd_disablebot(self, ctx: commands.Context):
        """Stop filtering by bot."""
        await self.config.guild(ctx.guild).allowed_bot.set(None)
        await ctx.send(info("Bot filter disabled."))

    @ifttt.command(name="toggle")
    async def _cmd_toggle(self, ctx: commands.Context):
        """Enable/disable IFTTT forwarding in this guild."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        state = "enabled" if not current else "disabled"
        await ctx.send(info(f"IFTTT forwarding is now {state}."))

    @ifttt.command(name="send")
    async def _cmd_send(self, ctx: commands.Context, *, message: str):
        """Manually send a message to the IFTTT webhook."""
        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send(error("IFTTT forwarding is disabled in this guild."))
            return

        clean = await self._sanitise(message)
        if not clean:
            await ctx.send(error("Message content is invalid."))
            return

        await self._post_to_ifttt(
            guild=ctx.guild,
            author_name=ctx.author.display_name,
            channel_id=ctx.channel.id,
            content=clean,
        )

        if ctx.channel.id in self.last_sent:
            await ctx.send(info("Message sent to IFTTT successfully!"))
        else:
            await ctx.send(
                warning(
                    "Message was not sent. Either rate-limited or the IFTTT URL "
                    "is incorrect. Check your logs."
                )
            )

    # ------------------------------------------------------------------ #
    # Listener                                                            #
    # ------------------------------------------------------------------ #
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Automatically forward qualifying non-command messages."""
        if message.guild is None:
            return

        if not await self.config.guild(message.guild).enabled():
            return

        if message.author.bot:
            allowed_bot = await self.config.guild(message.guild).allowed_bot()
            if not allowed_bot or message.author.id != allowed_bot:
                return

        if await self.bot.get_context(message).valid:
            return

        clean = await self._sanitise(message.content)
        if not clean:
            return

        await self._post_to_ifttt(
            guild=message.guild,
            author_name=message.author.display_name,
            channel_id=message.channel.id,
            content=clean,
        )

    # ------------------------------------------------------------------ #
    # Lifecycle                                                           #
    # ------------------------------------------------------------------ #
    def cog_unload(self):
        """Close aiohttp session when cog is unloaded."""
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, **_):
        """No personal user data stored."""
        return


async def setup(bot: Red):
    """Red entry-point."""
    await bot.add_cog(IFTTT(bot))
