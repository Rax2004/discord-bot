import os
import discord
from discord.ext import commands
from datetime import datetime
import asyncio

# ==================== CONFIGURATION ====================
CONTROL_PANEL_CHANNEL_ID = 1436027075029893120  # Channel where the control panel lives (visible dropdown)
ANNOUNCEMENT_CHANNEL_ID = 1436027120508993556    # Channel where announcements are sent
GUILD_ID = os.getenv("GUILD_ID")                 # Optional: for faster slash sync


# ==================== DROPDOWN MENU ====================
class AnnouncementDropdown(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label="City is Restarting",
                description="Announce that the city is restarting",
                emoji="🟡"
            ),
            discord.SelectOption(
                label="City is Under Maintenance",
                description="Announce that the city is under maintenance",
                emoji="🔴"
            ),
            discord.SelectOption(
                label="City is Online",
                description="Announce that the city is back online",
                emoji="🟢"
            ),
        ]
        super().__init__(
            placeholder="Select an announcement to send...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        """Triggered when someone selects an option from the dropdown."""
        # ✅ Acknowledge immediately to prevent "interaction failed"
        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        announcement_channel = guild.get_channel(ANNOUNCEMENT_CHANNEL_ID)

        if not announcement_channel:
            await interaction.followup.send(
                "⚠️ Announcement channel not found. Please check the configuration.",
                ephemeral=True
            )
            return

        selected = self.values[0]

        # Define embed details for each announcement
        announcements = {
            "City is Restarting": {
                "color": discord.Color.gold(),
                "title": "🟡 City Restarting",
                "desc": "The city is currently restarting. Please wait patiently before reconnecting."
            },
            "City is Under Maintenance": {
                "color": discord.Color.red(),
                "title": "🔴 City Under Maintenance",
                "desc": "The city is undergoing maintenance. We’ll notify you once it’s back online."
            },
            "City is Online": {
                "color": discord.Color.green(),
                "title": "🟢 City Online",
                "desc": "The city is now online! You may safely join and enjoy your RP experience."
            }
        }

        data = announcements[selected]

        embed = discord.Embed(
            title=data["title"],
            description=data["desc"],
            color=data["color"],
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text=f"Announced by {interaction.user.display_name}")

        # ✅ Send the announcement embed to the announcement channel
        await announcement_channel.send(embed=embed)

      

# ==================== VIEW (Persistent Dropdown) ====================
class ControlPanelView(discord.ui.View):
    """A persistent view containing the announcement dropdown."""
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(AnnouncementDropdown())


# ==================== COG ====================
class ControlPanel(commands.Cog):
    """Cog for the permanent control panel setup."""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def send_control_panel(self):
        """Sends or refreshes the control panel message."""
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(CONTROL_PANEL_CHANNEL_ID)

        if not channel:
            print(f"[WARN] Invalid CONTROL_PANEL_CHANNEL_ID: {CONTROL_PANEL_CHANNEL_ID}")
            return

        # Clear any old bot messages in the panel channel
        async for msg in channel.history(limit=5):
            if msg.author == self.bot.user:
                await msg.delete()

        embed = discord.Embed(
            title="🎛️ City Control Panel",
            description=(
                "Use the dropdown below to send a city-wide announcement.\n\n"
                "🟡 **City Restarting**\n"
                "🔴 **City Under Maintenance**\n"
                "🟢 **City Online**\n\n"
                "_Anyone who can see this channel can use this panel._"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.set_footer(text="Control Panel Active")

        view = ControlPanelView()
        await channel.send(embed=embed, view=view)
        print(f"[INFO] Control Panel active in #{channel.name}")

    @commands.Cog.listener()
    async def on_ready(self):
        """Syncs slash commands and ensures panel visibility."""
        await self.bot.wait_until_ready()
        try:
            if GUILD_ID:
                guild_obj = discord.Object(id=int(GUILD_ID))
                await self.bot.tree.sync(guild=guild_obj)
                print(f"[INFO] Control Panel commands synced for guild {GUILD_ID}")
            else:
                await self.bot.tree.sync()
                print("[INFO] Control Panel commands synced globally.")
        except Exception as e:
            print(f"[WARN] Control Panel sync failed: {e}")

        asyncio.create_task(self.send_control_panel())


async def setup(bot: commands.Bot):
    await bot.add_cog(ControlPanel(bot))
