"""
Orkestra — 10 (ve dinamik olarak büyüyen) ajanları yönetir.
- Dinamik ajan ekleme: yeni_ajan talepleri işlenir
- Ouroboros: kod değişiklik önerileri periyodik uygulanır
- Disk: her turda doluluk kontrol edilir
- WebSocket ile UI'ya canlı durum gönderilir
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ajan import Ajan, PERSPEKTIFLER
import hafiza_yoneticisi as hm
import disk_yoneticisi as dm
import kod_degistirici as kd
import egitim_veri_uretici as evu
import model_egitici as me
import model_yoneticisi as my

BASE = Path(__file__).parent
BASLANGIC_AJAN_SAYISI = 10
MAKS_AJAN_SAYISI = 50  # Dinamik üretilen dahil


class Orkestra:
    def __init__(self, api_anahtari: str):
        self.api_anahtari = api_anahtari
        self.ajanlar: list[Ajan] = []
        self.tasklar: list[asyncio.Task] = []
        self.calisiyor = False
        self.toplam_tur = 0
        self.durum_callback: Optional[Callable] = None
        self._sonraki_ajan_id = BASLANGIC_AJAN_SAYISI
        self._yonetim_task: Optional[asyncio.Task] = None
        self._yonetim_sayac = 0
        self._olustur_baslangic_ajanlar()

    def _olustur_baslangic_ajanlar(self):
        self.ajanlar = [
            Ajan(ajan_id=i, api_anahtari=self.api_anahtari)
            for i in range(BASLANGIC_AJAN_SAYISI)
        ]

    def durum_callback_ayarla(self, callback: Callable):
        self.durum_callback = callback

    async def _ajan_tamamlama_callback(self, sonuc: dict):
        self.toplam_tur += 1
        sonuc["toplam_tur"] = self.toplam_tur
        if self.durum_callback:
            await self.durum_callback(sonuc)

    async def baslat(self):
        if self.calisiyor:
            return
        self.calisiyor = True
        await hm.log_yaz(f"Orkestra başladı — {len(self.ajanlar)} ajan")

        self.tasklar = [
            asyncio.create_task(
                ajan.sonsuz_dongu(
                    durum_callback=self._ajan_tamamlama_callback,
                    bekleme_suresi=5.0,
                ),
                name=f"ajan-{ajan.id}",
            )
            for ajan in self.ajanlar
        ]

        # Yönetim döngüsü: disk + kod değişiklik + dinamik ajan
        self._yonetim_task = asyncio.create_task(
            self._yonetim_dongusu(), name="yonetim"
        )
        await hm.log_yaz("Yönetim döngüsü başladı")

    async def _yonetim_dongusu(self):
        """Her 60 saniyede bir disk, kod değişiklik ve dinamik ajan kontrolü."""
        while self.calisiyor:
            try:
                await asyncio.sleep(60)

                # 1) Disk kontrolü
                await dm.disk_kontrol_ve_optimize(self.api_anahtari)

                # 2) Ouroboros: bekleyen kod değişikliklerini uygula
                sonuclar = await kd.bekleyen_onerileri_isle(self.api_anahtari)
                for s in sonuclar:
                    await hm.log_yaz(
                        f"Ouroboros: {s['mesaj']}", "OPT" if s["basarili"] else "WARN"
                    )

                # 3) Dinamik ajan talepleri
                await self._dinamik_ajan_isle()

                # 4) Eğitim verisi üretimi (her 30 dakikada bir)
                self._yonetim_sayac += 1
                if self._yonetim_sayac % 30 == 0:
                    await evu.dataset_olustur(self.api_anahtari)

                # 5) Model eğitimi (model_egitici kendi 6h guard'ını yönetir)
                await me.egitim_calistir(self.api_anahtari)

            except asyncio.CancelledError:
                break
            except Exception as e:
                await hm.log_yaz(f"Yönetim döngüsü hatası: {e}", "ERROR")

    async def _dinamik_ajan_isle(self):
        """onerilen_degisiklikler/ içindeki yeni_ajan taleplerini işler."""
        klasor = hm.KLASORLER["onerilen_degisiklikler"]
        yeni_ajan_dosyalari = sorted(klasor.glob("*yeni_ajan*.json"))

        for dosya in yeni_ajan_dosyalari:
            if len(self.ajanlar) >= MAKS_AJAN_SAYISI:
                await hm.log_yaz(f"Maks ajan sayısına ulaşıldı ({MAKS_AJAN_SAYISI})", "WARN")
                break
            try:
                talep = json.loads(dosya.read_text(encoding="utf-8"))
                perspektif = talep.get("perspektif", "").strip()
                gerekce = talep.get("gerekce", "")

                if not perspektif:
                    dosya.unlink(missing_ok=True)
                    continue

                # Aynı perspektif zaten var mı?
                mevcut_perspektifler = {a.perspektif for a in self.ajanlar}
                if perspektif in mevcut_perspektifler:
                    dosya.unlink(missing_ok=True)
                    continue

                # Yeni ajan oluştur
                yeni_id = self._sonraki_ajan_id
                self._sonraki_ajan_id += 1
                yeni_ajan = Ajan(
                    ajan_id=yeni_id,
                    api_anahtari=self.api_anahtari,
                    perspektif=perspektif,
                )
                self.ajanlar.append(yeni_ajan)

                # Task başlat
                task = asyncio.create_task(
                    yeni_ajan.sonsuz_dongu(
                        durum_callback=self._ajan_tamamlama_callback,
                        bekleme_suresi=5.0,
                    ),
                    name=f"ajan-{yeni_id}",
                )
                self.tasklar.append(task)

                dosya.unlink(missing_ok=True)
                await hm.log_yaz(
                    f"Dinamik ajan oluşturuldu: A{yeni_id} — {perspektif} | Gerekçe: {gerekce[:80]}"
                )

            except Exception as e:
                await hm.log_yaz(f"Dinamik ajan hatası: {e}", "ERROR")
                try:
                    dosya.unlink(missing_ok=True)
                except Exception:
                    pass

    async def durdur(self):
        if not self.calisiyor:
            return
        self.calisiyor = False
        for ajan in self.ajanlar:
            ajan.durdur()
        if self._yonetim_task:
            self._yonetim_task.cancel()
        for task in self.tasklar:
            task.cancel()
        await asyncio.gather(
            *self.tasklar,
            *([] if not self._yonetim_task else [self._yonetim_task]),
            return_exceptions=True,
        )
        await hm.log_yaz("Orkestra durduruldu")

    def durum_raporu(self) -> dict:
        return {
            "calisiyor": self.calisiyor,
            "ajan_sayisi": len(self.ajanlar),
            "toplam_tur": self.toplam_tur,
            "disk_doluluk": round(dm.disk_doluluk_yuzde(), 1),
            "model_durumu": my.get().ozet_rapor(),
            "zaman": datetime.now().isoformat(),
            "ajanlar": [
                {
                    "id": a.id,
                    "tur": a.tur,
                    "durum": a.durum,
                    "perspektif": a.perspektif,
                }
                for a in self.ajanlar
            ],
        }
