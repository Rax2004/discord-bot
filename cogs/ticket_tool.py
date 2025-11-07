import os
import html
import asyncio
import traceback
import sys
from datetime import datetime

import discord
from discord.ext import commands

# ==================== CONFIGURATION ====================
TICKET_CHANNEL_ID = 1435559140599533610    # Channel where "Create Ticket" embed is shown
ACTIVE_CATEGORY_ID = 1435565165025427516   # Category where active tickets are created
LOG_CHANNEL_ID = 1286008582151602218       # Channel where logs/transcripts go
STAFF_ROLE_ID = 1101039305390043187        # Staff role that can view/close tickets
GUILD_ID = os.getenv("GUILD_ID")            # Your guild ID (string in .env)
GANG_ROLE_PREFIX = "Gang-"                  # Prefix used to detect gang roles

# ==================== MONGODB SETUP ====================
try:
    import motor.motor_asyncio as motor_asyncio
except ImportError:
    motor_asyncio = None
    print("[WARN] motor not installed. Run: pip install motor", file=sys.stderr)

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB", "tickets_db")
MONGO_COLLECTION_NAME = os.getenv("MONGO_COLLECTION", "transcripts")

mongo_client = None
mongo_db = None
if motor_asyncio and MONGO_URI:
    try:
        mongo_client = motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        mongo_db = mongo_client[MONGO_DB_NAME]
        print("[INFO] MongoDB connected successfully.")
    except Exception as e:
        print("[ERROR] MongoDB connection failed:", e, file=sys.stderr)
else:
    print("[WARN] MongoDB not configured or motor missing. Transcripts won’t be saved.")


# ==================== SEQUENTIAL TICKET NUMBER ====================
async def get_next_ticket_number() -> int:
    """Get the next sequential ticket number from MongoDB or local fallback."""
    if mongo_db is None:
        counter_file = "ticket_counter.txt"
        if os.path.exists(counter_file):
            with open(counter_file, "r") as f:
                count = int(f.read().strip())
        else:
            count = 0
        count += 1
        with open(counter_file, "w") as f:
            f.write(str(count))
        return count

    counters = mongo_db["ticket_counters"]
    doc = await counters.find_one_and_update(
        {"_id": "ticket_number"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return doc["seq"]


# ==================== TRANSCRIPT GENERATOR ====================
async def create_transcript(channel: discord.TextChannel) -> str:
    """Generate an HTML transcript of all messages in a channel."""
    messages = [msg async for msg in channel.history(limit=None, oldest_first=True)]
    html_content = f"""<!doctype html>
<html><head><meta charset='utf-8'>
<title>Transcript - {html.escape(channel.name)}</title>
<style>
body {{background:#2f3136;color:#dcddde;font-family:Arial,sans-serif}}
.msg{{margin:8px;padding:8px;border-bottom:1px solid #444}}
.author{{color:#7289da;font-weight:bold}}
.time{{color:#999;font-size:0.85em;margin-left:6px}}
.content{{margin-top:4px;white-space:pre-wrap}}
.embed{{margin:8px 0;padding:8px;border-left:4px solid #5865f2;background:#2c2f33}}
.embed-title{{color:#00b0f4;font-weight:bold}}
img{{max-width:400px;border-radius:4px;margin-top:4px}}
</style></head><body>
<h2>Transcript for #{html.escape(channel.name)}</h2>
<p>Server: {html.escape(channel.guild.name)}<br>
Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}</p><hr>
"""
    for msg in messages:
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = html.escape(str(msg.author))
        html_content += f'<div class="msg"><span class="author">{author}</span><span class="time">[{timestamp}]</span>'
        if msg.content:
            html_content += f'<div class="content">{html.escape(msg.content)}</div>'
        for embed in msg.embeds:
            html_content += '<div class="embed">'
            if embed.title:
                html_content += f'<div class="embed-title">{html.escape(embed.title)}</div>'
            if embed.description:
                html_content += f'<div class="content">{html.escape(embed.description)}</div>'
            for field in embed.fields:
                html_content += f'<div><b>{html.escape(field.name)}:</b> {html.escape(field.value)}</div>'
            html_content += '</div>'
        for attachment in msg.attachments:
            fname = html.escape(attachment.filename)
            if fname.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                html_content += f'<img src="{attachment.url}" alt="{fname}">'
            else:
                html_content += f'<a href="{attachment.url}">{fname}</a>'
        html_content += "</div>\n"
    html_content += "</body></html>"
    file_path = f"transcript_{channel.id}_{int(datetime.utcnow().timestamp())}.html"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return file_path


# ==================== SAVE TRANSCRIPT TO MONGO ====================
async def save_transcript_to_mongo(channel: discord.TextChannel, transcript_html: str, closed_by: discord.Member):
    """Save transcript HTML + metadata to MongoDB."""
    if mongo_db is None:
        print("[WARN] MongoDB not initialized. Skipping transcript save.")
        return
    doc = {
        "guild_id": channel.guild.id,
        "guild_name": channel.guild.name,
        "channel_id": channel.id,
        "channel_name": channel.name,
        "closed_by_name": str(closed_by) if closed_by else None,
        "created_at": datetime.utcnow(),
        "transcript_html": transcript_html,
    }
    try:
        await mongo_db[MONGO_COLLECTION_NAME].insert_one(doc)
        print(f"[MongoDB] Transcript saved for {channel.name}")
    except Exception:
        traceback.print_exc(file=sys.stderr)


# ==================== CLOSE TICKET VIEW ====================
class CloseTicketView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, bot: discord.Client):
        super().__init__(timeout=None)
        self.channel = channel
        self.bot = bot

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, _):
        creator_id = getattr(self.channel, "topic", None)
        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if str(interaction.user.id) != creator_id and (not staff_role or staff_role not in interaction.user.roles):
            await interaction.response.send_message("You don’t have permission to close this ticket.", ephemeral=True)
            return
        await interaction.response.send_message("Confirm closing this ticket?", view=ConfirmCloseView(self.channel, self.bot), ephemeral=True)


class ConfirmCloseView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, bot: discord.Client):
        super().__init__(timeout=30)
        self.channel = channel
        self.bot = bot

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        closed_by = interaction.user
        try:
            transcript_file = await create_transcript(self.channel)
            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                with open(transcript_file, "rb") as f:
                    file = discord.File(f, filename=os.path.basename(transcript_file))
                    embed = discord.Embed(title="Ticket Closed", description=f"{self.channel.name} closed by **{closed_by}**", color=discord.Color.orange(), timestamp=datetime.utcnow())
                    await log_channel.send(embed=embed, file=file)
            with open(transcript_file, "r", encoding="utf-8") as f:
                html_content = f.read()
            await save_transcript_to_mongo(self.channel, html_content, closed_by)
            os.remove(transcript_file)
        except Exception:
            traceback.print_exc(file=sys.stderr)
        await self.channel.send("Deleting this ticket in 2 ")
        await asyncio.sleep(3)
        await self.channel.delete()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)


# ==================== TICKET FORM ====================
class TicketForm(discord.ui.Modal, title="Create a Support Ticket"):
    def __init__(self, bot: discord.Client, is_gang_ticket: bool = False):
        super().__init__(timeout=None)
        self.bot = bot
        self.is_gang_ticket = is_gang_ticket
        self.title_input = discord.ui.TextInput(label="Title", required=True, max_length=80)
        self.pov_input = discord.ui.TextInput(label="Do you have POV?", required=True)
        self.time_input = discord.ui.TextInput(label="Time of event", required=True)
        self.description_input = discord.ui.TextInput(label="Explanation", style=discord.TextStyle.paragraph, required=True)
        self.add_item(self.title_input)
        self.add_item(self.pov_input)
        self.add_item(self.time_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)
        category = guild.get_channel(ACTIVE_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("⚠️ Active Tickets category not found.", ephemeral=True)
            return

        try:
            ticket_number = await get_next_ticket_number()
        except Exception as e:
            print(f"[ERROR] Ticket number generation failed: {e}")
            ticket_number = 1

        channel_name = f"gang-ticket-{ticket_number}" if self.is_gang_ticket else f"ticket-{ticket_number}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        if self.is_gang_ticket:
            user_roles = [r for r in interaction.user.roles if r.name.startswith(GANG_ROLE_PREFIX)]
            if user_roles:
                gang_role = user_roles[0]
                for member in gang_role.members:
                    overwrites[member] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites, topic=str(interaction.user.id))
            embed = discord.Embed(title=f"{'Gang Ticket' if self.is_gang_ticket else 'Ticket'} #{ticket_number}", color=discord.Color.blurple())
            embed.add_field(name="Title", value=self.title_input.value, inline=False)
            embed.add_field(name="POV", value=self.pov_input.value, inline=True)
            embed.add_field(name="Time", value=self.time_input.value, inline=True)
            embed.add_field(name="Explanation", value=self.description_input.value, inline=False)
            embed.set_footer(text="A staff member will assist you soon.")
            view = CloseTicketView(channel, self.bot)
            await channel.send(embed=embed, view=view)

            if staff_role:
                await channel.send(f"{staff_role.mention} — new ticket created by {interaction.user.mention}")

            log_channel = guild.get_channel(LOG_CHANNEL_ID)
            if log_channel:
                ticket_type = "Gang Ticket" if self.is_gang_ticket else "Normal Ticket"
                log_embed = discord.Embed(title=f"{ticket_type} Created", description=f"{ticket_type} **#{ticket_number}** created by **{interaction.user}**", color=discord.Color.green(), timestamp=datetime.utcnow())
                log_embed.add_field(name="Channel", value=channel.name, inline=True)
                log_embed.add_field(name="Title", value=self.title_input.value, inline=False)
                await log_channel.send(embed=log_embed)

            await interaction.followup.send(f"✅ Your {('gang ' if self.is_gang_ticket else '')}ticket has been created: {channel.mention}", ephemeral=True)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            await interaction.followup.send("❌ Something went wrong while creating your ticket.", ephemeral=True)


# ==================== BUTTON VIEW ====================
class TicketButton(discord.ui.View):
    def __init__(self, bot: discord.Client):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Normal Ticket", style=discord.ButtonStyle.blurple)
    async def create_normal_ticket(self, interaction: discord.Interaction, _):
        """Anyone can create a normal ticket."""
        await interaction.response.send_modal(TicketForm(self.bot, is_gang_ticket=False))

    @discord.ui.button(label="Create Gang Ticket", style=discord.ButtonStyle.danger)
    async def create_gang_ticket(self, interaction: discord.Interaction, _):
        """Only users with a gang role can create a gang ticket."""
        user_roles = [r for r in interaction.user.roles if r.name.startswith(GANG_ROLE_PREFIX)]

        if not user_roles:
            await interaction.response.send_message(
                "⚠️ You must have a gang role to create a gang ticket.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(TicketForm(self.bot, is_gang_ticket=True))


# ==================== MAIN COG ====================
class TicketTool(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def send_ticket_message(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(TICKET_CHANNEL_ID)
        if not channel:
            print(f"[WARN] Invalid TICKET_CHANNEL_ID: {TICKET_CHANNEL_ID}")
            return
        async for msg in channel.history(limit=5):
            if msg.author == self.bot.user:
                await msg.delete()
        embed = discord.Embed(title="Support Ticket System", description="Choose an option below to create a ticket.", color=discord.Color.green())
        embed.set_footer(text="Ticket System Active")
        view = TicketButton(self.bot)
        await channel.send(embed=embed, view=view)
        print(f"[INFO] Ticket system active in #{channel.name}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                await self.bot.tree.sync(guild=guild_obj)
                print(f"[INFO] Slash commands synced instantly to guild {GUILD_ID}")
            else:
                await self.bot.tree.sync()
                print("[INFO] Slash commands synced globally.")
        except Exception as e:
            print(f"[WARN] Sync failed: {e}")
        asyncio.create_task(self.send_ticket_message())

    @discord.app_commands.command(name="add", description="Add a user to this ticket.")
    async def add_user_to_ticket(self, interaction: discord.Interaction, member: discord.Member):
        channel = interaction.channel
        if not channel or not channel.name.startswith(("ticket-", "gang-ticket-")):
            await interaction.response.send_message("This command must be used inside a ticket channel.", ephemeral=True)
            return
        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)
        if not staff_role or staff_role not in interaction.user.roles:
            await interaction.response.send_message("You don’t have permission.", ephemeral=True)
            return
        await channel.set_permissions(member, read_messages=True, send_messages=True)
        await interaction.response.send_message(f"{member} added to this ticket.", ephemeral=True)
        await channel.send(f"{member} has been granted access by {interaction.user}.")

    @discord.app_commands.command(name="backup", description="Retrieve a saved ticket transcript from MongoDB.")
    async def backup_ticket(self, interaction: discord.Interaction, ticket_number: str):
        await interaction.response.defer(ephemeral=True, thinking=True)
        if mongo_db is None:
            await interaction.followup.send("MongoDB not configured.", ephemeral=True)
            return
        try:
            channel_name = f"ticket-{ticket_number}"
            doc = await mongo_db[MONGO_COLLECTION_NAME].find_one({"channel_name": channel_name})
            if not doc:
                channel_name = f"gang-ticket-{ticket_number}"
                doc = await mongo_db[MONGO_COLLECTION_NAME].find_one({"channel_name": channel_name})
                if not doc:
                    await interaction.followup.send(f"No transcript found for `{channel_name}`.", ephemeral=True)
                    return
            html_data = doc.get("transcript_html")
            filename = f"{channel_name}_backup.html"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(html_data)
            file = discord.File(filename, filename=filename)
            embed = discord.Embed(title="Transcript Backup Retrieved", description=f"Transcript for `{channel_name}` retrieved from MongoDB.", color=discord.Color.blue(), timestamp=datetime.utcnow())
            await interaction.followup.send(embed=embed, file=file, ephemeral=True)
            os.remove(filename)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            await interaction.followup.send("Error fetching transcript.", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketTool(bot))
