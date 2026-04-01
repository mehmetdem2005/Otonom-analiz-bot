"""
Ajan — Ouroboros mantığı ile çalışan otonom birim.
- Yalnızca 200 onaylı siteden internet verisi çeker
- Kaynak kodunu okur, değişiklik önerir (diğer ajanların kodunu da)
- Yeni ajan talep edebilir
- Gerçek sonsuz döngü — hiçbir hata döngüyü kıramaz
"""

import asyncio
import json
import random
import re
import httpx
from pathlib import Path
from datetime import datetime

import hafiza_yoneticisi as hm
import model_yoneticisi as my
import llm_istemci as llm
from kaynak_siteler import TUM_SITELER, HABER_SITELERI, ARASTIRMA_SITELERI

BASE = Path(__file__).parent
MASTER_PROMPT_DOSYASI = BASE / "ajanlar" / "master_prompt.md"

PERSPEKTIFLER = [
    "Araştırmacı: En son akademik literatürü tara, yeni teknikler bul.",
    "Mühendis: Sistemin teknik darboğazlarını bul, kod iyileştirmeleri öner.",
    "Eleştirmen: Mevcut hipotezleri ve sonuçları sorgula, zayıf noktaları bul.",
    "Sentezci: Farklı fikirleri birleştir, sinerji yarat, yeni bağlantılar kur.",
    "Stratejist: Uzun vadeli yol haritası çiz, öncelikleri belirle.",
    "Uygulayıcı: Teorileri uygulamaya dök, çalışan prototip/kod üret.",
    "Veri Analisti: Sayısal kanıtlar, metrikler, ölçülebilir gelişim üzerine odaklan.",
    "İnovatör: Hiç denenmemiş fikirler üret, alışılmışın dışına çık.",
    "Bütünleştirici: Tüm ajanların çıktılarını özetle, ortak bir vizyon oluştur.",
    "Kod Denetçisi: Kaynak kodu satır satır incele, güvenlik, performans, okunabilirlik öner.",
]

# HTML etiketlerini temizlemek için basit regex
_HTML_TAG = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s{3,}")


def _html_temizle(metin: str) -> str:
    temiz = _HTML_TAG.sub(" ", metin)
    temiz = _WHITESPACE.sub("\n", temiz)
    return temiz.strip()


def master_prompt_oku() -> str:
    if MASTER_PROMPT_DOSYASI.exists():
        return MASTER_PROMPT_DOSYASI.read_text(encoding="utf-8")
    return "SEN ÖZERK BİR GELİŞEN AJANISIN. PLANLARI OKU. HEDEFİNDEN SAPMA. DÖNGÜYÜ ASLA KIRMA."


class Ajan:
    def __init__(
        self,
        ajan_id: int,
        api_anahtari: str,
        llm_saglayici: str = "anthropic",
        model: str = "claude-haiku-4-5-20251001",
        perspektif: str = "",
    ):
        self.id = ajan_id
        self.perspektif = perspektif or PERSPEKTIFLER[ajan_id % len(PERSPEKTIFLER)]
        self.tur = 0
        self.api_anahtari = api_anahtari
        self.llm_saglayici = llm_saglayici
        self.model = model
        self.calisiyor = True
        self.durum = "bekliyor"
        self.son_cikti = ""
        self._http_headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; OtonomAjanBot/1.0; +research)"
            ),
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en,tr,zh;q=0.9",
        }

    # ─── İnternet Araçları ───────────────────────────────────────────────────

    async def _site_getir(self, url: str, karakter_siniri: int = 2500) -> str:
        """Onaylı bir siteden metin içerik çeker."""
        # Whitelist kontrolü
        izinli = any(url.startswith(s) or s in url for s in TUM_SITELER)
        if not izinli:
            return f"(izinsiz site, atlandı: {url})"

        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
                r = await c.get(url, headers=self._http_headers)
                if r.status_code != 200:
                    return f"(HTTP {r.status_code}: {url})"
                html = r.text

            # BeautifulSoup varsa kullan, yoksa regex ile temizle
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html, "html.parser")
                # Gereksiz elementleri kaldır
                for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form"]):
                    tag.decompose()
                metin = soup.get_text(separator="\n", strip=True)
            except ImportError:
                metin = _html_temizle(html)

            # Sadece anlamlı satırları al (50+ karakter)
            satirlar = [s.strip() for s in metin.splitlines() if len(s.strip()) > 50]
            ozet = "\n".join(satirlar[:40])
            return ozet[:karakter_siniri] if ozet else "(içerik çıkarılamadı)"

        except Exception as e:
            return f"(bağlantı hatası: {type(e).__name__})"

    async def rastgele_sitelerden_beslen(self, haber_n: int = 2, arastirma_n: int = 2) -> str:
        """Rastgele onaylı sitelerden içerik çekip birleştirir."""
        secilen_haberler = random.sample(HABER_SITELERI, min(haber_n, len(HABER_SITELERI)))
        secilen_arastirma = random.sample(ARASTIRMA_SITELERI, min(arastirma_n, len(ARASTIRMA_SITELERI)))

        gorevler = [
            self._site_getir(url) for url in (secilen_haberler + secilen_arastirma)
        ]
        sonuclar = await asyncio.gather(*gorevler, return_exceptions=True)

        parcalar = []
        for url, sonuc in zip(secilen_haberler + secilen_arastirma, sonuclar):
            if isinstance(sonuc, Exception):
                parcalar.append(f"[{url}] — hata: {sonuc}")
            else:
                parcalar.append(f"[{url}]\n{sonuc}")

        return "\n\n---\n\n".join(parcalar)

    async def arxiv_ara(self, konu: str, n: int = 4) -> str:
        """ArXiv'den son makaleleri çeker (ARASTIRMA_SITELERI'nde yer alır)."""
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                r = await c.get(
                    "https://export.arxiv.org/api/query",
                    params={
                        "search_query": f"all:{konu}",
                        "sortBy": "submittedDate",
                        "sortOrder": "descending",
                        "max_results": n,
                    },
                )
            if r.status_code != 200:
                return "(arxiv erişim hatası)"
            basliklar = re.findall(r"<title>(.*?)</title>", r.text, re.DOTALL)[1:]
            ozetler = re.findall(r"<summary>(.*?)</summary>", r.text, re.DOTALL)
            parcalar = [
                f"**{b.strip()}**\n{o.strip()[:250]}"
                for b, o in zip(basliklar[:n], ozetler[:n])
            ]
            return "\n\n".join(parcalar) if parcalar else "(arxiv sonuç yok)"
        except Exception as e:
            return f"(arxiv hatası: {e})"

    # ─── Prompt Oluşturma ────────────────────────────────────────────────────

    def _sistem_mesaji(self) -> str:
        master = master_prompt_oku()
        return (
            f"{master}\n\n---\n"
            f"Ajan ID: {self.id} | Tur: {self.tur} | "
            f"Perspektif: {self.perspektif} | Zaman: {datetime.now().isoformat()}\n\n"
            "SADECE KENDİ PERSPEKTİFİNDEN YAZ. ÖZGÜN OL. DÖNGÜYÜ KIRMA."
        )

    def _kullanici_mesaji(
        self,
        planlar: str,
        hafiza: str,
        sonuclar: str,
        kaynak_kod: str,
        internet_verisi: str,
        arxiv_verisi: str,
    ) -> str:
        return f"""## TUR {self.tur} — DURUM RAPORU

### PLANLAR (Değişmez Hedefler):
{planlar}

### SON HAFIZA (son 30):
{hafiza[:4000]}

### DİĞER AJANLARIN SON SONUÇLARI:
{sonuclar[:3000]}

### INTERNET VERİSİ (200 onaylı siteden):
{internet_verisi[:3000]}

### ARXİV SON MAKALELER:
{arxiv_verisi}

### KAYNAK KOD (değişiklik önerebilirsin):
{kaynak_kod[:4000]}

---

## GÖREV — Perspektifin: {self.perspektif}

1. Tüm verileri analiz et.
2. Bu perspektiften ÖZGÜN ve SOMUT bir çıktı üret.
3. Eğer kaynak kodda iyileştirme varsa şu JSON formatında yaz (```json bloğu içinde):
   {{"hedef_dosya": "ajan.py", "eski_kod": "...", "yeni_kod": "...", "gerekce": "..."}}
4. Eğer yeni bir ajan perspektifi gerekiyorsa: {{"tip": "yeni_ajan", "perspektif": "...", "gerekce": "..."}}

ÇIKTIN:
```
## [BAŞLIK]

### Analiz:
[Mevcut durumun değerlendirmesi]

### Yeni Bulgular (internetten):
[Getirilen bilgiler]

### Önerilen Aksiyonlar:
[Somut adımlar]

### Bir Sonraki Tur Hipotezi:
[Ne denenecek]
```
"""

    # ─── Tek Tur ─────────────────────────────────────────────────────────────

    async def tek_tur_calistir(self) -> dict:
        self.tur += 1
        self.durum = "okuyor"

        planlar, hafiza, sonuclar, kaynak_kod = await asyncio.gather(
            hm.planlari_oku(),
            hm.hafizayi_oku(30),
            hm.sonuclari_oku(15),
            hm.kaynak_kodu_oku(),
        )

        self.durum = "internet"
        internet_verisi, arxiv_verisi = await asyncio.gather(
            self.rastgele_sitelerden_beslen(haber_n=2, arastirma_n=2),
            self.arxiv_ara("artificial general intelligence self-improvement autonomous"),
        )

        self.durum = "düşünüyor"
        sistem = self._sistem_mesaji()
        kullanici = self._kullanici_mesaji(
            planlar, hafiza, sonuclar, kaynak_kod, internet_verisi, arxiv_verisi
        )

        self.durum = "yazıyor"
        maks_deneme = 6
        for deneme in range(maks_deneme):
            try:
                icerik = await self._api_cagir(sistem, kullanici)
                self.son_cikti = icerik

                # Ouroboros: JSON kod önerisini çıkar ve kaydet
                await self._ouroboros_isle(icerik)

                etiket = self.perspektif.split(":")[0].lower().replace(" ", "_")
                hw = await hm.hafizaya_yaz(self.id, self.tur, icerik, etiket=etiket)
                sw = await hm.sonuca_yaz(self.id, self.tur, icerik, kategori=etiket)

                durum = "yazildi" if (hw or sw) else "duplikat_reddedildi"
                await hm.log_yaz(f"A{self.id} T{self.tur}: {durum}")

                self.durum = "tamamlandı"
                return {
                    "ajan_id": self.id,
                    "tur": self.tur,
                    "perspektif": self.perspektif,
                    "durum": durum,
                    "icerik_ozet": icerik[:300],
                    "zaman": datetime.now().isoformat(),
                }

            except Exception as e:
                mesaj = str(e).lower()
                if "rate" in mesaj or "429" in mesaj:
                    bekleme = min(2 ** deneme + random.uniform(0, 2), 60)
                    await hm.log_yaz(f"A{self.id}: RateLimit — {bekleme:.1f}s", "WARN")
                    await asyncio.sleep(bekleme)
                    continue
                await asyncio.sleep(2 ** min(deneme, 4))
                await hm.log_yaz(f"A{self.id} T{self.tur} genel hata: {type(e).__name__}: {e}", "ERROR")

        self.durum = "hata"
        return {"ajan_id": self.id, "tur": self.tur, "durum": "hata", "icerik_ozet": "", "zaman": datetime.now().isoformat()}

    async def _api_cagir(self, sistem: str, kullanici: str) -> str:
        """Ollama hazırsa yerel modeli kullan, yoksa seçili bulut LLM'e düş."""
        yonetici = my.get()
        if yonetici.lokal_model_hazir_mi() and await yonetici.ollama_calistiriyor_mu():
            return await self._ollama_cagir(sistem, kullanici)
        return await self._bulut_llm_cagir(sistem, kullanici)

    async def _ollama_cagir(self, sistem: str, kullanici: str) -> str:
        model_adi = my.get().aktif_model_adi()
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model_adi,
                    "messages": [
                        {"role": "system", "content": sistem},
                        {"role": "user", "content": kullanici},
                    ],
                    "stream": False,
                },
            )
            return r.json()["message"]["content"]

    async def _bulut_llm_cagir(self, sistem: str, kullanici: str) -> str:
        return await llm.metin_uret(
            sistem,
            kullanici,
            max_tokens=2048,
            saglayici=self.llm_saglayici,
            api_key=self.api_anahtari,
            model=self.model,
        )

    async def _ouroboros_isle(self, icerik: str):
        """Yanıttaki JSON kod değişikliği ve yeni ajan taleplerini işler."""
        # Tüm ```json bloklarını bul
        json_bloklar = re.findall(r"```json\s*(.*?)\s*```", icerik, re.DOTALL)
        for blok in json_bloklar:
            try:
                veri = json.loads(blok)
                if veri.get("tip") == "yeni_ajan":
                    await hm.yeni_ajan_iste(
                        self.id,
                        veri.get("perspektif", ""),
                        veri.get("gerekce", ""),
                    )
                elif all(k in veri for k in ("hedef_dosya", "eski_kod", "yeni_kod")):
                    await hm.kod_degisiklik_oner(
                        self.id,
                        veri["hedef_dosya"],
                        veri["eski_kod"],
                        veri["yeni_kod"],
                        veri.get("gerekce", ""),
                    )
            except (json.JSONDecodeError, KeyError):
                pass

    # ─── Sonsuz Döngü ────────────────────────────────────────────────────────

    async def sonsuz_dongu(self, durum_callback=None, bekleme_suresi: float = 5.0):
        """Gerçek sonsuz döngü. CancelledError dışında hiçbir şey kıramaz."""
        await hm.log_yaz(f"A{self.id} başladı — {self.perspektif}")
        while self.calisiyor:
            try:
                sonuc = await self.tek_tur_calistir()
                if durum_callback:
                    await durum_callback(sonuc)
                await asyncio.sleep(bekleme_suresi + self.id * 0.7)
            except asyncio.CancelledError:
                await hm.log_yaz(f"A{self.id} durduruldu", "WARN")
                break
            except Exception as e:
                # Hiçbir hata döngüyü kırmaz
                await hm.log_yaz(f"A{self.id} kritik hata (devam): {type(e).__name__}: {e}", "ERROR")
                await asyncio.sleep(15)

    def durdur(self):
        self.calisiyor = False
        self.durum = "durduruldu"
