# -------------------- Imports --------------------
import os
import pymysql
from dotenv import load_dotenv

import discord
from discord import app_commands # '/' commands
from discord.ext import commands # basic commands

# -------------------- Load Token --------------------
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN') # Load token from .env
if not TOKEN:
    raise ValueError("No token found in .env")

# -------------------- Intents --------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="jarvis,", intents=intents)

# -------------------- Database --------------------
def get_db_connection(): #used literally everywhere dont edelete ts
    try:
        connection = pymysql.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            cursorclass=pymysql.cursors.DictCursor
        )
        return connection
    except Exception as e:
        raise

# INNIT !!!!
def init_db():
    connection = get_db_connection()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS profiles
            (
                id              INT AUTO_INCREMENT,
                discord_id      VARCHAR(255) UNIQUE,
                rank            VARCHAR(12)  NOT NULL default 'Rct.',
                first_name   VARCHAR(25)   NOT NULL,
                surname         VARCHAR(25)  NOT NULL,
                level           INT          NOT NULL DEFAULT 1,
                funds           INT          NOT NULL DEFAULT 0,
                is_nco           TINYINT(1)      NOT NULL DEFAULT 0,
                is_officer       TINYINT(1)      NOT NULL DEFAULT 0,
            
                created_at      TIMESTAMP             DEFAULT CURRENT_TIMESTAMP,
                updated_at      TIMESTAMP             DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            
                PRIMARY KEY (id),
                CHECK (rank IN ('Rct.', 'Pvt.', 'L/Cpl.', 'Cpl.', 'Sgt.', 'S/Sgt.', '2 Lt.', 'Lt.', 'Capt.', 'Maj.'))
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    connection.commit()

# -------------------- Startup Event --------------------
@bot.event
async def on_ready():
    print(f'[on_ready]: {bot.user.name} trying to connect to discord & database...')
    try:
        init_db()
        sync = await bot.tree.sync()
        print(f"[on_ready]: synced {len(sync)} command(s)")
        print(f"[on_ready]: {bot.user.name} is ready")
    except Exception as e:
        print(f"[on_ready]: Error: {e}")
        await bot.close()

# -------------------- Main Async Entry Point (COG's) --------------------
async def main():
    async with bot:

        await bot.load_extension('cogs.profiles') # dont forget to add cogs here

        await bot.start(TOKEN)

if __name__ == '__main__':
    import asyncio
    asyncio.run(main())

