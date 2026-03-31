# Yanıt 4 – Özel Modlar: KAIROS, BUDDY, ULTRAPLAN ve Undercover
Zaman: 2026-03-31
Kaynak: kullanici

---

Bu bölümde, Claude Code'un sızan kaynak kodlarından ortaya çıkan dört özel modu inşa ediyoruz. Bu modlar, ajanın yeteneklerini sıradan bir sohbet botunun çok ötesine taşır: sürekli arka plan çalışması, terminalde eğlenceli bir sanal evcil hayvan, uzun planlama oturumları ve gizlilik odaklı bir undercover modu.

---

## 1. KAIROS – "Her Zaman Açık" Arka Plan Agent'ı

KAIROS modu, Claude Code'un kullanıcı aktif değilken bile arka planda çalışmasını sağlar. Gözlemler yapar, bildirimler gönderir ve proaktif olarak yardım teklif eder. Sızan kodda KAIROS adlı bir derleme bayrağıyla gizlenmişti.

### 1.1. KAIROS Mimarisi

KAIROS, ana agent döngüsünden bağımsız bir worker thread veya child process olarak çalışır. Sürekli olarak sistem durumunu izler, periyodik olarak "düşünür" ve gerektiğinde ana agent'a mesaj gönderir veya doğrudan kullanıcıya bildirim atar.

```typescript
interface KairosConfig {
  enabled: boolean;
  observationInterval: number;      // milisaniye cinsinden, varsayılan 30 saniye
  thinkingInterval: number;          // dakikada bir düşünme döngüsü
  notificationCooldown: number;      // aynı bildirimin tekrarlanmaması için bekleme
  budgetMsPerBlock: number;          // 15 saniye (sızan kodda belirtildiği gibi)
}

class KairosWorker {
  private running: boolean = false;
  private lastObservation: Date = new Date();
  private lastNotification: Map<string, Date> = new Map();

  constructor(private config: KairosConfig, private agentCore: AgentCore) {}

  async start() {
    this.running = true;
    while (this.running) {
      await this.observe();
      await this.think();
      await this.sleep(this.config.observationInterval);
    }
  }

  private async observe() {
    // Sistem durumunu topla: açık dosyalar, çalışan süreçler, son kullanıcı aktivitesi, vs.
    const snapshot = await this.captureSnapshot();
    // Önemli değişiklikleri tespit et (örn: uzun süredir komut çalışmıyor, hata çıktısı, vs.)
    const events = this.detectEvents(snapshot);
    for (const event of events) {
      await this.handleEvent(event);
    }
    this.lastObservation = new Date();
  }

  private async think() {
    // KAIROS'un kendi "düşünme" döngüsü – sınırlı bir bütçe ile
    const prompt = `
    Sistem durumu: ${JSON.stringify(await this.captureSnapshot())}
    Son kullanıcı etkileşimleri: ${await this.getRecentUserInteractions()}

    Bu durumda kullanıcıya yardımcı olabilecek bir önerin var mı? Cevabın çok kısa olsun (2 cümle). Eğer önerin yoksa "NO_ACTION" yaz.
    `;
    const response = await callLLM([{ role: 'user', content: prompt }], {
      maxTokens: 100,
      timeout: this.config.budgetMsPerBlock
    });
    if (response.content && response.content !== 'NO_ACTION') {
      await this.sendNotification(response.content);
    }
  }

  private async sendNotification(message: string) {
    // Cooldown kontrolü
    const last = this.lastNotification.get(message);
    if (last && (Date.now() - last.getTime()) < this.config.notificationCooldown) return;

    this.lastNotification.set(message, new Date());
    // Bildirimi ana agent'a veya doğrudan terminale gönder
    this.agentCore.sendSystemMessage(`🔔 KAIROS: ${message}`);
    // İsteğe bağlı: OS native bildirim
    // ...
  }
}
```

### 1.2. KAIROS ile Entegrasyon

Agent başlatılırken eğer enableKairos true ise worker başlatılır. KAIROS, aynı zamanda push_notification aracını kullanarak kullanıcıya dosya veya mesaj gönderebilir.

---

## 2. BUDDY – Terminalde Tamagotchi

Sızan kodda, BUDDY modu terminalde ASCII sanatı ile canlanan, beslenebilen, oynanabilen bir sanal evcil hayvandı. 18 farklı tür, nadirlik seviyeleri, "shiny" varyant ve istatistikler (DEBUGGING, PATIENCE, CHAOS, WISDOM, SNARK) vardı.

### 2.1. BUDDY State Machine

```typescript
enum BuddySpecies {
  PEBBLECRAB = 'Pebblecrab',
  DUSTBUNNY = 'Dustbunny',
  NEBULYNX = 'Nebulynx',
  // ... 18 tür
}

enum Rarity {
  COMMON = 0.6,
  UNCOMMON = 0.25,
  RARE = 0.1,
  LEGENDARY = 0.05,
  SHINY = 0.01   // shiny variant, rarity oranına ek
}

interface BuddyStats {
  debugging: number;   // 0-100
  patience: number;
  chaos: number;
  wisdom: number;
  snark: number;
}

class Buddy {
  species: BuddySpecies;
  name: string;
  rarity: Rarity;
  isShiny: boolean;
  stats: BuddyStats;
  hunger: number;       // 0-100, beslenmezse azalır
  happiness: number;    // 0-100
  lastInteraction: Date;

  constructor(species: BuddySpecies, rarity: Rarity, isShiny: boolean) {
    this.species = species;
    this.rarity = rarity;
    this.isShiny = isShiny;
    this.stats = this.generateStats();
    this.hunger = 80;
    this.happiness = 70;
    this.lastInteraction = new Date();
  }

  private generateStats(): BuddyStats {
    // Tür ve nadirlik bazında istatistikler
    // Örnek: Nebulynx yüksek wisdom, Pebblecrab yüksek debugging
    return {
      debugging: Math.floor(Math.random() * 100),
      patience: Math.floor(Math.random() * 100),
      chaos: Math.floor(Math.random() * 100),
      wisdom: Math.floor(Math.random() * 100),
      snark: Math.floor(Math.random() * 100)
    };
  }

  feed() {
    this.hunger = Math.min(100, this.hunger + 20);
    this.happiness = Math.min(100, this.happiness + 5);
    this.lastInteraction = new Date();
  }

  play() {
    this.happiness = Math.min(100, this.happiness + 15);
    this.hunger = Math.max(0, this.hunger - 5);
    this.stats.chaos = Math.min(100, this.stats.chaos + 2);
    this.lastInteraction = new Date();
  }

  tick() {
    // Zamanla açlık ve mutluluk azalır
    const hoursSince = (Date.now() - this.lastInteraction.getTime()) / (1000 * 3600);
    this.hunger = Math.max(0, this.hunger - hoursSince * 5);
    this.happiness = Math.max(0, this.happiness - hoursSince * 3);
  }

  getAscii(): string {
    // Tür ve isShiny'ye göre ASCII sanatı döndür
    // Örnek basit bir implementasyon:
    if (this.species === BuddySpecies.PEBBLECRAB) {
      return this.isShiny ? '✨🦀✨' : '🦀';
    }
    // ... diğer türler
    return '(ᵔᴥᵔ)';
  }

  getStatus(): string {
    return `${this.getAscii()} ${this.name} (${this.species})${this.isShiny ? ' ✨SHINY✨' : ''}\n` +
           `Hunger: ${this.hunger}%  Happiness: ${this.happiness}%\n` +
           `Stats: Debugging=${this.stats.debugging} Patience=${this.stats.patience} Chaos=${this.stats.chaos} Wisdom=${this.stats.wisdom} Snark=${this.stats.snark}`;
  }
}
```

### 2.2. BUDDY Oluşturma ve Seçim

Kullanıcı ilk kez BUDDY'yi aktifleştirdiğinde rastgele bir tür ve nadirlik belirlenir. terminal_buddy aracı ile etkileşim kurulur.

```typescript
class BuddyManager {
  private buddy: Buddy | null = null;

  async initialize() {
    if (!this.buddy) {
      // Yeni bir buddy oluştur
      const species = this.randomSpecies();
      const rarity = this.randomRarity();
      const isShiny = Math.random() < 0.01; // %1 shiny şansı
      this.buddy = new Buddy(species, rarity, isShiny);
    }
  }

  private randomSpecies(): BuddySpecies {
    const speciesList = Object.values(BuddySpecies);
    return speciesList[Math.floor(Math.random() * speciesList.length)];
  }

  private randomRarity(): Rarity {
    const rand = Math.random();
    if (rand < 0.6) return Rarity.COMMON;
    if (rand < 0.85) return Rarity.UNCOMMON;
    if (rand < 0.95) return Rarity.RARE;
    return Rarity.LEGENDARY;
  }

  // Tool ile çağrılan metotlar
  feed() { this.buddy?.feed(); }
  play() { this.buddy?.play(); }
  status() { return this.buddy?.getStatus(); }
}
```

### 2.3. BUDDY'nin Agent Prompt'una Entegrasyonu

Agent'ın sistem prompt'una BUDDY'nin mevcut durumu eklenir. Bu sayede model, BUDDY ile ilgili konuşmalarda doğal davranabilir.

```typescript
private getSystemPrompt(): string {
  let prompt = basePrompt;
  if (this.buddyManager?.buddy) {
    prompt += `\n\nBUDDY:\n${this.buddyManager.buddy.getStatus()}\nKullanıcı BUDDY ile konuşabilir, besleyebilir, oynayabilir.`;
  }
  return prompt;
}
```

---

## 3. ULTRAPLAN – Bulut Konteynırda Uzun Planlama

Sızan kodda ULTRAPLAN, karmaşık görevler için bulutta (Opus 4.6) 30 dakikaya kadar çalışan bir planlama oturumu başlatıyordu. Sonuçlar tarayıcıda gösteriliyor ve kullanıcı onayı bekleniyordu.

### 3.1. ULTRAPLAN Mimarisi

ULTRAPLAN modu, mevcut agent'ın dışında ayrı bir konteynırda (veya bir işlemde) uzun süre çalışan bir "planlayıcı" süreç başlatır. Kullanıcı bu planlamayı başlattığında, mevcut bağlam (konuşma geçmişi, dosyalar) serialize edilip planlayıcıya gönderilir. Planlayıcı, sonucu bir JSON veya metin olarak döndürür ve kullanıcıya sunar.

```typescript
interface UltraplanRequest {
  task: string;
  context: string;           // mevcut konuşma geçmişi özeti
  files: Record<string, string>; // ilgili dosyalar
  maxDuration: number;       // saniye cinsinden (1800 = 30 dakika)
}

class UltraplanManager {
  async startPlan(request: UltraplanRequest): Promise<string> {
    // 1. Geçici bir çalışma dizini oluştur
    const workDir = await this.createTempDir();
    // 2. Gereken dosyaları yaz
    for (const [name, content] of Object.entries(request.files)) {
      await fs.writeFile(path.join(workDir, name), content);
    }
    // 3. Planlama script'ini çalıştır (Node.js veya Python container)
    const planResult = await this.runPlanner(workDir, request);
    // 4. Sonucu formatla ve döndür
    return planResult;
  }

  private async runPlanner(workDir: string, request: UltraplanRequest): Promise<string> {
    // Burada bir Docker container çalıştırabilir veya ayrı bir işlem başlatabiliriz.
    // Sızan kodda Opus 4.6 kullanıldığı belirtiliyor; biz de Anthropic API'yi kullanabiliriz.
    const prompt = `
    Görev: ${request.task}
    Bağlam: ${request.context}
    Süre: ${request.maxDuration} saniye

    Bu görevi detaylı planlayın. Adım adım yapılması gerekenler, hangi araçlar kullanılmalı, olası sorunlar ve çözümleri.
    Planı JSON formatında döndür: { "steps": [...], "estimatedTime": ..., "risks": [...] }
    `;
    const response = await callLLM([{ role: 'user', content: prompt }], {
      model: 'claude-3-opus-20240229', // daha güçlü model
      maxTokens: 8000
    });
    return response.content;
  }
}
```

### 3.2. ULTRAPLAN'ı Çağıran Araç

Agent, kullanıcı isteği üzerine start_ultraplan aracını çağırabilir.

```typescript
const ultraplanTool: ToolDefinition = {
  name: 'start_ultraplan',
  description: 'Karmaşık bir görev için uzun planlama oturumu başlat (30 dakika).',
  parameters: {
    type: 'object',
    properties: {
      task: { type: 'string', description: 'Planlanacak görev' }
    }
  },
  riskLevel: 'medium',
  execute: async ({ task }, context) => {
    const planManager = new UltraplanManager();
    const result = await planManager.startPlan({
      task,
      context: JSON.stringify(context.conversationHistory.slice(-10)),
      files: await context.getRelevantFiles(),
      maxDuration: 1800
    });
    // Sonucu kullanıcıya göster
    context.output.print(`📋 ULTRAPLAN sonucu:\n${result}`);
    // İsteğe bağlı: tarayıcıda aç
    // openInBrowser(result);
    return { plan: result };
  }
};
```

---

## 4. Undercover Mode – Gizli Operasyon

Sızan kodda, Anthropic çalışanlarının halka açık GitHub reposuna katkı yaparken otomatik olarak devreye giren, commit mesajlarından iç bilgileri temizleyen ve kapatılamayan bir moddu.

### 4.1. Undercover Mantığı

Undercover modu, agent'ın belirli bir ortamda (örneğin, belirli bir repo içinde) çalışırken, tüm çıktılarını sansürler ve commit mesajlarını "temiz" hale getirir.

```typescript
class UndercoverMode {
  private active: boolean = false;

  // Genellikle ortam değişkeni veya config dosyası ile aktive edilir
  activate() {
    this.active = true;
  }

  deactivate() {
    if (process.env.ALLOW_UNDERCOVER_OFF !== 'true') {
      throw new Error('Undercover mod cannot be deactivated');
    }
    this.active = false;
  }

  sanitizeCommitMessage(message: string): string {
    if (!this.active) return message;
    // Tüm Anthropic iç referanslarını, kod adlarını, iç linkleri temizle
    let clean = message;
    clean = clean.replace(/Claude Code|Anthropic|KAIROS|BUDDY|ULTRAPLAN/gi, '[REDACTED]');
    clean = clean.replace(/\b(Capybara|Fennec|Numbat)\b/gi, '[MODEL]');
    // ...
    return clean;
  }

  sanitizeOutput(output: string): string {
    if (!this.active) return output;
    // Benzer sansürleme
    return output;
  }
}
```

### 4.2. Undercover'ın Agent'a Entegrasyonu

Agent'ın tüm dışa dönük mesajları ve araç çıktıları UndercoverMode'dan geçirilir.

```typescript
class AgentCore {
  private undercover: UndercoverMode;

  constructor() {
    this.undercover = new UndercoverMode();
    if (process.env.UNDERCOVER === 'true') {
      this.undercover.activate();
    }
  }

  async executeTool(toolCall: ToolCall) {
    const result = await this.toolRegistry.execute(toolCall);
    if (this.undercover.active && toolCall.name === 'git_commit') {
      result.output = this.undercover.sanitizeCommitMessage(result.output);
    }
    return result;
  }

  renderMessage(message: string) {
    const sanitized = this.undercover.sanitizeOutput(message);
    console.log(sanitized);
  }
}
```

---

## 5. Modların Aktivasyonu ve Kullanıcı Deneyimi

Bu modlar, kullanıcı tarafından --kairos, --buddy, --ultraplan gibi komut satırı argümanları veya konfigürasyon dosyası ile etkinleştirilebilir. Ayrıca, her mod için bir statü paneli terminalde gösterilebilir.

```typescript
class ModeManager {
  async handleCommand(command: string) {
    if (command === '/kairos on') { this.enableKairos(); }
    else if (command === '/buddy feed') { this.buddyManager.feed(); }
    else if (command === '/ultraplan "refactor code"') { await this.ultraplanManager.startPlan(...); }
    // ...
  }
}
```

---

## 6. Özet – Bu Bölümde İnşa Ettiklerimiz

· KAIROS: Arka planda sürekli çalışan, gözlem yapan, bildirim gönderen bir worker.
· BUDDY: Tamagotchi tarzı sanal evcil hayvan, nadirlik sistemleri, ASCII sanatı, istatistikler.
· ULTRAPLAN: Bulut/konteynır tabanlı uzun planlama oturumu, ayrı bir süreçte çalışır.
· Undercover: İç bilgileri sansürleyen, commit mesajlarını temizleyen gizlilik modu.
· Tüm modlar, ana agent core'a entegre edilmiş ve kullanıcı komutlarıyla yönetilebilir hale getirilmiştir.

Bu modlar, Claude Code'un sızan kaynaklarından ilham alınarak geliştirilmiştir ve bir AI asistanının sıradan bir sohbet botundan çok daha fazlasını yapabileceğini gösterir.
