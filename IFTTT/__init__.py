from .ifttt import IFTTT


async def setup(bot):
    """Package entry point required by Red."""
    await bot.add_cog(IFTTT(bot))
