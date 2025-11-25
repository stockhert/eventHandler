import discord
from discord import app_commands
from discord.ext import commands

from main import get_db_connection

# -------------------- Button Confirmation (Delete Profile) --------------------
class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id: str):
        super().__init__(timeout=30.0)
        self.user_id = user_id
        self.value = None

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM profiles WHERE discord_id = %s", (self.user_id,))
                connection.commit()
            connection.close()

            self.value = True
            await interaction.response.edit_message(
                content="✅ Profile deleted successfully!",
                view=None
            )
            self.stop()
        except Exception as e:
            print(f"[confirm]: Error deleting profile: {e}")
            self.value = False
            await interaction.response.edit_message(
                content="❌ An error occurred while deleting the profile.",
                view=None
            )
            self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.edit_message(
            content="❌ Profile deletion cancelled.",
            view=None
        )
        self.stop()

    # TIMEOUT CATCH
    async def on_timeout(self):
        self.stop()


# -------------------- Cog Class --------------------
class Profile(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # -------------------- /createprofile --------------------
    @app_commands.command(name='createprofile', description='Create a new character profile')
    @app_commands.describe(
        first_name='The first name of your character.',
        surname='The surname of your character.'
    )
    async def createprofile(self, interaction: discord.Interaction, first_name: str, surname: str):
        user_id = str(interaction.user.id)  # DISCORD UUID
        first_name = first_name.capitalize()
        surname = surname.capitalize()

        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM profiles WHERE discord_id = %s",(user_id,))
                existing_profile = cursor.fetchone()

                if existing_profile:
                    await interaction.response.send_message(
                        f"❌ You already have a profile! Use `/deleteprofile` to delete it.",
                        ephemeral=True
                    )
                    return

                cursor.execute(
                    """
                    INSERT INTO profiles (discord_id, first_name, surname)
                    VALUES (%s, %s, %s)
                    """,
                    (user_id, first_name, surname)
                )
                connection.commit()

                # response message
                cursor.execute(
                    "SELECT rank, first_name, surname FROM profiles WHERE discord_id = %s",
                    (user_id,)
                )
                profile = cursor.fetchone()

                await interaction.response.send_message(
                    f"Profile created successfully!\n{profile['rank']} {profile['first_name'].strip()[0].upper()}. {profile['surname']}",
                    ephemeral=True
                )

        except Exception as e:
            print(f"[/createprofile]: Error creating profile: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while creating your profile. Please try again later.",
                ephemeral=True
            )
        finally:
            if connection:
                connection.close()

    # -------------------- /deleteprofile --------------------
    @app_commands.command(name='deleteprofile', description='Delete your character profile')
    @app_commands.describe(uuid='The UUID of the character profile (Admin Only)')
    async def deleteprofile(self, interaction: discord.Interaction, uuid: str = None):
        user_id = str(interaction.user.id)
        target_user_id = user_id
        if uuid:
            if not interaction.user.guild_permissions.administrator:
                await interaction.response.send_message(
                    "❌ Only administrators can delete other users' profiles.",
                    ephemeral=True
                )
                return
            target_user_id = uuid

        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM profiles WHERE discord_id = %s", (target_user_id,))
                existing_profile = cursor.fetchone()

                if not existing_profile:
                    await interaction.response.send_message(
                        "❌ No profile found to delete.",
                        ephemeral=True
                    )
                    connection.close()
                    return

                view = ConfirmDeleteView(target_user_id)
                if uuid:
                    message = f"⚠️ Are you sure you want to delete the profile for user `{target_user_id}`?\n" \
                              f"**{existing_profile['rank']} {existing_profile['first_name']}. {existing_profile['surname']}**"
                else:
                    message = f"⚠️ Are you sure you want to delete your profile?\n" \
                              f"**{existing_profile['rank']} {existing_profile['first_name']}. {existing_profile['surname']}**"

                await interaction.response.send_message(
                    message,
                    view=view,
                    ephemeral=True
                )

        except Exception as e:
            print(f"[deleteprofile]: Error: {e}")
            await interaction.response.send_message(
                "❌ An error occurred while processing your request.",
                ephemeral=True
            )
        finally:
            if connection:
                connection.close()

    # -------------------- /showprofiles --------------------
    @app_commands.command(name='showprofiles', description='Show all character profiles')
    async def showprofiles(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can view all profiles..", ephemeral=True)
            return

        try:
            connection = get_db_connection()
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM profiles")
                profiles = cursor.fetchall()

            if not profiles:
                await interaction.response.send_message("No profiles found.", ephemeral=True)
                return

            # beuatuiful embed format
            embed = discord.Embed(
                title="All Character Profiles",
                color=discord.Color.blue()
            )

            # profile line format
            profile_lines = []
            for profile in profiles:
                nco_status = "Yeah" if profile['is_nco'] else "Nah"
                officer_status = "Yeah" if profile['is_officer'] else "Nah"

                profile_line = (
                    f"**ID {profile['id']}:** {profile['rank']} {profile['first_name']} {profile['surname']}\n"
                    f"└ NCO: {nco_status} | Officer: {officer_status} | Discord: `{profile['discord_id']}`"
                )
                profile_lines.append(profile_line)

            # join all profiles with line breaks
            embed.description = "\n\n".join(profile_lines)

            embed.set_footer(text=f"Total profiles: {len(profiles)} | Requested by {interaction.user.name}")
            embed.timestamp = discord.utils.utcnow()

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            print(f"[/showprofiles]: Error: {e}")
            await interaction.response.send_message("Error fetching profiles.", ephemeral=True)
        finally:
            if connection:
                connection.close()

    # -------------------- /profile --------------------
    @app_commands.command(name='profile', description='Shows character profile')
    async def profile(self, interaction: discord.Interaction, uuid: str = None):
        try:
            connection = get_db_connection()
            if uuid:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM profiles WHERE discord_id = %s", (uuid,))
                    profile = cursor.fetchone()
            else:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT * FROM profiles WHERE discord_id = %s", (interaction.user.id,))
                    profile = cursor.fetchone()

            # error: no profile in sql
            if not profile:
                await interaction.response.send_message("No profile found!", ephemeral=True)
                return

            embed = discord.Embed(
                title=f"{profile['rank']} {profile['first_name']}. {profile['surname']}",
                color=discord.Color.dark_teal()
            )

            embed.add_field(name="Level", value=profile['level'], inline=False)
            embed.add_field(name="Funds", value=f"${profile['funds']:,.2f}", inline=False)

            if profile['is_nco']:
                embed.add_field(name="NCO", value="Yes", inline=False)
            if profile['is_officer']:
                embed.add_field(name="Officer", value="Yes", inline=False)

            embed.timestamp = discord.utils.utcnow()
            embed.set_footer(text=f"Requested by {interaction.user.name}")

            await interaction.response.send_message(embed=embed)

        except Exception as e:
            print(f"[/profile]: Error: {e}")
            await interaction.response.send_message("Error fetching profile.", ephemeral=True)
        finally:
            if connection:
                connection.close()

    # -------------------- /dump --------------------
    @app_commands.command(name='dump', description='Dumps SQL database as file')
    async def dump(self, interaction: discord.Interaction):
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can dump profiles.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        filename = None
        try:
            import os
            import subprocess
            from datetime import datetime

            timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"profiles_{timestamp}.sql"

            # .env variables, change in .env file. defaults contained just in case
            db_host = os.getenv('DB_HOST', 'localhost')
            db_user = os.getenv('DB_USER', 'root')
            db_password = os.getenv('DB_PASSWORD', '')
            db_name = os.getenv('DB_NAME', 'profiles')

            path = os.getenv('mysqldumpPath') # might wanna change sometime .........
            command = [
                path,
                "-h", db_host,
                "-u", db_user,
                f"--password={db_password}",
                db_name,
            ]

            with open(filename, 'w') as f:
                result = subprocess.run(command, stdout=f, stderr=subprocess.PIPE, text=True)

            if result.returncode != 0:
                raise Exception(f"mysqldump failed:\n{result.stderr}")

            file = discord.File(filename, filename=filename)
            await interaction.followup.send(
                f"Database backed up successfully.\nFile: `{filename}`",
                file=file,
                ephemeral=True
            )

        except FileNotFoundError:
            print(f"[/dump]: Error: mysqldump not found: {e}")
            await interaction.followup.send(
                "Error: `mysqldump` command not found. (install MySQL client tools!!)",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(f"Error during dump: {e}", ephemeral=True)
        finally:

            if filename and os.path.exists(filename):
                os.remove(filename)



async def setup(bot):
    await bot.add_cog(Profile(bot))