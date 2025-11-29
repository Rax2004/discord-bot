import os
import discord
from discord.ext import commands, tasks
from discord import ui
from dotenv import load_dotenv

# ================================
# LOAD ENV CONFIG
# ================================
load_dotenv()

def get_int(key, required=True):
    val = os.getenv(key)
    if not val:
        if required:
            raise ValueError(f"Missing env variable: {key}")
        return None
    return int(val)

REQUEST_CHANNEL_ID  = get_int("REQUEST_CHANNEL_ID")
APPROVAL_CHANNEL_ID = get_int("APPROVAL_CHANNEL_ID")
STAFF_ROLE_ID       = get_int("STAFF_ROLE_ID")
LOG_CHANNEL_ID      = get_int("LOG_CHANNEL_ID", required=False)


# ================================
# COG
# ================================
class NameChange(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ensure_button.start()

    def cog_unload(self):
        self.ensure_button.cancel()

    # ================================
    # AUTO-SEND REQUEST BUTTON
    # ================================
    @tasks.loop(count=1)
    async def ensure_button(self):
        await self.bot.wait_until_ready()

        channel = self.bot.get_channel(REQUEST_CHANNEL_ID)
        if not channel:
            print("❌ Invalid REQUEST_CHANNEL_ID")
            return

        # Avoid duplicates
        async for msg in channel.history(limit=20):
            if msg.author == self.bot.user and msg.embeds:
                if msg.embeds[0].title == "💠 Name Change Request":
                    return

        embed = discord.Embed(
            title="💠 Name Change Request",
            description="Click the button below to request a name change.",
            color=discord.Color.green()
        )

        view = ui.View(timeout=None)
        view.add_item(
            ui.Button(
                label="Request Name Change",
                style=discord.ButtonStyle.success,
                custom_id="namechange_modal"
            )
        )

        await channel.send(embed=embed, view=view)
        print("[NameChange] Request button sent.")

    @ensure_button.before_loop
    async def before(self):
        await self.bot.wait_until_ready()

    # ================================
    # INTERACTION HANDLER
    # ================================
    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):

        cid = interaction.data.get("custom_id") if interaction.data else None

        # User clicks the button
        if cid == "namechange_modal":
            await interaction.response.send_modal(NameChangeModal())
            return

        # Staff approves
        if cid and cid.startswith("approve:"):
            req_id = int(cid.split(":")[1])
            await self.handle_approve(interaction, req_id)
            return

        # Staff rejects
        if cid and cid.startswith("reject:"):
            req_id = int(cid.split(":")[1])
            RejectModal = reject_modal(req_id)
            await interaction.response.send_modal(RejectModal())
            return


    # ================================
    # HANDLE APPROVAL
    # ================================
    async def handle_approve(self, interaction: discord.Interaction, requester_id: int):

        staff = interaction.user
        original_msg = interaction.message
        original_embed = original_msg.embeds[0].copy()

        # Extract new name
        new_name = None
        for f in original_embed.fields:
            if f.name == "New Name":
                new_name = f.value

        # Insert status field
        original_embed.insert_field_at(
            0,
            name="Status",
            value=f"✅ Approved by {staff.mention}",
            inline=False
        )
        original_embed.color = discord.Color.green()

        # Freeze buttons
        frozen = ui.View()
        frozen.add_item(ui.Button(label="Approve", disabled=True, style=discord.ButtonStyle.success))
        frozen.add_item(ui.Button(label="Reject", disabled=True, style=discord.ButtonStyle.danger))

        # Update message
        await original_msg.edit(embed=original_embed, view=frozen)
        await interaction.response.send_message("Approved.", ephemeral=True)
        member = await interaction.guild.fetch_member(requester_id)
        await member.edit(nick=new_name)


        #
        # ================================

        # Log (optional)
        if LOG_CHANNEL_ID:
            logch = self.bot.get_channel(LOG_CHANNEL_ID)
            if logch:
                await logch.send(
                    f"✅ **Approved:** <@{requester_id}> → `{new_name}` by {staff.mention}"
                )


# ================================
# MODAL FOR USER REQUEST
# ================================
class NameChangeModal(ui.Modal, title="Name Change Request"):
    new_name = ui.TextInput(label="New Name", required=True)
    reason = ui.TextInput(label="Reason", required=True, style=discord.TextStyle.paragraph)

    async def on_submit(self, interaction: discord.Interaction):

        approval_ch = interaction.client.get_channel(APPROVAL_CHANNEL_ID)

        embed = discord.Embed(
            title="📝 New Name Change Request",
            color=discord.Color.blurple()
        )
        embed.add_field(name="New Name", value=self.new_name.value, inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(name="Requested By", value=interaction.user.mention, inline=False)
        embed.set_footer(text=f"User ID: {interaction.user.id}")

        # Staff buttons
        v = ui.View()
        v.add_item(ui.Button(label="Approve", style=discord.ButtonStyle.success, custom_id=f"approve:{interaction.user.id}"))
        v.add_item(ui.Button(label="Reject", style=discord.ButtonStyle.danger, custom_id=f"reject:{interaction.user.id}"))

        staff_role = interaction.guild.get_role(STAFF_ROLE_ID)

        await approval_ch.send(
            content=f"{staff_role.mention} — review this request:",
            embed=embed,
            view=v
        )

        await interaction.response.send_message("Your request has been submitted.", ephemeral=True)


# ================================
# REJECTION MODAL FACTORY
# ================================
def reject_modal(uid):
    class Reject(ui.Modal, title="Reject Request"):
        reason = ui.TextInput(
            label="Rejection Reason",
            style=discord.TextStyle.paragraph,
            required=True
        )

        async def on_submit(self, interaction: discord.Interaction):

            msg = interaction.message
            embed = msg.embeds[0].copy()

            embed.insert_field_at(
                0,
                name="Status",
                value=f"❌ Rejected by {interaction.user.mention}",
                inline=False
            )
            embed.add_field(name="Rejection Reason", value=self.reason.value, inline=False)
            embed.color = discord.Color.red()

            # Freeze buttons
            frozen = ui.View()
            frozen.add_item(ui.Button(label="Approve", disabled=True, style=discord.ButtonStyle.success))
            frozen.add_item(ui.Button(label="Reject", disabled=True, style=discord.ButtonStyle.danger))

            await msg.edit(embed=embed, view=frozen)
            await interaction.response.send_message("Rejected.", ephemeral=True)

            # Try DM the requester
            try:
                member = await interaction.guild.fetch_member(uid)
                await member.send(
                    f"❌ Your name change request was rejected.\nReason: {self.reason.value}"
                )
            except:
                pass

    return Reject


# ================================
# SETUP FUNCTION
# ================================
async def setup(bot):
    await bot.add_cog(NameChange(bot))
