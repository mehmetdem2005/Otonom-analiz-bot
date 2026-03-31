"""
Kod Değiştirici — Yılan Mantığı (Ouroboros)
Ajanlar birbirlerinin (ve kendi) kaynak kodunu değiştirebilir.

Akış:
  1. Ajan `onerilen_degisiklikler/` klasörüne JSON önerisi yazar
  2. Bu modül önerileri okur, doğrular, sözdizimi kontrol eder
  3. Yedek alır → uygular → py_compile ile kontrol eder
  4. Başarısızsa yedeği geri yükler
  5. Başarılıysa importlib ile modülü yeniden yükler
"""

import asyncio
import importlib
import json
import py_compile
import shutil
import sys
import tempfile
import aiofiles
from datetime import datetime
from pathlib import Path

import hafiza_yoneticisi as hm

BASE = Path(__file__).parent
YEDEK_KLASOR = BASE / "kaynak_kod_arsivi"
YEDEK_KLASOR.mkdir(exist_ok=True)

# Bu dosyalar değiştirilemez
KORUNAN_DOSYALAR = {"main.py", "kod_degistirici.py", ".env", ".env.example"}

_degisiklik_lock = asyncio.Lock()


def _yedek_al(dosya: Path) -> Path:
    """Dosyanın zaman damgalı yedeğini kaynak_kod_arsivi/ altına alır."""
    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    yedek = YEDEK_KLASOR / f"{dosya.stem}_{zaman}{dosya.suffix}"
    shutil.copy2(dosya, yedek)
    return yedek


def _sozdizimi_gecerli_mi(dosya: Path) -> tuple[bool, str]:
    """py_compile ile sözdizimi kontrolü yapar."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="w", encoding="utf-8") as tmp:
            tmp.write(dosya.read_text(encoding="utf-8"))
            tmp_yol = tmp.name
        py_compile.compile(tmp_yol, doraise=True)
        Path(tmp_yol).unlink(missing_ok=True)
        return True, ""
    except py_compile.PyCompileError as e:
        return False, str(e)
    except Exception as e:
        return False, str(e)


async def oneri_uygula(oneri_dosyasi: Path, api_anahtari: str = "") -> dict:
    """
    Tek bir öneri dosyasını okur ve uygulamaya çalışır.
    Döndürür: {"basarili": bool, "mesaj": str, "oneri": dict}
    """
    async with _degisiklik_lock:
        try:
            async with aiofiles.open(oneri_dosyasi, encoding="utf-8") as f:
                icerik = await f.read()
            oneri = json.loads(icerik)
        except Exception as e:
            return {"basarili": False, "mesaj": f"JSON parse hatası: {e}", "oneri": {}}

        hedef_adi = oneri.get("hedef_dosya", "")
        eski_kod = oneri.get("eski_kod", "")
        yeni_kod = oneri.get("yeni_kod", "")
        gerekce = oneri.get("gerekce", "")

        # Temel doğrulama
        if not hedef_adi or not eski_kod or not yeni_kod:
            return {"basarili": False, "mesaj": "Eksik alan: hedef_dosya / eski_kod / yeni_kod", "oneri": oneri}

        if hedef_adi in KORUNAN_DOSYALAR:
            return {"basarili": False, "mesaj": f"Korunan dosya: {hedef_adi}", "oneri": oneri}

        hedef = BASE / hedef_adi
        if not hedef.exists() or not hedef.is_file():
            return {"basarili": False, "mesaj": f"Dosya bulunamadı: {hedef_adi}", "oneri": oneri}

        # Hedef dosya proje dizininde mi?
        try:
            hedef.resolve().relative_to(BASE.resolve())
        except ValueError:
            return {"basarili": False, "mesaj": "Proje dışı dosya değişikliği reddedildi", "oneri": oneri}

        mevcut_icerik = hedef.read_text(encoding="utf-8")

        if eski_kod not in mevcut_icerik:
            return {"basarili": False, "mesaj": "eski_kod dosyada bulunamadı", "oneri": oneri}

        if eski_kod == yeni_kod:
            return {"basarili": False, "mesaj": "eski_kod ve yeni_kod aynı", "oneri": oneri}

        # Yedek al
        yedek = _yedek_al(hedef)

        # Değişikliği uygula
        yeni_icerik = mevcut_icerik.replace(eski_kod, yeni_kod, 1)
        hedef.write_text(yeni_icerik, encoding="utf-8")

        # Sözdizimi kontrolü
        gecerli, hata = _sozdizimi_gecerli_mi(hedef)
        if not gecerli:
            # Geri al
            shutil.copy2(yedek, hedef)
            await hm.log_yaz(f"Kod değişikliği reddedildi (sözdizimi hatası): {hedef_adi} — {hata}", "ERROR")
            return {"basarili": False, "mesaj": f"Sözdizimi hatası, geri alındı: {hata}", "oneri": oneri}

        # Modülü yeniden yükle (eğer yüklenmiş ise)
        modul_adi = hedef.stem
        if modul_adi in sys.modules:
            try:
                importlib.reload(sys.modules[modul_adi])
                await hm.log_yaz(f"Modül yeniden yüklendi: {modul_adi}", "INFO")
            except Exception as e:
                await hm.log_yaz(f"Modül yeniden yükleme hatası: {e}", "WARN")

        # Öneri dosyasını işlenmiş olarak işaretle (sil)
        oneri_dosyasi.rename(oneri_dosyasi.with_suffix(".uyguland"))

        mesaj = f"✓ {hedef_adi} değiştirildi | Gerekçe: {gerekce[:100]}"
        await hm.log_yaz(mesaj, "OPT")
        return {"basarili": True, "mesaj": mesaj, "oneri": oneri}


async def bekleyen_onerileri_isle(api_anahtari: str = "") -> list[dict]:
    """
    onerilen_degisiklikler/ klasöründeki tüm .json öneri dosyalarını işler.
    Her turda çağrılır.
    """
    klasor = hm.KLASORLER["onerilen_degisiklikler"]
    oneri_dosyalari = sorted(klasor.glob("*.json"), key=lambda x: x.stat().st_mtime)

    if not oneri_dosyalari:
        return []

    sonuclar = []
    for dosya in oneri_dosyalari[:5]:  # Tur başına max 5 öneri işle
        sonuc = await oneri_uygula(dosya, api_anahtari)
        sonuclar.append(sonuc)
        await asyncio.sleep(0.1)

    return sonuclar


def oneri_olustur(
    ajan_id: int,
    hedef_dosya: str,
    eski_kod: str,
    yeni_kod: str,
    gerekce: str,
) -> dict:
    """Ajan için öneri dict'i oluşturur (JSON olarak kaydedilecek)."""
    return {
        "hedef_dosya": hedef_dosya,
        "eski_kod": eski_kod,
        "yeni_kod": yeni_kod,
        "gerekce": gerekce,
        "ajan_id": ajan_id,
        "zaman": datetime.now().isoformat(),
    }
