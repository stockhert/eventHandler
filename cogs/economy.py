import discord
from discord import app_commands
from discord.ext import commands

from main import get_db_connection



async def setup(bot):
    await bot.add_cog(Profile(bot))