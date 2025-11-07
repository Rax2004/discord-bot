import os
import discord
from discord.ext import commands
from datetime import datetime
import asyncio
import traceback
import sys
import uuid

# =============== CONFIGURATION ===============
LOG_CHANNEL_ID = 1436031537631068290     # Channel where recordings are logged
GUILD_ID = os.getenv("GUILD_ID")         # Optional, for guild-only sync

# MongoDB setup
try:
    import motor.motor_asyncio as motor_asyncio
except ImportError:
    motor_asyncio = None
    print("[WARN] motor not installed. Run: pip install motor", file=sys.stderr)

MONGO_URI = os.getenv("MONGO_URI")
MONGO_DB_NAME = os.getenv("MONGO_DB", "tickets_db")
MONGO_COLLECTION_NAME = os.getenv("RECORD_COLLECTION", "recordings")

mongo_client = None
mongo_db = None
if motor_asyncio and MONGO_URI:
    try:
        mongo_client = motor_asyncio.AsyncIOMotorClient(MONGO_URI)
        mongo_db = mongo_client[MONGO_DB_NAME]
        print("[INFO] MongoDB connected successfully for recording manager.")
    except Exception as e:
        print("[ERROR] MongoDB connection failed:", e, file=sys.stderr)
else:
    print("[WARN] MongoDB not configured or motor missing. Recording logs won't be saved.")


# =============== RECORDING MANAGER COG ===============
class RecordManager(commands.Cog):
    """Handles simulated voice recordings and metadata logging."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.active_sessions = {}  # {guild_id: {"channel": id, "user": user, "start": time}}

    # ----- /record -----
    @discord.app_commands.command(name="record", description="Start recording in your current voice channel.")
    async def start_record(self, interaction: discord.Interaction):
        user = interaction.user
        guild = interaction.guild

        if not user.voice or not user.voice.channel:
            await interaction.response.send_message("❌ You must be in a voice channel to start recording.", ephemeral=True)
            return

        channel = user.voice.channel

        # Prevent duplicate sessions
        if guild.id in self.active_sessions:
            await interaction.response.send_message("⚠️ A recording session is already active in this server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        # Simulate connecting to VC (official bots cannot record real audio)
        try:
            vc = await channel.connect()
        except discord.ClientException:
            vc = guild.voice_client

        session_id = str(uuid.uuid4())
        start_time = datetime.utcnow()

        self.active_sessions[guild.id] = {
            "id": session_id,
            "channel_id": channel.id,
            "user": str(user),
            "start_time": start_time
        }

        embed = discord.Embed(
            title="🎙️ Recording Started",
            description=f"Recording session started by **{user}** in {channel.mention}",
            color=discord.Color.green(),
            timestamp=start_time
        )
        embed.add_field(name="Session ID", value=session_id, inline=False)
        embed.set_footer(text="Recording metadata saved")

        await interaction.followup.send(embed=embed, ephemeral=True)

        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)

    # ----- /stoprecord -----
    @discord.app_commands.command(name="stoprecord", description="Stop the current recording and log it.")
    async def stop_record(self, interaction: discord.Interaction, file: discord.Attachment | None = None):
        guild = interaction.guild
        user = interaction.user

        if guild.id not in self.active_sessions:
            await interaction.response.send_message("⚠️ No active recording session found.", ephemeral=True)
            return

        session = self.active_sessions.pop(guild.id)
        end_time = datetime.utcnow()

        # Disconnect if still connected
        if guild.voice_client:
            try:
                await guild.voice_client.disconnect()
            except Exception:
                pass

        # Prepare embed
        embed = discord.Embed(
            title="🎧 Recording Stopped",
            description=f"Recording stopped by **{user}**",
            color=discord.Color.orange(),
            timestamp=end_time
        )
        embed.add_field(name="Session ID", value=session["id"], inline=False)
        embed.add_field(name="Started By", value=session["user"], inline=True)
        embed.add_field(name="Voice Channel", value=f"<#{session['channel_id']}>", inline=True)
        embed.add_field(name="Duration", value=str(end_time - session["start_time"]), inline=True)

        # Upload file if provided
        uploaded_url = None
        if file:
            embed.add_field(name="Recording File", value=f"[{file.filename}]({file.url})", inline=False)
            uploaded_url = file.url

        # Send to log channel
        log_channel = guild.get_channel(LOG_CHANNEL_ID)
        if log_channel:
            await log_channel.send(embed=embed)

        await interaction.response.send_message("✅ Recording session closed and logged.", ephemeral=True)

        # Save to MongoDB
        if mongo_db:
            record_doc = {
                "session_id": session["id"],
                "guild_id": guild.id,
                "channel_id": session["channel_id"],
                "started_by": session["user"],
                "stopped_by": str(user),
                "start_time": session["start_time"],
                "end_time": end_time,
                "file_url": uploaded_url,
                "duration_seconds": (end_time - session["start_time"]).total_seconds(),
            }
            try:
                await mongo_db[MONGO_COLLECTION_NAME].insert_one(record_doc)
                print(f"[MongoDB] Recording metadata saved for session {session['id']}")
            except Exception:
                traceback.print_exc(file=sys.stderr)


    # ----- /recordings -----
    @discord.app_commands.command(name="recordings", description="List recent recordings from MongoDB.")
    async def list_recordings(self, interaction: discord.Interaction):
        if not mongo_db:
            await interaction.response.send_message("MongoDB not configured.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        docs = mongo_db[MONGO_COLLECTION_NAME].find({"guild_id": interaction.guild_id}).sort("start_time", -1).limit(5)
        result = [doc async for doc in docs]

        if not result:
            await interaction.followup.send("No recordings found for this server.", ephemeral=True)
            return

        embed = discord.Embed(
            title="🎞️ Recent Recordings",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        for doc in result:
            duration = round(doc.get("duration_seconds", 0))
            file_url = doc.get("file_url", "No file uploaded")
            embed.add_field(
                name=f"🎙️ {doc['started_by']} → {doc['stopped_by']}",
                value=f"Started: {doc['start_time'].strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                      f"Duration: {duration}s\n"
                      f"File: {file_url}",
                inline=False
            )
        await interaction.followup.send(embed=embed, ephemeral=True)


    # Auto-sync & setup
    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.wait_until_ready()
        try:
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                await self.bot.tree.sync(guild=guild_obj)
                print(f"[INFO] Recording commands synced to guild {GUILD_ID}")
            else:
                await self.bot.tree.sync()
                print("[INFO] Recording commands synced globally.")
        except Exception as e:
            print(f"[WARN] Command sync failed: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(RecordManager(bot))
