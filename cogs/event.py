import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks

from main import get_db_connection


ATTENDANCE_STATUSES = {
    "yes": "Yes",
    "tentative": "Tentative",
    "no": "No",
}


def make_operation_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        slug = "operation"
    return f"op_{slug[:55]}"


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[a-z0-9_]{1,64}", identifier):
        raise ValueError("Unsafe SQL identifier.")
    return f"`{identifier}`"


class OperationAttendanceView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Yes",
        style=discord.ButtonStyle.success,
        custom_id="operation_attendance:yes",
    )
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_attendance_interaction(interaction, "yes")

    @discord.ui.button(
        label="Tentative",
        style=discord.ButtonStyle.secondary,
        custom_id="operation_attendance:tentative",
    )
    async def tentative(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_attendance_interaction(interaction, "tentative")

    @discord.ui.button(
        label="No",
        style=discord.ButtonStyle.danger,
        custom_id="operation_attendance:no",
    )
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.handle_attendance_interaction(interaction, "no")


class OperationEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.event_start_watcher.start()

    def cog_unload(self):
        self.event_start_watcher.cancel()

    async def cog_load(self):
        self.init_db()
        self.bot.add_view(OperationAttendanceView(self))

    def init_db(self):
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    CREATE TABLE IF NOT EXISTS operation_events
                    (
                        id              INT AUTO_INCREMENT,
                        operation_name  VARCHAR(64) UNIQUE NOT NULL,
                        table_name      VARCHAR(64) UNIQUE NOT NULL,
                        title           VARCHAR(256) NOT NULL,
                        description     TEXT NOT NULL,
                        thumbnail_url   VARCHAR(512),
                        guild_id        VARCHAR(32) NOT NULL,
                        post_channel_id VARCHAR(32) NOT NULL,
                        message_id      VARCHAR(32) UNIQUE,
                        author_id       VARCHAR(32) NOT NULL,
                        event_at_utc    DATETIME NOT NULL,
                        timezone_name   VARCHAR(64) NOT NULL,
                        thread_id       VARCHAR(32),
                        thread_created  TINYINT(1) NOT NULL DEFAULT 0,
                        created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                        PRIMARY KEY (id)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
                    """
                )
                self.migrate_operation_tables(cursor)
            connection.commit()
        finally:
            connection.close()

    def create_operation_table(self, cursor, table_name: str):
        quoted_table = quote_identifier(table_name)
        cursor.execute(
            f"""
            CREATE TABLE {quoted_table}
            (
                id           INT AUTO_INCREMENT,
                discord_id   VARCHAR(32) UNIQUE NOT NULL,
                display_name VARCHAR(100) NOT NULL,
                status       ENUM('yes', 'no', 'tentative') NOT NULL,
                updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )

    def remove_operation_id_column(self, cursor, table_name: str):
        quoted_table = quote_identifier(table_name)
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        if not cursor.fetchone():
            return
        cursor.execute(f"SHOW COLUMNS FROM {quoted_table} LIKE 'operation_id'")
        if cursor.fetchone():
            cursor.execute(f"ALTER TABLE {quoted_table} DROP COLUMN operation_id")

    def migrate_operation_tables(self, cursor):
        cursor.execute("SELECT table_name FROM operation_events")
        for event_row in cursor.fetchall():
            self.remove_operation_id_column(cursor, event_row["table_name"])

    def get_profile_display_name(self, cursor, discord_id: str, fallback_name: str) -> str:
        cursor.execute(
            "SELECT rank, first_name, surname FROM profiles WHERE discord_id = %s",
            (discord_id,),
        )
        profile = cursor.fetchone()
        if not profile:
            return fallback_name
        return f"{profile['rank']} {profile['first_name']} {profile['surname']}"

    def discord_timestamp(self, event_at_utc: datetime, style: str = "F") -> str:
        if event_at_utc.tzinfo is None:
            event_at_utc = event_at_utc.replace(tzinfo=timezone.utc)
        return f"<t:{int(event_at_utc.timestamp())}:{style}>"

    async def ask_dm(self, dm_channel: discord.DMChannel, author: discord.User, prompt: str) -> str | None:
        await dm_channel.send(prompt)

        def check(message: discord.Message):
            return message.author.id == author.id and message.channel.id == dm_channel.id

        try:
            message = await self.bot.wait_for("message", timeout=300.0, check=check)
        except TimeoutError:
            await dm_channel.send("Timed out waiting for a response. Run `/createoperation` again when ready.")
            return None

        content = message.content.strip()
        if content.lower() == "cancel":
            await dm_channel.send("Operation creation cancelled.")
            return None
        return content

    async def prompt_for_event_data(self, interaction: discord.Interaction) -> dict | None:
        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(
            "Operation creation started. Reply `cancel` at any prompt to stop. "
            "Each prompt times out after 5 minutes."
        )

        title = await self.ask_dm(dm_channel, interaction.user, "Title?")
        if title is None:
            return None

        description = await self.ask_dm(dm_channel, interaction.user, "Description?")
        if description is None:
            return None

        date_text = await self.ask_dm(dm_channel, interaction.user, "Date? Use `YYYY-MM-DD`, for example `2026-05-14`.")
        if date_text is None:
            return None

        time_text = await self.ask_dm(dm_channel, interaction.user, "Time? Use 24-hour `HH:MM`, for example `19:30`.")
        if time_text is None:
            return None

        timezone_name = await self.ask_dm(dm_channel, interaction.user, "Timezone? Use an IANA name like `Europe/Stockholm`.")
        if timezone_name is None:
            return None

        thumbnail_url = await self.ask_dm(dm_channel, interaction.user, "Image URL? Reply `none` to skip.")
        if thumbnail_url is None:
            return None
        if thumbnail_url.lower() == "none":
            thumbnail_url = None

        post_channel_text = await self.ask_dm(
            dm_channel,
            interaction.user,
            "Post channel? Send a channel mention like `#events` or the channel ID.",
        )
        if post_channel_text is None:
            return None

        try:
            event_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            event_time = datetime.strptime(time_text, "%H:%M").time()
            local_timezone = ZoneInfo(timezone_name)
            event_at_local = datetime.combine(event_date, event_time, tzinfo=local_timezone)
            event_at_utc = event_at_local.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            await dm_channel.send("Invalid date or time format. Run `/createoperation` again.")
            return None
        except ZoneInfoNotFoundError:
            await dm_channel.send("Unknown timezone. Use a valid IANA timezone like `Europe/Stockholm`.")
            return None

        channel_id_match = re.search(r"\d{15,25}", post_channel_text)
        if not channel_id_match:
            await dm_channel.send("Could not read a channel ID from that response.")
            return None

        channel_id = int(channel_id_match.group(0))
        channel = interaction.guild.get_channel(channel_id) if interaction.guild else None
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except discord.DiscordException:
                channel = None

        if not isinstance(channel, discord.TextChannel):
            await dm_channel.send("That channel must be a text channel in this server.")
            return None
        if channel.guild.id != interaction.guild.id:
            await dm_channel.send("That channel is not in this server.")
            return None

        return {
            "title": title[:256],
            "description": description,
            "thumbnail_url": thumbnail_url,
            "channel": channel,
            "event_at_utc": event_at_utc,
            "timezone_name": timezone_name,
            "display_time": event_at_local,
        }

    def build_embeds(self, event_row: dict, attendees: dict[str, list[str]]) -> list[discord.Embed]:
        event_at_utc = event_row["event_at_utc"]

        info_embed = discord.Embed(
            title=event_row["title"],
            description=event_row["description"],
            color=discord.Color.dark_teal(),
            timestamp=discord.utils.utcnow(),
        )
        info_embed.add_field(
            name="When",
            value=f"{self.discord_timestamp(event_at_utc, 'F')}\n{self.discord_timestamp(event_at_utc, 'R')}",
            inline=False,
        )
        info_embed.set_footer(text=f"Operation ID: {event_row['id']}")
        if event_row.get("thumbnail_url"):
            info_embed.set_image(url=event_row["thumbnail_url"])

        attendance_embed = discord.Embed(
            title="Attendance",
            color=discord.Color.dark_teal(),
        )
        attendance_embed.add_field(name="Yes", value=self.format_names(attendees["yes"]), inline=True)
        attendance_embed.add_field(name="No", value=self.format_names(attendees["no"]), inline=True)
        attendance_embed.add_field(name="Tentative", value=self.format_names(attendees["tentative"]), inline=True)
        attendance_embed.set_footer(text=f"Operation ID: {event_row['id']} | Use the buttons below to update your attendance.")
        return [info_embed, attendance_embed]

    def format_names(self, names: list[str]) -> str:
        if not names:
            return "None"
        text = "\n".join(names)
        if len(text) <= 1024:
            return text
        return text[:1020] + "..."

    def fetch_attendees(self, cursor, table_name: str) -> dict[str, list[str]]:
        quoted_table = quote_identifier(table_name)
        cursor.execute(
            f"""
            SELECT
                COALESCE(CONCAT(p.rank, ' ', p.first_name, ' ', p.surname), attendance.display_name) AS display_name,
                attendance.status
            FROM {quoted_table} AS attendance
            LEFT JOIN profiles AS p ON p.discord_id = attendance.discord_id
            ORDER BY display_name
            """
        )
        attendees = {"yes": [], "no": [], "tentative": []}
        for row in cursor.fetchall():
            attendees[row["status"]].append(row["display_name"])
        return attendees

    def fetch_attendance_counts(self, cursor, table_name: str) -> dict[str, int]:
        quoted_table = quote_identifier(table_name)
        counts = {"yes": 0, "no": 0, "tentative": 0}
        cursor.execute(f"SELECT status, COUNT(*) AS count FROM {quoted_table} GROUP BY status")
        for row in cursor.fetchall():
            counts[row["status"]] = row["count"]
        return counts

    def parse_event_update_datetime(
        self,
        event_row: dict,
        date_text: str | None,
        time_text: str | None,
        timezone_name: str | None,
    ) -> tuple[datetime, str]:
        target_timezone_name = timezone_name or event_row["timezone_name"]
        local_timezone = ZoneInfo(target_timezone_name)
        current_utc = event_row["event_at_utc"]
        if current_utc.tzinfo is None:
            current_utc = current_utc.replace(tzinfo=timezone.utc)
        current_local = current_utc.astimezone(local_timezone)

        event_date = datetime.strptime(date_text, "%Y-%m-%d").date() if date_text else current_local.date()
        event_time = datetime.strptime(time_text, "%H:%M").time() if time_text else current_local.time().replace(second=0, microsecond=0)
        event_at_local = datetime.combine(event_date, event_time, tzinfo=local_timezone)
        event_at_utc = event_at_local.astimezone(timezone.utc).replace(tzinfo=None)
        return event_at_utc, target_timezone_name

    def graph_bar(self, value: int, max_value: int, width: int = 18) -> str:
        if max_value <= 0:
            return "." * width
        filled = round((value / max_value) * width)
        return "#" * filled + "." * (width - filled)

    async def refresh_event_embed(self, event_row: dict):
        channel = self.bot.get_channel(int(event_row["post_channel_id"]))
        if channel is None:
            channel = await self.bot.fetch_channel(int(event_row["post_channel_id"]))

        message = await channel.fetch_message(int(event_row["message_id"]))
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                attendees = self.fetch_attendees(cursor, event_row["table_name"])
            await message.edit(embeds=self.build_embeds(event_row, attendees), view=OperationAttendanceView(self))
        finally:
            connection.close()

    async def handle_attendance_interaction(self, interaction: discord.Interaction, status: str):
        if not interaction.message:
            await interaction.response.send_message("Could not find the operation message.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM operation_events WHERE message_id = %s",
                    (str(interaction.message.id),),
                )
                event_row = cursor.fetchone()
                if not event_row:
                    await interaction.followup.send("This operation is not tracked anymore.", ephemeral=True)
                    return

                fallback_name = getattr(interaction.user, "display_name", interaction.user.name)
                display_name = self.get_profile_display_name(cursor, str(interaction.user.id), fallback_name)

                quoted_table = quote_identifier(event_row["table_name"])
                self.remove_operation_id_column(cursor, event_row["table_name"])
                cursor.execute(
                    f"""
                    INSERT INTO {quoted_table} (discord_id, display_name, status)
                    VALUES (%s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        display_name = VALUES(display_name),
                        status = VALUES(status)
                    """,
                    (str(interaction.user.id), display_name[:100], status),
                )
                connection.commit()
            await self.refresh_event_embed(event_row)
            await interaction.followup.send(
                f"Marked you as {ATTENDANCE_STATUSES[status].lower()}.",
                ephemeral=True,
            )
        finally:
            connection.close()

    @app_commands.command(name="createoperation", description="Create an operation event announcement")
    async def createoperation(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can create operations.", ephemeral=True)
            return

        try:
            await interaction.user.send("Starting operation setup...")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I cannot DM you. Enable DMs from this server and run `/createoperation` again.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message("Check your DMs to finish creating the operation.", ephemeral=True)
        event_data = await self.prompt_for_event_data(interaction)
        if event_data is None:
            return

        operation_name = make_operation_slug(event_data["title"])
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT id FROM operation_events WHERE operation_name = %s OR table_name = %s",
                    (operation_name, operation_name),
                )
                if cursor.fetchone():
                    await interaction.user.send(
                        f"An operation named `{operation_name}` already exists. Choose a different title."
                    )
                    return
                cursor.execute("SHOW TABLES LIKE %s", (operation_name,))
                if cursor.fetchone():
                    await interaction.user.send(
                        f"A table named `{operation_name}` already exists. Choose a different title."
                    )
                    return

                self.create_operation_table(cursor, operation_name)
                cursor.execute(
                    """
                    INSERT INTO operation_events
                        (operation_name, table_name, title, description, thumbnail_url, guild_id,
                         post_channel_id, author_id, event_at_utc, timezone_name)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        operation_name,
                        operation_name,
                        event_data["title"],
                        event_data["description"],
                        event_data["thumbnail_url"],
                        str(interaction.guild.id),
                        str(event_data["channel"].id),
                        str(interaction.user.id),
                        event_data["event_at_utc"],
                        event_data["timezone_name"],
                    ),
                )
                event_id = cursor.lastrowid
                cursor.execute("SELECT * FROM operation_events WHERE id = %s", (event_id,))
                event_row = cursor.fetchone()
                attendees = self.fetch_attendees(cursor, operation_name)
                connection.commit()

            message = await event_data["channel"].send(
                embeds=self.build_embeds(event_row, attendees),
                view=OperationAttendanceView(self),
            )

            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE operation_events SET message_id = %s WHERE id = %s",
                    (str(message.id), event_id),
                )
                connection.commit()

            await interaction.user.send(
                f"Operation posted in {event_data['channel'].mention}.\n"
                f"Table created: `{operation_name}`"
            )
        except Exception as e:
            print(f"[/createoperation]: Error: {e}")
            await interaction.user.send("An error occurred while creating the operation.")
        finally:
            connection.close()

    @app_commands.command(name="editevent", description="Edit an operation event by operation ID")
    @app_commands.describe(
        operation_id="The operation ID shown in the event footer.",
        title="New event title.",
        description="New event description.",
        date="New date as YYYY-MM-DD.",
        time="New time as HH:MM in the selected timezone.",
        timezone_name="New IANA timezone, for example Europe/Stockholm.",
        image_url="New image URL, or `none` to remove it.",
        post_channel="Move the event announcement to another text channel.",
    )
    async def editevent(
        self,
        interaction: discord.Interaction,
        operation_id: int,
        title: str | None = None,
        description: str | None = None,
        date: str | None = None,
        time: str | None = None,
        timezone_name: str | None = None,
        image_url: str | None = None,
        post_channel: discord.TextChannel | None = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can edit operations.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM operation_events WHERE id = %s AND guild_id = %s",
                    (operation_id, str(interaction.guild.id)),
                )
                event_row = cursor.fetchone()
                if not event_row:
                    await interaction.followup.send(f"No operation found with ID `{operation_id}`.", ephemeral=True)
                    return

                try:
                    event_at_utc, final_timezone = self.parse_event_update_datetime(
                        event_row,
                        date,
                        time,
                        timezone_name,
                    )
                except ValueError:
                    await interaction.followup.send("Invalid date or time. Use `YYYY-MM-DD` and `HH:MM`.", ephemeral=True)
                    return
                except ZoneInfoNotFoundError:
                    await interaction.followup.send("Unknown timezone. Use an IANA timezone like `Europe/Stockholm`.", ephemeral=True)
                    return

                final_image = event_row["thumbnail_url"]
                if image_url is not None:
                    final_image = None if image_url.lower() == "none" else image_url

                final_channel = post_channel or interaction.guild.get_channel(int(event_row["post_channel_id"]))
                if not isinstance(final_channel, discord.TextChannel):
                    await interaction.followup.send("The post channel must be a text channel in this server.", ephemeral=True)
                    return

                event_time_changed = event_at_utc != event_row["event_at_utc"]
                cursor.execute(
                    """
                    UPDATE operation_events
                    SET title = %s,
                        description = %s,
                        thumbnail_url = %s,
                        post_channel_id = %s,
                        event_at_utc = %s,
                        timezone_name = %s,
                        thread_created = %s
                    WHERE id = %s
                    """,
                    (
                        (title or event_row["title"])[:256],
                        description if description is not None else event_row["description"],
                        final_image,
                        str(final_channel.id),
                        event_at_utc,
                        final_timezone,
                        0 if event_time_changed else event_row["thread_created"],
                        operation_id,
                    ),
                )
                connection.commit()

                cursor.execute("SELECT * FROM operation_events WHERE id = %s", (operation_id,))
                updated_event = cursor.fetchone()
                attendees = self.fetch_attendees(cursor, updated_event["table_name"])

            old_channel = interaction.guild.get_channel(int(event_row["post_channel_id"]))
            moved_channel = final_channel.id != int(event_row["post_channel_id"])
            if moved_channel:
                new_message = await final_channel.send(
                    embeds=self.build_embeds(updated_event, attendees),
                    view=OperationAttendanceView(self),
                )
                if old_channel:
                    try:
                        old_message = await old_channel.fetch_message(int(event_row["message_id"]))
                        await old_message.delete()
                    except discord.DiscordException:
                        pass
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE operation_events SET message_id = %s WHERE id = %s",
                        (str(new_message.id), operation_id),
                    )
                    connection.commit()
            else:
                await self.refresh_event_embed(updated_event)

            await interaction.followup.send(f"Operation `{operation_id}` updated.", ephemeral=True)
        except Exception as e:
            print(f"[/editevent]: Error: {e}")
            await interaction.followup.send("Error editing operation.", ephemeral=True)
        finally:
            connection.close()

    @app_commands.command(name="deleteevent", description="Delete an operation event by operation ID")
    @app_commands.describe(operation_id="The operation ID shown in the event footer.")
    async def deleteevent(self, interaction: discord.Interaction, operation_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can delete operations.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM operation_events WHERE id = %s AND guild_id = %s",
                    (operation_id, str(interaction.guild.id)),
                )
                event_row = cursor.fetchone()
                if not event_row:
                    await interaction.followup.send(f"No operation found with ID `{operation_id}`.", ephemeral=True)
                    return

            channel = interaction.guild.get_channel(int(event_row["post_channel_id"]))
            if channel and event_row.get("message_id"):
                try:
                    message = await channel.fetch_message(int(event_row["message_id"]))
                    await message.delete()
                except discord.DiscordException:
                    pass

            if event_row.get("thread_id"):
                thread = self.bot.get_channel(int(event_row["thread_id"]))
                if thread:
                    try:
                        await thread.delete()
                    except discord.DiscordException:
                        pass

            with connection.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {quote_identifier(event_row['table_name'])}")
                cursor.execute("DELETE FROM operation_events WHERE id = %s", (operation_id,))
                connection.commit()

            await interaction.followup.send(f"Operation `{operation_id}` deleted.", ephemeral=True)
        except Exception as e:
            print(f"[/deleteevent]: Error: {e}")
            await interaction.followup.send("Error deleting operation.", ephemeral=True)
        finally:
            connection.close()

    @app_commands.command(name="listoperations", description="List all planned and past operation events")
    async def listoperations(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can list operations.", ephemeral=True)
            return

        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM operation_events
                    WHERE guild_id = %s
                    ORDER BY event_at_utc ASC
                    """,
                    (str(interaction.guild.id),),
                )
                events = cursor.fetchall()

                if not events:
                    await interaction.response.send_message("No operations found.", ephemeral=True)
                    return

                lines = []
                now_utc = datetime.utcnow()
                for index, event_row in enumerate(events, start=1):
                    counts = self.fetch_attendance_counts(cursor, event_row["table_name"])
                    event_at_utc = event_row["event_at_utc"]
                    state = "Past" if event_at_utc <= now_utc else "Planned"
                    lines.append(
                        f"{index}. `#{event_row['id']}` **{state}** | {self.discord_timestamp(event_at_utc, 'f')} "
                        f"({self.discord_timestamp(event_at_utc, 'R')}) | **{event_row['title']}** | "
                        f"Yes: {counts['yes']} | Tentative: {counts['tentative']} | No: {counts['no']}"
                    )

            pages = []
            current_page = []
            current_length = 0
            for line in lines:
                separator_length = 1 if current_page else 0
                next_length = current_length + separator_length + len(line)
                if current_page and next_length > 3900:
                    pages.append("\n".join(current_page))
                    current_page = [line]
                    current_length = len(line)
                else:
                    current_page.append(line)
                    current_length = next_length
            if current_page:
                pages.append("\n".join(current_page))

            embeds = []
            for page_number, page in enumerate(pages, start=1):
                embed = discord.Embed(
                    title="Operations",
                    description=page,
                    color=discord.Color.dark_teal(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_footer(text=f"Page {page_number}/{len(pages)} | Total operations: {len(events)}")
                embeds.append(embed)

            await interaction.response.send_message(embed=embeds[0], ephemeral=True)
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[/listoperations]: Error: {e}")
            await interaction.response.send_message("Error listing operations.", ephemeral=True)
        finally:
            connection.close()

    @app_commands.command(name="operationgraph", description="Show planned and past operations as an attendee graph")
    async def operationgraph(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command must be used in a server.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("Only administrators can view the operation graph.", ephemeral=True)
            return

        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM operation_events
                    WHERE guild_id = %s
                    ORDER BY event_at_utc ASC
                    """,
                    (str(interaction.guild.id),),
                )
                events = cursor.fetchall()

                if not events:
                    await interaction.response.send_message("No operations found.", ephemeral=True)
                    return

                event_counts = []
                max_attendees = 0
                for event_row in events:
                    counts = self.fetch_attendance_counts(cursor, event_row["table_name"])
                    max_attendees = max(max_attendees, counts["yes"])
                    event_counts.append((event_row, counts))

            now_utc = datetime.utcnow()
            lines = []
            for event_row, counts in event_counts:
                state = "Past" if event_row["event_at_utc"] <= now_utc else "Planned"
                bar = self.graph_bar(counts["yes"], max_attendees)
                lines.append(
                    f"`#{event_row['id']:>3}` {state:<7} `{bar}` "
                    f"{counts['yes']:>2} attending | {self.discord_timestamp(event_row['event_at_utc'], 'd')} | "
                    f"{event_row['title']}"
                )

            pages = []
            current_page = []
            current_length = 0
            for line in lines:
                separator_length = 1 if current_page else 0
                next_length = current_length + separator_length + len(line)
                if current_page and next_length > 3900:
                    pages.append("\n".join(current_page))
                    current_page = [line]
                    current_length = len(line)
                else:
                    current_page.append(line)
                    current_length = next_length
            if current_page:
                pages.append("\n".join(current_page))

            embeds = []
            for page_number, page in enumerate(pages, start=1):
                embed = discord.Embed(
                    title="Operation Attendance Graph",
                    description=page,
                    color=discord.Color.dark_teal(),
                    timestamp=discord.utils.utcnow(),
                )
                embed.set_footer(text=f"Page {page_number}/{len(pages)} | Bar shows yes/attending count")
                embeds.append(embed)

            await interaction.response.send_message(embed=embeds[0], ephemeral=True)
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            print(f"[/operationgraph]: Error: {e}")
            await interaction.response.send_message("Error building operation graph.", ephemeral=True)
        finally:
            connection.close()

    @tasks.loop(minutes=1)
    async def event_start_watcher(self):
        now_utc = datetime.utcnow()
        connection = get_db_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM operation_events
                    WHERE thread_created = 0
                      AND message_id IS NOT NULL
                      AND event_at_utc <= %s
                    """,
                    (now_utc,),
                )
                due_events = cursor.fetchall()

            for event_row in due_events:
                await self.create_event_thread(event_row)
        finally:
            connection.close()

    @event_start_watcher.before_loop
    async def before_event_start_watcher(self):
        await self.bot.wait_until_ready()

    async def create_event_thread(self, event_row: dict):
        connection = get_db_connection()
        try:
            channel = self.bot.get_channel(int(event_row["post_channel_id"]))
            if channel is None:
                channel = await self.bot.fetch_channel(int(event_row["post_channel_id"]))
            message = await channel.fetch_message(int(event_row["message_id"]))

            mentions = []
            quoted_table = quote_identifier(event_row["table_name"])
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT discord_id FROM {quoted_table} WHERE status = 'yes'")
                for row in cursor.fetchall():
                    mentions.append(f"<@{row['discord_id']}>")

            thread = await message.create_thread(name=event_row["title"][:100])
            content = "Operation is starting."
            if mentions:
                content = f"{content}\n" + " ".join(mentions)
            await thread.send(content)

            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE operation_events
                    SET thread_created = 1, thread_id = %s
                    WHERE id = %s
                    """,
                    (str(thread.id), event_row["id"]),
                )
                connection.commit()

            await self.refresh_event_embed(event_row | {"thread_id": str(thread.id), "thread_created": 1})
        except Exception as e:
            print(f"[event_start_watcher]: Error creating thread for {event_row.get('title')}: {e}")
        finally:
            connection.close()


async def setup(bot):
    await bot.add_cog(OperationEvent(bot))
