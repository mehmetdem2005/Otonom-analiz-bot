"""
Orkestra — 10 ajanı yönetir, sonsuz döngüyü koordine eder,
WebSocket ile UI'ya canlı durum gönderir.
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from ajan import Ajan
import hafiza_yoneticisi as hm

AJAN_SAYISI = 10


class Orkestra:
    def __init__(self, api_anahtari: str):
        self.api_anahtari = api_anahtari
        self.ajanlar: list[Ajan] = []
        self.tasklar: list[asyncio.Task] = []
        self.calisiyor = False
        self.toplam_tur = 0
        self.durum_callback: Optional[Callable] = None
        self._olustur_ajanlar()

    def _olustur_ajanlar(self):
        self.ajanlar = [
            Ajan(
                ajan_id=i,
                api_anahtari=self.api_anahtari,
                model="claude-haiku-4-5-20251001",  # Ekonomik, hızlı
            )
            for i in range(AJAN_SAYISI)
        ]

    def durum_kaydet(self, callback: Callable):
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
        await hm.log_yaz(f"Orkestra başladı — {AJAN_SAYISI} ajan aktif")

        # Her ajan kendi sonsuz döngüsünde çalışır
        self.tasklar = [
            asyncio.create_task(
                ajan.sonsuz_dongu(
                    durum_callback=self._ajan_tamamlama_callback,
                    bekleme_suresi=5.0,
                )
            )
            for ajan in self.ajanlar
        ]

        await hm.log_yaz("Tüm ajan görevleri oluşturuldu")

    async def durdur(self):
        if not self.calisiyor:
            return
        self.calisiyor = False
        for ajan in self.ajanlar:
            ajan.durdur()
        for task in self.tasklar:
            task.cancel()
        await asyncio.gather(*self.tasklar, return_exceptions=True)
        await hm.log_yaz("Orkestra durduruldu")

    def durum_raporu(self) -> dict:
        return {
            "calisiyor": self.calisiyor,
            "ajan_sayisi": AJAN_SAYISI,
            "toplam_tur": self.toplam_tur,
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
