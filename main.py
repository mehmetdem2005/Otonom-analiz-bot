#!/usr/bin/env python3
"""
Otonom Ajan Sistemi — Giriş Noktası
Çalıştır: python main.py
Sonra: http://localhost:8000
"""

import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("PORT", 8000))
    print(f"""
╔═══════════════════════════════════════════╗
║    OTONOM AJAN SİSTEMİ — BAŞLANIYOR      ║
╠═══════════════════════════════════════════╣
║  Adres:  http://localhost:{port}           ║
║  Ajanlar: 10 paralel                      ║
║  API Key: {"✓ SET" if os.getenv("ANTHROPIC_API_KEY") else "✗ EKSİK (.env dosyasına ekle)"}                   ║
╚═══════════════════════════════════════════╝
""")
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("⚠ UYARI: ANTHROPIC_API_KEY bulunamadı!")
        print("  .env dosyası oluştur ve ANTHROPIC_API_KEY=sk-... ekle\n")

    uvicorn.run("web_arayuzu:app", host="0.0.0.0", port=port, reload=False)
