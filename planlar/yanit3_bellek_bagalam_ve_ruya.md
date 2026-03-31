# Yanıt 3 – Bellek, Bağlam ve Rüya (Dream) Sistemi
Zaman: 2026-03-31
Kaynak: kullanici

---

Bu bölümde, Claude Code'un sızan kaynak kodlarından öğrendiğimiz bellek yönetimi, oturum kalıcılığı ve en ilginci arka planda çalışan "rüya" (dream) konsolidasyon sistemini inşa edeceğiz. Bu sistem, ajanın uzun süreli hafıza tutmasını, oturumlar arasında bilgi taşımasını ve kullanıcı etkileşimi olmadığı zamanlarda hafızasını optimize etmesini sağlar.

---

## 1. Kısa Süreli Bellek (Conversation Management)

Agent'ın mevcut oturumdaki konuşma geçmişi, model bağlamına sığacak şekilde yönetilmelidir. Genel yaklaşım:

· Mesajları bir liste olarak tut (rol, içerik, timestamp).
· Bağlam penceresi sınırına ulaşıldığında eski mesajları özetle veya at.
· Özetleme işlemi için ayrı bir model çağrısı yapılabilir.

```typescript
interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp: Date;
  toolCallId?: string;
}

class ShortTermMemory {
  private messages: Message[] = [];
  private maxContextTokens: number = 200000; // Claude 3.5 Sonnet için yaklaşık
  private summarizer: Summarizer;

  constructor(summarizer: Summarizer) {
    this.summarizer = summarizer;
  }

  addMessage(message: Omit<Message, 'id' | 'timestamp'>) {
    this.messages.push({
      ...message,
      id: randomUUID(),
      timestamp: new Date()
    });
    this.trimIfNeeded();
  }

  private async trimIfNeeded() {
    let currentTokens = this.estimateTokens(this.messages);
    if (currentTokens <= this.maxContextTokens) return;

    // Bağlam aşımı: eski mesajları özetle
    const messagesToSummarize = this.messages.slice(0, -5); // son 5 mesajı koru
    const summary = await this.summarizer.summarize(messagesToSummarize);

    // Özeti sistem mesajı olarak ekle, eski mesajları kaldır
    this.messages = [
      {
        id: randomUUID(),
        role: 'system',
        content: `Önceki konuşmanın özeti: ${summary}`,
        timestamp: new Date()
      },
      ...this.messages.slice(-5)
    ];
  }

  private estimateTokens(messages: Message[]): number {
    // Basit bir tahmin: her 4 karakter ~1 token
    return messages.reduce((sum, m) => sum + Math.ceil(m.content.length / 4), 0);
  }

  getMessagesForLLM(): Message[] {
    return this.messages;
  }
}
```

Özetleyici sınıfı:

```typescript
class Summarizer {
  async summarize(messages: Message[]): Promise<string> {
    const text = messages.map(m => `${m.role}: ${m.content}`).join('\n');
    // Özetleme için model çağrısı (daha küçük bir model kullanılabilir)
    const response = await callLLM([
      { role: 'system', content: 'Aşağıdaki konuşmayı kısa bir özet haline getir.' },
      { role: 'user', content: text }
    ]);
    return response.content;
  }
}
```

---

## 2. Uzun Süreli Bellek (Vektör Veritabanı ve Anılar)

Claude Code'un sızan kodlarında, MemoryStore adında bir modül vardı. Bu, konuşmalardan anılar çıkarır, vektörleştirir ve gerektiğinde bağlama ekler. Biz benzer bir sistem kuracağız.

### 2.1. Anı Çıkarma (Memory Extraction)

Her oturumdan sonra veya periyodik olarak, konuşmalardan önemli bilgiler (kullanıcı tercihleri, proje detayları, kararlar) çıkarılır.

```typescript
interface Memory {
  id: string;
  content: string;
  embedding: number[];
  importance: number; // 0-1
  createdAt: Date;
  lastAccessed: Date;
}

class MemoryExtractor {
  async extractMemories(messages: Message[]): Promise<string[]> {
    // Modelden önemli noktaları çıkarmasını iste
    const prompt = `Aşağıdaki konuşmadan uzun süreli hafızaya alınması gereken önemli bilgileri (kullanıcı tercihleri, proje detayları, öğrenilen şeyler) madde madde yaz. Her madde tek bir cümle olsun.\n\nKonuşma:\n${messages.map(m => `${m.role}: ${m.content}`).join('\n')}`;
    const response = await callLLM([{ role: 'user', content: prompt }]);
    return response.content.split('\n').filter(l => l.trim().length > 0);
  }
}
```

### 2.2. Vektör Veritabanı

Anıları vektörleştirip saklamak için basit bir dosya tabanlı veya sqlite-vss gibi bir çözüm kullanılabilir. Örnekte chromadb veya lancedb kullanılabilir.

```typescript
import { ChromaClient } from 'chromadb';

class VectorMemoryStore {
  private client: ChromaClient;
  private collection: any;

  async init() {
    this.client = new ChromaClient({ path: './memory_db' });
    this.collection = await this.client.getOrCreateCollection({
      name: 'memories',
      metadata: { 'hnsw:space': 'cosine' }
    });
  }

  async addMemory(memory: Omit<Memory, 'embedding'>) {
    const embedding = await this.generateEmbedding(memory.content);
    await this.collection.add({
      ids: [memory.id],
      embeddings: [embedding],
      metadatas: [{ content: memory.content, importance: memory.importance, createdAt: memory.createdAt.toISOString() }]
    });
  }

  async searchMemories(query: string, limit: number = 5): Promise<Memory[]> {
    const queryEmbedding = await this.generateEmbedding(query);
    const results = await this.collection.query({
      queryEmbeddings: [queryEmbedding],
      nResults: limit
    });
    return results.metadatas[0].map((meta, i) => ({
      id: results.ids[0][i],
      content: meta.content,
      importance: meta.importance,
      createdAt: new Date(meta.createdAt),
      lastAccessed: new Date(),
      embedding: results.embeddings[0][i]
    }));
  }

  private async generateEmbedding(text: string): Promise<number[]> {
    // OpenAI veya yerel embedding modeli kullan
    const response = await fetch('https://api.openai.com/v1/embeddings', {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${process.env.OPENAI_API_KEY}` },
      body: JSON.stringify({ model: 'text-embedding-3-small', input: text })
    });
    const data = await response.json();
    return data.data[0].embedding;
  }
}
```

### 2.3. Bağlama Anıları Ekleme

Her THINKING aşamasında, mevcut kullanıcı mesajına benzer anılar aranır ve sistem prompt'una eklenir.

```typescript
class MemoryAugmenter {
  constructor(private memoryStore: VectorMemoryStore) {}

  async getRelevantMemories(userMessage: string): Promise<string> {
    const memories = await this.memoryStore.searchMemories(userMessage, 3);
    if (memories.length === 0) return '';
    return `İlgili anılar:\n${memories.map(m => `- ${m.content}`).join('\n')}`;
  }
}
```

Agent'ın buildMessages fonksiyonunda:

```typescript
private async buildMessages(): Promise<Message[]> {
  const userMessage = this.state.conversationHistory[this.state.conversationHistory.length - 1];
  const relevantMemories = await this.memoryAugmenter.getRelevantMemories(userMessage.content);
  const systemPrompt = this.getSystemPrompt() + (relevantMemories ? `\n\n${relevantMemories}` : '');
  return [
    { role: 'system', content: systemPrompt },
    ...this.shortTermMemory.getMessagesForLLM()
  ];
}
```

---

## 3. Oturum Yönetimi (Session Persistence)

Claude Code, kullanıcı terminali kapatsa bile oturumu kaydeder ve tekrar başlatıldığında kaldığı yerden devam eder. Bunun için:

· Oturum verilerini (kısa süreli bellek, araç durumları, BUDDY durumu) diske yaz.
· Agent başlarken en son oturumu yükle.

```typescript
class SessionManager {
  private sessionPath: string;

  constructor(sessionId?: string) {
    this.sessionPath = path.join(os.homedir(), '.myagent', 'sessions', sessionId || 'default');
  }

  async save(context: AgentContext) {
    const data = {
      conversationHistory: context.conversationHistory,
      buddyState: context.buddy?.serialize(),
      kairosState: context.kairos?.serialize(),
      timestamp: new Date().toISOString()
    };
    await fs.promises.writeFile(this.sessionPath, JSON.stringify(data, null, 2));
  }

  async load(): Promise<Partial<AgentContext>> {
    try {
      const data = JSON.parse(await fs.promises.readFile(this.sessionPath, 'utf-8'));
      return data;
    } catch {
      return {};
    }
  }
}
```

Agent başlangıcında:

```typescript
async initialize() {
  const sessionData = await this.sessionManager.load();
  if (sessionData.conversationHistory) {
    this.state.conversationHistory = sessionData.conversationHistory;
  }
  // ... diğer durumları yükle
}
```

---

## 4. Dream Sistemi (Arka Plan Konsolidasyonu)

Sızan kodda "autoDream" adında bir sistem vardı. Claude, boşta olduğu zamanlarda arka planda çalışarak hafızasını birleştirir, çelişkileri giderir ve bilgileri güçlendirir. Üç aşamalı tetikleyici:

1. Zaman gate: Son rüyadan 24 saat geçmiş olmalı.
2. Oturum gate: Son rüyadan beri en az 5 oturum.
3. Kilit gate: Aynı anda birden fazla rüya çalışmasın.

Biz de benzer bir mekanizma kuracağız.

### 4.1. Dream Trigger

```typescript
class DreamScheduler {
  private lastDreamTime: Date | null = null;
  private sessionCountSinceLastDream: number = 0;

  constructor() {
    this.loadFromDisk();
  }

  shouldDream(): boolean {
    const now = new Date();
    const timeGate = !this.lastDreamTime || (now.getTime() - this.lastDreamTime.getTime()) > 24 * 60 * 60 * 1000;
    const sessionGate = this.sessionCountSinceLastDream >= 5;
    return timeGate && sessionGate;
  }

  recordSessionEnd() {
    this.sessionCountSinceLastDream++;
    this.saveToDisk();
  }

  async markDreamPerformed() {
    this.lastDreamTime = new Date();
    this.sessionCountSinceLastDream = 0;
    this.saveToDisk();
  }

  private saveToDisk() { /* ... */ }
  private loadFromDisk() { /* ... */ }
}
```

### 4.2. Dream İşlemi

Dream işlemi, mevcut tüm anıları, kısa süreli belleği ve oturum geçmişini alır, bir "rüya" senaryosu oluşturur ve bu sırada:

· Çelişkili anıları tespit eder (örneğin "kullanıcı Python seviyor" ve "kullanıcı Python'dan nefret ediyor").
· Benzer anıları birleştirir.
· Önem skorlarını günceller (sık kullanılan anıların importance'ı artar).

```typescript
class DreamProcessor {
  constructor(
    private memoryStore: VectorMemoryStore,
    private shortTermMemory: ShortTermMemory,
    private sessionHistory: SessionHistory
  ) {}

  async processDream() {
    // 1. Tüm anıları al
    const allMemories = await this.memoryStore.getAllMemories();

    // 2. Modeli kullanarak çelişkileri ve birleştirilebilecek anıları bul
    const prompt = `
    Aşağıdaki anı listesinde çelişkili veya birleştirilebilecek anıları bul. Her bir grup için:
    - Çelişkili ise hangisinin doğru olduğuna karar ver (veya ikisini de tut ama çelişkiyi not et)
    - Benzer ise tek bir anıda birleştir.

    Anılar:
    ${allMemories.map(m => `- ${m.content} (importance: ${m.importance})`).join('\n')}

    Çıktıyı JSON formatında ver: { conflicts: [...], merges: [...] }
    `;
    const response = await callLLM([{ role: 'user', content: prompt }]);
    const plan = JSON.parse(response.content);

    // 3. Çelişkileri çöz (örneğin en yüksek importance olanı koru)
    for (const conflict of plan.conflicts) {
      const winner = conflict.memories.reduce((a, b) => a.importance > b.importance ? a : b);
      // Diğerlerini sil veya güncelle
    }

    // 4. Birleştirilecek anıları tek bir anıya dönüştür
    for (const mergeGroup of plan.merges) {
      const mergedContent = mergeGroup.newContent;
      const avgImportance = mergeGroup.memories.reduce((sum, m) => sum + m.importance, 0) / mergeGroup.memories.length;
      // Yeni anı ekle, eskileri sil
    }

    // 5. Özetlenmiş kısa süreli belleği anılara ekle (önemli bilgileri çıkar)
    const summary = await this.shortTermMemory.getSummary();
    const newMemories = await this.extractMemoriesFromSummary(summary);
    for (const mem of newMemories) {
      await this.memoryStore.addMemory(mem);
    }

    // 6. Anıların importance'ını güncelle (recency, frequency)
    await this.updateImportanceScores();
  }

  private async extractMemoriesFromSummary(summary: string): Promise<Memory[]> {
    // Model kullanarak özetten anı çıkar
    // ...
  }

  private async updateImportanceScores() {
    // Her anının son erişim zamanına göre importance'ını azalt, sık kullanılanları artır
  }
}
```

### 4.3. Dream Worker

Ayrı bir thread veya işlemde çalışabilir. Agent ana döngüsü IDLE durumunda iken periyodik olarak kontrol edilir.

```typescript
class DreamWorker {
  private running: boolean = false;

  async start() {
    this.running = true;
    while (this.running) {
      await this.sleep(3600000); // her saat kontrol et
      if (this.dreamScheduler.shouldDream()) {
        await this.processDream();
        await this.dreamScheduler.markDreamPerformed();
      }
    }
  }

  stop() {
    this.running = false;
  }
}
```

Agent kapatıldığında, dream worker da durdurulur.

---

## 5. Bellek Sistemi ile Agent Entegrasyonu

AgentCore sınıfımızı bu yeni bileşenlerle genişletiyoruz:

```typescript
class AgentCore {
  private shortTermMemory: ShortTermMemory;
  private longTermMemory: VectorMemoryStore;
  private memoryAugmenter: MemoryAugmenter;
  private sessionManager: SessionManager;
  private dreamScheduler: DreamScheduler;
  private dreamWorker: DreamWorker;

  async initialize() {
    await this.longTermMemory.init();
    this.memoryAugmenter = new MemoryAugmenter(this.longTermMemory);
    this.shortTermMemory = new ShortTermMemory(new Summarizer());

    const sessionData = await this.sessionManager.load();
    if (sessionData.conversationHistory) {
      for (const msg of sessionData.conversationHistory) {
        this.shortTermMemory.addMessage(msg);
      }
    }

    this.dreamScheduler = new DreamScheduler();
    this.dreamWorker = new DreamWorker(this.dreamScheduler, this.longTermMemory, this.shortTermMemory);
    this.dreamWorker.start(); // arka planda başlat
  }

  async shutdown() {
    await this.sessionManager.save(this.state);
    this.dreamWorker.stop();
  }
}
```

---

## 6. Özet – Bu Bölümde İnşa Ettiklerimiz

· Kısa süreli bellek yönetimi: bağlam penceresi taşmasında otomatik özetleme.
· Uzun süreli bellek: vektör veritabanında anı saklama, arama ve bağlama ekleme.
· Oturum kalıcılığı: diske kayıt ve yükleme.
· Dream sistemi: zaman ve oturum tabanlı tetikleyicilerle arka planda hafıza konsolidasyonu, çelişki giderme, önem skoru güncelleme.
· Tüm bileşenlerin ana agent'a entegrasyonu.

Artık ajanımız, sadece anlık sohbet yapabilen değil, aynı zamanda uzun süreli hafızası olan, oturumlar arası bilgi taşıyan ve boşta kaldığında kendi hafızasını optimize eden bir yapıya kavuştu.
