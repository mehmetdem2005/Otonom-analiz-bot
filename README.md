# Otonom Basyazar / NEXUS-120

Bu repo, DAL 1-15 mimarisiyle icerik uretimi, medya ciktilari, dagitim ve OSINT destekli bir otonom medya sistemi iskeleti sunar.

## DAL Agaci (1-15)

- DAL 1: Persona motoru
- DAL 2: Makale uretimi (Markdown)
- DAL 3: Kapak prompt uretimi
- DAL 4: Yerel arsiv yonetimi
- DAL 5: Canli haber fuzyonu (Serper + scraping girisi)
- DAL 6: Sesli podcast (Edge TTS)
- DAL 7: Otonom kapak cizimi (DALL-E / Pollinations)
- DAL 8: Dinamik veri gorsellestirme (Chart.js)
- DAL 9: Golge dagitim agi (X / Telegram)
- DAL 10: Reserved
- DAL 11: OSINT istihbarat radari
- DAL 12: Otonom video motoru
- DAL 13: Sonsuz tavsan deligi (derin genisleme)
- DAL 14: Chronos kahin modu
- DAL 15: Otonom yorum botlari

## Kod Mimarisi

- server.js: Uygulama giris noktasi (minimal bootstrap)
- routes/apiRoutes.js: DAL endpointleri
- services/aiService.js: Groq model cagri ve fallback model denemeleri
- services/contentService.js: metin temizleme, ozetleme, keyword/entity cikarimi, yorum/future promptlari
- utils/jsonStore.js: JSON okuma/yazma
- utils/processRunner.js: Python betiklerini stderr/stdout yakalayarak calistirma
- utils/logger.js: merkezi loglama
- public/index.html: kontrol paneli
- live_news_fusion.py: DAL 5
- tts_edge.py: DAL 6
- video-generator.js: DAL 12
- osint-radar.js: DAL 11
- comment-profiles.json: DAL 15 profil tanimlari
- archive.json: Makale arsivi
- alerts.json: OSINT alarmlari
- distribution-log.json: Dagitim kayitlari
- plan_ve_durum_listesi.txt: tik/X takip listesi
- prompt_kayitlari/: kullanici prompt arsivi

## Kurulum

1. Node paketlerini kurun:

```bash
npm install
```

2. Python bagimliliklarini kurun:

```bash
pip install requests beautifulsoup4 lxml edge-tts
```

3. Ortam degiskenlerini ayarlayin:

```bash
cp .env.example .env
```

`.env` icinde en kritik alanlar:

- GROQ_API_KEY
- GROQ_MODEL=openai/gpt-oss-120b
- SERPER_API_KEY
- WHALE_ALERT_API_KEY (opsiyonel)
- TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID (opsiyonel)
- DALL_E_API_KEY (opsiyonel)

## Ornek .env

```bash
GROQ_API_KEY=YOUR_GROQ_KEY
GROQ_MODEL=openai/gpt-oss-120b
SERPER_API_KEY=YOUR_SERPER_KEY
WHALE_ALERT_API_KEY=
DALL_E_API_KEY=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
PORT=3000
```

Not: Anahtarlarinizi asla repoya commit etmeyin.

## Calistirma

```bash
npm start
```

Tarayici:

```text
http://localhost:3000
```

Opsiyonel OSINT servis:

```bash
npm run osint
```

## DAL 7-10 Durumu

- DAL 7 aktif: Ucretsiz fallback ile kapak gorseli uretilir.
- DAL 8 aktif: Makaleden sayisal veri cekip Chart.js grafikleri olusturur.
- DAL 9 aktif: X flood parcalama + Telegram gonderim (kimlik bilgisi yoksa simulasyon).
- DAL 10 aktif: Reserved endpoint (`/api/dal10`).

## Kritik Not (Groq)

`Yapay Zeka yanit vermedi` hatasi alirsaniz:

1. `GROQ_API_KEY` degerini yenileyin.
2. `GROQ_MODEL=llama-3.3-70b-versatile` kullanin.
3. Sunucuyu yeniden baslatin.