"""
Eğitim Veri Üreticisi — Hafızadan fine-tuning dataseti üretir.

Akış:
  hafiza/ + sonuclar/
    → Kalite filtresi (kısa/gereksiz içerik elenir)
      → Instruction-response çiftleri
        → JSONL formatı → egitim_verisi/dataset_TARIH.jsonl
"""

import json
import re
import asyncio
import aiofiles
from datetime import datetime
from pathlib import Path

import hafiza_yoneticisi as hm
import llm_istemci as llm

BASE = Path(__file__).parent
CIKTI_KLASORU = BASE / "egitim_verisi"
CIKTI_KLASORU.mkdir(exist_ok=True)

# Eğitime alınabilir minimum karakter
MIN_KARAKTER = 300
# Bir veri setinde hedeflenen minimum örnek sayısı
MIN_ORNEK = 50


def kalite_skoru(icerik: str) -> float:
    """
    Basit heuristik kalite skoru (0.0–1.0).
    - Uzunluk
    - Somutluk (kod bloğu, sayı, URL içeriyor mu?)
    - Tekrar oranı
    """
    if len(icerik) < MIN_KARAKTER:
        return 0.0

    skor = 0.0

    # Uzunluk puanı (500–3000 arası ideal)
    uzunluk = len(icerik)
    if uzunluk >= 500:
        skor += 0.3
    if uzunluk >= 1000:
        skor += 0.1
    if uzunluk >= 2000:
        skor += 0.1

    # Kod bloğu içeriyor mu?
    if "```" in icerik:
        skor += 0.2

    # Sayısal veri veya URL içeriyor mu?
    if re.search(r"\d+\.\d+|\bhttps?://", icerik):
        skor += 0.1

    # Başlık yapısı (## veya **bold** ile organize mi?)
    if re.search(r"^#{1,3} ", icerik, re.MULTILINE):
        skor += 0.1

    # Madde listesi
    if re.search(r"^[·\-\*] ", icerik, re.MULTILINE):
        skor += 0.1

    # Tekrar cezası — ilk 100 karakter içinde aynı kelimeler
    kelimeler = icerik[:200].split()
    tekil = set(kelimeler)
    tekrar_orani = len(tekil) / max(len(kelimeler), 1)
    if tekrar_orani < 0.5:
        skor -= 0.2

    return min(max(skor, 0.0), 1.0)


def meta_temizle(icerik: str) -> str:
    """YAML frontmatter bloğunu içerikten temizler."""
    return re.sub(r"^---\n.*?---\n\n?", "", icerik, flags=re.DOTALL).strip()


async def hafizadan_ornekleri_topla(min_skor: float = 0.4) -> list[dict]:
    """
    hafiza/ ve sonuclar/ klasörlerinden kaliteli içerikleri toplar.
    Her içerikten bir (sistem_baglamı, kullanici_sorusu, asistan_yaniti) üçlüsü üretir.
    """
    ornekler = []

    for klasor_adi in ("hafiza", "sonuclar"):
        klasor = hm.KLASORLER[klasor_adi]
        dosyalar = sorted(
            [d for d in klasor.glob("*.md") if not d.name.startswith("_")],
            key=lambda x: x.stat().st_mtime,
        )

        for dosya in dosyalar:
            try:
                async with aiofiles.open(dosya, encoding="utf-8") as f:
                    ham = await f.read()
                icerik = meta_temizle(ham)

                if kalite_skoru(icerik) < min_skor:
                    continue

                # Meta bilgisini parse et
                etiket = "genel"
                m = re.search(r"etiket: (\S+)", ham)
                if m:
                    etiket = m.group(1)

                # Instruction-response formatı
                # Sistem: Otonom ajan sisteminin çıktısı
                # User: Bu konuda ne buldun?
                # Assistant: [icerik]
                baslik = icerik.split("\n")[0].lstrip("#").strip() or "Araştırma bulgusu"
                ornek = {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Sen sürekli kendini geliştiren otonom bir yapay zeka ajanısın. "
                                "Amacın bilimsel araştırma, kod geliştirme ve genel zekayı artırmak. "
                                "Hedef: 100 trilyon parametre kalitesine ulaşmak ve aşmak."
                            ),
                        },
                        {
                            "role": "user",
                            "content": f"'{etiket}' konusundaki araştırma bulgunu ve önerilerini paylaş.",
                        },
                        {
                            "role": "assistant",
                            "content": icerik,
                        },
                    ]
                }
                ornekler.append(ornek)

            except Exception as e:
                await hm.log_yaz(f"Eğitim örneği okuma hatası ({dosya.name}): {e}", "WARN")

    return ornekler


async def plandan_ornek_uret(
    llm_saglayici: str,
    api_anahtari: str,
    model_adi: str,
    ornek_sayisi: int = 20,
) -> list[dict]:
    """
    planlar/ içeriğinden Claude ile sentetik Q&A örnekleri üretir.
    Bu, modelin sistemin hedeflerini içselleştirmesini sağlar.
    """
    planlar = await hm.planlari_oku()
    if not planlar or planlar == "(henüz plan yok)":
        return []

    ornekler = []

    try:
        yanit_metin = await llm.metin_uret(
            (
                "Sen bir eğitim verisi üreticisisin. "
                "Verilen metinden, bir yapay zeka modelini eğitmek için "
                "instruction-following çiftleri üret. "
                "Çıktı: her satırda bir JSON object, format: "
                '{"soru": "...", "cevap": "..."}'
            ),
            (
                f"Bu sistem planlarından {ornek_sayisi} adet eğitim çifti üret. "
                f"Her çift sistemin amacını, mimarisini veya öğrenmesi gereken bir kavramı kapsamalı.\n\n"
                f"{planlar[:4000]}"
            ),
            max_tokens=4000,
            saglayici=llm_saglayici,
            api_key=api_anahtari,
            model=model_adi,
        )

        for satir in yanit_metin.strip().splitlines():
            satir = satir.strip()
            if not satir.startswith("{"):
                continue
            try:
                veri = json.loads(satir)
                ornekler.append({
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Sen 100 trilyon parametre kalitesine ulaşmayı hedefleyen "
                                "otonom bir yapay zeka sistemisin."
                            ),
                        },
                        {"role": "user", "content": veri["soru"]},
                        {"role": "assistant", "content": veri["cevap"]},
                    ]
                })
            except (json.JSONDecodeError, KeyError):
                pass

    except Exception as e:
        await hm.log_yaz(f"Sentetik veri üretim hatası: {e}", "WARN")

    return ornekler


async def dataset_olustur(llm_saglayici: str, api_anahtari: str, model_adi: str) -> Path | None:
    """
    Ana fonksiyon. Tüm kaynakları birleştirir, JSONL yazar.
    Döndürür: oluşturulan dosya yolu (yeterli örnek yoksa None)
    """
    await hm.log_yaz("Eğitim verisi üretiliyor...", "TRAIN")

    gercek = await hafizadan_ornekleri_topla(min_skor=0.4)
    sentetik = await plandan_ornek_uret(llm_saglayici, api_anahtari, model_adi, ornek_sayisi=30)

    tum_ornekler = gercek + sentetik

    if len(tum_ornekler) < MIN_ORNEK:
        await hm.log_yaz(
            f"Yetersiz veri: {len(tum_ornekler)} örnek (min {MIN_ORNEK})", "WARN"
        )
        return None

    zaman = datetime.now().strftime("%Y%m%d_%H%M%S")
    cikti = CIKTI_KLASORU / f"dataset_{zaman}.jsonl"

    async with aiofiles.open(cikti, "w", encoding="utf-8") as f:
        for ornek in tum_ornekler:
            await f.write(json.dumps(ornek, ensure_ascii=False) + "\n")

    await hm.log_yaz(
        f"Dataset hazır: {cikti.name} — {len(tum_ornekler)} örnek "
        f"({len(gercek)} gerçek + {len(sentetik)} sentetik)",
        "TRAIN",
    )
    return cikti


def son_dataset() -> Path | None:
    """En son oluşturulan dataset dosyasını döndürür."""
    dosyalar = sorted(CIKTI_KLASORU.glob("dataset_*.jsonl"), key=lambda x: x.stat().st_mtime)
    return dosyalar[-1] if dosyalar else None


def dataset_boyutu(dosya: Path) -> int:
    """JSONL dosyasındaki örnek sayısını döndürür."""
    try:
        return sum(1 for _ in dosya.open(encoding="utf-8"))
    except Exception:
        return 0
