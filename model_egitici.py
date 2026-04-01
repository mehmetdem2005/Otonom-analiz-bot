"""
Model Eğitici — Fine-tuning pipeline.

Akış:
  egitim_verisi/dataset_XXXX.jsonl
    → Base model indir (HuggingFace)
      → LoRA / QLoRA fine-tuning (unsloth veya transformers+peft)
        → Değerlendir
          → Ollama'ya aktar (GGUF)
            → model_yoneticisi'ne kaydet
              → Ajanlar artık bu modeli kullanır

GPU yoksa: CPU'da küçük modelle yavaş ama işler.
"""

import asyncio
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import hafiza_yoneticisi as hm
import model_yoneticisi as my
from egitim_veri_uretici import son_dataset, dataset_boyutu

BASE = Path(__file__).parent
MODELLER_DIZIN = BASE / "modeller"
MODELLER_DIZIN.mkdir(exist_ok=True)

# Son eğitimden bu yana geçmesi gereken minimum süre (saniye)
MIN_EGITIM_ARALIK = 3600 * 6  # 6 saat

_son_egitim_zamani: float = 0.0


def _unsloth_kurulu_mu() -> bool:
    try:
        import unsloth  # noqa: F401
        return True
    except ImportError:
        return False


def _transformers_kurulu_mu() -> bool:
    try:
        import transformers  # noqa: F401
        import peft          # noqa: F401
        import trl           # noqa: F401
        return True
    except ImportError:
        return False


def _ollama_kurulu_mu() -> bool:
    return subprocess.run(
        ["which", "ollama"], capture_output=True
    ).returncode == 0


# Güvenli yüklenebilecek paket listesi — LLM çıktısından gelen istek bu listeyle kontrol edilir
_IZINLI_PAKETLER = frozenset({
    "unsloth", "unsloth[colab-new]",
    "transformers", "peft", "trl", "accelerate", "bitsandbytes",
    "datasets", "torch", "torchvision",
})


async def _paket_yukle(paket: str):
    """Eksik paketi runtime'da yükler (yalnızca izin verilenler)."""
    temel = paket.split("[")[0].lower()
    if temel not in {p.split("[")[0].lower() for p in _IZINLI_PAKETLER}:
        await hm.log_yaz(f"İzinsiz paket yükleme engellendi: {paket}", "ERROR")
        return False
    await hm.log_yaz(f"Paket yükleniyor: {paket}", "TRAIN")
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", "-q", paket,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        await hm.log_yaz(f"Paket yükleme hatası ({paket}): {stderr.decode()[:200]}", "ERROR")
        return False
    return True


async def unsloth_ile_egit(
    model_adi: str,
    hf_adi: str,
    veri_dosyasi: Path,
    cikti_dizini: Path,
) -> bool:
    """
    Unsloth ile hızlı LoRA fine-tuning.
    Unsloth kurulu değilse otomatik yükler.
    """
    if not _unsloth_kurulu_mu():
        ok = await _paket_yukle("unsloth[colab-new]")
        if not ok:
            return False

    try:
        from unsloth import FastLanguageModel
        from trl import SFTTrainer
        from transformers import TrainingArguments
        from datasets import Dataset
        import torch

        await hm.log_yaz(f"Unsloth ile {model_adi} eğitimi başlıyor...", "TRAIN")

        # Veriyi yükle
        ornekler = [json.loads(l) for l in veri_dosyasi.open(encoding="utf-8")]

        # Modeli yükle (4bit quantized)
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=hf_adi,
            max_seq_length=2048,
            load_in_4bit=True,
        )

        # LoRA ekle
        model = FastLanguageModel.get_peft_model(
            model,
            r=16,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_alpha=16,
            lora_dropout=0,
            bias="none",
        )

        # Dataset hazırla
        def formatlayici(ornek):
            mesajlar = ornek["messages"]
            metin = tokenizer.apply_chat_template(mesajlar, tokenize=False)
            return {"text": metin}

        dataset = Dataset.from_list(ornekler).map(formatlayici)

        # Eğit
        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            dataset_text_field="text",
            max_seq_length=2048,
            args=TrainingArguments(
                per_device_train_batch_size=2,
                gradient_accumulation_steps=4,
                warmup_steps=5,
                max_steps=60,
                learning_rate=2e-4,
                fp16=not torch.cuda.is_bf16_supported(),
                bf16=torch.cuda.is_bf16_supported(),
                logging_steps=10,
                optim="adamw_8bit",
                output_dir=str(cikti_dizini),
                save_strategy="no",
            ),
        )
        trainer.train()

        # GGUF olarak kaydet (Ollama için)
        gguf_yolu = cikti_dizini / f"{model_adi}.gguf"
        model.save_pretrained_gguf(str(cikti_dizini), tokenizer, quantization_method="q4_k_m")

        await hm.log_yaz(f"Unsloth eğitimi tamamlandı: {model_adi}", "TRAIN")
        return True

    except Exception as e:
        await hm.log_yaz(f"Unsloth eğitim hatası: {e}", "ERROR")
        return False


async def transformers_ile_egit(
    model_adi: str,
    hf_adi: str,
    veri_dosyasi: Path,
    cikti_dizini: Path,
) -> bool:
    """
    Standart transformers + peft LoRA fine-tuning.
    Unsloth yoksa fallback olarak kullanılır.
    """
    if not _transformers_kurulu_mu():
        for paket in ["transformers", "peft", "trl", "accelerate", "bitsandbytes", "datasets"]:
            await _paket_yukle(paket)

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer, TrainingArguments, BitsAndBytesConfig
        from peft import LoraConfig, get_peft_model, TaskType
        from trl import SFTTrainer
        from datasets import Dataset
        import torch

        await hm.log_yaz(f"Transformers ile {model_adi} eğitimi başlıyor...", "TRAIN")

        ornekler = [json.loads(l) for l in veri_dosyasi.open(encoding="utf-8")]

        bnb_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.float16,
        )

        tokenizer = AutoTokenizer.from_pretrained(hf_adi)
        tokenizer.pad_token = tokenizer.eos_token

        model = AutoModelForCausalLM.from_pretrained(
            hf_adi,
            quantization_config=bnb_config,
            device_map="auto",
        )

        lora_config = LoraConfig(
            r=8,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        )
        model = get_peft_model(model, lora_config)

        def formatlayici(ornek):
            mesajlar = ornek["messages"]
            metin = " ".join(f"{m['role']}: {m['content']}" for m in mesajlar)
            return {"text": metin}

        dataset = Dataset.from_list(ornekler).map(formatlayici)

        trainer = SFTTrainer(
            model=model,
            tokenizer=tokenizer,
            train_dataset=dataset,
            dataset_text_field="text",
            max_seq_length=1024,
            args=TrainingArguments(
                output_dir=str(cikti_dizini),
                num_train_epochs=1,
                per_device_train_batch_size=1,
                gradient_accumulation_steps=4,
                learning_rate=2e-4,
                fp16=True,
                logging_steps=10,
                save_strategy="no",
            ),
        )
        trainer.train()
        model.save_pretrained(str(cikti_dizini))
        tokenizer.save_pretrained(str(cikti_dizini))

        await hm.log_yaz(f"Transformers eğitimi tamamlandı: {model_adi}", "TRAIN")
        return True

    except Exception as e:
        await hm.log_yaz(f"Transformers eğitim hatası: {e}", "ERROR")
        return False


async def ollama_ya_yukle(model_adi: str, model_dizini: Path) -> bool:
    """
    Eğitilmiş GGUF modelini Ollama'ya yükler.
    Böylece ajanlar HTTP API üzerinden kullanabilir.
    """
    if not _ollama_kurulu_mu():
        await hm.log_yaz("Ollama kurulu değil — model kaydedildi ama servis edilemiyor", "WARN")
        return False

    # Modelfile oluştur
    gguf_dosyalari = list(model_dizini.glob("*.gguf"))
    if not gguf_dosyalari:
        # Adapter-only (transformers) — dönüştürme gerekli
        await hm.log_yaz("GGUF bulunamadı, Ollama yüklemesi atlandı", "WARN")
        return False

    gguf = gguf_dosyalari[0]
    modelfile_icerik = (
        f"FROM {gguf.resolve()}\n"
        f"SYSTEM \"Sen 100 trilyon parametre kalitesine ulaşmayı hedefleyen otonom bir yapay zeka sistemisin.\"\n"
    )
    modelfile_yolu = model_dizini / "Modelfile"
    modelfile_yolu.write_text(modelfile_icerik)

    proc = await asyncio.create_subprocess_exec(
        "ollama", "create", model_adi, "-f", str(modelfile_yolu),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        await hm.log_yaz(f"Ollama yükleme hatası: {stderr.decode()[:200]}", "ERROR")
        return False

    await hm.log_yaz(f"Ollama'ya yüklendi: {model_adi}", "TRAIN")
    return True


async def egitim_calistir(api_anahtari: str = "") -> bool:
    """
    Ana eğitim fonksiyonu. Orkestra tarafından periyodik çağrılır.
    Yeterli veri yoksa veya çok yakın zamanda eğitim yapıldıysa atlar.
    """
    global _son_egitim_zamani
    import time

    gecen = time.time() - _son_egitim_zamani
    if gecen < MIN_EGITIM_ARALIK:
        await hm.log_yaz(
            f"Eğitim atlandı: son eğitimden {int(gecen/3600)}s geçmiş (min {MIN_EGITIM_ARALIK//3600}s)",
            "TRAIN",
        )
        return False

    yonetici = my.get()
    hedef = yonetici.sonraki_hedef()
    if not hedef or not hedef.get("hf_adi"):
        await hm.log_yaz("Sonraki hedef model belirlenemedi", "WARN")
        return False

    veri = son_dataset()
    if not veri or dataset_boyutu(veri) < 50:
        await hm.log_yaz("Yetersiz eğitim verisi — dataset üretimi bekleniyor", "TRAIN")
        return False

    model_adi = hedef["adi"]
    hf_adi = hedef["hf_adi"]
    cikti = MODELLER_DIZIN / model_adi
    cikti.mkdir(exist_ok=True)

    await hm.log_yaz(
        f"EĞİTİM BAŞLIYOR: {model_adi} ({hedef['parametre_milyar']}B) | "
        f"Veri: {dataset_boyutu(veri)} örnek",
        "TRAIN",
    )

    # Unsloth tercih edilir, yoksa transformers
    if _unsloth_kurulu_mu():
        basari = await unsloth_ile_egit(model_adi, hf_adi, veri, cikti)
    else:
        basari = await transformers_ile_egit(model_adi, hf_adi, veri, cikti)

    if not basari:
        return False

    # Ollama'ya yükle
    await ollama_ya_yukle(model_adi, cikti)

    # Kayıt
    kayit = my.ModelKayit(
        adi=model_adi,
        yol=str(cikti),
        parametre_milyar=hedef["parametre_milyar"],
        kalite_skoru=0.5,  # İlk tahmini skor; ileride değerlendirme ile güncellenir
        egitim_veri_sayisi=dataset_boyutu(veri),
    )
    yonetici.model_kaydet(kayit)

    _son_egitim_zamani = time.time()

    await hm.log_yaz(
        f"EĞİTİM TAMAMLANDI: {model_adi} ({hedef['parametre_milyar']}B) "
        f"→ Sistem artık bu modeli kullanabilir",
        "TRAIN",
    )
    return True
