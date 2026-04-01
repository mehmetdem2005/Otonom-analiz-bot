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
    return HTML


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

/* Sağ üst hamburger menü */
#menu-btn { margin-left: auto; width: 36px; height: 36px; padding: 0; font-size: 1.1em; display:flex; align-items:center; justify-content:center; }
#drawer-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.45); display: none; z-index: 90; }
#drawer-backdrop.acik { display: block; }
#drawer { position: fixed; top: 0; right: -320px; width: 300px; height: 100vh; background: #0d1115; border-left: 1px solid #1f2d1f; z-index: 100; padding: 16px; transition: right 0.25s ease; }
#drawer.acik { right: 0; }
#drawer h3 { color: #00ff88; margin-bottom: 12px; font-size: 0.95em; letter-spacing: 1px; }
.drawer-link { display:block; width: 100%; text-align:left; color:#b7f7cc; text-decoration:none; padding:10px 12px; border:1px solid #234; border-radius:6px; margin-bottom:8px; background: transparent; }
.drawer-link:hover { background:#00ff88; color:#000; }
#error-alarm { margin-left: 10px; color: #ff6666; font-size: 0.78em; display: none; animation: pulse 0.8s infinite; }
#drawer small { color:#6ea57a; display:block; margin-top:12px; line-height:1.5; }

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

/* Sağ panel log kutusu */
#log-baslik { padding: 10px 16px; background: #0d0d0d; color: #888; font-size: 0.8em; letter-spacing: 1px; border-bottom: 1px solid #1a1a1a; border-top: 1px solid #1a1a1a; }
#log-filtre { width: 100%; padding: 6px 10px; background:#111; color:#9ff0b8; border:1px solid #284; margin: 0; border-radius: 0; }
#sistem-loglar { height: 220px; overflow-y: auto; padding: 10px 12px; font-size: 0.74em; line-height: 1.45; color: #c8f3d5; background: #090d09; }
.log-satir { padding: 2px 0; border-bottom: 1px dotted #1a2a1a; }
.side-panel { display: none; }
.side-panel.aktif { display: block; }
#db-panel, #analytics-panel, #errors-panel { padding: 10px 12px; color: #bdecc8; font-size: 0.78em; line-height: 1.5; background:#090d09; height: 220px; overflow-y: auto; }
#db-panel pre, #analytics-panel pre, #errors-panel pre { white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
<div id="header">
  <div id="status-dot"></div>
  <h1>// OTONOM AJAN SİSTEMİ</h1>
  <span id="tur-sayac">Tur: 0</span>
  <span id="ajan-sayac" style="color:#556655;font-size:0.8em">Ajan: 10</span>
  <span id="disk-goster" style="color:#556655;font-size:0.8em">Disk: -%</span>
  <span id="error-alarm">HATA VAR</span>
  <button id="menu-btn" onclick="menuAcKapat()">☰</button>
</div>

<div id="drawer-backdrop" onclick="menuKapat()"></div>
<aside id="drawer">
  <h3>HIZLI MENÜ</h3>
  <button class="drawer-link" onclick="panelAc('chat')">Chat Paneli</button>
  <button class="drawer-link" onclick="panelAc('logs')">Canlı Loglar</button>
  <button class="drawer-link" onclick="panelAc('errors')">Hata Paneli</button>
  <button class="drawer-link" onclick="panelAc('db')">Database Paneli</button>
  <button class="drawer-link" onclick="panelAc('analytics')">Analitik Paneli</button>
  <a class="drawer-link" href="/dashboard">Gelişmiş Dashboard</a>
  <small>
    Tek sayfa mod aktif: Menüden panel değiştir.<br>
    Başlat sonrası loglar canlı olarak akar.
  </small>
</aside>

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
    <div class="side-panel aktif" id="panel-logs">
      <div id="log-baslik">SİSTEM LOGLARI (CANLI)</div>
      <select id="log-filtre" onchange="loglariYukle()">
        <option value="ALL">Tüm Ajanlar</option>
        <option value="A0">A0 - Araştırmacı</option>
        <option value="A1">A1 - Mühendis</option>
        <option value="A2">A2 - Eleştirmen</option>
        <option value="A3">A3 - Sentezci</option>
        <option value="A4">A4 - Stratejist</option>
        <option value="A5">A5 - Uygulayıcı</option>
        <option value="A6">A6 - Veri Analisti</option>
        <option value="A7">A7 - İnovatör</option>
        <option value="A8">A8 - Bütünleştirici</option>
        <option value="A9">A9 - Kod Denetçisi</option>
      </select>
      <div id="sistem-loglar"></div>
    </div>

    <div class="side-panel" id="panel-chat">
      <div id="chat-baslik">CHAT MODU</div>
      <div id="chat-mesajlar">
        <div class="chat-mesaj sistem">Sistem hazır. Ajanları başlatmak için ▶ BAŞLAT'a bas.<br>Ya da bana bir şey sor.</div>
      </div>
      <div id="chat-input-alani">
        <textarea id="chat-input" placeholder="Sisteme bir yönerge veya soru yaz..." rows="3" onkeydown="enterGonder(event)"></textarea>
        <button id="chat-gonder" onclick="chatGonder()">→</button>
      </div>
    </div>

    <div class="side-panel" id="panel-errors">
      <div id="log-baslik">HATA PANELİ</div>
      <div id="errors-panel"><pre>Henüz hata yok.</pre></div>
    </div>

    <div class="side-panel" id="panel-db">
      <div id="log-baslik">DATABASE PANELİ</div>
      <div id="db-panel"><pre>Yükleniyor...</pre></div>
    </div>

    <div class="side-panel" id="panel-analytics">
      <div id="log-baslik">ANALİTİK PANELİ</div>
      <div id="analytics-panel"><pre>Yükleniyor...</pre></div>
    </div>
  </div>
</div>

<script>
let ws = null;
let turSayac = 0;
const ajanlar = {};
let sonLogImza = "";
let sonHataSayisi = 0;

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

async function loglariYukle() {
  try {
    const filtre = document.getElementById('log-filtre')?.value || 'ALL';
    const endpoint = filtre === 'ALL'
      ? '/api/logs/recent?lines=80'
      : `/api/logs/agent/${encodeURIComponent(filtre)}?lines=80`;

    const r = await fetch(endpoint);
    const d = await r.json();
    const logs = Array.isArray(d.logs) ? d.logs : [];
    const imza = logs.slice(-1)[0] || '';
    if (imza === sonLogImza) return;
    sonLogImza = imza;

    const kutu = document.getElementById('sistem-loglar');
    kutu.innerHTML = logs.slice(-80).map(s => `<div class="log-satir">${s.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</div>`).join('');
    kutu.scrollTop = kutu.scrollHeight;

    const hataLogs = logs.filter(s => s.includes('[ERROR]') || s.includes('[WARN]'));
    document.getElementById('errors-panel').innerHTML = `<pre>${hataLogs.length ? hataLogs.join('\\n') : 'Henüz hata yok.'}</pre>`;
    if (hataLogs.length > sonHataSayisi) {
      hataUyarisiAc();
    }
    sonHataSayisi = hataLogs.length;
  } catch (_) {
    // sessiz geç
  }
}

async function panelVeriYukle(panel) {
  if (panel === 'db') {
    const adlar = ['alerts.json', 'archive.json', 'tweets.json', 'comment-profiles.json'];
    let metin = '';
    for (const ad of adlar) {
      try {
        const r = await fetch(`/api/database/${ad}`);
        const j = await r.json();
        metin += `=== ${ad} ===\n${JSON.stringify(j, null, 2).slice(0, 1500)}\n\n`;
      } catch {
        metin += `=== ${ad} ===\nYüklenemedi\n\n`;
      }
    }
    document.getElementById('db-panel').innerHTML = `<pre>${metin.replace(/</g,'&lt;').replace(/>/g,'&gt;')}</pre>`;
  }

  if (panel === 'analytics') {
    try {
      const r = await fetch('/api/analytics');
      const j = await r.json();
      document.getElementById('analytics-panel').innerHTML = `<pre>${JSON.stringify(j, null, 2)}</pre>`;
    } catch {
      document.getElementById('analytics-panel').innerHTML = '<pre>Analitik yüklenemedi.</pre>';
    }
  }
}

function panelAc(panel) {
  document.querySelectorAll('.side-panel').forEach(p => p.classList.remove('aktif'));
  const hedef = document.getElementById(`panel-${panel}`);
  if (hedef) hedef.classList.add('aktif');
  panelVeriYukle(panel);
  menuKapat();
}

function hataUyarisiAc() {
  const alarm = document.getElementById('error-alarm');
  alarm.style.display = 'inline';
  setTimeout(() => {
    alarm.style.display = 'none';
  }, 4500);
}

function menuAcKapat() {
  const d = document.getElementById('drawer');
  const b = document.getElementById('drawer-backdrop');
  const acik = d.classList.contains('acik');
  if (acik) {
    d.classList.remove('acik');
    b.classList.remove('acik');
  } else {
    d.classList.add('acik');
    b.classList.add('acik');
  }
}

function menuKapat() {
  document.getElementById('drawer').classList.remove('acik');
  document.getElementById('drawer-backdrop').classList.remove('acik');
}

renderAjanGrid();
wsBaslat();
fetch('/durum').then(r=>r.json()).then(durumGuncelle);
loglariYukle();
setInterval(loglariYukle, 2000);
setInterval(() => panelVeriYukle('analytics'), 7000);
</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
async def anasayfa():
  return HTML


@app.get("/legacy", response_class=HTMLResponse)
async def legacy_arayuz():
  return HTML


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
    "Veri Analisti", "İnovatör"
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
  api_calls = log_icerik.count("[LLM]")
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
