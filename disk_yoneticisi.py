"""
Disk Yöneticisi — Sunucu dolmadan önce akıllıca optimizasyon yapar.
Doluluk seviyesine göre 3 aşamalı tepki:
  %75 → Kısa/gereksiz dosyaları sil
  %85 → LLM ile eski hafızayı özetle (kısa cümleleri at)
  %92 → Logları arşivle, eski sonuçları sil
"""

import asyncio
import shutil
import aiofiles
from pathlib import Path
from datetime import datetime, timedelta

import hafiza_yoneticisi as hm
import llm_istemci as llm

BASE = Path(__file__).parent

# Dosya 200 karakterden kısaysa anlamsız kabul et
MIN_ANLAMLI_BOYUT = 200
# Hafıza dosyası 30 günden eskiyse özetlenebilir
OZET_YASINDA_GUN = 7
# Her optimizasyon çağrısından sonra kaç saniye beklenir
OPT_ARALIK_SANIYE = 300  # 5 dakika

_son_optimizasyon: datetime = datetime.min
_opt_lock = asyncio.Lock()


def disk_doluluk_yuzde() -> float:
    """Projenin bulunduğu disk bölümünün doluluk yüzdesi."""
    kullanim = shutil.disk_usage(BASE)
    return (kullanim.used / kullanim.total) * 100


def klasor_boyutu_mb(klasor: Path) -> float:
    return sum(f.stat().st_size for f in klasor.rglob("*") if f.is_file()) / (1024 * 1024)


async def kisa_dosyalari_sil() -> int:
    """200 karakterden küçük hafıza ve sonuç dosyalarını siler."""
    silindi = 0
    for klasor_adi in ("hafiza", "sonuclar"):
        klasor = hm.KLASORLER[klasor_adi]
        for dosya in list(klasor.glob("*.md")):
            if dosya.name.startswith("_"):
                continue
            try:
                boyut = dosya.stat().st_size
                if boyut < MIN_ANLAMLI_BOYUT:
                    dosya.unlink()
                    silindi += 1
            except Exception:
                pass
    await hm.log_yaz(f"Disk opt: {silindi} kısa dosya silindi", "OPT")
    return silindi


async def eski_loglari_temizle() -> int:
    """30 günden eski log dosyalarını siler."""
    sinir = datetime.now() - timedelta(days=30)
    silindi = 0
    for log in list(hm.KLASORLER["log"].glob("*.log")):
        try:
            t = datetime.fromtimestamp(log.stat().st_mtime)
            if t < sinir:
                log.unlink()
                silindi += 1
        except Exception:
            pass
    await hm.log_yaz(f"Disk opt: {silindi} eski log silindi", "OPT")
    return silindi


async def eski_sonuclari_temizle(tut_son_n: int = 500) -> int:
    """sonuclar/ klasöründe en son N dışındakileri siler."""
    dosyalar = sorted(
        hm.KLASORLER["sonuclar"].glob("*.md"),
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    sildi = 0
    for d in dosyalar[tut_son_n:]:
        try:
            d.unlink()
            sildi += 1
        except Exception:
            pass
    await hm.log_yaz(f"Disk opt: eski {sildi} sonuç dosyası silindi", "OPT")
    return sildi


async def hafizayi_ozetle_ve_sikistir(
    llm_saglayici: str,
    api_anahtari: str,
    model_adi: str,
    max_dosya: int = 20,
) -> int:
    """
    Eski hafıza dosyalarını LLM ile özetler:
    - Gereksiz kısa cümleleri atar
    - İçeriği yoğunlaştırır
    - Orijinal dosyanın yerini alır
    """
    sinir_tarihi = datetime.now() - timedelta(days=OZET_YASINDA_GUN)
    dosyalar = sorted(
        [
            d for d in hm.KLASORLER["hafiza"].glob("*.md")
            if not d.name.startswith("_")
            and datetime.fromtimestamp(d.stat().st_mtime) < sinir_tarihi
        ],
        key=lambda x: x.stat().st_mtime,
    )[:max_dosya]

    if not dosyalar:
        return 0

    ozetlen = 0

    for dosya in dosyalar:
        try:
            async with aiofiles.open(dosya, encoding="utf-8") as f:
                icerik = await f.read()

            if len(icerik) < 300:
                dosya.unlink()
                ozetlen += 1
                continue

            ozet = await llm.metin_uret(
                (
                    "Sen bir bilgi sıkıştırma motorusun. "
                    "Verilen metni şu kurallara göre yoğunlaştır:\n"
                    "1. Gereksiz kısa cümleleri (50 karakterden az) çıkar.\n"
                    "2. Tekrar eden bilgileri tek cümleye indir.\n"
                    "3. Somut olmayan felsefi sözleri at.\n"
                    "4. Asıl içgörü ve eyleme geçirilebilir bilgileri koru.\n"
                    "5. Çıktın orijinalin en fazla %40'ı kadar olsun.\n"
                    "6. Yorum ekleme, sadece sıkıştır."
                ),
                icerik[:3000],
                max_tokens=512,
                saglayici=llm_saglayici,
                api_key=api_anahtari,
                model=model_adi,
            )
            ozet = ozet.strip()

            if ozet:
                ozet_ile_meta = f"---\n[SIKIŞTIRILD: {datetime.now().isoformat()}]\n---\n\n{ozet}"
                async with aiofiles.open(dosya, "w", encoding="utf-8") as f:
                    await f.write(ozet_ile_meta)
                ozetlen += 1

        except Exception as e:
            await hm.log_yaz(f"Özet hatası ({dosya.name}): {e}", "WARN")

    await hm.log_yaz(f"Disk opt: {ozetlen} hafıza dosyası özetlendi/sıkıştırıldı", "OPT")
    return ozetlen


async def disk_kontrol_ve_optimize(llm_saglayici: str, api_anahtari: str, model_adi: str):
    """
    Ana optimizasyon fonksiyonu. Doluluk seviyesine göre tepki verir.
    OPT_ARALIK_SANIYE süreden önce tekrar çalışmaz.
    """
    global _son_optimizasyon

    async with _opt_lock:
        gecen = (datetime.now() - _son_optimizasyon).total_seconds()
        if gecen < OPT_ARALIK_SANIYE:
            return

        doluluk = disk_doluluk_yuzde()
        await hm.log_yaz(f"Disk doluluk: %{doluluk:.1f}", "OPT")

        if doluluk >= 92:
            await hm.log_yaz("KRİTİK disk doluluk (%92+) — agresif temizlik", "WARN")
            await kisa_dosyalari_sil()
            await eski_loglari_temizle()
            await eski_sonuclari_temizle(tut_son_n=200)
            await hafizayi_ozetle_ve_sikistir(llm_saglayici, api_anahtari, model_adi, max_dosya=50)

        elif doluluk >= 85:
            await hm.log_yaz("Yüksek disk doluluk (%85+) — hafıza özeti", "WARN")
            await kisa_dosyalari_sil()
            await hafizayi_ozetle_ve_sikistir(llm_saglayici, api_anahtari, model_adi, max_dosya=20)

        elif doluluk >= 75:
            await hm.log_yaz("Orta disk doluluk (%75+) — kısa dosya temizliği", "INFO")
            await kisa_dosyalari_sil()
            await eski_loglari_temizle()

        _son_optimizasyon = datetime.now()
