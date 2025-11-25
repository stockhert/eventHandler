import discord
from discord import app_commands
from discord.ext import commands

from main import get_db_connection

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
# -------------------- /createoperation --------------------
@app_commands.command(name='createoperation', description='plan a new operation')
@app_commands.describe(opname='The name of the operation.', date='The date of the operation.', time='The time of the operation.', timezone='The timezone of the operation.')
async def dump(self, interaction: discord.Interaction, opname: str = None, date: str = None, time: str = None, timezone: str = 'CEST'):
    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("Only administrators can create events.", ephemeral=True)
        return
    try:
        connection = get_db_connection()
        with connection.cursor() as cursor:
            cursor.execute(f"CREATE TABLE {OpName} (event_name VARCHAR(255), event_date DATE, event_time TIME, attendingID VARCHAR(255))")

async def setup(bot):
    await bot.add_cog(Profile(bot))