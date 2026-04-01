#!/usr/bin/env python3
"""
Otonom Ajan Sistemi — Giriş Noktası
Çalıştır: python main.py
Sonra: http://localhost:8000
"""

import os
import uvicorn
from dotenv import load_dotenv
import llm_istemci as llm

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    anthropic_var = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
    groq_var = bool(os.getenv("GROQ_API_KEY", "").strip())
    secili = "YOK"
    try:
        secili = llm.etkin_baglanti()[0].upper()
    except Exception:
        pass

    print(f"""
╔═══════════════════════════════════════════╗
║    OTONOM AJAN SİSTEMİ — BAŞLANIYOR      ║
╠═══════════════════════════════════════════╣
║  Adres:  http://localhost:{port}           ║
║  Ajanlar: 10 paralel                      ║
║  Anthropic: {"✓ SET" if anthropic_var else "✗ EKSİK"}                         ║
║  Groq:      {"✓ SET" if groq_var else "✗ EKSİK"}                         ║
║  Seçili LLM: {secili}                           ║
╚═══════════════════════════════════════════╝
""")
    if not (anthropic_var or groq_var):
        print("⚠ UYARI: LLM anahtarı bulunamadı!")
        print("  .env dosyasına ANTHROPIC_API_KEY=... veya GROQ_API_KEY=... ekle\n")

    uvicorn.run("web_arayuzu:app", host="0.0.0.0", port=port, reload=False)
