import os
import json
import requests
import litellm
from dotenv import load_dotenv
from litellm import completion

load_dotenv()
litellm.drop_params = True  # Otomatis buang parameter yang tidak didukung model


def get_active_gemini_models() -> list:
    """Mengambil otomatis daftar model Gemini Flash aktif dari akun AI Studio."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return ["gemini/gemini-3.8-flash"]
    try:
        res = requests.get(
            f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
            timeout=5
        )
        models = res.json().get("models", [])
        flash_models = []
        for m in models:
            name = m.get("name", "").replace("models/", "")
            methods = m.get("supportedGenerationMethods", [])
            if "flash" in name.lower() and "generateContent" in methods and "tts" not in name.lower() and "image" not in name.lower():
                flash_models.append(f"gemini/{name}")
        if flash_models:
            # Urutkan versi terbaru di atas
            flash_models.sort(reverse=True)
            return flash_models[:2]
    except Exception:
        pass
    return ["gemini/gemini-3.8-flash"]


def get_active_groq_model() -> str:
    """Mengambil otomatis model aktif pertama dari API Groq."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return "groq/meta-llama/llama-4-scout-17b-16e-instruct"
    try:
        res = requests.get(
            "https://api.groq.com/openai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=5
        )
        models = [m["id"] for m in res.json().get("data", []) if "whisper" not in m["id"] and "guard" not in m["id"]]
        for preferred in ["qwen", "llama-4", "llama-3.3", "llama"]:
            for m in models:
                if preferred in m.lower():
                    return f"groq/{m}"
        if models:
            return f"groq/{models[0]}"
    except Exception:
        pass
    return "groq/meta-llama/llama-4-scout-17b-16e-instruct"


FALLBACK_MODELS = get_active_gemini_models() + [get_active_groq_model()]


def ask_llm(prompt: str, system_prompt: str = "Kamu adalah asisten riset dan akademik yang cerdas dan ringkas.", json_mode: bool = False) -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]

    last_error = None

    for model_name in FALLBACK_MODELS:
        try:
            kwargs = {
                "model": model_name,
                "messages": messages,
                "timeout": 20,  # Maksimal tunggu 20 detik, jika lambat langsung pindah ke cadangan!
            }
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}

            response = completion(**kwargs)
            content = response.choices[0].message.content.strip()
            print(f"[INFO] Berhasil dijawab oleh: {model_name}")
            return content

        except Exception as e:
            print(f"[WARN] Model {model_name} dilewati ({type(e).__name__}). Beralih ke cadangan...")
            last_error = e
            continue

    raise RuntimeError(f"Semua provider LLM gagal merespons. Error terakhir: {last_error}")


def extract_json_from_llm(prompt: str, system_prompt: str) -> dict:
    raw_text = ask_llm(prompt, system_prompt=system_prompt, json_mode=True)
    cleaned = raw_text.replace("```json", "").replace("```", "").strip()
    return json.loads(cleaned)


if __name__ == "__main__":
    print("Daftar model aktif:", FALLBACK_MODELS)
    hasil = ask_llm("Sebutkan 3 rasio keuangan utama untuk menilai kesehatan emiten di BEI dalam 3 poin singkat.")
    print("\nHasil Respons:\n", hasil)