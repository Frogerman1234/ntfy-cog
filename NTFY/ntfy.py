import asyncio
import re
import json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

import aiohttp
import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import error, warning, info

class NTFY(commands.Cog):
    """Send messages to NTFY endpoints with configurable settings."""

    def __init__(self, bot: Red):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5849321)
        self.session = aiohttp.ClientSession()
        self.last_sent: Dict[int, datetime] = {}  # {channel_id: last_sent_time}
        self.send_to_ntfy_task = None
        
        default_guild = {
            "ntfy_url": "",
            "headers": {},
            "auth_token": "",
            "rate_limit": 30,
            "allowed_bot": None,
            "enabled": True
        }
        
        self.config.register_guild(**default_guild)
        
    def cog_unload(self):
        """Cancel tasks and close session when cog unloads."""
        if self.send_to_ntfy_task:
            self.send_to_ntfy_task.cancel()
        asyncio.create_task(self.session.close())

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        pass

    @tasks.loop(seconds=5.0)
    async def send_to_ntfy(self, message: discord.Message):
        """Background task to send messages to NTFY."""
        if not message.guild:
            return
            
        guild = message.guild
        channel = message.channel
        
        # Check if cog is enabled for this guild
        if not await self.config.guild(guild).enabled():
            return
            
        # Check rate limit
        last_sent = self.last_sent.get(channel.id)
        rate_limit = await self.config.guild(guild).rate_limit()
        if last_sent and (datetime.now() - last_sent) < timedelta(seconds=rate_limit):
            return
            
        # Check if message is from a bot
        if message.author.bot:
            allowed_bot = await self.config.guild(guild).allowed_bot()
            if not allowed_bot or message.author.id != allowed_bot:
                return
                
        # Sanitize message
        clean_content = await self.sanitize_message(message.content)
        if not clean_content:
            return
            
        # Get config values
        ntfy_url = await self.config.guild(guild).ntfy_url()
        if not ntfy_url:
            return
            
        headers = await self.config.guild(guild).headers()
        auth_token = await self.config.guild(guild).auth_token()
        
        # Add auth token if exists
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
            
        try:
            async with self.session.post(
                ntfy_url,
                data=clean_content,
                headers=headers
            ) as response:
                if response.status >= 400:
                    self.bot.log.warning(
                        f"Failed to send message to NTFY: {response.status} - {await response.text()}"
                    )
                else:
                    self.last_sent[channel.id] = datetime.now()
        except Exception as e:
            self.bot.log.error(f"Error sending to NTFY: {str(e)}")

    @send_to_ntfy.before_loop
    async def before_send_to_ntfy(self):
        await self.bot.wait_until_ready()

    async def sanitize_message(self, content: str) -> Optional[str]:
        """Sanitize the message content to prevent malicious content."""
        if not content:
            return None
            
        # Remove any potential HTML tags
        clean_content = re.sub(r'<[^>]+>', '', content)
        
        # Remove excessive whitespace
        clean_content = ' '.join(clean_content.split())
        
        # Limit length to prevent abuse
        clean_content = clean_content[:2000]
        
        return clean_content if clean_content else None

    @commands.group()
    @commands.guild_only()
    @checks.admin_or_permissions(manage_guild=True)
    async def ntfy(self, ctx: commands.Context):
        """Configure NTFY settings."""
        pass

    @ntfy.command(name="url")
    async def ntfy_url(self, ctx: commands.Context, url: str):
        """Set the NTFY endpoint URL."""
        if not url.startswith("https://"):
            await ctx.send(error("URL must start with https://"))
            return
            
        await self.config.guild(ctx.guild).ntfy_url.set(url)
        await ctx.send(info(f"NTFY URL set to: {url}"))

    @ntfy.command(name="token")
    async def ntfy_token(self, ctx: commands.Context, token: str):
        """Set the authorization token for NTFY."""
        await self.config.guild(ctx.guild).auth_token.set(token)
        await ctx.send(info("Authorization token set."))

    @ntfy.command(name="headers")
    async def ntfy_headers(self, ctx: commands.Context, *, headers_json: str):
        """Set custom headers for NTFY requests in JSON format.
        
        Example: {"Title": "My Title", "Priority": "high"}
        """
        try:
            headers = json.loads(headers_json)
            if not isinstance(headers, dict):
                raise ValueError("Headers must be a dictionary")
                
            await self.config.guild(ctx.guild).headers.set(headers)
            await ctx.send(info("Headers updated."))
        except json.JSONDecodeError:
            await ctx.send(error("Invalid JSON format."))
        except ValueError as e:
            await ctx.send(error(str(e)))

    @ntfy.command(name="ratelimit")
    async def ntfy_ratelimit(self, ctx: commands.Context, seconds: int):
        """Set the minimum seconds between messages (minimum 30)."""
        if seconds < 30:
            await ctx.send(warning("Rate limit cannot be less than 30 seconds. Setting to 30."))
            seconds = 30
            
        await self.config.guild(ctx.guild).rate_limit.set(seconds)
        await ctx.send(info(f"Rate limit set to {seconds} seconds."))

    @ntfy.command(name="allowbot")
    async def ntfy_allowbot(self, ctx: commands.Context, bot: discord.User):
        """Set a specific bot that can trigger NTFY messages."""
        if not bot.bot:
            await ctx.send(error("The specified user is not a bot."))
            return
            
        await self.config.guild(ctx.guild).allowed_bot.set(bot.id)
        await ctx.send(info(f"Now listening to messages from bot: {bot.name}"))

    @ntfy.command(name="disablebot")
    async def ntfy_disablebot(self, ctx: commands.Context):
        """Stop listening to any bot messages."""
        await self.config.guild(ctx.guild).allowed_bot.set(None)
        await ctx.send(info("No longer listening to any bot messages."))

    @ntfy.command(name="toggle")
    async def ntfy_toggle(self, ctx: commands.Context):
        """Enable or disable NTFY functionality."""
        current = await self.config.guild(ctx.guild).enabled()
        await self.config.guild(ctx.guild).enabled.set(not current)
        status = "enabled" if not current else "disabled"
        await ctx.send(info(f"NTFY functionality is now {status}."))

    @ntfy.command(name="send")
    async def send_ntfy(self, ctx: commands.Context, *, message: str):
        """Send a message to the configured NTFY endpoint."""
        if not await self.config.guild(ctx.guild).enabled():
            await ctx.send(error("NTFY functionality is disabled for this server."))
            return
            
        # Check rate limit
        last_sent = self.last_sent.get(ctx.channel.id)
        rate_limit = await self.config.guild(ctx.guild).rate_limit()
        if last_sent and (datetime.now() - last_sent) < timedelta(seconds=rate_limit):
            remaining = rate_limit - (datetime.now() - last_sent).seconds
            await ctx.send(warning(f"Please wait {remaining} seconds before sending another message."))
            return
            
        # Sanitize message
        clean_content = await self.sanitize_message(message)
        if not clean_content:
            await ctx.send(error("Message content is invalid."))
            return
            
        # Get config values
        ntfy_url = await self.config.guild(ctx.guild).ntfy_url()
        if not ntfy_url:
            await ctx.send(error("NTFY URL is not configured."))
            return
            
        headers = await self.config.guild(ctx.guild).headers()
        auth_token = await self.config.guild(ctx.guild).auth_token()
        
        # Add auth token if exists
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
            
        try:
            async with self.session.post(
                ntfy_url,
                data=clean_content,
                headers=headers
            ) as response:
                if response.status >= 400:
                    await ctx.send(error(f"Failed to send message: {response.status}"))
                else:
                    self.last_sent[ctx.channel.id] = datetime.now()
                    await ctx.send(info("Message sent to NTFY successfully!"))
        except Exception as e:
            await ctx.send(error(f"Error sending message: {str(e)}"))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for messages and send to NTFY if conditions are met."""
        if message.guild is None:
            return
            
        if not await self.config.guild(message.guild).enabled():
            return
            
        if message.author.bot:
            allowed_bot = await self.config.guild(message.guild).allowed_bot()
            if not allowed_bot or message.author.id != allowed_bot:
                return
                
        # Don't process commands
        if await self.bot.get_context(message).valid:
            return
            
        await self.send_to_ntfy(message)

    async def cog_load(self):
        """Start the task when the cog loads."""
        self.send_to_ntfy_task = self.send_to_ntfy.start()
