# PLAN KÖKü — ANA İNDEKS & KEŞİF TEKNİĞİ
Zaman: 2026-03-31
Kaynak: sistem

---

## TEKNİK: DOSYA:SATIR REFERANS SİSTEMİ

Her bölüm `DOSYA:SATIR` formatında adreslenmiştir.
Ajan bir konuya bakmak istediğinde:
1. Bu dosyada kategori başlığını bul
2. İlgili `DOSYA:SATIR` adresine git
3. O satırdan itibaren bir sonraki `##` başlığına kadar oku

Arama için kullanılabilecek etiketler her kategorinin altında `#etiket` formatında listelenmiştir.
Grep ile arama: `#kairos` → yanit4, L11

---

## HIZLI REF TABLOSU

| Arıyorum | Dosya | Satır |
|---|---|---|
| Agent döngüsü nasıl çalışır? | yanit1 | L36 |
| State machine durumları | yanit1 | L11 |
| Tool nasıl kaydedilir? | yanit1 | L186 |
| Dosya okuma/yazma aracı | yanit2 | L81 |
| Shell komutu çalıştırma | yanit2 | L81 |
| Araçlar paralel nasıl çalışır? | yanit2 | L165 |
| İzin sistemi nasıl kurulur? | yanit2 | L34 |
| Sandbox stratejisi | yanit2 | L281 |
| Kısa süreli bellek yönetimi | yanit3 | L11 |
| Vektör veritabanı kurulumu | yanit3 | L95 |
| Oturum kaydı ve yükleme | yanit3 | L212 |
| Rüya/dream sistemi nedir? | yanit3 | L262 |
| KAIROS modu nasıl çalışır? | yanit4 | L11 |
| BUDDY tamagotchi sistemi | yanit4 | L92 |
| ULTRAPLAN uzun planlama | yanit4 | L246 |
| Undercover gizli mod | yanit4 | L330 |
| NPM vs binary dağıtım | yanit5 | L11 |
| Source map sızıntısı önlemi | yanit5 | L82 |
| Güncelleme mekanizması | yanit5 | L117 |
| Axios RAT saldırısı dersleri | yanit5 | L152 |
| Kullanıcı direktifi (otonom bot) | direktif_otonom_bot_sistemi | L1 |

---

## KATEGORİ 1 — MİMARİ
`#mimari #state-machine #event-loop #agent-core #döngü`

Ajanın iskelet yapısı. Buradan başlanır.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Agent Durum Makinesi | yanit1 | L11 | IDLE→THINKING→TOOL_CALLING→DREAMING geçiş mantığı |
| Ana Döngü (Event Loop) | yanit1 | L36 | `while(true)` + switch/case ile sürekli çalışma |
| thinkAndAct() | yanit1 | L82 | Model çağrısı, tool call mı normal yanıt mı? |
| executeTools() | yanit1 | L128 | Tool sıralı yürütme, onay, sonuçları modele ilet |
| İlk Çalıştırma (initialize) | yanit1 | L304 | Oturum yükleme, araç kaydı, mod başlatma |

---

## KATEGORİ 2 — ARAÇ SİSTEMİ
`#tool #registry #araç #function-calling #tool-call`

Ajanın elleri. Ne yapabildiğini belirler.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Tool Registry (temel) | yanit1 | L186 | register(), execute(), getToolDefinitions() |
| Tool Registry (gelişmiş) | yanit2 | L11 | riskLevel, allowParallel, maxConcurrent eklendi |
| Dosya Araçları (read/write) | yanit2 | L81 | İzin kontrolü ile read_file, write_file |
| Shell Komutu (execute_command) | yanit2 | L81 | Zaman aşımı, cwd, onay mekanizması |
| Paralel Yürütme | yanit2 | L165 | Promise.all, gruplandırma, eşzamanlılık limiti |
| send_user_file | yanit2 | L187 | Dosyayı kullanıcıya gönderme aracı |
| push_notification | yanit2 | L187 | OS native veya terminal bildirimi |
| terminal_buddy (araç) | yanit2 | L187 | BUDDY ile feed/play/status etkileşimi |

---

## KATEGORİ 3 — PROMPT & API
`#prompt #api #model #sistem-promptu #dinamik-prompt`

Modele ne söyleneceği ve nasıl çağrıldığı.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Model API Çağrısı | yanit1 | L232 | Anthropic API fetch, tool definitions gönderimi |
| Prompt Yönetimi | yanit1 | L260 | Moda göre dinamik sistem promptu oluşturma |
| Terminal Entegrasyonu | yanit1 | L282 | readline ile stdin okuma |
| buildMessages() | yanit1 | L82 | Sistem promptu + geçmiş + araç tanımları birleştirme |

---

## KATEGORİ 4 — GÜVENLİK & SANDBOX
`#güvenlik #izin #sandbox #permission #risk`

Neye izin verilip verilmeyeceği.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| İzin Sistemi (PermissionManager) | yanit2 | L34 | allowPath/allowCommand, statik + dinamik izin |
| Onay Mekanizması | yanit2 | L34 | requireConfirmation(), yüksek riskli araçlar |
| Sandboxing (SandboxedExecutor) | yanit2 | L281 | Sınırlı PATH, child_process izolasyonu |
| Güvenlik En İyi Uygulamaları | yanit2 | L329 | 5 madde: sınırla, geçici klasör, anonimleştir, imzala |
| Tedarik Zinciri Saldırısı | yanit5 | L152 | Axios RAT olayı, bağımlılık dondurma |
| Source Map Koruması | yanit5 | L82 | sourcesContent: false, .npmignore, CI kontrolü |
| Gizli Modların Korunması | yanit5 | L152 | String gizleme yetersiz — lisanslama kullan |

---

## KATEGORİ 5 — BELLEK SİSTEMİ
`#bellek #memory #hafıza #vektör #embedding #oturum`

Ajanın ne hatırladığı ve nasıl hatırladığı.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Kısa Süreli Bellek | yanit3 | L11 | Mesaj listesi, token tahmini, otomatik özetleme |
| Özetleyici (Summarizer) | yanit3 | L11 | Bağlam taştığında eski mesajları modele özetletme |
| Anı Çıkarma (MemoryExtractor) | yanit3 | L95 | Konuşmadan önemli bilgileri madde madde çıkar |
| Vektör Veritabanı (ChromaDB) | yanit3 | L95 | addMemory, searchMemories, cosine similarity |
| Embedding Üretimi | yanit3 | L95 | text-embedding-3-small ile vektörleştirme |
| Bağlama Anı Ekleme | yanit3 | L95 | getRelevantMemories() → sistem promptuna ekleme |
| Oturum Yönetimi | yanit3 | L212 | save/load session, disk kalıcılığı |
| Bellek + Agent Entegrasyonu | yanit3 | L404 | Tüm bileşenlerin AgentCore'a bağlanması |

---

## KATEGORİ 6 — RÜYA SİSTEMİ
`#rüya #dream #konsolidasyon #autoDream #çelişki`

Ajan uyurken (boştayken) hafızasını optimize eder.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Dream Trigger (DreamScheduler) | yanit3 | L262 | Zaman gate (24s) + oturum gate (5 oturum) + kilit |
| Dream Processor | yanit3 | L262 | Çelişki tespiti, anı birleştirme, önem skoru güncelleme |
| Dream Worker | yanit3 | L262 | Her saat kontrol eden arka plan döngüsü |

---

## KATEGORİ 7 — ÖZEL MODLAR
`#kairos #buddy #ultraplan #undercover #mod`

Ajanı sıradan chatbot'tan ayıran özellikler.

### KAIROS — Her Zaman Açık
`#kairos #arka-plan #bildirim #proaktif`

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| KAIROS Mimarisi | yanit4 | L11 | Worker thread, gözlem, düşünme, bildirim döngüsü |
| KairosWorker.observe() | yanit4 | L11 | Sistem snapshot, değişiklik tespiti |
| KairosWorker.think() | yanit4 | L11 | 15s bütçe ile proaktif öneri üretme |
| KAIROS Entegrasyonu | yanit4 | L92 | enableKairos flag, push_notification kullanımı |

### BUDDY — Terminal Tamagotchi
`#buddy #tamagotchi #ascii #nadirlik #istatistik`

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| BUDDY State Machine | yanit4 | L92 | 18 tür, nadirlik, shiny, 5 istatistik |
| BuddyStats & Rarity | yanit4 | L92 | DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK |
| feed() / play() / tick() | yanit4 | L92 | Açlık/mutluluk değişimi, zamanla azalma |
| BuddyManager | yanit4 | L92 | Rastgele tür/nadirlik seçimi, %1 shiny şansı |
| Prompt Entegrasyonu | yanit4 | L92 | Sistem promptuna BUDDY durumu ekleme |

### ULTRAPLAN — Uzun Planlama
`#ultraplan #planlama #konteyner #opus #30-dakika`

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| ULTRAPLAN Mimarisi | yanit4 | L246 | Ayrı süreç/konteyner, 30dk, dosya serialize |
| UltraplanManager | yanit4 | L246 | createTempDir, runPlanner, sonuç formatı |
| start_ultraplan Aracı | yanit4 | L246 | Araç tanımı, Opus 4.6 modeli, JSON plan çıktısı |

### UNDERCOVER — Gizli Mod
`#undercover #gizli #commit #sansür #kapatılamaz`

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Undercover Mantığı | yanit4 | L330 | Kapatılamayan bayrak, commit sanitizer |
| sanitizeCommitMessage() | yanit4 | L330 | KAIROS/BUDDY/Anthropic referanslarını temizle |
| Agent Entegrasyonu | yanit4 | L330 | Tüm çıktıların UndercoverMode'dan geçirilmesi |

---

## KATEGORİ 8 — DAĞITIM
`#dağıtım #binary #npm #kurulum #güncelleme`

Botun son kullanıcıya ulaşması.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| NPM vs Binary Karşılaştırması | yanit5 | L11 | Avantaj/dezavantaj, tedarik zinciri riski |
| Binary Derleme (pkg) | yanit5 | L11 | node20-linux/macos/win targets, codesign |
| Güvenli Kurulum Scripti | yanit5 | L11 | curl + sha256sum doğrulaması |
| Güncelleme Mekanizması | yanit5 | L117 | Versiyon kontrolü, imza doğrulama, rollback |
| Adım Adım Dağıtım Rehberi | yanit5 | L186 | Build → Paketle → İmzala → Kur → İzle |

---

## KATEGORİ 9 — DİREKTİFLER
`#direktif #hedef #otonom #7/24 #çakışmasızlık`

Sistemin asıl amacını tanımlayan kullanıcı direktifleri.

| Bölüm | Dosya | Satır | Özet |
|---|---|---|---|
| Otonom Bot Sistemi Direktifi | direktif_otonom_bot_sistemi | L1 | Her problem için özel bot, 7/24 bilimsel araştırma, çakışmasızlık sistemi |

---

## DOSYA HARİTASI

```
planlar/
├── _INDEX.md                          ← BU DOSYA — başlangıç noktası
├── yanit1_cekirdek_ajan_mimarisi.md   ← KATEGORİ 1, 3 (mimari, prompt/api)
├── yanit2_arac_sistemi_ve_guvenlik.md ← KATEGORİ 2, 4 (araçlar, güvenlik)
├── yanit3_bellek_bagalam_ve_ruya.md   ← KATEGORİ 5, 6 (bellek, rüya)
├── yanit4_ozel_modlar.md              ← KATEGORİ 7 (KAIROS/BUDDY/ULTRAPLAN/Undercover)
├── yanit5_dagitim_guvenlik_cikarimlar.md ← KATEGORİ 4, 8 (güvenlik, dağıtım)
└── direktif_otonom_bot_sistemi.md     ← KATEGORİ 9 (direktifler)
```

---

## ETİKET SÖZLÜĞÜ (grep ile aranabilir)

```
#mimari          → yanit1: L11, L36, L82, L128, L304
#araç            → yanit1: L186 | yanit2: L11, L81, L165, L187
#prompt          → yanit1: L232, L260, L282
#güvenlik        → yanit2: L34, L281, L329 | yanit5: L82, L152
#bellek          → yanit3: L11, L95, L212, L404
#rüya            → yanit3: L262
#kairos          → yanit4: L11
#buddy           → yanit4: L92
#ultraplan       → yanit4: L246
#undercover      → yanit4: L330
#dağıtım         → yanit5: L11, L117, L186
#direktif        → direktif_otonom_bot_sistemi: L1
```
