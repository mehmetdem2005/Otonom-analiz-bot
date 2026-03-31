# Yanıt 2 – Araç Sistemi ve Güvenlik
Zaman: 2026-03-31
Kaynak: kullanici
Kategori: ARAÇ SİSTEMİ | GÜVENLİK & SANDBOX
Etiketler: #araç #tool #registry #izin #sandbox #güvenlik #paralel #send_user_file #push_notification #buddy
İlgili: yanit1_cekirdek_ajan_mimarisi.md, yanit5_dagitim_guvenlik_cikarimlar.md
İndeks: _INDEX.md → KATEGORİ 2, KATEGORİ 4

---

Bir önceki bölümde çekirdek agent döngüsünü ve temel tool call mekanizmasını kurduk. Şimdi bu sistemi genişletilebilir, güvenli ve kullanıcı dostu hale getirecek araçları, izolasyon stratejilerini ve özel modlara ait araçları inşa edeceğiz.

---

## 1. Tool Registry'nin Genişletilmesi

Tool'lar artık sadece çalıştırma değil, aynı zamanda izin yönetimi, paralel yürütme ve onay akışları için de bilgi taşıyacak.

```typescript
interface ToolPermission {
  riskLevel: 'safe' | 'medium' | 'high';       // yüksek riskliler onay ister
  allowParallel?: boolean;                     // aynı anda çalıştırılabilir mi?
  maxConcurrent?: number;                      // eşzamanlı çağrı limiti
}

interface ToolDefinition extends ToolPermission {
  name: string;
  description: string;
  parameters: JSONSchema;
  execute: (args: any, context: ToolContext) => Promise<ToolOutput>;
}
```

ToolContext her çağrıda geçirilir; çalışma dizini, oturum bilgisi, kullanıcı onay fonksiyonu gibi bağlamları içerir.

---

## 2. Güvenlik Duvarı: İzin Sistemi ve Sandbox

Claude Code, kullanıcının izin verdiği işlemleri otomatikleştirir. İki katmanlı bir yaklaşım kullanabiliriz:

· Statik İzinler: Kullanıcının başlangıçta veya çalışma anında verdiği genel izinler (örn: /allow read /home/user/project).
· Dinamik Onaylar: Yüksek riskli işlemler için anlık onay isteme.

### 2.1. İzin Yönetimi Katmanı

```typescript
class PermissionManager {
  private allowedPaths: Set<string> = new Set();
  private allowedCommands: Set<string> = new Set();

  // Kullanıcı komutu ile izin ekleme
  allowPath(path: string) { this.allowedPaths.add(path); }
  allowCommand(cmd: string) { this.allowedCommands.add(cmd); }

  canReadFile(filePath: string): boolean {
    return [...this.allowedPaths].some(p => filePath.startsWith(p));
  }

  canExecuteShell(command: string): boolean {
    return [...this.allowedCommands].some(c => command.includes(c));
  }
}
```

### 2.2. Araç İçi Onay Mekanizması

Tool çağrılırken, eğer risk seviyesi high ise context.requireConfirmation() çağrılır.

```typescript
async execute(args: any, context: ToolContext): Promise<ToolOutput> {
  if (this.riskLevel === 'high') {
    const confirmed = await context.requireConfirmation({
      message: `Bu işlem sisteminizi değiştirebilir: ${JSON.stringify(args)}. Devam etmek istiyor musunuz?`,
      timeout: 30000
    });
    if (!confirmed) return { error: 'Kullanıcı reddetti' };
  }
  // asıl işlem
}
```

---

## 3. Temel Araçların Gerçeklenmesi

### 3.1. Dosya Sistemi Araçları (read_file, write_file, edit_file)

Güvenlik için izin kontrolleri yapılır.

```typescript
const fileTools: ToolDefinition[] = [
  {
    name: 'read_file',
    description: 'Bir dosyanın içeriğini okur.',
    parameters: { type: 'object', properties: { path: { type: 'string' } } },
    riskLevel: 'medium',
    execute: async ({ path }, context) => {
      if (!context.permissions.canReadFile(path)) {
        throw new Error('Dosya okuma izni yok');
      }
      const content = await fs.promises.readFile(path, 'utf-8');
      return { content };
    }
  },
  {
    name: 'write_file',
    description: 'Bir dosyaya yazar (üzerine yazar).',
    parameters: { type: 'object', properties: { path: { type: 'string' }, content: { type: 'string' } } },
    riskLevel: 'high',
    requiresConfirmation: true,
    execute: async ({ path, content }, context) => {
      if (!context.permissions.canWriteFile(path)) {
        throw new Error('Dosya yazma izni yok');
      }
      await fs.promises.writeFile(path, content);
      return { success: true };
    }
  }
];
```

### 3.2. Shell Komutları (execute_command)

Kritik araçtır. Çıktıyı stream edebilir, zaman aşımı ve çalışma dizini ayarlanabilir.

```typescript
const shellTool: ToolDefinition = {
  name: 'execute_command',
  description: 'Bir shell komutu çalıştırır.',
  parameters: {
    type: 'object',
    properties: {
      command: { type: 'string' },
      cwd: { type: 'string' },
      timeout: { type: 'number' }
    }
  },
  riskLevel: 'high',
  execute: async ({ command, cwd, timeout = 60000 }, context) => {
    // Önce izin kontrolü
    if (!context.permissions.canExecuteShell(command)) {
      throw new Error('Bu komut için izin yok');
    }
    // Onay iste (high risk)
    const confirmed = await context.requireConfirmation({
      message: `Komut çalıştırılacak: ${command}\nDizini: ${cwd || process.cwd()}\nOnaylıyor musunuz?`
    });
    if (!confirmed) return { error: 'Kullanıcı reddetti' };

    // Komutu çalıştır
    const { exec } = require('child_process');
    return new Promise((resolve, reject) => {
      const proc = exec(command, { cwd, timeout }, (error, stdout, stderr) => {
        if (error) reject(error);
        else resolve({ stdout, stderr });
      });
    });
  }
};
```

### 3.3. Web İstekleri (http_request)

Basit GET/POST istekleri. Risk seviyesi orta, çünkü harici veri alabilir.

---

## 4. Paralel Araç Yürütme

Claude Code, aynı anda birden fazla aracı paralel çalıştırabilir. Bunun için Promise.all kullanılır, ancak aynı tool tipi için eşzamanlılık sınırlanabilir.

```typescript
private async executeToolsParallel(toolCalls: ToolCall[]): Promise<ToolResult[]> {
  // Gruplama: aynı anda çalıştırılabilecekleri belirle
  const groups = this.groupByParallelCapability(toolCalls);
  const results: ToolResult[] = [];

  for (const group of groups) {
    const groupResults = await Promise.all(
      group.map(call => this.executeSingleTool(call))
    );
    results.push(...groupResults);
  }
  return results;
}
```

---

## 5. Özel Araçlar (Claude Code'dan Esinlenmeler)

Sızan kodda görülen bazı özel araçları temel alarak benzerlerini inşa edelim.

### 5.1. send_user_file – Kullanıcıya Dosya Gönderme

Bu araç, KAIROS modunda veya kullanıcıya belge/veri iletmek için kullanılır.

```typescript
const sendUserFileTool: ToolDefinition = {
  name: 'send_user_file',
  description: 'Kullanıcıya bir dosya gönderir (indirme linki veya terminale çıktı).',
  parameters: {
    type: 'object',
    properties: {
      filePath: { type: 'string' },
      description: { type: 'string' }
    }
  },
  riskLevel: 'medium',
  execute: async ({ filePath, description }, context) => {
    // Dosyayı oku
    const content = await fs.promises.readFile(filePath);
    // Kullanıcıya göster (örneğin base64 encode edip terminale yazdır)
    const b64 = content.toString('base64');
    // Terminalde mesaj göster
    context.output.print(`📎 ${description || 'Dosya'}:\n${b64}`);
    return { success: true };
  }
};
```

### 5.2. push_notification – Bildirim Gönderme

KAIROS modunun proaktif bildirimlerini sağlar. Terminal üzerinden veya işletim sisteminin bildirim sistemine gönderilebilir.

```typescript
const pushNotificationTool: ToolDefinition = {
  name: 'push_notification',
  description: 'Kullanıcıya bir bildirim gönderir (terminal veya OS native).',
  parameters: {
    type: 'object',
    properties: {
      title: { type: 'string' },
      body: { type: 'string' },
      priority: { type: 'string', enum: ['low', 'normal', 'high'] }
    }
  },
  riskLevel: 'low',
  execute: async ({ title, body, priority }, context) => {
    // Terminalde göster
    context.output.print(`🔔 [${priority}] ${title}: ${body}`);
    // Native bildirim (node-notifier gibi bir kütüphane ile)
    // ...
    return { success: true };
  }
};
```

### 5.3. terminal_buddy – BUDDY Sistemi İçin Araç

BUDDY (sanal evcil hayvan) ile etkileşim sağlar. Durum güncelleme, besleme, oyun oynama gibi eylemler.

```typescript
const buddyTool: ToolDefinition = {
  name: 'terminal_buddy',
  description: 'Terminaldeki sanal evcil hayvan ile etkileşim kur.',
  parameters: {
    type: 'object',
    properties: {
      action: { type: 'string', enum: ['feed', 'play', 'status', 'pet'] }
    }
  },
  riskLevel: 'low',
  execute: async ({ action }, context) => {
    const buddy = context.buddy; // Agent core'dan enjekte edilir
    switch (action) {
      case 'feed':
        buddy.feed();
        return { message: `${buddy.name} mutlu oldu!` };
      case 'play':
        buddy.play();
        return { message: `${buddy.name} oynuyor!` };
      case 'status':
        return { status: buddy.getStatus() };
      default:
        return { error: 'Geçersiz aksiyon' };
    }
  }
};
```

---

## 6. Tool Yürütme Ortamı (Sandboxing)

Kritik araçlar (özellikle shell) için daha sıkı bir sandbox gerekebilir. Node.js'de vm2 veya daha iyisi ayrı bir süreç (worker_threads veya child_process) ile izolasyon sağlanabilir.

Örnek: Shell komutlarını child_process ile ayrı bir süreçte çalıştırıp çıktıyı yakalamak yeterli olabilir. Ancak daha ileri güvenlik için Docker container veya firecracker microVM kullanılabilir. Claude Code, sızan kodlarda sandbox modülü içeriyordu. Biz basit bir yaklaşım kullanacağız:

```typescript
class SandboxedExecutor {
  async execute(command: string, cwd: string, timeout: number): Promise<{ stdout: string; stderr: string }> {
    return new Promise((resolve, reject) => {
      const proc = spawn(command, {
        shell: true,
        cwd,
        timeout,
        env: { ...process.env, PATH: '/usr/bin:/bin' } // sınırlı PATH
      });
      let stdout = '', stderr = '';
      proc.stdout.on('data', data => stdout += data);
      proc.stderr.on('data', data => stderr += data);
      proc.on('close', code => {
        if (code !== 0) reject(new Error(`Komut ${code} ile bitti: ${stderr}`));
        else resolve({ stdout, stderr });
      });
    });
  }
}
```

---

## 7. Kullanıcı Deneyimi: Terminalde Araç Çıktılarının Gösterimi

Claude Code, araç çıktılarını renklendirilmiş ve formatlanmış şekilde gösterir. Aynı şeyi chalk ile yapabiliriz.

```typescript
class TerminalOutput {
  printToolStart(name: string, args: any) {
    console.log(chalk.blue(`🔧 ${name}(${JSON.stringify(args)})`));
  }
  printToolResult(result: any) {
    if (result.error) console.log(chalk.red(`   ❌ ${result.error}`));
    else console.log(chalk.green(`   ✅ ${JSON.stringify(result).slice(0, 200)}`));
  }
}
```

---

## 8. Güvenlik En İyi Uygulamaları

· İzinleri sınırla: Varsayılan olarak hiçbir dosya/komut izni yok. Kullanıcı açıkça /allow ile izin vermeli.
· Tool yürütme sırasında geçici klasör kullan: Özellikle indirilen dosyalar için.
· Sensör verilerini anonimleştir: Kullanıcıya ait hassas bilgileri loglama.
· Güncellemeleri imzala: Dağıtım sırasında paket bütünlüğünü kontrol et (npm veya binary).
· Kullanıcı onayı olmadan ağa çıkma: Özellikle http_request için.

---

## 9. Özet – Bu Bölümde İnşa Ettiklerimiz

· Tool registry'yi zenginleştirdik: risk seviyesi, paralellik, izin yönetimi.
· İzin yönetimi katmanı (PermissionManager) ile dosya ve komut erişimini kontrol ettik.
· Temel araçlar: read_file, write_file, execute_command, http_request.
· Özel araçlar: send_user_file, push_notification, terminal_buddy.
· Paralel çalıştırma desteği.
· Sandbox yaklaşımı ve güvenlik ipuçları.
· Terminal çıktı formatlaması.

Bir sonraki bölümde bellek, bağlam ve rüya (dream) sistemi üzerinde çalışacağız. Bu sistem, uzun süreli hafıza, oturumlar arası bilgi taşıma ve arka planda çalışan "dream" konsolidasyonunu içerecek.
