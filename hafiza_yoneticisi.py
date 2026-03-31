"""
Hafıza Yöneticisi — Tüm ajanların ortak belleği.
Duplicate detection, okuma/yazma, index yönetimi.
"""

import hashlib
import json
import os
import asyncio
import aiofiles
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).parent

KLASORLER = {
    "planlar": BASE / "planlar",
    "hafiza": BASE / "hafiza",
    "sonuclar": BASE / "sonuclar",
    "konusmalar": BASE / "konusmalar",
    "onerilen_degisiklikler": BASE / "onerilen_degisiklikler",
    "kaynak_kod_arsivi": BASE / "kaynak_kod_arsivi",
    "log": BASE / "log",
}

# Tüm klasörleri oluştur
for k in KLASORLER.values():
    k.mkdir(parents=True, exist_ok=True)

HASH_INDEX_DOSYASI = BASE / "hafiza" / "_hash_index.json"
_hash_lock = asyncio.Lock()
_hash_index: set[str] = set()


def _hash_index_yukle():
    global _hash_index
    if HASH_INDEX_DOSYASI.exists():
        try:
            with open(HASH_INDEX_DOSYASI) as f:
                _hash_index = set(json.load(f))
        except Exception:
            _hash_index = set()
    else:
        _hash_index = set()


_hash_index_yukle()


def icerik_hash(icerik: str) -> str:
    return hashlib.sha256(icerik.strip().encode()).hexdigest()


async def duplikat_mi(icerik: str) -> bool:
    h = icerik_hash(icerik)
    async with _hash_lock:
        return h in _hash_index


async def hash_kaydet(icerik: str):
    h = icerik_hash(icerik)
    async with _hash_lock:
        _hash_index.add(h)
        async with aiofiles.open(HASH_INDEX_DOSYASI, "w") as f:
            await f.write(json.dumps(list(_hash_index)))


async def hafizaya_yaz(ajan_id: int, tur: int, icerik: str, etiket: str = "genel") -> bool:
    """Benzersiz içeriği hafızaya yazar. Duplikat ise False döner."""
    if await duplikat_mi(icerik):
        return False

    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya_adi = KLASORLER["hafiza"] / f"{zaman}_ajan{ajan_id:02d}_tur{tur:04d}_{etiket[:20]}.md"

    meta = f"---\najan: {ajan_id}\ntur: {tur}\nzaman: {datetime.now().isoformat()}\netiket: {etiket}\n---\n\n"
    async with aiofiles.open(dosya_adi, "w", encoding="utf-8") as f:
        await f.write(meta + icerik)

    await hash_kaydet(icerik)
    return True


async def sonuca_yaz(ajan_id: int, tur: int, icerik: str, kategori: str = "analiz") -> bool:
    """Sonuçlar klasörüne benzersiz çıktı yazar."""
    if await duplikat_mi(icerik):
        return False

    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya_adi = KLASORLER["sonuclar"] / f"{zaman}_ajan{ajan_id:02d}_{kategori[:20]}.md"

    meta = f"---\najan: {ajan_id}\ntur: {tur}\nzaman: {datetime.now().isoformat()}\nkategori: {kategori}\n---\n\n"
    async with aiofiles.open(dosya_adi, "w", encoding="utf-8") as f:
        await f.write(meta + icerik)

    await hash_kaydet(icerik)
    return True


async def degisiklik_oner(ajan_id: int, dosya_adi_hedef: str, diff_icerik: str, aciklama: str):
    """Kaynak koda değişiklik önerisini yazar."""
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya = KLASORLER["onerilen_degisiklikler"] / f"{zaman}_ajan{ajan_id:02d}_{dosya_adi_hedef[:30]}.diff"

    icerik = f"# Hedef Dosya: {dosya_adi_hedef}\n# Ajan: {ajan_id}\n# Açıklama: {aciklama}\n\n{diff_icerik}"
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(icerik)


async def planlari_oku() -> str:
    """planlar/ klasöründeki tüm dosyaları birleştirip döndürür."""
    parcalar = []
    dosyalar = sorted(KLASORLER["planlar"].glob("*.md")) + sorted(KLASORLER["planlar"].glob("*.txt"))
    for d in dosyalar:
        async with aiofiles.open(d, encoding="utf-8") as f:
            icerik = await f.read()
        parcalar.append(f"=== {d.name} ===\n{icerik}")
    return "\n\n".join(parcalar) if parcalar else "(henüz plan yok — planlar/ klasörü boş)"


async def hafizayi_oku(son_n: int = 30) -> str:
    """hafiza/ klasöründen en son N dosyayı okur."""
    dosyalar = sorted(KLASORLER["hafiza"].glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)
    dosyalar = [d for d in dosyalar if not d.name.startswith("_")][:son_n]
    parcalar = []
    for d in reversed(dosyalar):
        async with aiofiles.open(d, encoding="utf-8") as f:
            icerik = await f.read()
        parcalar.append(f"--- {d.name} ---\n{icerik}")
    return "\n\n".join(parcalar) if parcalar else "(hafıza boş)"


async def sonuclari_oku(son_n: int = 20) -> str:
    """sonuclar/ klasöründen en son N dosyayı okur."""
    dosyalar = sorted(KLASORLER["sonuclar"].glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True)[:son_n]
    parcalar = []
    for d in reversed(dosyalar):
        async with aiofiles.open(d, encoding="utf-8") as f:
            icerik = await f.read()
        parcalar.append(f"--- {d.name} ---\n{icerik}")
    return "\n\n".join(parcalar) if parcalar else "(sonuç yok)"


async def kaynak_kodu_oku() -> str:
    """Projenin Python kaynak dosyalarını okur."""
    kaynak_dosyalar = sorted(BASE.glob("*.py"))
    parcalar = []
    for d in kaynak_dosyalar:
        async with aiofiles.open(d, encoding="utf-8") as f:
            icerik = await f.read()
        parcalar.append(f"=== {d.name} ===\n```python\n{icerik}\n```")
    return "\n\n".join(parcalar) if parcalar else "(kaynak kod bulunamadı)"


async def log_yaz(mesaj: str, seviye: str = "INFO"):
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satir = f"[{zaman}] [{seviye}] {mesaj}\n"
    log_dosya = KLASORLER["log"] / f"sistem_{datetime.now().strftime('%Y%m%d')}.log"
    async with aiofiles.open(log_dosya, "a", encoding="utf-8") as f:
        await f.write(satir)
