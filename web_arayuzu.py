"""
Web Arayüzü — FastAPI + WebSocket
3 bölüm: Sonuçlar | Başlat/Durdur | Chat
"""

import asyncio
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Set

import aiofiles
from dotenv import load_dotenv
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

import hafiza_yoneticisi as hm
import llm_istemci as llm
from orkestra import Orkestra
from quality_evaluator import QualityEvaluator

load_dotenv()

app = FastAPI(title="Otonom Ajan Sistemi")

_orkestra: Orkestra = None
_ws_baglantilar: Set[WebSocket] = set()
_calendar_sync_task: asyncio.Task | None = None
COZULEN_HATA_KLASOR = hm.KLASORLER["log"] / "cozulen_hatalar"
COZULEN_HATA_KLASOR.mkdir(parents=True, exist_ok=True)


async def _calendar_sync_loop():
    while True:
        try:
            QualityEvaluator.maybe_auto_sync_calendar_overrides(force=False)
        except Exception:
            pass

        try:
            interval = int(os.getenv("AGENT_CANARY_CALENDAR_SCHEDULER_INTERVAL_SEC", "300"))
        except Exception:
            interval = 300
        await asyncio.sleep(max(30, interval))


@app.on_event("startup")
async def startup_calendar_scheduler():
    global _calendar_sync_task
    if os.getenv("AGENT_CANARY_CALENDAR_SCHEDULER", "0").strip() != "1":
        return
    if _calendar_sync_task is not None and not _calendar_sync_task.done():
        return

    try:
        QualityEvaluator.maybe_auto_sync_calendar_overrides(force=False)
    except Exception:
        pass
    _calendar_sync_task = asyncio.create_task(_calendar_sync_loop())


@app.on_event("shutdown")
async def shutdown_calendar_scheduler():
    global _calendar_sync_task
    if _calendar_sync_task is None:
        return
    _calendar_sync_task.cancel()
    try:
        await _calendar_sync_task
    except asyncio.CancelledError:
        pass
    finally:
        _calendar_sync_task = None


def _cozulen_hata_dosya() -> Path:
    return COZULEN_HATA_KLASOR / f"cozulen_{datetime.now().strftime('%Y%m%d')}.jsonl"


def _cozulen_hata_temizle(gun: int = 30):
    esik = datetime.now() - timedelta(days=gun)
    for f in COZULEN_HATA_KLASOR.glob("cozulen_*.jsonl"):
        try:
            tarih_str = f.stem.replace("cozulen_", "")
            tarih = datetime.strptime(tarih_str, "%Y%m%d")
            if tarih < esik:
                f.unlink(missing_ok=True)
        except Exception:
            # Dosya adında tarih parse edilemiyorsa güvenli şekilde geç.
            continue


def _log_satiri_ayristir(satir: str) -> dict:
    eslesme = re.match(r"^\[(.*?)\]\s+\[(.*?)\]\s+(.*)$", satir)
    if not eslesme:
        return {
            "timestamp": datetime.now().isoformat(),
            "level": "INFO",
            "message": satir,
            "raw": satir,
        }
    zaman, seviye, mesaj = eslesme.groups()
    return {
        "timestamp": zaman,
        "level": seviye,
        "message": mesaj,
        "raw": satir,
    }


async def _cozulen_hata_kaydet(kayit: dict):
    _cozulen_hata_temizle(30)
    hedef = _cozulen_hata_dosya()
    kayit["resolvedAt"] = datetime.now().isoformat()
    kayit["expiresAt"] = (datetime.now() + timedelta(days=30)).isoformat()
    async with aiofiles.open(hedef, "a", encoding="utf-8") as f:
        await f.write(json.dumps(kayit, ensure_ascii=False) + "\n")


async def _cozulen_hata_listele(limit: int = 200) -> list[dict]:
    _cozulen_hata_temizle(30)
    kayitlar: list[dict] = []
    for dosya in sorted(COZULEN_HATA_KLASOR.glob("cozulen_*.jsonl"), reverse=True):
        try:
            async with aiofiles.open(dosya, "r", encoding="utf-8") as f:
                icerik = await f.read()
            for satir in icerik.splitlines():
                if not satir.strip():
                    continue
                try:
                    kayitlar.append(json.loads(satir))
                except Exception:
                    continue
        except Exception:
            continue
        if len(kayitlar) >= limit:
            break
    return kayitlar[:limit]


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
    try:
        cozulenler = await _cozulen_hata_listele(limit=5000)
        bugun = datetime.now().strftime("%Y-%m-%d")
        bugun_cozulen = sum(1 for k in cozulenler if str(k.get("resolvedAt", "")).startswith(bugun))
        errors = max(0, hata_sayisi - bugun_cozulen)
    except Exception:
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


@app.get("/api/traces/recent")
async def get_recent_traces(lines: int = 120, event: str = "", tool: str = "", ok: str = ""):
    """Agent trace kayıtlarını JSONL formatından döndürür."""
    try:
        trace_file = hm.KLASORLER["log"] / f"agent_trace_{datetime.now().strftime('%Y%m%d')}.jsonl"
        if not trace_file.exists():
            return {"traces": []}
        async with aiofiles.open(trace_file, "r", encoding="utf-8") as f:
            content = await f.read()
        satirlar = [satir for satir in content.splitlines() if satir.strip()]
        sonuc = []
        for satir in satirlar[-lines:]:
            try:
                item = json.loads(satir)
                if event and str(item.get("event", "")) != event:
                    continue
                if tool and str(item.get("tool", "")) != tool:
                    continue
                if ok:
                    ok_bool = ok.lower() in {"1", "true", "yes"}
                    if bool(item.get("ok")) != ok_bool:
                        continue
                sonuc.append(item)
            except Exception:
                continue
        return {"traces": sonuc}
    except Exception as e:
        return {"error": str(e), "traces": []}


@app.get("/api/traces/summary")
async def get_trace_summary(lines: int = 500):
    """Son trace kayıtlarından özet metrik döndürür."""
    try:
        trace_file = hm.KLASORLER["log"] / f"agent_trace_{datetime.now().strftime('%Y%m%d')}.jsonl"
        if not trace_file.exists():
            return {
                "total": 0,
                "toolResults": 0,
                "successRate": 0,
                "denied": 0,
                "retries": 0,
                "lastError": "",
                "lastDeniedReason": "",
            }

        async with aiofiles.open(trace_file, "r", encoding="utf-8") as f:
            content = await f.read()
        satirlar = [satir for satir in content.splitlines() if satir.strip()][-lines:]
        items = []
        for satir in satirlar:
            try:
                items.append(json.loads(satir))
            except Exception:
                continue

        tool_results = [i for i in items if i.get("event") == "tool_result"]
        total_tool = len(tool_results)
        ok_count = sum(1 for i in tool_results if bool(i.get("ok")))
        denied = sum(1 for i in items if i.get("event") == "tool_denied")
        retries = sum(max(0, int(i.get("attempts", 1)) - 1) for i in tool_results)
        success_rate = int((ok_count / total_tool) * 100) if total_tool else 0
        last_error = ""
        last_denied_reason = ""

        for i in reversed(tool_results):
            if not bool(i.get("ok")) and i.get("error"):
                last_error = str(i.get("error"))[:200]
                break

        for i in reversed(items):
            if i.get("event") == "tool_denied":
                last_denied_reason = str(i.get("error") or i.get("tool") or "denied")[:200]
                break

        # Plan JSON özeti: son plan_json alanını döndür
        last_plan_json: dict = {}
        for i in reversed(items):
            if i.get("plan_json"):
                last_plan_json = i["plan_json"]
                break

        # Rollback sayısı
        rollbacks = sum(
            1 for i in items
            if i.get("event") in {"auto_rollback_applied", "auto_rollback_applied_broad"}
        )

        return {
            "total": len(items),
            "toolResults": total_tool,
            "successRate": success_rate,
            "denied": denied,
            "retries": retries,
            "rollbacks": rollbacks,
            "lastError": last_error,
            "lastDeniedReason": last_denied_reason,
            "lastPlanJson": last_plan_json,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/memory/recent")
async def get_memory_recent(limit: int = 30):
    """Agent hafıza kayıtlarının son N satırını döndürür."""
    try:
        from memory_store import MemoryStore
        ms = MemoryStore()
        recs = ms.recall_recent(limit=limit)
        return {"records": [r.__dict__ for r in recs]}
    except Exception as e:
        return {"error": str(e), "records": []}


@app.get("/api/memory/stats")
async def get_memory_stats():
    """Hafıza istatistiklerini döndürür: toplam kayıt, rollback sayısı, başarı oranı."""
    try:
        from memory_store import MemoryStore
        ms = MemoryStore()
        recs = ms.load_all()
        total = len(recs)
        rollbacks = sum(1 for r in recs if r.kind == "rollback")
        successes = sum(1 for r in recs if r.kind == "fix_attempt" and r.outcome == "success")
        fix_attempts = sum(1 for r in recs if r.kind == "fix_attempt")
        overall_rate = round(successes / fix_attempts, 3) if fix_attempts else None
        # path başına özet
        paths: dict[str, dict] = {}
        for r in recs:
            p = r.path or ""
            if p not in paths:
                paths[p] = {"attempts": 0, "successes": 0, "rollbacks": 0}
            if r.kind == "fix_attempt":
                paths[p]["attempts"] += 1
                if r.outcome == "success":
                    paths[p]["successes"] += 1
            elif r.kind == "rollback":
                paths[p]["rollbacks"] += 1
        return {
            "total": total,
            "rollbacks": rollbacks,
            "fix_attempts": fix_attempts,
            "successes": successes,
            "overall_success_rate": overall_rate,
            "by_path": paths,
        }
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/memory/search")
async def get_memory_search(
    query: str,
    limit: int = 10,
    path: str = "",
    mode: str = "overlap",
    time_weight: float = 0.15,
    half_life_days: float = 14.0,
):
    """Hafıza kayıtlarında sorgu metnine göre semantik benzerlik araması yapar."""
    try:
        from memory_store import MemoryStore

        ms = MemoryStore()
        results = ms.semantic_search(
            query=query,
            limit=limit,
            path=path,
            mode=mode,
            time_weight=time_weight,
            half_life_days=half_life_days,
        )
        return {
            "query": query,
            "mode": mode,
            "time_weight": time_weight,
            "half_life_days": half_life_days,
            "count": len(results),
            "results": [
                {
                    "score": x["score"],
                    "relevance": x.get("relevance"),
                    "freshness": x.get("freshness"),
                    "record": x["record"].__dict__,
                }
                for x in results
            ],
        }
    except Exception as e:
        return {
            "error": str(e),
            "query": query,
            "mode": mode,
            "time_weight": time_weight,
            "half_life_days": half_life_days,
            "count": 0,
            "results": [],
        }


@app.get("/api/memory/recommended-mode")
async def get_memory_recommended_mode():
    """Hafıza araması için önerilen varsayılan modu döndürür."""
    try:
        from memory_store import MemoryStore

        ms = MemoryStore()
        return {
            "recommended_mode": ms.recommended_mode(),
            "records": ms._line_count(ms.path),
            "index_records": ms._line_count(ms.index_path),
        }
    except Exception as e:
        return {"error": str(e), "recommended_mode": "overlap", "records": 0, "index_records": 0}


@app.get("/api/memory/benchmark")
async def get_memory_benchmark(query: str, limit: int = 10, repeats: int = 5, path: str = ""):
    """Semantik hafıza arama modlarının mikro-benchmark sonucunu döndürür."""
    try:
        from memory_store import MemoryStore

        ms = MemoryStore()
        return ms.benchmark_search(query=query, limit=limit, repeats=repeats, path=path)
    except Exception as e:
        return {"error": str(e), "query": query, "records": 0, "index_records": 0, "modes": {}}


@app.get("/api/evaluator/summary")
async def get_evaluator_summary(lines: int = 1000, days: float = 7.0):
    """Ajan kalitesini trace + hafiza sinyalleri ile ozetler."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        return evaluator.summary(lines=lines, days=days)
    except Exception as e:
        return {
            "error": str(e),
            "status": "critical",
            "score": 0.0,
            "trace": {},
            "memory": {},
            "promotion": {"ready": False, "reason": "exception"},
        }


@app.get("/api/evaluator/canary-decision")
async def get_evaluator_canary_decision(
    lines: int = 1000,
    days: float = 7.0,
    min_promote_score: float = 80.0,
    rollback_score: float = 45.0,
    max_denied: int = 5,
    max_rollbacks: int = 2,
    adaptive: bool = False,
):
    """Canary için promote/hold/rollback kararını döndürür."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        summary = evaluator.summary(lines=lines, days=days)
        canary = evaluator.canary_decision(
            summary,
            min_promote_score=min_promote_score,
            rollback_score=rollback_score,
            max_denied=max_denied,
            max_rollbacks=max_rollbacks,
            adaptive=adaptive,
        )
        return {
            "status": summary.get("status"),
            "score": summary.get("score"),
            "decision": canary.get("decision"),
            "reason": canary.get("reason", []),
            "adaptive": bool(canary.get("adaptive", adaptive)),
            "thresholds": canary.get("thresholds", {}),
            "summary": summary,
        }
    except Exception as e:
        return {
            "error": str(e),
            "decision": "hold",
            "reason": ["exception"],
            "adaptive": adaptive,
            "thresholds": {
                "min_promote_score": min_promote_score,
                "rollback_score": rollback_score,
                "max_denied": max_denied,
                "max_rollbacks": max_rollbacks,
            },
        }


@app.get("/api/evaluator/adaptive-thresholds")
async def get_evaluator_adaptive_thresholds(
    lines: int = 1000,
    days: float = 7.0,
    min_promote_base: float = 80.0,
    rollback_base: float = 45.0,
    max_denied_base: int = 5,
    max_rollbacks_base: int = 2,
):
    """Canary icin dinamik threshold hesaplamasini dondurur."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        summary = evaluator.summary(lines=lines, days=days)
        thresholds = evaluator.adaptive_thresholds(
            summary,
            min_promote_base=min_promote_base,
            rollback_base=rollback_base,
            max_denied_base=max_denied_base,
            max_rollbacks_base=max_rollbacks_base,
        )
        return {
            "status": summary.get("status"),
            "score": summary.get("score"),
            "thresholds": thresholds,
            "summary": summary,
        }
    except Exception as e:
        return {
            "error": str(e),
            "thresholds": {
                "min_promote_score": min_promote_base,
                "rollback_score": rollback_base,
                "max_denied": max_denied_base,
                "max_rollbacks": max_rollbacks_base,
                "signals": {},
            },
        }


@app.get("/api/evaluator/trend-summary")
async def get_evaluator_trend_summary(lines: int = 20000):
    """1h/24h trend pencerelerinde evaluator metriklerini döndürür."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        return evaluator.trend_window_summary(lines=lines)
    except Exception as e:
        return {"error": str(e), "windows": {}}


@app.get("/api/evaluator/trend-canary-decision")
async def get_evaluator_trend_canary_decision(
    lines: int = 20000,
    use_ewma: bool = False,
    ewma_alpha: float = 0.35,
    use_seasonality: bool = False,
    seasonality_lookback_days: int = 14,
):
    """1h/24h trend karsilastirmasina gore canary kararini döndürür."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        return evaluator.trend_canary_decision(
            lines=lines,
            use_ewma=use_ewma,
            ewma_alpha=ewma_alpha,
            use_seasonality=use_seasonality,
            seasonality_lookback_days=seasonality_lookback_days,
        )
    except Exception as e:
        return {
            "error": str(e),
            "decision": "hold",
            "reason": ["exception"],
            "ewma": {"enabled": use_ewma, "alpha": ewma_alpha},
            "seasonality": {
                "enabled": use_seasonality,
                "lookback_days": seasonality_lookback_days,
                "hour_baseline": None,
                "hour_observed": None,
                "adjustment": 0.0,
            },
            "trend": {"windows": {}},
        }


@app.get("/api/evaluator/trend-diagnostics")
async def get_evaluator_trend_diagnostics(
    lines: int = 20000,
    use_ewma: bool = False,
    ewma_alpha: float = 0.35,
    use_seasonality: bool = False,
    seasonality_lookback_days: int = 14,
):
    """Trend kararini, sync-risk verisini ve pencere detaylarini tek payload'da döndürür."""
    try:
        evaluator = QualityEvaluator(trace_dir=hm.KLASORLER["log"])
        decision = evaluator.trend_canary_decision(
            lines=lines,
            use_ewma=use_ewma,
            ewma_alpha=ewma_alpha,
            use_seasonality=use_seasonality,
            seasonality_lookback_days=seasonality_lookback_days,
        )
        sync_status = QualityEvaluator.get_calendar_sync_status()
        return {
            "decision": decision,
            "trend": decision.get("trend", {}),
            "sync_risk": decision.get("sync_risk", {}),
            "explainability_tree": QualityEvaluator.build_trend_explainability_tree(decision, sync_status),
            "snapshots_preview": QualityEvaluator.get_trend_explainability_snapshots(limit=10).get("items", []),
            "calendar_sync": {
                "status": sync_status.get("status") if isinstance(sync_status, dict) else None,
                "ok": sync_status.get("ok") if isinstance(sync_status, dict) else None,
                "trend_summary": (sync_status.get("trend_summary", {}) if isinstance(sync_status, dict) else {}),
            },
        }
    except Exception as e:
        return {
            "error": str(e),
            "decision": {
                "decision": "hold",
                "reason": ["exception"],
                "sync_risk": {"enabled": True, "status": "error"},
            },
            "trend": {"windows": {}},
            "sync_risk": {"enabled": True, "status": "error"},
            "explainability_tree": {"root": "decision", "nodes": [], "leaves": []},
            "snapshots_preview": [],
            "calendar_sync": {"status": "error", "ok": False, "trend_summary": {}},
        }


@app.get("/api/evaluator/risk-components-timeseries")
async def get_evaluator_risk_components_timeseries(
    lookback_hours: int = 24,
    bucket_minutes: int = 60,
    max_points: int = 48,
):
    """Calendar sync risk bileşenlerinin zaman-serisini döndürür."""
    try:
        return QualityEvaluator.get_calendar_risk_components_timeseries(
            lookback_hours=lookback_hours,
            bucket_minutes=bucket_minutes,
            max_points=max_points,
        )
    except Exception as e:
        return {
            "error": str(e),
            "lookback_hours": lookback_hours,
            "bucket_minutes": bucket_minutes,
            "points": [],
        }


@app.get("/api/evaluator/trend-explainability-snapshots")
async def get_evaluator_trend_explainability_snapshots(limit: int = 50):
    """Persist edilen trend explainability snapshot kayıtlarını döndürür."""
    try:
        return QualityEvaluator.get_trend_explainability_snapshots(limit=limit)
    except Exception as e:
        return {"error": str(e), "items": []}


@app.get("/api/evaluator/calendar-overrides")
async def get_evaluator_calendar_overrides():
    """Aktif takvim override kayitlarini döndürür."""
    try:
        rows = QualityEvaluator._load_calendar_overrides()
        return {
            "items": rows,
            "count": len(rows),
            "path": str(QualityEvaluator._calendar_file_path()),
        }
    except Exception as e:
        return {"error": str(e), "items": [], "count": 0}


@app.post("/api/evaluator/calendar-sync")
async def post_evaluator_calendar_sync(request: Request):
    """Dis JSON kaynaklarindan takvim override listesini senkronize eder."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    source = str(body.get("source", "")).strip() or None
    merge = bool(body.get("merge", True))
    try:
        return QualityEvaluator.sync_calendar_overrides(source=source, merge=merge)
    except Exception as e:
        return {"ok": False, "error": str(e), "imported": 0, "written": 0}


@app.get("/api/evaluator/calendar-sync-status")
async def get_evaluator_calendar_sync_status():
    """Takvim senkron durumunu ve son telemetri kaydini döndürür."""
    try:
        status = QualityEvaluator.get_calendar_sync_status()
        status["scheduler"] = {
            "enabled": os.getenv("AGENT_CANARY_CALENDAR_SCHEDULER", "0").strip() == "1",
            "running": bool(_calendar_sync_task is not None and not _calendar_sync_task.done()),
            "interval_sec": max(30, int(os.getenv("AGENT_CANARY_CALENDAR_SCHEDULER_INTERVAL_SEC", "300") or "300")),
        }
        status["auto_sync"] = {
            "enabled": os.getenv("AGENT_CANARY_CALENDAR_AUTO_SYNC", "0").strip() == "1",
            "interval_sec": max(60, int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_INTERVAL_SEC", "21600") or "21600")),
            "error_cooldown_sec": max(
                60,
                int(os.getenv("AGENT_CANARY_CALENDAR_SYNC_ERROR_COOLDOWN_SEC", "900") or "900"),
            ),
            "merge": os.getenv("AGENT_CANARY_CALENDAR_SYNC_MERGE", "1").strip() != "0",
            "source": os.getenv("AGENT_CANARY_CALENDAR_SYNC_SOURCE", "").strip() or None,
        }
        return status
    except Exception as e:
        return {"ok": False, "status": "error", "error": str(e)}


@app.post("/api/evaluator/calendar-sync-auto")
async def post_evaluator_calendar_sync_auto(request: Request):
    """Stale ise veya force=true ise takvim senkronunu tetikler."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    force = bool(body.get("force", False))
    try:
        return QualityEvaluator.maybe_auto_sync_calendar_overrides(force=force)
    except Exception as e:
        return {"ok": False, "status": "error", "error": str(e)}


@app.get("/api/errors/recent")
async def get_recent_errors(lines: int = 200):
    """Son loglardan hata detaylarını döndürür."""
    try:
        log_file = hm.KLASORLER["log"] / f"sistem_{datetime.now().strftime('%Y%m%d')}.log"
        if not log_file.exists():
            return {"errors": []}
        async with aiofiles.open(log_file, "r", encoding="utf-8") as f:
            icerik = await f.read()
        satirlar = [s for s in icerik.splitlines() if s.strip()]
        hata_satirlari = [s for s in satirlar if "[ERROR]" in s]
        detaylar = []
        for s in hata_satirlari[-lines:]:
            detay = _log_satiri_ayristir(s)
            detay["id"] = str(abs(hash(s)))
            detaylar.append(detay)
        return {"errors": detaylar}
    except Exception as e:
        return {"error": str(e), "errors": []}


@app.post("/api/errors/resolve")
async def resolve_error(request: Request):
    """Bir hata mesajını çözüldü olarak ayrı kayda taşır."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "Geçersiz JSON"}

    mesaj = str(body.get("message", "")).strip()
    ham_satir = str(body.get("raw", "")).strip()
    if not mesaj and not ham_satir:
        return {"ok": False, "error": "message veya raw zorunlu"}

    kayit = {
        "message": mesaj or ham_satir,
        "raw": ham_satir,
        "source": body.get("source", "manual"),
    }
    await _cozulen_hata_kaydet(kayit)
    await hm.log_yaz(f"Çözülen hata kaydedildi: {kayit['message'][:120]}", "SUCCESS")
    return {"ok": True}


@app.get("/api/errors/resolved")
async def get_resolved_errors(limit: int = 200):
    """Çözüldü olarak işaretlenen hataları döndürür (30 gün saklama)."""
    try:
        kayitlar = await _cozulen_hata_listele(limit=limit)
        return {"resolved": kayitlar}
    except Exception as e:
        return {"error": str(e), "resolved": []}


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
