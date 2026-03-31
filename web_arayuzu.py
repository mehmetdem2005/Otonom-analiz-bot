"""
Web Arayüzü — FastAPI + WebSocket
3 bölüm: Sonuçlar | Başlat/Durdur | Chat
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Set

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import hafiza_yoneticisi as hm
from orkestra import Orkestra

load_dotenv()
API_ANAHTARI = os.getenv("ANTHROPIC_API_KEY", "")

app = FastAPI(title="Otonom Ajan Sistemi")

_orkestra: Orkestra = None
_ws_baglantilar: Set[WebSocket] = set()


async def herkese_gonder(mesaj: dict):
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


HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Otonom Ajan Sistemi</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Courier New', monospace; background: #0a0a0f; color: #00ff88; min-height: 100vh; }
#header { background: #111; border-bottom: 1px solid #00ff88; padding: 12px 20px; display: flex; align-items: center; gap: 20px; }
#header h1 { font-size: 1.1em; letter-spacing: 2px; color: #00ff88; }
#status-dot { width: 12px; height: 12px; border-radius: 50%; background: #333; transition: background 0.3s; }
#status-dot.aktif { background: #00ff88; box-shadow: 0 0 8px #00ff88; animation: pulse 1.5s infinite; }
@keyframes pulse { 0%,100% { opacity:1 } 50% { opacity:0.4 } }
#layout { display: grid; grid-template-columns: 1fr 320px; height: calc(100vh - 50px); }
#sol { display: flex; flex-direction: column; border-right: 1px solid #222; }
#sag { display: flex; flex-direction: column; }

/* Kontrol */
#kontrol { padding: 16px; background: #111; border-bottom: 1px solid #222; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
button { background: #0a1a0a; border: 1px solid #00ff88; color: #00ff88; padding: 8px 20px; cursor: pointer; font-family: inherit; font-size: 0.85em; letter-spacing: 1px; transition: all 0.2s; }
button:hover { background: #00ff88; color: #000; }
button.dur { border-color: #ff4444; color: #ff4444; }
button.dur:hover { background: #ff4444; color: #000; }
#tur-sayac { font-size: 0.8em; color: #888; }

/* Ajan Durumları */
#ajan-grid { padding: 12px; display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; background: #0d0d0d; border-bottom: 1px solid #222; }
.ajan-kart { background: #111; border: 1px solid #1a1a1a; padding: 6px; border-radius: 3px; font-size: 0.7em; text-align: center; }
.ajan-kart .ajan-id { color: #00ff88; font-weight: bold; }
.ajan-kart .ajan-durum { color: #666; font-size: 0.85em; }
.ajan-kart.aktif { border-color: #00ff88; }
.ajan-kart.dusunuyor { border-color: #ffaa00; }
.ajan-kart.hata { border-color: #ff4444; }

/* Sonuçlar */
#sonuclar-baslik { padding: 10px 16px; background: #0d0d0d; color: #888; font-size: 0.8em; letter-spacing: 1px; border-bottom: 1px solid #1a1a1a; }
#sonuclar { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
.sonuc-kart { background: #0d0f0d; border: 1px solid #1a2a1a; border-left: 3px solid #00ff88; padding: 10px; font-size: 0.78em; border-radius: 2px; }
.sonuc-kart .meta { color: #556655; margin-bottom: 6px; font-size: 0.9em; }
.sonuc-kart .ozet { color: #aaffaa; line-height: 1.5; white-space: pre-wrap; }
.sonuc-kart.duplikat { border-left-color: #333; opacity: 0.5; }

/* Chat */
#chat-baslik { padding: 10px 16px; background: #0d0d0d; color: #888; font-size: 0.8em; letter-spacing: 1px; border-bottom: 1px solid #1a1a1a; }
#chat-mesajlar { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }
.chat-mesaj { padding: 8px 10px; border-radius: 4px; font-size: 0.8em; line-height: 1.5; max-width: 90%; }
.chat-mesaj.kullanici { background: #0a1a0a; border: 1px solid #00aa44; align-self: flex-end; color: #ccffcc; }
.chat-mesaj.sistem { background: #111; border: 1px solid #333; align-self: flex-start; color: #aaaaaa; }
#chat-input-alani { padding: 10px; border-top: 1px solid #1a1a1a; display: flex; gap: 8px; }
#chat-input { flex: 1; background: #111; border: 1px solid #333; color: #00ff88; padding: 8px; font-family: inherit; font-size: 0.8em; resize: none; }
#chat-input:focus { outline: none; border-color: #00ff88; }
#chat-gonder { padding: 8px 14px; font-size: 0.8em; }
</style>
</head>
<body>
<div id="header">
  <div id="status-dot"></div>
  <h1>// OTONOM AJAN SİSTEMİ</h1>
  <span id="tur-sayac">Tur: 0</span>
  <span id="ajan-sayac" style="color:#556655;font-size:0.8em">Ajan: 10</span>
  <span id="disk-goster" style="color:#556655;font-size:0.8em">Disk: -%</span>
</div>

<div id="layout">
  <div id="sol">
    <div id="kontrol">
      <button id="btn-baslat" onclick="baslat()">▶ BAŞLAT</button>
      <button id="btn-durdur" class="dur" onclick="durdur()" style="display:none">■ DURDUR</button>
      <span style="color:#556655;font-size:0.75em">10 ajan paralel çalışıyor</span>
    </div>
    <div id="ajan-grid">
      <!-- JS ile doldurulur -->
    </div>
    <div id="sonuclar-baslik">CANLI ÇIKTILAR</div>
    <div id="sonuclar"></div>
  </div>

  <div id="sag">
    <div id="chat-baslik">CHAT MODU</div>
    <div id="chat-mesajlar">
      <div class="chat-mesaj sistem">Sistem hazır. Ajanları başlatmak için ▶ BAŞLAT'a bas.<br>Ya da bana bir şey sor.</div>
    </div>
    <div id="chat-input-alani">
      <textarea id="chat-input" placeholder="Sisteme bir yönerge veya soru yaz..." rows="3" onkeydown="enterGonder(event)"></textarea>
      <button id="chat-gonder" onclick="chatGonder()">→</button>
    </div>
  </div>
</div>

<script>
let ws = null;
let turSayac = 0;
const ajanlar = {};

function wsBaslat() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (e) => isleMessaj(JSON.parse(e.data));
  ws.onclose = () => setTimeout(wsBaslat, 2000);
  ws.onerror = () => ws.close();
}

function isleMessaj(msg) {
  if (msg.tip === 'ajan_sonuc') {
    const d = msg.veri;
    turSayac = d.toplam_tur || turSayac + 1;
    document.getElementById('tur-sayac').textContent = `Tur: ${turSayac}`;
    ajanGuncelle(d);
    sonucEkle(d);
  } else if (msg.tip === 'durum') {
    durumGuncelle(msg.veri);
  } else if (msg.tip === 'chat_yanit') {
    chatEkle('sistem', msg.veri);
  }
}

function ajanGuncelle(d) {
  ajanlar[d.ajan_id] = d;
  renderAjanGrid();
}

function renderAjanGrid() {
  const grid = document.getElementById('ajan-grid');
  grid.innerHTML = '';
  for (let i = 0; i < 10; i++) {
    const a = ajanlar[i] || { id: i, durum: 'bekliyor', tur: 0 };
    const sinif = a.durum === 'dusunuyor' ? 'dusunuyor' : a.durum === 'hata' ? 'hata' : a.durum === 'tamamlandi' ? 'aktif' : '';
    grid.innerHTML += `<div class="ajan-kart ${sinif}">
      <div class="ajan-id">A${String(i).padStart(2,'0')}</div>
      <div class="ajan-durum">${a.durum || 'bekliyor'}</div>
      <div style="color:#556655;font-size:0.8em">T${a.tur || 0}</div>
    </div>`;
  }
}

function sonucEkle(d) {
  const kutu = document.getElementById('sonuclar');
  const kart = document.createElement('div');
  kart.className = 'sonuc-kart' + (d.durum === 'duplikat_reddedildi' ? ' duplikat' : '');
  const perspKisa = d.perspektif ? d.perspektif.split(':')[0] : '?';
  kart.innerHTML = `<div class="meta">A${String(d.ajan_id).padStart(2,'0')} • ${perspKisa} • Tur ${d.tur} • ${d.durum}</div>
    <div class="ozet">${(d.icerik_ozet||'').substring(0,400)}</div>`;
  kutu.prepend(kart);
  // Max 100 kart tut
  while (kutu.children.length > 100) kutu.removeChild(kutu.lastChild);
}

function durumGuncelle(v) {
  const dot = document.getElementById('status-dot');
  const btnB = document.getElementById('btn-baslat');
  const btnD = document.getElementById('btn-durdur');
  if (v.calisiyor) {
    dot.className = 'aktif';
    btnB.style.display = 'none';
    btnD.style.display = 'inline-block';
  } else {
    dot.className = '';
    btnB.style.display = 'inline-block';
    btnD.style.display = 'none';
  }
  if (v.ajan_sayisi !== undefined) {
    document.getElementById('ajan-sayac').textContent = `Ajan: ${v.ajan_sayisi}`;
  }
  if (v.disk_doluluk !== undefined) {
    const disk = v.disk_doluluk;
    const renk = disk > 85 ? '#ff4444' : disk > 75 ? '#ffaa00' : '#556655';
    const el = document.getElementById('disk-goster');
    el.textContent = `Disk: %${disk}`;
    el.style.color = renk;
  }
}

function baslat() {
  fetch('/baslat', {method:'POST'}).then(r=>r.json()).then(d=>console.log(d));
}
function durdur() {
  fetch('/durdur', {method:'POST'}).then(r=>r.json()).then(d=>console.log(d));
}

function chatEkle(tip, metin) {
  const kutu = document.getElementById('chat-mesajlar');
  const el = document.createElement('div');
  el.className = `chat-mesaj ${tip}`;
  el.textContent = metin;
  kutu.appendChild(el);
  kutu.scrollTop = kutu.scrollHeight;
}

function chatGonder() {
  const inp = document.getElementById('chat-input');
  const metin = inp.value.trim();
  if (!metin) return;
  chatEkle('kullanici', metin);
  if (ws && ws.readyState === 1) {
    ws.send(JSON.stringify({tip:'chat', metin}));
  }
  inp.value = '';
}

function enterGonder(e) {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); chatGonder(); }
}

renderAjanGrid();
wsBaslat();
fetch('/durum').then(r=>r.json()).then(durumGuncelle);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def anasayfa():
    return HTML


@app.post("/baslat")
async def baslat():
    global _orkestra
    if not API_ANAHTARI:
        return {"hata": "ANTHROPIC_API_KEY env değişkeni ayarlanmamış"}
    if _orkestra is None:
        _orkestra = Orkestra(API_ANAHTARI)
        _orkestra.durum_callback_ayarla(ajan_sonuc_callback)
    await _orkestra.baslat()
    await herkese_gonder({"tip": "durum", "veri": _orkestra.durum_raporu()})
    return {"durum": "başlatıldı"}


@app.post("/durdur")
async def durdur():
    global _orkestra
    if _orkestra:
        await _orkestra.durdur()
        await herkese_gonder({"tip": "durum", "veri": _orkestra.durum_raporu()})
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
    import anthropic as ant

    # Planlar'a kaydet
    zaman = datetime.now()
    dosya = Path("planlar") / f"chat_{zaman.strftime('%Y%m%d_%H%M%S')}.md"
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(f"# Kullanıcı Direktifi\nZaman: {zaman.isoformat()}\n\n{metin}")

    if not API_ANAHTARI:
        await ws.send_text(json.dumps({"tip": "chat_yanit", "veri": "API anahtarı yok."}))
        return

    client = ant.AsyncAnthropic(api_key=API_ANAHTARI)
    planlar = await hm.planlari_oku()
    sonuclar = await hm.sonuclari_oku(5)

    yanit = await client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1024,
        system="Sen otonom ajan sisteminin chat arayüzüsün. Kullanıcıya sistemin durumunu açıkla, sorularını yanıtla, direktifleri planlar klasörüne kaydettiğini bildir.",
        messages=[
            {"role": "user", "content": f"Direktif/Soru: {metin}\n\nMevcut Planlar:\n{planlar[:500]}\n\nSon Sonuçlar:\n{sonuclar[:500]}"}
        ],
    )
    cevap = yanit.content[0].text
    await ws.send_text(json.dumps({"tip": "chat_yanit", "veri": cevap}))
