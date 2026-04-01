"""
Model Yöneticisi — Sistemin sinir sistemi.

Döngü:
  Claude API (başlangıç)
    → phi-2 2.7B fine-tuned (ilk adım)
      → mistral-7b fine-tuned
        → llama-13b fine-tuned
          → ... (sonsuz, her döngü daha büyük)

Agents buradan hangi modeli kullanacaklarını öğrenir.
Claude API her zaman fallback olarak kalır.
"""

import json
import asyncio
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

import hafiza_yoneticisi as hm

BASE = Path(__file__).parent
KAYIT_DOSYASI = BASE / "modeller" / "_kayit.json"
OLLAMA_URL = "http://localhost:11434"

# Model ilerleme basamakları — küçükten büyüğe
# Her biri bir öncekinin fine-tuned halidir
HEDEF_SIRASI = [
    {"adi": "phi-2",             "parametre_milyar": 2.7,   "hf_adi": "microsoft/phi-2"},
    {"adi": "mistral-7b",        "parametre_milyar": 7.0,   "hf_adi": "mistralai/Mistral-7B-Instruct-v0.3"},
    {"adi": "llama-13b",         "parametre_milyar": 13.0,  "hf_adi": "meta-llama/Llama-2-13b-chat-hf"},
    {"adi": "mixtral-8x7b",      "parametre_milyar": 56.0,  "hf_adi": "mistralai/Mixtral-8x7B-Instruct-v0.1"},
    {"adi": "llama-70b",         "parametre_milyar": 70.0,  "hf_adi": "meta-llama/Llama-2-70b-chat-hf"},
    {"adi": "llama-405b",        "parametre_milyar": 405.0, "hf_adi": "meta-llama/Meta-Llama-3.1-405B-Instruct"},
    # Buradan sonra sistem kendi hedeflerini üretir
]


@dataclass
class ModelKayit:
    adi: str
    yol: str                      # Yerel dosya yolu veya "api:claude"
    parametre_milyar: float
    kalite_skoru: float           # 0.0 – 1.0, test setinden
    egitim_veri_sayisi: int       # Kaç örnekle eğitildi
    zaman: str = field(default_factory=lambda: datetime.now().isoformat())
    aktif: bool = True


class ModelYoneticisi:
    def __init__(self):
        self.kayitlar: list[ModelKayit] = []
        self._yukle()

    def _yukle(self):
        if KAYIT_DOSYASI.exists():
            try:
                veri = json.loads(KAYIT_DOSYASI.read_text(encoding="utf-8"))
                self.kayitlar = [ModelKayit(**k) for k in veri]
            except Exception:
                self.kayitlar = []

    def _kaydet(self):
        KAYIT_DOSYASI.parent.mkdir(exist_ok=True)
        KAYIT_DOSYASI.write_text(
            json.dumps([asdict(k) for k in self.kayitlar], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def model_kaydet(self, kayit: ModelKayit):
        # Aynı isimde eski kaydı deaktive et
        for k in self.kayitlar:
            if k.adi == kayit.adi:
                k.aktif = False
        self.kayitlar.append(kayit)
        self._kaydet()

    def en_iyi_lokal_model(self) -> ModelKayit | None:
        """En yüksek parametre sayılı ve aktif yerel modeli döndürür."""
        lokaller = [
            k for k in self.kayitlar
            if k.aktif and not k.yol.startswith("api:")
        ]
        if not lokaller:
            return None
        return max(lokaller, key=lambda k: k.parametre_milyar)

    def lokal_model_hazir_mi(self) -> bool:
        m = self.en_iyi_lokal_model()
        if not m:
            return False
        return Path(m.yol).exists()

    def aktif_model_adi(self) -> str:
        """Ollama'ya gönderilecek model adı."""
        m = self.en_iyi_lokal_model()
        return m.adi if m else "claude-fallback"

    def sonraki_hedef(self) -> dict | None:
        """
        Şu ana kadar eğitilen en büyük modelin üstündeki
        HEDEF_SIRASI'ndan bir sonraki hedefi döndürür.
        """
        en_buyuk = max(
            (k.parametre_milyar for k in self.kayitlar if k.aktif and not k.yol.startswith("api:")),
            default=0.0,
        )
        for hedef in HEDEF_SIRASI:
            if hedef["parametre_milyar"] > en_buyuk:
                return hedef
        # Tüm listeyi bitirdiyse sonsuz büyüme — yeni bir hedef üret
        return {
            "adi": f"custom-{int(en_buyuk * 2)}b",
            "parametre_milyar": en_buyuk * 2,
            "hf_adi": None,  # Sisteme kendi mimarisini öner
        }

    async def ollama_calistiriyor_mu(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{OLLAMA_URL}/api/tags")
                return r.status_code == 200
        except Exception:
            return False

    def ozet_rapor(self) -> dict:
        return {
            "toplam_model": len(self.kayitlar),
            "en_iyi_lokal": self.en_iyi_lokal_model().adi if self.en_iyi_lokal_model() else "yok",
            "sonraki_hedef": self.sonraki_hedef(),
            "lokal_hazir": self.lokal_model_hazir_mi(),
        }


# Singleton
_yonetici: ModelYoneticisi | None = None


def get() -> ModelYoneticisi:
    global _yonetici
    if _yonetici is None:
        _yonetici = ModelYoneticisi()
    return _yonetici
