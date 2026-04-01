"""
Hafıza Yöneticisi — Tüm ajanların ortak belleği.
SHA-256 duplikat engeli, okuma/yazma, JSON öneri kaydı, kod değişiklik önerisi.
"""

import hashlib
import json
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

for k in KLASORLER.values():
    k.mkdir(parents=True, exist_ok=True)

HASH_INDEX_DOSYASI = BASE / "hafiza" / "_hash_index.json"
_hash_lock = asyncio.Lock()
_hash_index: set[str] = set()


def _hash_index_yukle():
    global _hash_index
    if HASH_INDEX_DOSYASI.exists():
        try:
            with open(HASH_INDEX_DOSYASI, encoding="utf-8") as f:
                _hash_index = set(json.load(f))
        except Exception:
            _hash_index = set()
    else:
        _hash_index = set()


_hash_index_yukle()


def icerik_hash(icerik: str) -> str:
    return hashlib.sha256(icerik.strip().encode("utf-8")).hexdigest()


async def duplikat_mi(icerik: str) -> bool:
    h = icerik_hash(icerik)
    async with _hash_lock:
        return h in _hash_index


async def _hash_kaydet(icerik: str):
    h = icerik_hash(icerik)
    async with _hash_lock:
        _hash_index.add(h)
        async with aiofiles.open(HASH_INDEX_DOSYASI, "w", encoding="utf-8") as f:
            await f.write(json.dumps(list(_hash_index)))


async def hafizaya_yaz(ajan_id: int, tur: int, icerik: str, etiket: str = "genel") -> bool:
    # Duplikat kontrol geçici kapatıldı (debug)
    # if await duplikat_mi(icerik):
    #     return False
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya = KLASORLER["hafiza"] / f"{zaman}_a{ajan_id:02d}_t{tur:04d}_{etiket[:20]}.md"
    meta = f"---\najan: {ajan_id}\ntur: {tur}\nzaman: {datetime.now().isoformat()}\netiket: {etiket}\n---\n\n"
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(meta + icerik)
    await _hash_kaydet(icerik)
    return True


async def sonuca_yaz(ajan_id: int, tur: int, icerik: str, kategori: str = "analiz") -> bool:
    if await duplikat_mi(icerik):
        return False
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya = KLASORLER["sonuclar"] / f"{zaman}_a{ajan_id:02d}_{kategori[:20]}.md"
    meta = f"---\najan: {ajan_id}\ntur: {tur}\nzaman: {datetime.now().isoformat()}\nkategori: {kategori}\n---\n\n"
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(meta + icerik)
    await _hash_kaydet(icerik)
    return True


async def kod_degisiklik_oner(
    ajan_id: int,
    hedef_dosya: str,
    eski_kod: str,
    yeni_kod: str,
    gerekce: str,
):
    """Ouroboros — ajan kaynak koda değişiklik önerisi yazar (JSON)."""
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya = KLASORLER["onerilen_degisiklikler"] / f"{zaman}_a{ajan_id:02d}_{hedef_dosya[:25]}.json"
    oneri = {
        "hedef_dosya": hedef_dosya,
        "eski_kod": eski_kod,
        "yeni_kod": yeni_kod,
        "gerekce": gerekce,
        "ajan_id": ajan_id,
        "zaman": datetime.now().isoformat(),
    }
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(json.dumps(oneri, ensure_ascii=False, indent=2))


async def yeni_ajan_iste(ajan_id: int, perspektif: str, gerekce: str):
    """Ajan yeni bir ajan oluşturulmasını talep eder."""
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    dosya = KLASORLER["onerilen_degisiklikler"] / f"{zaman}_yeni_ajan_a{ajan_id:02d}.json"
    talep = {
        "tip": "yeni_ajan",
        "perspektif": perspektif,
        "gerekce": gerekce,
        "isteyen_ajan": ajan_id,
        "zaman": datetime.now().isoformat(),
    }
    async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
        await f.write(json.dumps(talep, ensure_ascii=False, indent=2))


async def planlari_oku() -> str:
    parcalar = []
    dosyalar = sorted(KLASORLER["planlar"].glob("*.md")) + sorted(KLASORLER["planlar"].glob("*.txt"))
    for d in dosyalar:
        try:
            async with aiofiles.open(d, encoding="utf-8") as f:
                icerik = await f.read()
            if icerik.strip():
                parcalar.append(f"=== {d.name} ===\n{icerik}")
        except Exception:
            pass
    return "\n\n".join(parcalar) if parcalar else "(henüz plan yok)"


async def hafizayi_oku(son_n: int = 30) -> str:
    dosyalar = sorted(
        [d for d in KLASORLER["hafiza"].glob("*.md") if not d.name.startswith("_")],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )[:son_n]
    parcalar = []
    for d in reversed(dosyalar):
        try:
            async with aiofiles.open(d, encoding="utf-8") as f:
                icerik = await f.read()
            parcalar.append(f"--- {d.name} ---\n{icerik}")
        except Exception:
            pass
    return "\n\n".join(parcalar) if parcalar else "(hafıza boş)"


async def sonuclari_oku(son_n: int = 20) -> str:
    dosyalar = sorted(
        KLASORLER["sonuclar"].glob("*.md"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )[:son_n]
    parcalar = []
    for d in reversed(dosyalar):
        try:
            async with aiofiles.open(d, encoding="utf-8") as f:
                icerik = await f.read()
            parcalar.append(f"--- {d.name} ---\n{icerik}")
        except Exception:
            pass
    return "\n\n".join(parcalar) if parcalar else "(sonuç yok)"


async def kaynak_kodu_oku() -> str:
    kaynak_dosyalar = sorted(BASE.glob("*.py"))
    parcalar = []
    for d in kaynak_dosyalar:
        try:
            async with aiofiles.open(d, encoding="utf-8") as f:
                icerik = await f.read()
            parcalar.append(f"=== {d.name} ===\n```python\n{icerik}\n```")
        except Exception:
            pass
    return "\n\n".join(parcalar) if parcalar else "(kaynak kod bulunamadı)"


async def log_yaz(mesaj: str, seviye: str = "INFO"):
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    satir = f"[{zaman}] [{seviye}] {mesaj}\n"
    log_dosya = KLASORLER["log"] / f"sistem_{datetime.now().strftime('%Y%m%d')}.log"
    try:
        async with aiofiles.open(log_dosya, "a", encoding="utf-8") as f:
            await f.write(satir)
    except Exception:
        pass
