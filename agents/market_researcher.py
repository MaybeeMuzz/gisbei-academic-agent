import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from dotenv import load_dotenv
from tavily import TavilyClient

from agents.llm_router import ask_llm, extract_json_from_llm
from database.db_manager import (
    DB_PATH,
    init_db,
    save_daily_outlook,
    get_latest_outlook,
    is_news_processed,
    mark_news_processed,
    get_past_education_ideas,
    save_education_idea,
)

load_dotenv()
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
WIB = timezone(timedelta(hours=7))

MNCS_MORNING_NAVIGATOR_URL = "https://www.mncsekuritas.id/riset/mncs-morning-navigator/"

OFFICIAL_FINANCE_DOMAINS = [
    "idx.co.id",
    "cnbcindonesia.com",
    "kontan.co.id",
    "bisnis.com",
    "investor.id",
    "idnfinancials.com",
    "mncsekuritas.id",
]

BULAN_ID = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember"
]

CURATED_EDUCATION_TOPICS = [
    "Perbedaan Saham, Reksa Dana, dan Obligasi (Karakteristik, Imbal Hasil, Risiko & Kecocokan Investor)",
    "Apa Itu Corporate Action? Memahami Dividen, Stock Split, Right Issue, Buyback & Merger",
    "Kenapa Perusahaan Membagi Dividen? Makna Rasio Pembayaran, Cum Date, Ex Date & Dividend Trap",
    "Apa Itu Buyback Saham? Alasan Emiten Melakukan Pembelian Kembali & Dampaknya ke Harga Saham",
    "Apa Itu MSCI dan Bagaimana MSCI Index Review Bekerja di Pasar Modal?",
    "Dampak MSCI Terhadap Saham Indonesia: Arus Dana Asing, Bobot Konstituen & Volatilitas Big Caps",
    "Apa yang Terjadi Jika Indonesia Menjadi Standalone Market? Implikasi Likuiditas, Aliran Dana & Sentimen Global",
    "Membaca Laporan Keuangan Emiten untuk Pemula: Pendapatan, Laba Bersih, Arus Kas & Utang",
    "Memahami Valuasi Saham Dasar: PER, PBV, ROE, dan DER Secara Proporsional",
    "Manajemen Risiko & Diversifikasi Portofolio: Menghindari All-In dan Jebakan FOMO",
    "Pasar Modal Syariah Indonesia: Seleksi Saham Syariah (DES/ISSI), Prinsip Akad & Sukuk",
    "Membedakan Investor Jangka Panjang, Swing Trader, dan Trader Harian di BEI",
]


def get_realtime_date_tokens() -> dict:
    now = datetime.now(WIB)
    yesterday = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    return {
        "iso_today": now.strftime("%Y-%m-%d"),
        "upper_today": f"{now.day:02d} {BULAN_ID[now.month].upper()} {now.year}",
        "id_today": f"{now.day} {BULAN_ID[now.month]} {now.year}",
        "id_yesterday": f"{yesterday.day} {BULAN_ID[yesterday.month]} {yesterday.year}",
        "id_week_ago": f"{week_ago.day} {BULAN_ID[week_ago.month]} {week_ago.year}",
        "year_str": str(now.year),
    }


def detect_source_portal(url: str) -> str:
    low = (url or "").lower()
    if "mncsekuritas.id" in low:
        return "MNC Sekuritas (Morning Navigator)"
    if "idx.co.id" in low:
        return "IDX (Bursa Efek Indonesia)"
    if "cnbcindonesia.com" in low:
        return "CNBC Indonesia"
    if "kontan.co.id" in low:
        return "Kontan"
    if "bisnis.com" in low:
        return "Bisnis.com"
    if "investor.id" in low:
        return "Investor.id"
    if "idnfinancials.com" in low:
        return "IDNFinancials"
    return "Sumber Resmi Pasar Modal"


def _fetch_mncs_morning_navigator() -> str:
    try:
        ext = tavily.extract(urls=[MNCS_MORNING_NAVIGATOR_URL])
        results = ext.get("results", [])
        if results and results[0].get("raw_content"):
            raw = re.sub(r"\s+", " ", results[0]["raw_content"]).strip()
            return f"[MNC Sekuritas — Morning Navigator ({MNCS_MORNING_NAVIGATOR_URL})]: {raw[:2500]}"
    except Exception as e:
        print(f"[WARN] Gagal extract MNC Morning Navigator: {e}")

    try:
        dt_info = get_realtime_date_tokens()
        res = tavily.search(
            query=f"MNC Morning Navigator market outlook {dt_info['id_today']} {dt_info['id_yesterday']}",
            topic="news",
            days=7,
            search_depth="basic",
            max_results=3,
            include_domains=["mncsekuritas.id"],
        )
        items = [
            f"- [{r.get('published_date', 'Terbaru')}] {r.get('title')}: {r.get('content')} ({r.get('url')})"
            for r in res.get("results", [])
        ]
        if items:
            return "[MNC Sekuritas — Morning Navigator]:\n" + "\n".join(items)
    except Exception as e:
        print(f"[WARN] Gagal search MNC Morning Navigator: {e}")

    return ""


def is_within_hours(item: dict, strict_hours: int = 6, max_fallback_hours: int = 12) -> bool:
    now = datetime.now(WIB)
    cutoff_strict = now - timedelta(hours=strict_hours)
    cutoff_max = now - timedelta(hours=max_fallback_hours)
    url = item.get("url", "")
    pub_raw = item.get("published_date") or item.get("publishedDate") or ""

    if pub_raw:
        try:
            dt = datetime.fromisoformat(pub_raw.replace("Z", "+00:00"))
            dt = dt.replace(tzinfo=WIB) if dt.tzinfo is None else dt.astimezone(WIB)
            return cutoff_strict <= dt <= (now + timedelta(minutes=15))
        except Exception:
            try:
                dt = parsedate_to_datetime(pub_raw)
                dt = dt.replace(tzinfo=WIB) if dt.tzinfo is None else dt.astimezone(WIB)
                return cutoff_strict <= dt <= (now + timedelta(minutes=15))
            except Exception:
                pass

    match_full = re.search(r"/(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])([01]\d|2[0-3])([0-5]\d)", url)
    if match_full:
        try:
            url_dt = datetime(
                int(match_full.group(1)),
                int(match_full.group(2)),
                int(match_full.group(3)),
                int(match_full.group(4)),
                int(match_full.group(5)),
                tzinfo=WIB,
            )
            return cutoff_strict <= url_dt <= (now + timedelta(minutes=15))
        except Exception:
            pass

    match_ymd = re.search(r"/(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])", url)
    if match_ymd:
        try:
            url_dt = datetime(
                int(match_ymd.group(1)),
                int(match_ymd.group(2)),
                int(match_ymd.group(3)),
                23, 59,
                tzinfo=WIB,
            )
            return url_dt >= cutoff_max
        except Exception:
            pass

    return False


def clear_processed_news():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM processed_news")
    conn.commit()
    conn.close()


# ==============================================================================
# 1. MARKET OUTLOOK HARIAN, MINGGUAN & PERBAIKAN (/perbaiki)
# ==============================================================================

def generate_daily_market_outlook() -> str:
    init_db()
    now_wib = datetime.now(WIB)
    today_str = now_wib.strftime("%Y-%m-%d")
    hari_list = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
    hari_ini = hari_list[now_wib.weekday()]
    dt_info = get_realtime_date_tokens()

    mncs_context = _fetch_mncs_morning_navigator()

    queries = [
        f"penutupan bursa Wall Street Dow Jones Nasdaq S&P 500 komoditas minyak emas {dt_info['id_today']} {dt_info['id_yesterday']}",
        f"pergerakan IHSG net foreign buy sell rupiah cadangan devisa {dt_info['id_today']} {dt_info['id_yesterday']}",
        f"berita korporasi emiten sukuk obligasi dividen ekspansi BEI {dt_info['id_today']} {dt_info['id_yesterday']}",
    ]

    raw_context = []
    if mncs_context:
        raw_context.append(mncs_context)

    for q in queries:
        try:
            res = tavily.search(
                query=q,
                topic="news",
                days=1,
                search_depth="advanced",
                max_results=5,
                include_domains=OFFICIAL_FINANCE_DOMAINS,
            )
            for item in res.get("results", []):
                if is_within_hours(item, strict_hours=12, max_fallback_hours=24):
                    pub = item.get("published_date", dt_info["iso_today"])
                    raw_context.append(f"- [{pub}] {item['title']}: {item['content']} (Sumber: {item['url']})")
        except Exception as e:
            print(f"[WARN] Gagal search Market Outlook '{q}': {e}")

    context_text = "\n".join(raw_context) if raw_context else "Belum ada rilis data perdagangan terbaru."

    system_prompt = (
        "Kamu adalah Analis Riset Pasar Modal dari GISBEI UPI YPTK Padang. "
        "Susun ringkasan Market Outlook bergaya naratif riset sekuritas institusional yang padat data, "
        "objektif, runtut, dan memadukan rujukan sekuritas (seperti MNC Morning Navigator) serta portal keuangan resmi."
    )

    prompt = f"""
    Susun laporan Market Outlook untuk tanggal {dt_info['upper_today']} berdasarkan data dan rujukan berikut:

    {context_text}

    WAJIB ikuti persis struktur dan gaya penulisan naratif berikut (tanpa menambahkan rekomendasi beli/jual saham):

    **MARKET OUTLOOK — {dt_info['upper_today']}**

    **Global Market Summary**
    (1 paragraf padat merangkum penutupan indeks Wall Street seperti Dow Jones, S&P 500, dan Nasdaq beserta persentase perubahannya, sentimen saham teknologi/semikonduktor, data ekonomi makro AS/global, implikasinya terhadap suku bunga The Fed, serta pergerakan komoditas utama.)

    **Domestik Highlight**
    (2 paragraf padat merangkum pergerakan IHSG terakhir beserta level penutupannya, aliran dana asing/net foreign buy-sell, saham penggerak indeks, data makro domestik, serta posisi nilai tukar Rupiah. Paragraf kedua mengulas area support & resistance teknikal IHSG, indikator momentum/volume, serta agenda katalis pasar ke depan.)

    **Top News**
    (2 paragraf naratif merangkum berita korporasi atau kebijakan emiten paling menonjol seperti penerbitan obligasi/sukuk, peringkat kredit, ekspansi proyek strategis, maupun pembagian dividen.)

    ⚠️ *Disclaimer: ON — Disusun untuk tujuan edukasi & literasi pasar modal oleh GISBEI UPI YPTK Padang (Referensi: MNC Morning Navigator & Portal Resmi Pasar Modal).*
    """

    laporan = ask_llm(prompt, system_prompt=system_prompt)
    save_daily_outlook(today_str, hari_ini, laporan)
    return laporan


def revise_market_outlook(feedback_instruction: str) -> str:
    latest = get_latest_outlook()
    if not latest:
        return "Belum ada data Market Outlook tersimpan di database untuk diperbaiki. Jalankan `/outlook` terlebih dahulu."

    date_str, day_name, old_content = latest
    system_prompt = (
        "Kamu adalah Senior Editor Riset Pasar Modal GISBEI UPI YPTK Padang. "
        "Perbaiki laporan Market Outlook sesuai arahan revisi user dengan tetap menjaga akurasi data, "
        "kerapian format naratif, dan objektivitas analisis."
    )

    prompt = f"""
    Draf Market Outlook saat ini:
    {old_content}

    Arahan Perbaikan dari User:
    {feedback_instruction}

    Hasilkan versi Market Outlook yang sudah diperbaiki secara lengkap dan rapi.
    """
    revised = ask_llm(prompt, system_prompt=system_prompt)
    save_daily_outlook(date_str, day_name, revised)
    return revised


def generate_weekly_wrapup() -> str:
    """
    Mencari langsung data & rangkuman pergerakan pasar sepekan (Senin - Jumat / 7 hari terakhir)
    dari sumber sekuritas dan portal keuangan resmi tanpa mengandalkan data yang tersimpan di database.
    """
    dt_info = get_realtime_date_tokens()
    mncs_context = _fetch_mncs_morning_navigator()

    weekly_queries = [
        f"kinerja IHSG sepekan net buy sell asing bursa saham {dt_info['id_week_ago']} {dt_info['id_today']}",
        f"bursa global Wall Street Dow Jones Nasdaq sepekan komoditas emas minyak {dt_info['year_str']}",
        f"nilai tukar Rupiah inflasi suku bunga makroekonomi sepekan {dt_info['year_str']}",
        f"top news aksi korporasi dividen emiten BEI sepekan {dt_info['year_str']}",
    ]

    raw_weekly_context = []
    if mncs_context:
        raw_weekly_context.append(mncs_context)

    seen_urls = set()
    for q in weekly_queries:
        try:
            res = tavily.search(
                query=q,
                topic="news",
                days=7,
                search_depth="advanced",
                max_results=5,
                include_domains=OFFICIAL_FINANCE_DOMAINS,
            )
            for item in res.get("results", []):
                url = item.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    pub = item.get("published_date", dt_info["iso_today"])
                    raw_weekly_context.append(
                        f"- [{pub}] {item.get('title', '')}: {item.get('content', '')} (Sumber: {url})"
                    )
        except Exception as e:
            print(f"[WARN] Gagal search Weekly Market Outlook '{q}': {e}")

    context_text = (
        "\n".join(raw_weekly_context)
        if raw_weekly_context
        else "Ringkasan perdagangan sepekan belum tersedia dari hasil pencarian eksternal saat ini."
    )

    system_prompt = (
        "Kamu adalah Analis Riset Pasar Modal dari GISBEI UPI YPTK Padang. "
        "Susun Weekly Market Outlook sepekan secara langsung dari data pencarian pasar sepekan terakhir "
        "(tanpa bergantung pada arsip database internal), bergaya naratif riset sekuritas institusional."
    )

    prompt = f"""
    Berdasarkan data dan rangkuman berita pasar sepekan terakhir ({dt_info['id_week_ago']} – {dt_info['id_today']}) berikut:

    {context_text}

    Susun **WEEKLY MARKET OUTLOOK — PEKAN {dt_info['upper_today']}** dengan struktur wajib berikut:

    **Global Market Summary (Sepekan)**
    (1 paragraf naratif merangkum tren mingguan bursa global AS & Asia, rilis data makro global, dan pergerakan komoditas utama selama sepekan.)

    **Domestik & IHSG Highlight (Sepekan)**
    (2 paragraf naratif merangkum kinerja IHSG selama sepekan perdagangan, akumulasi aliran dana asing, pergerakan nilai tukar Rupiah, serta level support-resistance penting dan katalis utama untuk pekan depan.)

    **Top Corporate & Policy Highlights**
    (1-2 paragraf naratif merangkum aksi korporasi, rilis kinerja emiten, dan kebijakan ekonomi paling berdampak selama sepekan.)

    ⚠️ *Disclaimer: ON — Disusun untuk tujuan edukasi & literasi pasar modal oleh GISBEI UPI YPTK Padang (Referensi: MNC Morning Navigator & Portal Resmi Pasar Modal).*
    """
    return ask_llm(prompt, system_prompt=system_prompt)


# ==============================================================================
# 2. BREAKING NEWS OTOMATIS (3 JAM, 6-12 JAM) & MANUAL (/berita_harian 24 JAM)
# ==============================================================================

def _filter_and_format_news(candidates: list, impact_focus: str = "big money maupun ritel") -> list:
    if not candidates:
        return []

    daftar_kandidat = "\n\n".join([
        f"ID: {i}\nWaktu Terbit: {c.get('published_date', 'Terbaru')}\nJudul: {c['title']}\nSumber: {c['portal']} ({c['url']})\nCuplikan: {c['snippet'][:350]}"
        for i, c in enumerate(candidates)
    ])

    system_prompt = (
        "Kamu adalah Kepala Redaksi & Analis Riset Pasar Modal GISBEI UPI YPTK Padang. "
        "Tugasmu menyaring berita pasar modal secara ketat. "
        "TOLAK berita receh, klikbait, opini ringan, atau berita kecil yang tidak menggerakkan pasar. "
        f"LOLOSKAN HANYA berita penting dan berdampak pada {impact_focus} (pergerakan IHSG, aliran dana asing, "
        "suku bunga BI/The Fed, kurs Rupiah, dividen, IPO, akuisisi/merger, suspensi/delisting BEI, atau kinerja emiten). "
        "Jika TIDAK ADA berita yang memenuhi kriteria penting tersebut, kembalikan list kosong []. "
        "Keluarkan HANYA JSON valid."
    )

    prompt = f"""
    Evaluasi kandidat berita berikut dan saring hanya yang benar-benar penting & berdampak:

    {daftar_kandidat}

    Format JSON wajib:
    {{
      "selected_news": [
        {{
          "id": 0,
          "judul": "Judul berita bersih, formal, dan profesional (tanpa nama portal di akhir)",
          "kategori": "Makroekonomi / Pergerakan IHSG / Corporate Action / Pengumuman IDX / Kinerja Emiten",
          "emiten_atau_fokus": "Kode emiten (misal: BBCA, ADRO) atau fokus makro (misal: IHSG / BI Rate / Rupiah)",
          "uraian": "Uraian pengantar 2 kalimat yang menjelaskan konteks peristiwa secara objektif dan akurat sesuai sumber.",
          "poin_penting": [
            "Poin fakta/angka utama pertama beserta penjelasannya (1-2 kalimat).",
            "Poin mekanisme/jadwal/katalis kedua beserta penjelasannya (1-2 kalimat).",
            "Poin dampak bagi investor institusi (big money) maupun ritel (1-2 kalimat)."
          ]
        }}
      ]
    }}
    """
    try:
        parsed = extract_json_from_llm(prompt, system_prompt=system_prompt)
        selected = parsed.get("selected_news", [])
        results = []
        for item in selected:
            idx = item.get("id")
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                orig = candidates[idx]
                item["url"] = orig["url"]
                item["portal"] = orig["portal"]
                item["published_date"] = orig.get("published_date", "Terbaru")
                results.append(item)
        return results
    except Exception as e:
        print(f"[WARN] Gagal memformat berita terkurasi: {e}")
        return []


def _scan_news_candidates(days_param: int, strict_hours: int, max_fallback_hours: int) -> list:
    init_db()
    dt_info = get_realtime_date_tokens()

    queries = [
        f"IHSG bursa saham asing net buy sell {dt_info['id_today']} {dt_info['id_yesterday']}",
        f"Bank Indonesia suku bunga Rupiah inflasi makroekonomi {dt_info['id_today']} {dt_info['id_yesterday']}",
        f"keterbukaan informasi dividen RUPS IPO akuisisi suspensi BEI {dt_info['id_today']} {dt_info['id_yesterday']}",
        f"laba bersih pendapatan kinerja emiten saham BEI {dt_info['id_today']} {dt_info['id_yesterday']}",
    ]

    raw_candidates = []
    seen_urls = set()
    seen_titles = set()

    for q in queries:
        try:
            res = tavily.search(
                query=q,
                topic="news",
                days=days_param,
                search_depth="basic",
                max_results=5,
                include_domains=OFFICIAL_FINANCE_DOMAINS,
            )
            for item in res.get("results", []):
                url = item.get("url", "").strip()
                title = item.get("title", "").strip()
                norm_title = re.sub(r"\W+", "", title.lower())[:50]

                if not url or url in seen_urls or norm_title in seen_titles:
                    continue
                if url.rstrip("/").endswith(("/market", "/pengumuman", "/suspensi", "/aksi-korporasi", "/investasi", "/news")):
                    continue

                if not is_within_hours(item, strict_hours=strict_hours, max_fallback_hours=max_fallback_hours):
                    continue

                if not is_news_processed(url):
                    seen_urls.add(url)
                    seen_titles.add(norm_title)

                    raw_candidates.append({
                        "title": title,
                        "url": url,
                        "portal": detect_source_portal(url),
                        "snippet": item.get("content", ""),
                        "published_date": item.get("published_date", dt_info["iso_today"]),
                    })
        except Exception as e:
            print(f"[WARN] Gagal memindai query '{q}': {e}")

    for c in raw_candidates:
        mark_news_processed(c["url"], c["title"])

    return raw_candidates


def fetch_realtime_breaking_news() -> list:
    candidates = _scan_news_candidates(days_param=1, strict_hours=6, max_fallback_hours=12)
    return _filter_and_format_news(candidates, impact_focus="investor big money maupun ritel")


def fetch_24h_quality_news() -> list:
    candidates = _scan_news_candidates(days_param=1, strict_hours=24, max_fallback_hours=24)
    return _filter_and_format_news(candidates, impact_focus="top news, pembagian dividen, aksi korporasi, dan pasar modal")


# ==============================================================================
# 3. KONSEPTOR IDE KONTEN (#rangkuman-konten) — ANTI DUPLIKASI
# ==============================================================================

def generate_content_ideas_from_news(curated_news: list) -> str:
    if not curated_news:
        return ""

    ringkasan_bahan = "\n\n---\n\n".join([
        f"Judul: {n['judul']}\nFokus: {n.get('emiten_atau_fokus', '-')}\nUraian: {n['uraian']}\n"
        f"Poin: {' | '.join(n.get('poin_penting', []))}\nSumber: {n['portal']} ({n['url']})"
        for n in curated_news
    ])

    system_prompt = (
        "Kamu adalah Content Strategist GISBEI UPI YPTK Padang. "
        "Pilih berita yang paling cocok dan menarik untuk dijadikan postingan carousel edukasi/informasi pasar modal. "
        "Jika tidak ada yang cocok, kembalikan has_idea: false. Keluarkan HANYA JSON valid."
    )

    prompt = f"""
    Tinjau berita terkurasi berikut dan pilih yang paling cocok untuk diposting ke channel rangkuman konten:

    {ringkasan_bahan}

    Format JSON wajib:
    {{
      "has_idea": true,
      "judul_topik": "Judul topik utama",
      "sumber_terpercaya": "Nama Portal Resmi & Link URL",
      "slides_outline": [
        {{
          "slide": "Slide 1",
          "bagian": "Cover",
          "judul": "Judul Cover Singkat & Menarik",
          "isi": "Subjudul singkat 1 kalimat pengantar berita"
        }},
        {{
          "slide": "Slide 2",
          "bagian": "Isi Poin News",
          "judul": "Poin Utama Berita",
          "isi": "Ringkasan fakta, angka, atau jadwal penting dari berita"
        }},
        {{
          "slide": "Slide 3",
          "bagian": "Penjelasan",
          "judul": "Penjelasan & Dampak Pasar",
          "isi": "Penjelasan latar belakang peristiwa serta dampaknya bagi investor"
        }}
      ],
      "draf_caption": "Draf caption singkat dengan Disclaimer: ON dan Sumber"
    }}
    """
    try:
        res = extract_json_from_llm(prompt, system_prompt=system_prompt)
        if not res.get("has_idea"):
            return ""

        lines = [
            "📌 **REKOMENDASI BERITA UNTUK POSTINGAN (#rangkuman-konten)**",
            f"**Topik:** {res.get('judul_topik', '')}",
            f"**Sumber Terpercaya:** {res.get('sumber_terpercaya', '')}\n",
        ]
        for s in res.get("slides_outline", []):
            lines.append(
                f"• **{s.get('slide')} — {s.get('bagian')}:** *\"{s.get('judul')}\"*\n  {s.get('isi')}"
            )

        if res.get("draf_caption"):
            lines.append(f"\n📝 **Draf Caption:**\n{res['draf_caption']}")

        return "\n".join(lines)
    except Exception as e:
        print(f"[WARN] Gagal menyusun rangkuman konten berita: {e}")
        return ""


def _pick_next_education_topic(past_titles: list[str]) -> str:
    past_joined = " ".join(past_titles).lower()
    for topic in CURATED_EDUCATION_TOPICS:
        keywords = [w for w in re.findall(r"[a-zA-Z]{4,}", topic.lower()) if w not in {"apa", "itu", "dan", "untuk", "yang", "dari", "pada"}]
        already_covered = sum(1 for kw in keywords[:4] if kw in past_joined)
        if already_covered < 2:
            return topic
    return CURATED_EDUCATION_TOPICS[len(past_titles) % len(CURATED_EDUCATION_TOPICS)]


def generate_unique_education_idea() -> str:
    dt_info = get_realtime_date_tokens()
    past_titles = get_past_education_ideas(limit=40)
    past_list_text = "\n".join([f"- {t}" for t in past_titles]) if past_titles else "- (Belum ada ide edukasi sebelumnya)"
    target_topic = _pick_next_education_topic(past_titles)

    system_prompt = (
        "Kamu adalah Edukator Senior Pasar Modal Syariah dari GISBEI UPI YPTK Padang. "
        "Buat 1 ide postingan edukasi pasar modal yang tajam, substansial, mudah dipahami mahasiswa/investor ritel, "
        "dan TIDAK BOLEH mengulang topik atau sudut pandang yang sudah pernah dibuat sebelumnya. "
        "Keluarkan HANYA JSON valid."
    )

    prompt = f"""
    Buatkan 1 konsep ide postingan edukasi pasar modal mendalam untuk tanggal {dt_info['id_today']}.
    Fokus Topik Utama: **{target_topic}**

    DAFTAR TOPIK/JUDUL YANG SUDAH PERNAH DIBUAT (DILARANG KERAS MENGULANG INI):
    {past_list_text}

    Format JSON wajib:
    {{
      "topic_key": "kode_topik_singkat",
      "judul_ide": "Judul Ide Edukasi Jelas, Menarik & Substansial",
      "sumber_referensi": "Sumber literasi resmi (IDX / OJK / Modul SPM BEI / MSCI Methodology)",
      "slides": [
        {{
          "slide": "Slide 1",
          "bagian": "Cover",
          "judul": "Judul Cover Kuat & Hook Edukatif",
          "isi": "Subjudul singkat 1 kalimat yang memancing rasa ingin tahu"
        }},
        {{
          "slide": "Slide 2",
          "bagian": "Isi Poin Edukasi",
          "judul": "Poin-Poin Kunci & Perbedaan Utama",
          "isi": "Ringkasan poin terstruktur (perbandingan, mekanisme, atau faktor kunci) yang jelas dan padat"
        }},
        {{
          "slide": "Slide 3",
          "bagian": "Penjelasan",
          "judul": "Penjelasan Mendalam & Dampak di Pasar",
          "isi": "Uraian penjelasan logika mekanisme, dampak ke harga/likuiditas/investor, serta contoh nyata"
        }},
        {{
          "slide": "Slide 4",
          "bagian": "Catatan Praktis",
          "judul": "Hal yang Wajib Diperhatikan Investor",
          "isi": "Langkah praktis / risiko yang perlu dicermati sebelum mengambil keputusan"
        }}
      ]
    }}
    """
    try:
        res = extract_json_from_llm(prompt, system_prompt=system_prompt)
        judul_ide = res.get("judul_ide", target_topic).strip()
        topic_key = res.get("topic_key", "edukasi_pasar_modal").strip()
        sumber = res.get("sumber_referensi", "IDX / OJK / Modul SPM BEI").strip()

        lines = [
            "📚 **IDE EDUKASI POSTINGAN PASAR MODAL — GISBEI UPI YPTK PADANG**",
            f"**Judul Ide:** {judul_ide}",
            f"**Sumber Referensi:** {sumber}\n",
        ]
        for s in res.get("slides", []):
            lines.append(
                f"• **{s.get('slide')} — {s.get('bagian')}:** *\"{s.get('judul')}\"*\n  {s.get('isi')}"
            )
        lines.append("\n⚠️ *Disclaimer: ON*")
        formatted = "\n".join(lines)

        save_education_idea(topic_key, judul_ide, formatted)
        return formatted
    except Exception as e:
        print(f"[WARN] Gagal membuat ide edukasi unik: {e}")
        fallback_title = f"{target_topic.split('(')[0].strip()} ({dt_info['id_today']})"
        save_education_idea("edukasi_fallback", fallback_title, fallback_title)
        return f"📚 **IDE EDUKASI — {fallback_title}**\n• **Slide 1 — Cover:** *\"{fallback_title}\"*\n⚠️ *Disclaimer: ON*"