# Yanıt 1 – Çekirdek Agent Mimarisi
Zaman: 2026-03-31
Kaynak: kullanici

---

Bu bölümde, Claude Code'un sızan kodundan esinlenerek bir sohbet botunun/ajanın çekirdek döngüsünü, prompt yönetimini, araç çağırma temelini ve durum makinesini nasıl kurabileceğimizi adım adım inceleyeceğiz. Her şeyi TypeScript ile yazacağız.

---

## 1. Agent Durum Makinesi (State Machine)

Agent sürekli döngü halinde çalışır. Temel durumlar:

· IDLE: Kullanıcıdan mesaj bekleniyor (terminal/HTTP).
· THINKING: Model çağrılıyor, yanıt veya araç talebi alınıyor.
· TOOL_CALLING: Bir veya daha fazla araç yürütülüyor.
· WAITING_CONFIRM: Kritik araçlar için kullanıcı onayı bekleniyor (opsiyonel).
· DREAMING (arka planda): Sadece KAIROS veya özel bir modda aktif.

Her geçiş bir olayla (event) tetiklenir.

```typescript
type AgentState = 'IDLE' | 'THINKING' | 'TOOL_CALLING' | 'WAITING_CONFIRM' | 'DREAMING';

interface AgentContext {
  state: AgentState;
  conversationHistory: Message[];
  pendingToolCalls: ToolCall[];
  userConfirmationRequired?: boolean;
}
```

---

## 2. Ana Döngü (Event Loop)

Claude Code'da ana döngü QueryEngine.ts içinde yer alır. Basitleştirilmiş hali:

```typescript
class AgentCore {
  private state: AgentContext = {
    state: 'IDLE',
    conversationHistory: [],
    pendingToolCalls: []
  };

  async run() {
    while (true) {
      switch (this.state.state) {
        case 'IDLE':
          // Kullanıcı girdisini bekle
          const userInput = await this.waitForUserInput();
          this.state.conversationHistory.push({ role: 'user', content: userInput });
          this.state.state = 'THINKING';
          break;

        case 'THINKING':
          await this.thinkAndAct();
          break;

        case 'TOOL_CALLING':
          await this.executeTools();
          break;

        case 'WAITING_CONFIRM':
          await this.waitForConfirmation();
          break;

        case 'DREAMING':
          // Arka planda çalışır, genelde ayrı bir thread/worker
          await this.dream();
          break;
      }
    }
  }
}
```

---

## 3. thinkAndAct() – Model Çağrısı ve Araç Yönetimi

Model çağrılırken sistem prompt'u, araç tanımları ve konuşma geçmişi gönderilir. Dönen yanıt:

· Doğrudan bir mesaj (assistant yanıtı)
· Bir veya daha fazla araç çağrısı (tool calls)

```typescript
private async thinkAndAct() {
  const messages = this.buildMessages(); // sistem + geçmiş + araç tanımları
  const response = await this.callLLM(messages);

  if (response.toolCalls && response.toolCalls.length > 0) {
    // Araç çağrıları var
    this.state.pendingToolCalls = response.toolCalls;
    this.state.state = 'TOOL_CALLING';
  } else {
    // Normal mesaj
    this.state.conversationHistory.push({
      role: 'assistant',
      content: response.content
    });
    this.state.state = 'IDLE';
    this.renderMessage(response.content);
  }
}
```

buildMessages() işlevi, sistem prompt'unu ve araç tanımlarını dinamik olarak ekler. Örneğin:

```typescript
private buildMessages(): any[] {
  const systemPrompt = this.getSystemPrompt(); // mevcut moda göre
  const tools = this.toolRegistry.getToolDefinitions(); // araçların JSON schema'ları

  return [
    { role: 'system', content: systemPrompt },
    ...this.state.conversationHistory,
    // Modelin tool call formatına uygun ek yönlendirmeler
    { role: 'system', content: `Available tools: ${JSON.stringify(tools)}` }
  ];
}
```

---

## 4. Tool Call Yürütme (executeTools())

Claude Code'da tool'lar sırayla veya paralel çalıştırılabilir. Güvenlik için onay mekanizması eklenebilir.

```typescript
private async executeTools() {
  const results: ToolResult[] = [];

  for (const toolCall of this.state.pendingToolCalls) {
    // Güvenlik kontrolü – kritik araçlar için onay iste
    if (this.requiresConfirmation(toolCall.name)) {
      this.state.state = 'WAITING_CONFIRM';
      this.state.userConfirmationRequired = true;
      // onay bekleme döngüsüne girilir, daha sonra tekrar buraya dönülür
      return;
    }

    const result = await this.toolRegistry.execute(toolCall);
    results.push(result);
  }

  // Tüm sonuçları conversation'a ekle
  for (const result of results) {
    this.state.conversationHistory.push({
      role: 'tool',
      tool_call_id: result.toolCallId,
      content: result.output
    });
  }

  this.state.pendingToolCalls = [];
  this.state.state = 'THINKING'; // sonuçları modele geri gönder
  // tekrar thinkAndAct() çağrılacak (döngü)
}
```

Onay bekleme durumu:

```typescript
private async waitForConfirmation() {
  const userConfirmed = await this.promptUserConfirmation();
  if (userConfirmed) {
    this.state.state = 'TOOL_CALLING'; // kaldığı yerden devam
    this.state.userConfirmationRequired = false;
  } else {
    // Onay verilmezse tool call iptal edilir, yanıt üretilir
    this.state.conversationHistory.push({
      role: 'assistant',
      content: 'İşlem onaylanmadı, devam edemiyorum.'
    });
    this.state.pendingToolCalls = [];
    this.state.state = 'IDLE';
  }
}
```

---

## 5. Araç Kayıt Sistemi (Tool Registry)

Basit bir registry:

```typescript
interface ToolDefinition {
  name: string;
  description: string;
  parameters: any; // JSON schema
  execute: (args: any) => Promise<string>;
  requiresConfirmation?: boolean;
}

class ToolRegistry {
  private tools = new Map<string, ToolDefinition>();

  register(tool: ToolDefinition) {
    this.tools.set(tool.name, tool);
  }

  getToolDefinitions() {
    return Array.from(this.tools.values()).map(t => ({
      type: 'function',
      function: {
        name: t.name,
        description: t.description,
        parameters: t.parameters
      }
    }));
  }

  async execute(call: ToolCall): Promise<ToolResult> {
    const tool = this.tools.get(call.name);
    if (!tool) throw new Error(`Tool ${call.name} not found`);
    const output = await tool.execute(call.args);
    return { toolCallId: call.id, output };
  }

  requiresConfirmation(toolName: string): boolean {
    return this.tools.get(toolName)?.requiresConfirmation ?? false;
  }
}
```

---

## 6. Model API Çağrısı

Claude Code, Anthropic API'yi kullanır, ancak siz OpenAI, Anthropic veya herhangi bir model sağlayıcıyı entegre edebilirsiniz. Örnek:

```typescript
private async callLLM(messages: any[]): Promise<LLMResponse> {
  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'x-api-key': process.env.ANTHROPIC_API_KEY,
      'anthropic-version': '2023-06-01',
      'content-type': 'application/json'
    },
    body: JSON.stringify({
      model: 'claude-3-5-sonnet-20241022',
      max_tokens: 4096,
      messages: messages,
      tools: this.toolRegistry.getToolDefinitions() // eğer tool call desteği varsa
    })
  });
  const data = await response.json();
  // dönüşü kendi formatınıza çevirin
  return this.parseLLMResponse(data);
}
```

---

## 7. Prompt Yönetimi ve Dinamik İçerik

Sistem prompt'u, mevcut moda göre değişir. Ayrıca BUDDY gibi özellikler için dinamik eklemeler yapılır.

```typescript
private getSystemPrompt(): string {
  let prompt = `Sen bir yazılım mühendisliği asistanısın. Kullanıcıya terminalde yardımcı oluyorsun.`;

  if (this.isKairosMode) {
    prompt += `\nKAIROS modu aktif: arka planda sürekli çalışıyor, kullanıcıya bildirim gönderebilirsin.`;
  }
  if (this.buddyActive) {
    const buddyStatus = this.buddy.getStatusAscii();
    prompt += `\n\nBUDDY durumu:\n${buddyStatus}`;
  }
  // Undercover mod gizlilik eklemeleri...
  return prompt;
}
```

---

## 8. Kullanıcı Girdisi / Terminal Entegrasyonu

Claude Code'da terminal girdisi readline veya Node.js process.stdin ile alınır. Ayrıca inquirer gibi kütüphanelerle zenginleştirilebilir.

```typescript
private waitForUserInput(): Promise<string> {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout
  });

  return new Promise((resolve) => {
    rl.question('> ', (answer) => {
      rl.close();
      resolve(answer);
    });
  });
}
```

---

## 9. İlk Çalıştırma ve Bağlam Yükleme

Agent başlatıldığında, varsa önceki oturumları yükler, gerekli araçları kaydeder, modları başlatır.

```typescript
async initialize() {
  await this.loadSession();          // conversationHistory'yi diskten yükle
  this.registerDefaultTools();       // dosya, shell vs.
  if (this.config.enableBuddy) {
    await this.buddy.load();         // BUDDY durumunu yükle
  }
  // KAIROS arka plan işlemini başlat
  if (this.config.enableKairos) {
    this.startKairosWorker();
  }
}
```

---

## 10. Özet – Çekirdek Mimaride Ne İnşa Ettik?

· State machine ile ajanın yaşam döngüsü
· Model çağrısı ve araç çağrısı yönetimi
· Tool registry ile genişletilebilir araç sistemi
· Dinamik prompt oluşturma (modlara göre)
· Onay mekanizması (kritik işlemler için)

Bu yapı, Claude Code'un temel mantığını oluşturur. İlerleyen yanıtlarda bu temel üzerine araçları, bellek sistemini, özel modları (KAIROS, BUDDY, ULTRAPLAN, Undercover) ve dağıtım/güvenlik katmanlarını ekleyeceğiz.
