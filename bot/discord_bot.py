import os
import asyncio
from typing import Optional
from datetime import datetime, timezone, timedelta
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv

from agents.market_researcher import (
    generate_daily_market_outlook,
    revise_market_outlook,
    generate_weekly_wrapup,
    fetch_realtime_breaking_news,
    fetch_24h_quality_news,
    generate_content_ideas_from_news,
    generate_unique_education_idea,
    clear_processed_news,
)
from agents.academic_tutor import run_tutor_mode, run_project_mode
from database.db_manager import init_db

load_dotenv()
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
WIB = timezone(timedelta(hours=7))

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

CHANNEL_ALLOWED_COMMANDS = {
    "daily-market-outlook": {"outlook", "weekly", "perbaiki"},
    "breaking-market-news": {"cek_berita", "reset_berita", "berita_harian"},
    "rangkuman-konten": {"ide_edukasi"},
    "kuliah-assistant": {"tutor", "proyek"},
}

sent_breaking_titles: set[str] = set()
sent_content_news_titles: set[str] = set()


def get_channel_by_name(name: str):
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if ch.name == name:
                return ch
    return None


async def is_command_allowed_in_channel(interaction: discord.Interaction, cmd_name: str) -> bool:
    ch_name = getattr(interaction.channel, "name", "")
    if ch_name in CHANNEL_ALLOWED_COMMANDS:
        allowed = CHANNEL_ALLOWED_COMMANDS[ch_name]
        if cmd_name not in allowed:
            allowed_str = ", ".join([f"`/{c}`" for c in sorted(allowed)])
            await interaction.response.send_message(
                f"⚠️ Perintah `/{cmd_name}` tidak berlaku di **#{ch_name}**. Gunakan perintah khusus channel ini: {allowed_str}.",
                ephemeral=True,
            )
            return False
    return True


async def send_long_message(channel, text: str):
    if not text:
        return
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    for chunk in chunks:
        await channel.send(chunk)


async def send_long_followup(interaction: discord.Interaction, text: str):
    if not text:
        return
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)]
    await interaction.followup.send(chunks[0])
    for chunk in chunks[1:]:
        await interaction.channel.send(chunk)


async def read_attachment_file(attachment: Optional[discord.Attachment]) -> str:
    if not attachment:
        return ""
    allowed_ext = (".py", ".cpp", ".c", ".java", ".php", ".js", ".html", ".css", ".sql", ".txt", ".md", ".json")
    if attachment.filename.lower().endswith(allowed_ext):
        try:
            data = await attachment.read()
            return f"--- File: {attachment.filename} ---\n{data.decode('utf-8', errors='ignore')}"
        except Exception as e:
            print(f"[WARN] Gagal membaca lampiran {attachment.filename}: {e}")
    return ""


def build_breaking_news_embed(news: dict, label: str = "BREAKING NEWS") -> discord.Embed:
    uraian = news.get("uraian", "").strip()
    poin_list = news.get("poin_penting", [])

    formatted_poin = "\n".join([f"• {p.strip()}" for p in poin_list if str(p).strip()])
    description = f"{uraian}\n\n**Poin & Penjelasan Utama:**\n{formatted_poin}"

    if len(description) > 3900:
        description = description[:3897] + "..."

    embed = discord.Embed(
        title=news.get("judul", "Market News"),
        url=news.get("url", ""),
        description=description,
        color=0x5D0C14,
        timestamp=datetime.now(WIB),
    )
    embed.set_author(name=f"🚨 {label} • {news.get('kategori', 'PASAR MODAL').upper()}")
    embed.add_field(name="Emiten / Fokus", value=f"`{news.get('emiten_atau_fokus', 'BEI')}`", inline=True)
    embed.add_field(
        name="Sumber Resmi Terpercaya",
        value=f"[{news.get('portal', 'Sumber Resmi')}]({news.get('url', '')})",
        inline=True,
    )
    embed.set_footer(text=f"GISBEI UPI YPTK Padang • Terbit: {news.get('published_date', 'Real-Time')} (Disclaimer: ON)")
    return embed


async def broadcast_filtered_news(news_list: list, ch_news, ch_konten=None, label: str = "BREAKING NEWS", push_content_idea: bool = True) -> int:
    unique_news = []
    for n in news_list:
        key = n.get("judul", "").strip().lower()
        if key and key not in sent_breaking_titles:
            sent_breaking_titles.add(key)
            unique_news.append(n)

    if not unique_news:
        return 0

    for news in unique_news:
        await ch_news.send(embed=build_breaking_news_embed(news, label=label))

    if push_content_idea and ch_konten:
        unposted_for_content = [
            n for n in unique_news
            if n.get("judul", "").strip().lower() not in sent_content_news_titles
        ]
        if unposted_for_content:
            ide_konten = await asyncio.to_thread(generate_content_ideas_from_news, unposted_for_content)
            if ide_konten:
                for n in unposted_for_content:
                    sent_content_news_titles.add(n.get("judul", "").strip().lower())
                await send_long_message(ch_konten, ide_konten)

    return len(unique_news)


# ==============================================================================
# PENJADWALAN OTOMATIS (BACKGROUND TASKS)
# ==============================================================================

@tasks.loop(minutes=1)
async def scheduled_market_tasks():
    now_wib = datetime.now(WIB)
    jam_menit = now_wib.strftime("%H:%M")
    hari_ke = now_wib.weekday()

    # 1. Market Outlook Harian: Senin - Jumat pukul 07:30 WIB
    if hari_ke < 5 and jam_menit == "07:30":
        ch = get_channel_by_name("daily-market-outlook")
        if ch:
            laporan = await asyncio.to_thread(generate_daily_market_outlook)
            await send_long_message(ch, laporan)

    # 2. Market Outlook Mingguan: Jumat pukul 20:30 WIB
    if hari_ke == 4 and jam_menit == "20:30":
        ch = get_channel_by_name("daily-market-outlook")
        if ch:
            rekap = await asyncio.to_thread(generate_weekly_wrapup)
            await send_long_message(ch, rekap)

    # 3. Minimal 1 Ide Edukasi Pasar Modal Setiap Hari pukul 09:00 WIB (anti berulang)
    if jam_menit == "09:00":
        ch_konten = get_channel_by_name("rangkuman-konten")
        if ch_konten:
            ide_edukasi = await asyncio.to_thread(generate_unique_education_idea)
            await send_long_message(ch_konten, ide_edukasi)


@tasks.loop(hours=3)
async def monitor_breaking_news_every_3h():
    ch_news = get_channel_by_name("breaking-market-news")
    ch_konten = get_channel_by_name("rangkuman-konten")
    if not ch_news:
        return

    curated = await asyncio.to_thread(fetch_realtime_breaking_news)
    if not curated:
        return

    await broadcast_filtered_news(curated, ch_news, ch_konten, label="BREAKING NEWS", push_content_idea=True)


# ==============================================================================
# OBROLAN TEKS LANGSUNG DI #kuliah-assistant
# ==============================================================================

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    if isinstance(message.channel, discord.TextChannel) and message.channel.name == "kuliah-assistant":
        async with message.channel.typing():
            history_lines = []
            async for msg in message.channel.history(limit=7):
                if msg.id == message.id:
                    continue
                role = "Asisten AI" if msg.author == bot.user else "Mahasiswa"
                if msg.content:
                    history_lines.append(f"{role}: {msg.content[:600]}")
            history_lines.reverse()
            chat_history = "\n".join(history_lines)

            attachment_texts = []
            for att in message.attachments:
                txt = await read_attachment_file(att)
                if txt:
                    attachment_texts.append(txt)
            file_content = "\n\n".join(attachment_texts)

            user_query = message.content.strip() or "Tolong jelaskan dan analisis lampiran ini."
            jawaban = await asyncio.to_thread(run_tutor_mode, user_query, file_content, chat_history)
            await send_long_message(message.channel, jawaban)
        return

    await bot.process_commands(message)


# ==============================================================================
# SLASH COMMANDS PER CHANNEL
# ==============================================================================

@bot.tree.command(name="outlook", description="[#daily-market-outlook] Generate Market Outlook harian")
async def slash_outlook(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "outlook"):
        return
    await interaction.response.defer(thinking=True)
    laporan = await asyncio.to_thread(generate_daily_market_outlook)
    await send_long_followup(interaction, laporan)


@bot.tree.command(name="weekly", description="[#daily-market-outlook] Generate Market Outlook mingguan")
async def slash_weekly(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "weekly"):
        return
    await interaction.response.defer(thinking=True)
    rekap = await asyncio.to_thread(generate_weekly_wrapup)
    await send_long_followup(interaction, rekap)


@bot.tree.command(name="perbaiki", description="[#daily-market-outlook] Perbaiki hasil Market Outlook terakhir dengan AI")
@app_commands.describe(arahan="Tuliskan bagian yang ingin diperbaiki atau ditambahkan pada Market Outlook")
async def slash_perbaiki(interaction: discord.Interaction, arahan: str):
    if not await is_command_allowed_in_channel(interaction, "perbaiki"):
        return
    await interaction.response.defer(thinking=True)
    revisi = await asyncio.to_thread(revise_market_outlook, arahan)
    await send_long_followup(interaction, revisi)


@bot.tree.command(name="cek_berita", description="[#breaking-market-news] Cek & saring berita terbaru sekarang")
async def slash_cek_berita(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "cek_berita"):
        return
    await interaction.response.defer(thinking=True)
    ch_news = get_channel_by_name("breaking-market-news") or interaction.channel
    ch_konten = get_channel_by_name("rangkuman-konten")
    curated = await asyncio.to_thread(fetch_realtime_breaking_news)
    jumlah = await broadcast_filtered_news(curated, ch_news, ch_konten, label="BREAKING NEWS", push_content_idea=True)
    if jumlah == 0:
        await interaction.followup.send("ℹ️ Tidak ada berita penting baru yang belum terkirim.")
    else:
        await interaction.followup.send(f"✅ {jumlah} berita penting baru berhasil dikirim.")


@bot.tree.command(name="berita_harian", description="[#breaking-market-news] Cari & saring berita bagus 24 jam terakhir (manual)")
async def slash_berita_harian(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "berita_harian"):
        return
    await interaction.response.defer(thinking=True)
    ch_news = get_channel_by_name("breaking-market-news") or interaction.channel
    ch_konten = get_channel_by_name("rangkuman-konten")
    curated = await asyncio.to_thread(fetch_24h_quality_news)
    jumlah = await broadcast_filtered_news(curated, ch_news, ch_konten, label="TOP NEWS 24 JAM", push_content_idea=True)
    if jumlah == 0:
        await interaction.followup.send("ℹ️ Tidak ada berita unggulan/top news 24 jam terakhir yang belum terkirim.")
    else:
        await interaction.followup.send(f"✅ {jumlah} berita unggulan 24 jam terakhir berhasil ditampilkan.")


@bot.tree.command(name="reset_berita", description="[#breaking-market-news] Reset riwayat berita di database")
async def slash_reset_berita(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "reset_berita"):
        return
    await interaction.response.defer(thinking=True)
    sent_breaking_titles.clear()
    sent_content_news_titles.clear()
    await asyncio.to_thread(clear_processed_news)
    await interaction.followup.send("✅ Riwayat berita berhasil direset.")


@bot.tree.command(name="ide_edukasi", description="[#rangkuman-konten] Tambah ide edukasi postingan baru (anti berulang)")
async def slash_ide_edukasi(interaction: discord.Interaction):
    if not await is_command_allowed_in_channel(interaction, "ide_edukasi"):
        return
    await interaction.response.defer(thinking=True)
    ide = await asyncio.to_thread(generate_unique_education_idea)
    await send_long_followup(interaction, ide)


@bot.tree.command(name="tutor", description="[#kuliah-assistant] Minta penjelasan materi (dengan/tanpa file)")
@app_commands.describe(pertanyaan="Topik atau hal yang ingin dijelaskan", file_materi="Opsional: lampirkan file materi/kode")
async def slash_tutor(interaction: discord.Interaction, pertanyaan: str, file_materi: Optional[discord.Attachment] = None):
    if not await is_command_allowed_in_channel(interaction, "tutor"):
        return
    await interaction.response.defer(thinking=True)
    file_text = await read_attachment_file(file_materi)
    jawaban = await asyncio.to_thread(run_tutor_mode, pertanyaan, file_text)
    await send_long_followup(interaction, jawaban)


@bot.tree.command(name="proyek", description="[#kuliah-assistant] Kerjakan soal atau proyek (dengan/tanpa file)")
@app_commands.describe(instruksi="Detail soal, tugas, atau proyek", file_tugas="Opsional: lampirkan file soal/kode")
async def slash_proyek(interaction: discord.Interaction, instruksi: str, file_tugas: Optional[discord.Attachment] = None):
    if not await is_command_allowed_in_channel(interaction, "proyek"):
        return
    await interaction.response.defer(thinking=True)
    file_text = await read_attachment_file(file_tugas)
    jawaban = await asyncio.to_thread(run_project_mode, instruksi, file_text)
    await send_long_followup(interaction, jawaban)


@bot.event
async def on_ready():
    init_db()
    try:
        synced = await bot.tree.sync()
        print(f"✅ Berhasil menyinkronkan {len(synced)} Slash Commands ke Discord!")
    except Exception as e:
        print(f"[WARN] Gagal sync Slash Commands: {e}")

    print(f"✅ Bot berhasil online sebagai {bot.user}!")
    if not scheduled_market_tasks.is_running():
        scheduled_market_tasks.start()
    if not monitor_breaking_news_every_3h.is_running():
        monitor_breaking_news_every_3h.start()


if __name__ == "__main__":
    if not TOKEN:
        raise ValueError("DISCORD_BOT_TOKEN belum diisi di file .env!")
    bot.run(TOKEN)