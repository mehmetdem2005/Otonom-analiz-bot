"""
LLM İstemcisi
- Anthropic ve Groq arasında anahtar durumuna göre geçiş yapar.
- Varsayılan öncelik: LLM_PROVIDER belirtilmediyse Anthropic -> Groq.
"""

import os
import asyncio
import random
import time
from typing import Tuple

import httpx


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
_llm_istek_sayaci = 0
_llm_deneme_sayaci = 0


def llm_istek_sayisi() -> int:
    """Bu süreç boyunca yapılan başarılı LLM API çağrı sayısını döndürür."""
    return _llm_istek_sayaci


def llm_deneme_sayisi() -> int:
    """Bu süreç boyunca başlatılan LLM API deneme sayısını döndürür."""
    return _llm_deneme_sayaci


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
    """(saglayici, api_key, model) döndürür."""
    provider_env = os.getenv("LLM_PROVIDER", "").strip().lower()
    anthropic_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    groq_key = os.getenv("GROQ_API_KEY", "").strip()

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

    # Otomatik seçim: önce Anthropic, sonra Groq
    if anthropic_key:
        return "anthropic", anthropic_key, os.getenv("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    if groq_key:
        return "groq", groq_key, os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")

    raise RuntimeError("Geçerli LLM anahtarı bulunamadı (ANTHROPIC_API_KEY veya GROQ_API_KEY)")


def llm_hazir_mi() -> bool:
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
    global _llm_istek_sayaci, _llm_deneme_sayaci
    if not saglayici or not api_key or not model:
        secilen = etkin_baglanti()
        saglayici = saglayici or secilen[0]
        api_key = api_key or secilen[1]
        model = model or secilen[2]

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
