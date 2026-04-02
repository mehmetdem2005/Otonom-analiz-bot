# Otonom Ajan Sistemi (Port 8000)

Bu repo artik sadece Port 8000 uzerinde calisan Python/FastAPI tabanli otonom ajan sistemine odaklidir.

## Amac
- Cok ajanli (10 paralel ajan) dusunme ve analiz uretimi
- Hafiza, plan ve sonuc dosyalariyla surekli ogrenme dongusu
- LLM tabanli karar verme ve sistem ici iyilestirme onerileri

## Ana Bilesenler
- main.py: Uygulama giris noktasi (Uvicorn)
- web_arayuzu.py: FastAPI endpointleri + arayuz + websocket
- orkestra.py: Ajan yasam dongusu yonetimi
- ajan.py: Ajan davranisi ve tur calisma akisi
- llm_istemci.py: LLM baglanti katmani
- hafiza_yoneticisi.py: Hafiza/sonuc/log dosya yonetimi
- model_egitici.py, model_yoneticisi.py, egitim_veri_uretici.py: egitim ve veri hattlari

## Calistirma
1. Sanal ortami aktif et:

```bash
source .venv/bin/activate
```

2. Bagimliliklari kur (gerekirse):

```bash
pip install -r requirements.txt
```

3. Sunucuyu baslat:

```bash
python main.py
```

4. Arayuz:

```text
http://localhost:8000
```

## Notlar
- Port 3000/Node tarafi bu repodan kaldirilmistir.
- Sistem LLM anahtari olmadan sinirli/fallback modunda calisabilir.
- Ortam degiskenleri `.env` dosyasindan okunur.
