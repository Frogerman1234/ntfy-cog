from .ntfy import NTFY

async def setup(bot):
    """Add the cog to the bot."""
    cog = NTFY(bot)
    await bot.add_cog(cog)
