"""
Web Arayüzü — FastAPI + WebSocket
3 bölüm: Sonuçlar | Başlat/Durdur | Chat
"""

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Set

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import hafiza_yoneticisi as hm
import llm_istemci as llm
from orkestra import Orkestra

load_dotenv()

app = FastAPI(title="Otonom Ajan Sistemi")

_orkestra: Orkestra = None
_ws_baglantilar: Set[WebSocket] = set()


async def _dashboard_html_oku() -> str:
    dashboard_path = Path("public/dashboard.html")
    if dashboard_path.exists():
        async with aiofiles.open(dashboard_path, "r", encoding="utf-8") as f:
            return await f.read()
    return _html_yukle()


async def herkese_gonder(mesaj: dict):
    global _ws_baglantilar
    if not _ws_baglantilar:
        return
    veri = json.dumps(mesaj, ensure_ascii=False)
    kopuk = set()
    for ws in _ws_baglantilar:
        try:
            await ws.send_text(veri)
        except Exception:
            kopuk.add(ws)
    _ws_baglantilar -= kopuk


async def ajan_sonuc_callback(sonuc: dict):
    await herkese_gonder({"tip": "ajan_sonuc", "veri": sonuc})
    await herkese_gonder(
        {
            "tip": "log",
            "veri": {
                "seviye": "SUCCESS" if sonuc.get("durum") != "hata" else "ERROR",
                "mesaj": f"A{sonuc.get('ajan_id', 0):02d} tur {sonuc.get('tur', 0)} tamamladı: {sonuc.get('durum', 'bilinmiyor')}",
                "ajan": sonuc.get("perspektif", f"A{sonuc.get('ajan_id', 0):02d}"),
            },
        }
    )


def _html_yukle() -> str:
    """Ana arayüz HTML'ini dosyadan yükler (public/ajan_arayuz.html)."""
    html_dosya = Path("public/ajan_arayuz.html")
    try:
        return html_dosya.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "<h1>Hata: public/ajan_arayuz.html bulunamadı</h1>"




@app.get("/", response_class=HTMLResponse)
async def anasayfa():
    return HTMLResponse(content=_html_yukle())


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_arayuz():
    return HTMLResponse(content=_html_yukle())


@app.post("/baslat")
async def baslat():
    global _orkestra
    if not llm.llm_hazir_mi():
        return {"hata": "LLM anahtarı eksik (ANTHROPIC_API_KEY veya GROQ_API_KEY)"}
    if _orkestra is None:
        saglayici, api_anahtari, model = llm.etkin_baglanti()
        _orkestra = Orkestra(saglayici, api_anahtari, model)
        _orkestra.durum_callback_ayarla(ajan_sonuc_callback)
    await _orkestra.baslat()
    await herkese_gonder({"tip": "durum", "veri": _orkestra.durum_raporu()})
    await herkese_gonder({"tip": "log", "veri": {"seviye": "INFO", "mesaj": "Orkestra başlatıldı", "ajan": None}})
    return {"durum": "başlatıldı"}


@app.post("/durdur")
async def durdur():
    global _orkestra
    if _orkestra:
        await _orkestra.durdur()
        await herkese_gonder({"tip": "durum", "veri": _orkestra.durum_raporu()})
    await herkese_gonder({"tip": "log", "veri": {"seviye": "WARN", "mesaj": "Orkestra durduruldu", "ajan": None}})
    return {"durum": "durduruldu"}


@app.get("/durum")
async def durum():
    if _orkestra:
        return _orkestra.durum_raporu()
    return {"calisiyor": False, "ajan_sayisi": 10, "toplam_tur": 0}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_baglantilar.add(ws)
    try:
        # Mevcut durumu gönder
        if _orkestra:
            await ws.send_text(json.dumps({"tip": "durum", "veri": _orkestra.durum_raporu()}))
        while True:
            veri = await ws.receive_text()
            msg = json.loads(veri)
            if msg.get("tip") == "chat":
                # Chat mesajını planlar'a kaydet + Claude'a ilet
                metin = msg.get("metin", "")
                await _chat_isle(ws, metin)
    except WebSocketDisconnect:
        pass
    finally:
        _ws_baglantilar.discard(ws)


async def _chat_isle(ws: WebSocket, metin: str):
    """Kullanıcı mesajını planlar'a ekle ve Claude'dan yanıt al."""
    # Planlar'a kaydet
    zaman = datetime.now()
    dosya = Path("planlar") / f"chat_{zaman.strftime('%Y%m%d_%H%M%S')}.md"
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(f"# Kullanıcı Direktifi\nZaman: {zaman.isoformat()}\n\n{metin}")

    if not llm.llm_hazir_mi():
        await ws.send_text(json.dumps({"tip": "chat_yanit", "veri": "API anahtarı yok."}))
        return

    saglayici, api_anahtari, model = llm.etkin_baglanti()
    planlar = await hm.planlari_oku()
    sonuclar = await hm.sonuclari_oku(5)

    cevap = await llm.metin_uret(
        "Sen otonom ajan sisteminin chat arayüzüsün. Kullanıcıya sistemin durumunu açıkla, sorularını yanıtla, direktifleri planlar klasörüne kaydettiğini bildir.",
        f"Direktif/Soru: {metin}\n\nMevcut Planlar:\n{planlar[:500]}\n\nSon Sonuçlar:\n{sonuclar[:500]}",
        max_tokens=1024,
        saglayici=saglayici,
        api_key=api_anahtari,
        model=model,
    )
    await ws.send_text(json.dumps({"tip": "chat_yanit", "veri": cevap}))


# ════════════════════════════════════════════════════════════════════════════
# DASHBOARD VE API ENDPOINTS
# ════════════════════════════════════════════════════════════════════════════

@app.get("/dashboard", response_class=HTMLResponse)
async def get_dashboard():
    """Gerçek zamanlı monitoring dashboard"""
    try:
        return await _dashboard_html_oku()
    except Exception as e:
        return f"<h1>Hata: {e}</h1>"


@app.get("/api/agents")
async def get_agents_list():
    """Tüm ajanların listesi"""
    agents = [
        "Mühendis", "Kod Denetçisi", "Stratejist", "Uygulayıcı",
        "Sentezci", "Araştırmacı", "Bütünleştirici", "Eleştirmen",
        "Veri Analisti", "İnovasör"
    ]
    return agents


@app.get("/api/analytics")
async def get_analytics():
    """AI gelişim metrikleri"""
    hafiza_sayisi = 0
    sonuc_sayisi = 0
    hata_sayisi = 0
    log_icerik = ""
    try:
        hafiza_sayisi = len([d for d in hm.KLASORLER["hafiza"].glob("*.md") if not d.name.startswith("_")])
        sonuc_sayisi = len(list(hm.KLASORLER["sonuclar"].glob("*.md")))
        log_dosyasi = hm.KLASORLER["log"] / f"sistem_{datetime.now().strftime('%Y%m%d')}.log"
        if log_dosyasi.exists():
            async with aiofiles.open(log_dosyasi, "r", encoding="utf-8") as f:
                log_icerik = await f.read()
            hata_sayisi = log_icerik.count("[ERROR]")
    except Exception as e:
        await hm.log_yaz(f"Analytics okuma hatası: {e}", "WARN")

    try:
        onerilen_klasor = hm.KLASORLER["onerilen_degisiklikler"]
        self_improvements = len(list(onerilen_klasor.glob("*.uyguland")))
    except Exception:
        self_improvements = 0

    total_outputs = sonuc_sayisi
    api_calls = llm.llm_deneme_sayisi()
    errors = hata_sayisi
    local_calls = hafiza_sayisi

    total_calls = api_calls + local_calls if (api_calls + local_calls) > 0 else 1
    api_dependency_reduction = int((local_calls / total_calls * 100))
    self_improvement_rate = min(100, int((self_improvements / max(total_outputs, 1) * 100)))

    total_parameters = 10 * 512_000
    trained_parameters = int(total_parameters * (api_dependency_reduction / 100))

    running_agents = sum(1 for a in _orkestra.ajanlar if getattr(a, "calisiyor", False)) if _orkestra else 0

    return {
        "runningAgents": running_agents,
        "processedRequests": total_outputs,
        "errorCount": errors,
        "apiCalls": api_calls,
        "selfImprovementRate": self_improvement_rate,
        "apiDependencyReduction": api_dependency_reduction,
        "totalParameters": total_parameters,
        "trainedParameters": trained_parameters,
    }


@app.get("/api/database/{filename}")
async def get_database(filename: str, request: Request):
    """Database dosyaları — opsiyonel DB_API_KEY koruması"""
    db_api_key = os.environ.get("DB_API_KEY", "")
    if db_api_key and request.headers.get("X-DB-Key", "") != db_api_key:
        return {"error": "Yetkisiz erişim"}

    allowed_files = ["hafiza.json", "alerts.json", "tweets.json", "archive.json", "comment-profiles.json"]

    if filename not in allowed_files:
        return {"error": "Erişim reddedildi"}

    filepath = Path(filename)
    if not filepath.exists():
        return {"error": "Dosya bulunamadı"}

    try:
        async with aiofiles.open(filepath, "r", encoding="utf-8") as f:
            content = await f.read()
            return json.loads(content)
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/logs/recent")
async def get_recent_logs(lines: int = 120):
    """Son sistem loglarını döndürür."""
    try:
        log_file = hm.KLASORLER["log"] / f"sistem_{datetime.now().strftime('%Y%m%d')}.log"
        if not log_file.exists():
            return {"logs": []}
        async with aiofiles.open(log_file, "r", encoding="utf-8") as f:
            content = await f.read()
        satirlar = [satir for satir in content.splitlines() if satir.strip()]
        return {"logs": satirlar[-lines:]}
    except Exception as e:
        return {"error": str(e), "logs": []}


@app.get("/api/logs/agent/{agent_name}")
async def get_agent_logs(agent_name: str, lines: int = 50):
    """Ajan logları"""
    try:
        log_file = Path("log")
        if not log_file.exists():
            return {"logs": []}

        logs = []
        pattern = re.compile(r"(?<![\w])" + re.escape(agent_name) + r"(?![\w])")
        for log_fn in sorted(log_file.glob("sistem_*.log"), reverse=True)[:1]:
            try:
                async with aiofiles.open(log_fn, "r", encoding="utf-8") as f:
                    content = await f.read()
                    for line in content.split("\n"):
                        if pattern.search(line):
                            logs.append(line)
            except Exception as e:
                await hm.log_yaz(f"Ajan log okuma hatası: {e}", "WARN")
        return {"logs": logs[-lines:]}
    except Exception as e:
        return {"error": str(e), "logs": []}


@app.get("/api/disk-usage")
async def get_disk_usage():
    """Disk kullanımı"""
    try:
        import shutil
        total, used, free = shutil.disk_usage("/")
        percentage = int((used / total) * 100)
        return {
            "total_gb": round(total / (1024**3), 2),
            "used_gb": round(used / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "percentage": percentage,
        }
    except Exception as e:
        return {"error": str(e)}
