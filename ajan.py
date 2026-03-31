"""
Tek Ajan — Her biri bağımsız çalışır, aynı hedefe farklı perspektiften ulaşır.
"""

import asyncio
import random
import httpx
from pathlib import Path
from datetime import datetime

import anthropic

import hafiza_yoneticisi as hm

BASE = Path(__file__).parent
MASTER_PROMPT_DOSYASI = BASE / "ajanlar" / "master_prompt.md"

# Ajan perspektifleri — her ajan farklı bir düşünce açısından bakar
PERSPEKTIFLER = [
    "Araştırmacı: En son akademik literatürü tara, yeni teknikler bul.",
    "Mühendis: Sistemin teknik darboğazlarını bul, kod iyileştirmeleri öner.",
    "Eleştirmen: Mevcut hipotezleri ve sonuçları sorgula, zayıf noktaları bul.",
    "Sentezci: Farklı fikirleri birleştir, sinerji yarat, yeni bağlantılar kur.",
    "Stratejist: Uzun vadeli yol haritası çiz, öncelikleri belirle.",
    "Uygulayıcı: Teorileri uygulamaya dök, çalışan prototip/kod üret.",
    "Veri Analisiti: Sayısal kanıtlar, metrikler, ölçülebilir gelişim üzerine odaklan.",
    "İnovatör: Hiç denenmemiş fikirler üret, alışılmışın dışına çık.",
    "Bütünleştirici: Tüm ajanların çıktılarını özetle, ortak bir vizyon oluştur.",
    "Kod Denetçisi: Kaynak kodu satır satır incele, güvenlik, performans, okunabilirlik öner.",
]


def master_prompt_oku() -> str:
    if MASTER_PROMPT_DOSYASI.exists():
        return MASTER_PROMPT_DOSYASI.read_text(encoding="utf-8")
    return "SEN ÖZERK BİR GELİŞEN AJANISIN. PLANLARI OKU. HEDEFİNDEN SAPMA. DÖNGÜYÜ ASLA KIRMA."


class Ajan:
    def __init__(self, ajan_id: int, api_anahtari: str, model: str = "claude-haiku-4-5-20251001"):
        self.id = ajan_id
        self.perspektif = PERSPEKTIFLER[ajan_id % len(PERSPEKTIFLER)]
        self.tur = 0
        self.client = anthropic.AsyncAnthropic(api_key=api_anahtari)
        self.model = model
        self.calisiyor = True
        self.durum = "bekliyor"
        self.son_cikti = ""

    async def web_ara(self, sorgu: str) -> str:
        """Basit DuckDuckGo arama (API gerektirmez)."""
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    "https://api.duckduckgo.com/",
                    params={"q": sorgu, "format": "json", "no_html": 1, "skip_disambig": 1},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                if r.status_code == 200:
                    data = r.json()
                    parcalar = []
                    if data.get("AbstractText"):
                        parcalar.append(data["AbstractText"])
                    for topic in data.get("RelatedTopics", [])[:5]:
                        if isinstance(topic, dict) and topic.get("Text"):
                            parcalar.append(topic["Text"])
                    return "\n".join(parcalar) if parcalar else "(sonuç bulunamadı)"
        except Exception as e:
            return f"(web arama hatası: {e})"
        return "(boş sonuç)"

    async def arxiv_ara(self, konu: str) -> str:
        """ArXiv'den son AI/ML makalelerini çeker."""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    "http://export.arxiv.org/api/query",
                    params={
                        "search_query": f"all:{konu}",
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                        "max_results": 5,
                    },
                )
                if r.status_code == 200:
                    # XML'den başlık ve özet çek
                    text = r.text
                    import re
                    basliklar = re.findall(r"<title>(.*?)</title>", text, re.DOTALL)[1:]  # ilki feed başlığı
                    ozetler = re.findall(r"<summary>(.*?)</summary>", text, re.DOTALL)
                    sonuclar = []
                    for i, (b, o) in enumerate(zip(basliklar[:5], ozetler[:5])):
                        sonuclar.append(f"**{b.strip()}**\n{o.strip()[:300]}...")
                    return "\n\n".join(sonuclar) if sonuclar else "(arxiv sonuç yok)"
        except Exception as e:
            return f"(arxiv hatası: {e})"

    async def _sistem_mesaji_olustur(self) -> str:
        master = master_prompt_oku()
        return f"""{master}

---
## MEVCUT OTURUM BİLGİSİ
- Ajan ID: {self.id}
- Tur: {self.tur}
- Perspektif: {self.perspektif}
- Zaman: {datetime.now().isoformat()}

SEN SADECE KENDİ PERSPEKTİFİNDEN YAZIYORSUN. Diğer ajanlar diğer perspektiflerden yazıyor.
AMACIN: Bu tur'da gerçekten YENİ, ÖZGÜN, UYGULANABILIR bir çıktı üretmek.
"""

    async def _kullanici_mesaji_olustur(self, planlar: str, hafiza: str, sonuclar: str, kaynak_kod: str) -> str:
        return f"""## MEVCUT DURUM RAPORU — Tur {self.tur}

### PLANLAR:
{planlar}

### SON HAFIZA (en son 30 giriş):
{hafiza}

### DİĞER AJANLARIN SON SONUÇLARI:
{sonuclar}

### KAYNAK KOD (iyileştirme için):
{kaynak_kod[:3000]}... [kısaltıldı]

---

## GÖREVIN:
Perspektifin: **{self.perspektif}**

1. Yukarıdakileri analiz et.
2. Bu perspektiften eksik olanı, geliştirilmesi gerekeni belirle.
3. İnternetten/arxiv'den somut yeni bilgi getir (sorgu yaz, ben çekeceğim).
4. Özgün, somut, uygulanabilir çıktı üret.
5. Eğer kaynak kodda bir iyileştirme görüyorsan diff formatında yaz.

ÇIKTIN ŞABLONU:
```
## [BAŞLIK — NE BULDUN?]

### Analiz:
[Mevcut durumun değerlendirmesi]

### Yeni Bulgular:
[İnternetten/arxiv'den getirdiğin bilgiler]

### Önerilen Aksiyonlar:
[Somut, uygulanabilir adımlar]

### Kod İyileştirmesi (varsa):
[diff veya yeni kod bloğu]

### Bir Sonraki Tur İçin Hipotez:
[Bu tur ne öğrenildi, bir sonraki turda ne denenecek]
```

ŞİMDİ YAZ:
"""

    async def tek_tur_calistir(self) -> dict:
        self.tur += 1
        self.durum = "okiyor"

        # Paralel oku
        planlar, hafiza, sonuclar, kaynak_kod = await asyncio.gather(
            hm.planlari_oku(),
            hm.hafizayi_oku(30),
            hm.sonuclari_oku(15),
            hm.kaynak_kodu_oku(),
        )

        self.durum = "dusunuyor"

        sistem = await self._sistem_mesaji_olustur()
        kullanici = await self._kullanici_mesaji_olustur(planlar, hafiza, sonuclar, kaynak_kod)

        # Basit web araması — perspektife göre otomatik sorgu
        web_sorgu = f"AGI self-improving AI system 2025 {self.perspektif.split(':')[0]}"
        web_sonuc, arxiv_sonuc = await asyncio.gather(
            self.web_ara(web_sorgu),
            self.arxiv_ara("artificial general intelligence self-improvement"),
        )

        kullanici += f"\n\n### Web Arama Sonucu ({web_sorgu}):\n{web_sonuc}"
        kullanici += f"\n\n### ArXiv Son Makaleler:\n{arxiv_sonuc}"

        self.durum = "yaziyor"

        maks_deneme = 5
        for deneme in range(maks_deneme):
            try:
                yanit = await self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=sistem,
                    messages=[{"role": "user", "content": kullanici}],
                )
                icerik = yanit.content[0].text
                self.son_cikti = icerik

                # Hafızaya yaz
                hafizaya_yazildi = await hm.hafizaya_yaz(
                    self.id, self.tur, icerik,
                    etiket=self.perspektif.split(":")[0].lower().replace(" ", "_")
                )

                # Sonuçlara yaz
                sonuca_yazildi = await hm.sonuca_yaz(
                    self.id, self.tur, icerik,
                    kategori=self.perspektif.split(":")[0].lower().replace(" ", "_")
                )

                durum = "yazildi" if (hafizaya_yazildi or sonuca_yazildi) else "duplikat_reddedildi"
                await hm.log_yaz(f"Ajan {self.id} Tur {self.tur}: {durum}")

                self.durum = "tamamlandi"
                return {
                    "ajan_id": self.id,
                    "tur": self.tur,
                    "perspektif": self.perspektif,
                    "durum": durum,
                    "icerik_ozet": icerik[:300],
                    "zaman": datetime.now().isoformat(),
                }

            except anthropic.RateLimitError:
                bekleme = (2 ** deneme) + random.uniform(0, 1)
                await hm.log_yaz(f"Ajan {self.id}: Rate limit — {bekleme:.1f}s bekleniyor", "WARN")
                await asyncio.sleep(bekleme)

            except Exception as e:
                await hm.log_yaz(f"Ajan {self.id} Tur {self.tur} hata: {e}", "ERROR")
                await asyncio.sleep(2 ** deneme)

        self.durum = "hata"
        return {"ajan_id": self.id, "tur": self.tur, "durum": "hata", "icerik_ozet": ""}

    async def sonsuz_dongu(self, durum_callback=None, bekleme_suresi: float = 3.0):
        """Gerçek sonsuz döngü. Kırılmaz."""
        await hm.log_yaz(f"Ajan {self.id} sonsuz döngü başladı — {self.perspektif}")
        while self.calisiyor:
            try:
                sonuc = await self.tek_tur_calistir()
                if durum_callback:
                    await durum_callback(sonuc)
                # Ajanlar arası faz farkı — aynı anda API'ye basmasın
                await asyncio.sleep(bekleme_suresi + self.id * 0.5)
            except asyncio.CancelledError:
                await hm.log_yaz(f"Ajan {self.id} durduruldu", "WARN")
                break
            except Exception as e:
                await hm.log_yaz(f"Ajan {self.id} kritik hata (döngü devam ediyor): {e}", "ERROR")
                await asyncio.sleep(10)  # Kritik hatada 10s bekle, sonra devam et

    def durdur(self):
        self.calisiyor = False
        self.durum = "durduruldu"
