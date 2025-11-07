import os
import html
import asyncio
import traceback
import sys
from datetime import datetime

import discord
from discord.ext import commands

# ==================== CONFIGURATION ====================
SUPPORT_TICKET_CHANNEL_ID = 1436010903006085237   # Channel where support embed is posted (replace)
SUPPORT_CATEGORY_ID = 1436013928541851799         # Category for new support tickets (replace)
SUPPORT_LOG_CHANNEL_ID = 1436014408571818215      # Channel to send support logs (replace)
STAFF_ROLE_ID = 1101039305390043187             # Role that can view/close tickets
GUILD_ID = os.getenv("GUILD_ID")

# ==================== MONGODB SETUP ====================
try:
    import motor.motor_asyncio as motor_asyncio
except ImportError:
    motor_asyncio = None
    print("[WARN] motor not installed. Run: pip install motor", file=sys.stderr)

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB", "tickets_db")
MONGO_COLLECTION_NAME = "support"

mongo_client = None
mongo_db = None
if motor_asyncio and MONGO_URI:
    try:
        mongo_client = motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        mongo_db = mongo_client[MONGO_DB_NAME]
        print("[INFO] MongoDB connected successfully for support tickets.")
    except Exception as e:
        print("[ERROR] MongoDB connection failed:", e, file=sys.stderr)
else:
    print("[WARN] MongoDB not configured or motor missing. Support transcripts won’t be saved.")


# ==================== SEQUENTIAL SUPPORT TICKET NUMBER ====================
async def get_next_support_ticket_number() -> int:
    """Get next sequential support ticket number."""
    if mongo_db is None:
        counter_file = "support_ticket_counter.txt"
        if os.path.exists(counter_file):
            with open(counter_file, "r") as f:
                count = int(f.read().strip())
        else:
            count = 0
        count += 1
        with open(counter_file, "w") as f:
            f.write(str(count))
        return count

    counters = mongo_db["support_ticket_counters"]
    doc = await counters.find_one_and_update(
        {"_id": "support_ticket_number"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    return doc["seq"]


# ==================== TRANSCRIPT GENERATOR ====================
async def create_support_transcript(channel: discord.TextChannel) -> str:
    """Generate HTML transcript for support ticket."""
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
</style></head><body>
<h2>Support Ticket Transcript - {html.escape(channel.name)}</h2>
<p>Server: {html.escape(channel.guild.name)}<br>
Generated: {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")}</p><hr>
"""
    for msg in messages:
        timestamp = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
        author = html.escape(str(msg.author))
        html_content += f"<div class='msg'><span class='author'>{author}</span><span class='time'>[{timestamp}]</span>"
        if msg.content:
            html_content += f"<div class='content'>{html.escape(msg.content)}</div>"
        html_content += "</div>\n"
    html_content += "</body></html>"
    path = f"support_transcript_{channel.id}_{int(datetime.utcnow().timestamp())}.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_content)
    return path


# ==================== SAVE TRANSCRIPT TO MONGO ====================
async def save_support_transcript_to_mongo(channel: discord.TextChannel, transcript_html: str, closed_by: discord.Member):
    """Save transcript HTML + metadata to MongoDB collection 'support'."""
    if mongo_db is None:
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
        print(f"[MongoDB] Support transcript saved for {channel.name}")
    except Exception:
        traceback.print_exc(file=sys.stderr)


# ==================== CLOSE SUPPORT VIEW ====================
class CloseSupportView(discord.ui.View):
    def __init__(self, channel: discord.TextChannel, bot: discord.Client):
        super().__init__(timeout=None)
        self.channel = channel
        self.bot = bot

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger)
    async def close_ticket(self, interaction: discord.Interaction, _):
        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)
        creator_id = getattr(self.channel, "topic", None)

        if str(interaction.user.id) != creator_id and (not staff_role or staff_role not in interaction.user.roles):
            await interaction.response.send_message("You don’t have permission to close this support ticket.", ephemeral=True)
            return

        await interaction.response.send_message("Confirm closing this ticket?", view=ConfirmSupportClose(self.channel, self.bot), ephemeral=True)


class ConfirmSupportClose(discord.ui.View):
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
            transcript_file = await create_support_transcript(self.channel)
            log_channel = guild.get_channel(SUPPORT_LOG_CHANNEL_ID)
            if log_channel:
                with open(transcript_file, "rb") as f:
                    file = discord.File(f, filename=os.path.basename(transcript_file))
                    embed = discord.Embed(title="Support Ticket Closed", color=discord.Color.orange(), timestamp=datetime.utcnow())
                    embed.add_field(name="Channel", value=self.channel.name, inline=False)
                    embed.add_field(name="Closed By", value=str(closed_by), inline=False)
                    await log_channel.send(embed=embed, file=file)

            with open(transcript_file, "r", encoding="utf-8") as f:
                html_content = f.read()
            await save_support_transcript_to_mongo(self.channel, html_content, closed_by)
            os.remove(transcript_file)
        except Exception:
            traceback.print_exc(file=sys.stderr)

        await self.channel.send("✅ Ticket closed. This channel will be deleted in 3 seconds.")
        await asyncio.sleep(3)
        await self.channel.delete()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _):
        await interaction.response.send_message("Close cancelled.", ephemeral=True)


# ==================== SUPPORT BUTTON (NO FORM) ====================
class SupportButton(discord.ui.View):
    def __init__(self, bot: discord.Client):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(label="Create Support Ticket", style=discord.ButtonStyle.blurple)
    async def create_support_ticket(self, interaction: discord.Interaction, _):
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild = interaction.guild
        staff_role = guild.get_role(STAFF_ROLE_ID)
        category = guild.get_channel(SUPPORT_CATEGORY_ID)

        if not category or not isinstance(category, discord.CategoryChannel):
            await interaction.followup.send("⚠️ Support ticket category not found.", ephemeral=True)
            return

        try:
            ticket_number = await get_next_support_ticket_number()
        except Exception:
            ticket_number = 1

        channel_name = f"support-{ticket_number}"
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        try:
            # Create the ticket channel
            channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=str(interaction.user.id)
            )

            # Ticket header embed
            header_embed = discord.Embed(
                title=f"Support Ticket #{ticket_number}",
                description=f"Ticket opened by **{interaction.user}**.",
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )

            # Instruction embed for user
            instruction_embed = discord.Embed(
                title="📝 Please Explain Your Issue",
                description=(
                    "Our support team will attend to you shortly.\n"
                    "While you wait, please describe your problem in detail below — "
                    "include any relevant screenshots, time of occurrence, or other info."
                ),
                color=discord.Color.green()
            )

            # Send both embeds
            view = CloseSupportView(channel, self.bot)
            await channel.send(embed=header_embed)
            await channel.send(embed=instruction_embed, view=view)

            if staff_role:
                await channel.send(f"{staff_role.mention} — new support ticket created by {interaction.user.mention}")

            log_channel = guild.get_channel(SUPPORT_LOG_CHANNEL_ID)
            if log_channel:
                log_embed = discord.Embed(
                    title="New Support Ticket Created",
                    description=f"Ticket **#{ticket_number}** created by **{interaction.user}**",
                    color=discord.Color.green(),
                    timestamp=datetime.utcnow()
                )
                await log_channel.send(embed=log_embed)

            await interaction.followup.send(f"✅ Your support ticket has been created: {channel.mention}", ephemeral=True)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            await interaction.followup.send("❌ Failed to create support ticket.", ephemeral=True)


# ==================== MAIN COG ====================
class SupportTicket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def send_support_embed(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(SUPPORT_TICKET_CHANNEL_ID)
        if not channel:
            print(f"[WARN] Invalid SUPPORT_TICKET_CHANNEL_ID: {SUPPORT_TICKET_CHANNEL_ID}")
            return
        try:
            async for msg in channel.history(limit=5):
                if msg.author == self.bot.user:
                    await msg.delete()
        except Exception:
            pass

        embed = discord.Embed(
            title="Support Ticket System",
            description="Click below to create a support ticket. A private channel will be made for you.",
            color=discord.Color.green()
        )
        embed.set_footer(text="Support System Active")
        view = SupportButton(self.bot)
        await channel.send(embed=embed, view=view)
        print(f"[INFO] Support ticket system active in #{channel.name}")

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                await self.bot.tree.sync(guild=guild_obj)
                print(f"[INFO] Support slash commands synced for guild {GUILD_ID}")
            else:
                await self.bot.tree.sync()
                print("[INFO] Support slash commands synced globally.")
        except Exception as e:
            print(f"[WARN] Support command sync failed: {e}")
        asyncio.create_task(self.send_support_embed())


async def setup(bot: commands.Bot):
    await bot.add_cog(SupportTicket(bot))
