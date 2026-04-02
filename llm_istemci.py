"""
LLM İstemcisi
- Yerel LLM (Ollama/OpenAI-uyumlu), Anthropic ve Groq sağlayıcıları.
- Varsayılan öncelik (auto): yerel → Anthropic → Groq.
- LLM_PROVIDER=local → yalnızca yerel; local → fallback zinciri.

Yerel LLM env ayarları:
  LOCAL_LLM_URL   : sunucu adresi (varsayılan: http://localhost:11434)
  LOCAL_LLM_MODEL : model adı     (varsayılan: llama3.2:3b)
  LOCAL_LLM_TIMEOUT: istek zaman aşımı sn (varsayılan: 60)
"""

import os
import asyncio
import random
import time
from typing import Tuple

import httpx


# ---- Groq ayarları ----
_GROQ_CONCURRENCY = int(os.getenv("GROQ_CONCURRENCY", "1"))
_GROQ_MIN_INTERVAL_SEC = float(os.getenv("GROQ_MIN_INTERVAL_SEC", "0.5"))
_GROQ_MAX_RETRY = int(os.getenv("GROQ_MAX_RETRY", "30"))
_GROQ_413_CHUNK_CHARS = int(os.getenv("GROQ_413_CHUNK_CHARS", "7000"))

# Çoklu anahtar havuzu
_GROQ_KEYS = [k.strip() for k in os.getenv("GROQ_API_KEYS", os.getenv("GROQ_API_KEY", "")).split(",") if k.strip()]
_GROQ_CURRENT_KEY_IDX = 0
_GROQ_KEY_KILIT = asyncio.Lock()
_GROQ_KEY_YANLIS_SAYACI = {}

_groq_sem = asyncio.Semaphore(_GROQ_CONCURRENCY)
_groq_ritim_kilit = asyncio.Lock()
_groq_son_istek = 0.0
_groq_dinamik_aralik = _GROQ_MIN_INTERVAL_SEC

# ---- Yerel LLM ayarları ----
_LOCAL_LLM_URL = os.getenv("LOCAL_LLM_URL", "http://localhost:11434").rstrip("/")
_LOCAL_LLM_MODEL = os.getenv("LOCAL_LLM_MODEL", "llama3.2:3b")
_LOCAL_LLM_TIMEOUT = float(os.getenv("LOCAL_LLM_TIMEOUT", "60"))

# ---- Metrik sayaçları ----
_local_istek_sayaci = 0
_llm_istek_sayaci = 0
_llm_deneme_sayaci = 0


def llm_istek_sayisi() -> int:
    """Bu süreç boyunca yapılan başarılı LLM API çağrı sayısını döndürür."""
    return _llm_istek_sayaci


def llm_deneme_sayisi() -> int:
    """Bu süreç boyunca başlatılan LLM API deneme sayısını döndürür."""
    return _llm_deneme_sayaci


def local_istek_sayisi() -> int:
    """Bu süreç boyunca yerel LLM'den karşılanan başarılı istek sayısını döndürür."""
    return _local_istek_sayaci


def api_bagimlilik_orani() -> float:
    """Yerel LLM dışındaki API isteklerinin oranını döndürür (0.0 = tam yerel, 1.0 = tam API).

    Toplam istek 0 ise 1.0 döner (henüz ölçüm yok → API bağımlı varsayım).
    """
    total = _llm_istek_sayaci + _local_istek_sayaci
    if total == 0:
        return 1.0
    return _llm_istek_sayaci / total


# ---------------------------------------------------------------------------
# Yerel LLM (Ollama / OpenAI-uyumlu endpoint)
# ---------------------------------------------------------------------------

async def _local_llm_hazir(url: str | None = None, timeout: float = 3.0) -> bool:
    """Yerel LLM sunucusunun çalışıp çalışmadığını kontrol eder (hızlı health check)."""
    base = url or _LOCAL_LLM_URL
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(f"{base}/api/tags")
            return r.status_code < 500
    except Exception:
        return False


async def _local_chat_raw(
    sistem: str,
    kullanici: str,
    *,
    model: str | None = None,
    url: str | None = None,
    max_tokens: int = 2048,
    timeout: float | None = None,
) -> str:
    """Yerel Ollama/OpenAI-uyumlu sunucuya chat isteği gönderir, metin yanıtı döndürür."""
    base = url or _LOCAL_LLM_URL
    mdl = model or _LOCAL_LLM_MODEL
    tout = timeout if timeout is not None else _LOCAL_LLM_TIMEOUT

    messages = []
    if sistem:
        messages.append({"role": "system", "content": sistem})
    messages.append({"role": "user", "content": kullanici})

    payload = {
        "model": mdl,
        "messages": messages,
        "stream": False,
        "options": {"num_predict": max_tokens},
    }

    async with httpx.AsyncClient(timeout=tout) as c:
        r = await c.post(
            f"{base}/api/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        r.raise_for_status()
        data = r.json()

    # Ollama native response format
    if "message" in data:
        return str(data["message"].get("content", ""))

    # OpenAI-compatible fallback
    choices = data.get("choices", [])
    if choices:
        return str(choices[0].get("message", {}).get("content", ""))

    raise ValueError(f"Yerel LLM yanıtı tanınamadı: {list(data.keys())}")


async def _groq_ritim_bekle() -> None:
    """Global istek ritmi: sağlayıcı limitine toplu halde çarpmayı azaltır."""
    global _groq_son_istek
    async with _groq_ritim_kilit:
        kalan = _groq_dinamik_aralik - (time.monotonic() - _groq_son_istek)
        if kalan > 0:
            await asyncio.sleep(kalan)
        _groq_son_istek = time.monotonic()


async def _groq_geri_bildirim(basarili: bool, durum_kodu: int | None = None) -> None:
    """429 geldikçe ritmi gevşetir, başarılı çağrılarda kademeli hızlandırır."""
    global _groq_dinamik_aralik
    async with _groq_ritim_kilit:
        if basarili:
            _groq_dinamik_aralik = max(_GROQ_MIN_INTERVAL_SEC, _groq_dinamik_aralik * 0.95)
            return
        if durum_kodu == 429:
            _groq_dinamik_aralik = min(8.0, _groq_dinamik_aralik * 1.25 + 0.1)


async def _groq_sonraki_anahtar() -> str:
    """Havuzdan sağlıklı bir anahtar döndür; yanlış anahtarları atla."""
    global _GROQ_CURRENT_KEY_IDX
    if not _GROQ_KEYS:
        raise RuntimeError("Groq API anahtarı bulunamadı")
    
    async with _GROQ_KEY_KILIT:
        denemeler = 0
        while denemeler < len(_GROQ_KEYS):
            anahtar = _GROQ_KEYS[_GROQ_CURRENT_KEY_IDX]
            _GROQ_CURRENT_KEY_IDX = (_GROQ_CURRENT_KEY_IDX + 1) % len(_GROQ_KEYS)
            
            yanlis_sayisi = _GROQ_KEY_YANLIS_SAYACI.get(anahtar, 0)
            if yanlis_sayisi < 3:
                return anahtar
            denemeler += 1
        
        return _GROQ_KEYS[0]


async def _groq_anahtar_hatasi(anahtar: str) -> None:
    """Anahtar hatasını not et, sonra sıradaki anahtarı dene."""
    async with _GROQ_KEY_KILIT:
        _GROQ_KEY_YANLIS_SAYACI[anahtar] = _GROQ_KEY_YANLIS_SAYACI.get(anahtar, 0) + 1


def _metni_parcala(metin: str, parca_boyutu: int) -> list[str]:
    """Metni kayıpsız biçimde parçalar; veri atmaz, sadece taşımayı böler."""
    if len(metin) <= parca_boyutu:
        return [metin]

    parcalar: list[str] = []
    bas = 0
    uzunluk = len(metin)
    while bas < uzunluk:
        son = min(bas + parca_boyutu, uzunluk)
        if son < uzunluk:
            bolme_noktasi = metin.rfind("\n\n", bas + int(parca_boyutu * 0.6), son)
            if bolme_noktasi > bas:
                son = bolme_noktasi
        parca = metin[bas:son]
        if not parca:
            son = min(bas + parca_boyutu, uzunluk)
            parca = metin[bas:son]
        parcalar.append(parca)
        bas = son
    return parcalar


async def _groq_chat_raw(
    api_key: str,
    model: str,
    sistem: str,
    kullanici: str,
    max_tokens: int,
) -> str:
    global _llm_istek_sayaci, _llm_deneme_sayaci
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sistem},
            {"role": "user", "content": kullanici},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    async with _groq_sem:
        await _groq_ritim_bekle()
        _llm_deneme_sayaci += 1
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(url, headers=headers, json=payload)
            try:
                r.raise_for_status()
            except httpx.HTTPStatusError:
                await _groq_geri_bildirim(False, r.status_code)
                raise
            veri = r.json()
        await _groq_geri_bildirim(True)
        _llm_istek_sayaci += 1
    return veri["choices"][0]["message"]["content"]


async def _groq_cok_asamali_calis(
    api_key: str,
    model: str,
    sistem: str,
    kullanici: str,
    max_tokens: int,
) -> str:
    """413 durumunda tam veriyi atlamadan çok aşamalı işlem uygular."""
    parcalar = _metni_parcala(kullanici, _GROQ_413_CHUNK_CHARS)
    parcali_ciktilar: list[str] = []

    protokol_sistem = (
        f"{sistem}\n\n"
        "EK PROTOKOL: Bu istek büyük bir içeriğin bölünmüş parçalarını işler. "
        "Parçada yazan hiçbir kritik bilgiyi atlamadan, detaylı analiz üret."
    )

    for i, parca in enumerate(parcalar, start=1):
        alt_kullanici = (
            f"PARÇA {i}/{len(parcalar)}\n"
            "Aşağıdaki içeriği eksiksiz analiz et ve bu parçanın önemli çıkarımlarını ayrıntılı yaz.\n\n"
            f"{parca}"
        )
        alt_sonuc = await _groq_chat_raw(api_key, model, protokol_sistem, alt_kullanici, max_tokens)
        parcali_ciktilar.append(f"### PARÇA {i}\n{alt_sonuc}")

    birlesik = "\n\n".join(parcali_ciktilar)
    final_kullanici = (
        "Aşağıda tüm parçaların detaylı analizleri var. "
        "Tek bir bütünsel, tutarlı ve eyleme dönük final yanıt üret.\n\n"
        f"{birlesik}"
    )
    return await _groq_chat_raw(api_key, model, sistem, final_kullanici, max_tokens)


def etkin_baglanti() -> Tuple[str, str, str]:
    """(saglayici, api_key_or_url, model) döndürür.

    Sağlayıcılar: "local" | "anthropic" | "groq"
    Yerel modelde api_key yerine LOCAL_LLM_URL döner.
    """
    provider_env = os.getenv("LLM_PROVIDER", "").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    local_url = _LOCAL_LLM_URL
    local_model = _LOCAL_LLM_MODEL

    if provider_env == "local":
        return "local", local_url, local_model

    if provider_env == "anthropic":
        if anthropic_key:
            return "anthropic", anthropic_key, os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        if groq_key:
            return "groq", groq_key, os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        raise RuntimeError("LLM_PROVIDER=anthropic ama ANTHROPIC_API_KEY yok")

    if provider_env == "groq":
        if groq_key:
            return "groq", groq_key, os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
        if anthropic_key:
            return "anthropic", anthropic_key, os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
        raise RuntimeError("LLM_PROVIDER=groq ama GROQ_API_KEY yok")

    # auto: önce yerel (eğer LOCAL_LLM_URL tanımlıysa runtime'da dene), sonra Anthropic, sonra Groq
    if provider_env == "auto":
        return "auto", local_url, local_model

    # Varsayılan: önce Anthropic, sonra Groq
    if anthropic_key:
        return "anthropic", anthropic_key, os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    if groq_key:
        return "groq", groq_key, os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    raise RuntimeError("Geçerli LLM anahtarı bulunamadı (ANTHROPIC_API_KEY, GROQ_API_KEY veya LLM_PROVIDER=local)")


def llm_hazir_mi() -> bool:
    """Herhangi bir sağlayıcı kullanılabilir mi? (yerel de dahil)."""
    provider_env = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider_env in ("local", "auto"):
        return True  # Yerel sunucu runtime'da kontrol edilir
    return bool(os.getenv("ANTHROPIC_API_KEY", "").strip() or os.getenv("GROQ_API_KEY", "").strip())


async def metin_uret(
    sistem: str,
    kullanici: str,
    *,
    max_tokens: int = 2048,
    saglayici: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
) -> str:
    """Sistem + kullanıcı mesajından tek bir metin yanıtı üretir."""
    global _llm_istek_sayaci, _llm_deneme_sayaci, _local_istek_sayaci
    if not saglayici or not api_key or not model:
        secilen = etkin_baglanti()
        saglayici = saglayici or secilen[0]
        api_key = api_key or secilen[1]
        model = model or secilen[2]

    # Yerel LLM – doğrudan
    if saglayici == "local":
        _llm_deneme_sayaci += 1
        yanit = await _local_chat_raw(sistem, kullanici, model=model, url=api_key, max_tokens=max_tokens)
        _local_istek_sayaci += 1
        return yanit

    # Auto mod – yerel dene, başarısız olursa cloud fallback
    if saglayici == "auto":
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        local_ok = await _local_llm_hazir(url=api_key)
        if local_ok:
            try:
                _llm_deneme_sayaci += 1
                yanit = await _local_chat_raw(sistem, kullanici, model=model, url=api_key, max_tokens=max_tokens)
                _local_istek_sayaci += 1
                return yanit
            except Exception:
                pass  # Yerel başarısız → cloud fallback
        # Cloud fallback sırası: Anthropic → Groq
        if anthropic_key:
            return await metin_uret(sistem, kullanici, max_tokens=max_tokens,
                                    saglayici="anthropic", api_key=anthropic_key,
                                    model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"))
        if groq_key:
            return await metin_uret(sistem, kullanici, max_tokens=max_tokens,
                                    saglayici="groq", api_key=groq_key,
                                    model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"))
        raise RuntimeError("auto modunda yerel sunucu erişilemez ve cloud API anahtarı yok")

    if saglayici == "anthropic":
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        _llm_deneme_sayaci += 1
        yanit = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=sistem,
            messages=[{"role": "user", "content": kullanici}],
        )
        _llm_istek_sayaci += 1
        return yanit.content[0].text

    if saglayici == "groq":
        anahtar = api_key
        son_hata: Exception | None = None
        for deneme in range(_GROQ_MAX_RETRY):
            try:
                if deneme > 0 and deneme % 3 == 0:
                    anahtar = await _groq_sonraki_anahtar()
                return await _groq_chat_raw(anahtar, model, sistem, kullanici, max_tokens)
            except httpx.HTTPStatusError as e:
                kod = e.response.status_code if e.response is not None else 0
                if kod == 401:
                    await _groq_anahtar_hatasi(anahtar)
                    anahtar = await _groq_sonraki_anahtar()
                    continue
                if kod == 413:
                    return await _groq_cok_asamali_calis(anahtar, model, sistem, kullanici, max_tokens)
                if kod in (429, 500, 502, 503, 504):
                    son_hata = e
                    retry_after = 0.0
                    if e.response is not None:
                        ra = e.response.headers.get("retry-after", "").strip()
                        if ra:
                            try:
                                retry_after = float(ra)
                            except ValueError:
                                retry_after = 0.0
                    bekleme = max(retry_after, min(45.0, (1.7 ** deneme) + random.uniform(0.2, 0.9)))
                    await asyncio.sleep(bekleme)
                    continue
                raise
            except httpx.RequestError as e:
                son_hata = e
                bekleme = min(20.0, (1.5 ** deneme) + random.uniform(0.2, 0.7))
                await asyncio.sleep(bekleme)
                if deneme > 2:
                    anahtar = await _groq_sonraki_anahtar()

        raise RuntimeError(f"Groq isteği başarısız (yeniden deneme limiti aşıldı): {son_hata}")

    raise RuntimeError(f"Desteklenmeyen sağlayıcı: {saglayici}")


# ─────────────────────────────────────────────────────────────────────────────
# STREAMING — çok turlu sohbet için token-by-token async generator
# ─────────────────────────────────────────────────────────────────────────────

async def stream_chat(
    messages: list[dict],
    sistem: str = "",
    *,
    saglayici: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    max_tokens: int = 2048,
):
    """Çok turlu sohbet için token-by-token string parçaları yield eder.

    messages: [{"role": "user"|"assistant", "content": str}, ...]
    Döndürür: AsyncGenerator[str, None] — her yield bir token parçası.
    """
    global _local_istek_sayaci, _llm_istek_sayaci

    if not saglayici or not api_key or not model:
        secilen = etkin_baglanti()
        saglayici = saglayici or secilen[0]
        api_key = api_key or secilen[1]
        model = model or secilen[2]

    # ── Auto mod: önce yerel dene ──
    if saglayici == "auto":
        local_ok = await _local_llm_hazir(url=api_key)
        if local_ok:
            try:
                async for token in _stream_local(messages, sistem, model=model, url=api_key, max_tokens=max_tokens):
                    yield token
                _local_istek_sayaci += 1
                return
            except Exception:
                pass
        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        if anthropic_key:
            async for token in stream_chat(messages, sistem, saglayici="anthropic",
                                           api_key=anthropic_key,
                                           model=os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
                                           max_tokens=max_tokens):
                yield token
            return
        if groq_key:
            async for token in stream_chat(messages, sistem, saglayici="groq",
                                           api_key=groq_key,
                                           model=os.getenv("GROQ_MODEL", "llama-3.1-8b-instant"),
                                           max_tokens=max_tokens):
                yield token
            return
        raise RuntimeError("auto modunda yerel sunucu erişilemez ve cloud API anahtarı yok")

    # ── Yerel (Ollama) ──
    if saglayici == "local":
        async for token in _stream_local(messages, sistem, model=model, url=api_key, max_tokens=max_tokens):
            yield token
        _local_istek_sayaci += 1
        return

    # ── Anthropic ──
    if saglayici == "anthropic":
        import anthropic as _ant
        client = _ant.AsyncAnthropic(api_key=api_key)
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=sistem,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text
        _llm_istek_sayaci += 1
        return

    # ── Groq (OpenAI-uyumlu SSE) ──
    if saglayici == "groq":
        await _groq_ritim_bekle()
        url = "https://api.groq.com/openai/v1/chat/completions"
        hdrs = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        body: list[dict] = []
        if sistem:
            body.append({"role": "system", "content": sistem})
        body.extend(messages)
        payload = {"model": model, "messages": body, "max_tokens": max_tokens,
                   "temperature": 0.3, "stream": True}
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("POST", url, headers=hdrs, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if raw == "[DONE]":
                        break
                    try:
                        chunk = __import__("json").loads(raw)
                        delta = chunk["choices"][0]["delta"].get("content", "")
                        if delta:
                            yield delta
                    except Exception:
                        continue
        await _groq_geri_bildirim(True)
        _llm_istek_sayaci += 1
        return

    raise RuntimeError(f"Desteklenmeyen sağlayıcı: {saglayici}")


async def _stream_local(
    messages: list[dict],
    sistem: str,
    *,
    model: str,
    url: str,
    max_tokens: int,
):
    """Ollama'dan token-by-token NDJSON stream okur."""
    import json as _json
    mdl = model or _LOCAL_LLM_MODEL
    base = url or _LOCAL_LLM_URL
    body: list[dict] = []
    if sistem:
        body.append({"role": "system", "content": sistem})
    body.extend(messages)
    payload = {"model": mdl, "messages": body, "stream": True,
               "options": {"num_predict": max_tokens}}
    async with httpx.AsyncClient(timeout=_LOCAL_LLM_TIMEOUT) as c:
        async with c.stream("POST", f"{base}/api/chat",
                            json=payload,
                            headers={"Content-Type": "application/json"}) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = _json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield token
                    if chunk.get("done"):
                        break
                except Exception:
                    continue
