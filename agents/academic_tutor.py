from agents.llm_router import ask_llm


def run_tutor_mode(query: str, file_content: str = "", chat_history: str = "") -> str:
    """
    Mode Asisten Kuliah & Diskusi Bebas di #kuliah-assistant:
    Menjelaskan konsep, membantu logika algoritma, dan menulis/memperbaiki kode
    secara natural, runtut, dan kontekstual.
    """
    system_prompt = (
        "Kamu adalah Asisten Akademik & Mentor Teknik Informatika yang cerdas, sabar, dan komunikatif. "
        "Jawab langsung ke inti persoalan dengan bahasa Indonesia yang natural dan jelas. "
        "Jika user meminta penjelasan lebih detail, metode alternatif, atau perbaikan kode berdasarkan "
        "percakapan sebelumnya, ikuti instruksi tersebut secara menyeluruh dan terstruktur."
    )

    sections = []
    if chat_history:
        sections.append(f"Riwayat Percakapan Sebelumnya di Channel:\n{chat_history}")
    if file_content:
        sections.append(f"Lampiran File Kode / Dokumen:\n{file_content}")
    sections.append(f"Pesan Mahasiswa Saat Ini:\n{query}")

    prompt = "\n\n---\n\n".join(sections)
    return ask_llm(prompt, system_prompt=system_prompt)


def run_project_mode(query: str, file_content: str = "") -> str:
    """
    Mode Pembuatan Kode & Debugging Terstruktur via Slash Command /proyek.
    """
    system_prompt = (
        "Kamu adalah Senior Software Engineer & Asisten Praktikum Teknik Informatika. "
        "Berikan solusi kode yang bersih, modern, bebas bug, dan siap dijalankan."
    )

    prompt = f"""
    Tugas / Permasalahan Pemrograman:
    {query}

    {f"Kode Sumber Terlampir:{chr(10)}{file_content}" if file_content else ""}

    Susun jawaban dengan format:
    1. **Analisis Kebutuhan / Akar Masalah**
    2. **Kode Lengkap Siap Pakai**
    3. **Penjelasan Alur & Cara Menjalankan**
    """
    return ask_llm(prompt, system_prompt=system_prompt)