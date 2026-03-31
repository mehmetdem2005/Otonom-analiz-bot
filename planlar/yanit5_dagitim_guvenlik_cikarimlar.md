# Yanıt 5 – Dağıtım, Güvenlik ve Çıkarımlar
Zaman: 2026-03-31
Kaynak: kullanici
Kategori: DAĞITIM | GÜVENLİK & SANDBOX
Etiketler: #dağıtım #binary #npm #kurulum #güncelleme #source-map #axios #tedarik-zinciri #imza #rollback
İlgili: yanit2_arac_sistemi_ve_guvenlik.md
İndeks: _INDEX.md → KATEGORİ 4, KATEGORİ 8

---

Bu son bölümde, Claude Code'un sızan kaynak kodlarından ve yaşanan güvenlik olaylarından çıkardığımız derslerle, kendi yapay zeka botunuzu güvenli bir şekilde nasıl dağıtabileceğinizi adım adım ele alacağız. Dağıtım modeli, kaynak koruma, güncelleme mekanizması ve tedarik zinciri saldırılarından korunma stratejilerini inceleyeceğiz.

---

## 1. Dağıtım Modelleri: NPM Paketi vs. Bağımsız Binary

Claude Code başlangıçta npm üzerinden yayınlanıyordu, ancak sızıntının ardından bağımsız binary (curl ile kurulum) modeline geçti. Bu değişimin nedenlerini ve hangi modelin daha güvenli olduğunu inceleyelim.

### 1.1. NPM Paketi

Avantajları:

· Kolay kurulum: npm install -g @anthropic-ai/claude-code
· Otomatik bağımlılık yönetimi
· npm ekosistemine entegre güncellemeler

Dezavantajları:

· Tedarik zinciri riski: npm'de bir paketin ele geçirilmesi durumunda tüm kullanıcılar etkilenir. (axios olayında olduğu gibi)
· Source map sızıntısı: Derlenmiş pakette .map dosyalarının unutulmasıyla kaynak kod ifşa olabilir.
· Bağımlılık karmaşası: Yüzlerce dolaylı bağımlılık güvenlik açığı oluşturabilir.

Güvenli kullanım için alınması gereken önlemler:

· Paketi npm install --ignore-scripts ile kurup script'leri manuel inceleyin.
· package-lock.json veya bun.lockb'yi sıkı tutun ve npm ci kullanın.
· npm audit ile düzenli tarama yapın.

### 1.2. Bağımsız Binary (curl ile kurulum)

Avantajları:

· Tek bir binary, bağımlılık yok.
· Signature doğrulama ile binary'nin bütünlüğü sağlanabilir.
· Source map veya kaynak kod ifşa riski yok (binary obfuskasyonu ayrı bir konu).

Dezavantajları:

· Güncelleme mekanizması manuel veya özel bir arka plan işlemi gerektirir.
· İşletim sistemine özel derleme gerekir.

Örnek güvenli kurulum komutu (Claude Code'un yeni yöntemi):

```bash
curl -fsSL https://claude.ai/install.sh | bash
```

Bu komut:

· SSL sertifikasını doğrular (-fsSL).
· İmzalı binary'yi indirir.
· Hash kontrolü yapar (install script içinde).

Kendi botunuz için binary dağıtımı oluşturma:

```bash
# Build a single executable with pkg or esbuild
npm run build
pkg dist/index.js --targets node20-linux-x64,node20-macos-x64,node20-win-x64 --output myagent

# Sign the binary (macOS/Linux)
codesign -s "Developer ID Application: Your Name" myagent

# Provide a checksum
sha256sum myagent > myagent.sha256

# Install script example (install.sh)
#!/bin/bash
curl -fsSL https://yourdomain.com/myagent -o /usr/local/bin/myagent
echo "$(curl -fsSL https://yourdomain.com/myagent.sha256) /usr/local/bin/myagent" | sha256sum -c -
chmod +x /usr/local/bin/myagent
```

---

## 2. Source Map ve Kaynak Kod Koruma Stratejileri

Sızıntının temel nedeni, npm paketinde source map dosyalarının sourcesContent alanıyla birlikte bulunmasıydı. Bu, orijinal TypeScript kaynak kodunun tamamen ifşa olmasına yol açtı.

Nasıl önlenir?

1. Source map'leri ayrı bir dosya olarak saklayın, pakete dahil etmeyin. Eğer debug için gerekiyorsa, ayrı bir -debug paketi yayınlayın.
2. Build sırasında sourcesContent'i false yapın (TypeScript compiler option: "sourceMap": true, "inlineSources": false).
3. Derleme sonrası .map dosyalarını silin veya .npmignore ile hariç tutun.
4. Obfuskasyon kullanarak tersine mühendisliği zorlaştırabilirsiniz (ancak tam koruma sağlamaz).

Örnek tsconfig.json (güvenli)

```json
{
  "compilerOptions": {
    "sourceMap": true,
    "inlineSources": false,
    "outDir": "./dist"
  },
  "exclude": ["**/*.map", "**/*.tsbuildinfo"]
}
```

.npmignore

```
*.map
*.tsbuildinfo
src/
tests/
```

---

## 3. Güncelleme Mekanizmaları

Binary dağıtımda güncellemeleri nasıl yöneteceğiniz önemlidir. İki yaklaşım:

· Arka plan güncellemeleri: Agent çalışırken periyodik olarak yeni sürümü kontrol eder, indirir ve bir sonraki başlatmada geçer.
· Açık kullanıcı komutu: myagent update gibi bir komutla güncelleme tetiklenir.

Güvenlik için güncelleme mekanizmasında şunları yapın:

· İndirilen binary'nin imzasını doğrulayın.
· Güncelleme sunucusunu hardcode etmeyin; güvenli bir domain kullanın (HTTPS).
· Yedekleme ve rollback imkanı sağlayın.

Örnek güncelleme modülü:

```typescript
class Updater {
  async checkForUpdates() {
    const currentVersion = '1.0.0';
    const response = await fetch('https://updates.myagent.com/latest.json');
    const { version, url, signature } = await response.json();
    if (version !== currentVersion) {
      const binary = await fetch(url);
      const binaryBuffer = await binary.arrayBuffer();
      // signature doğrulama (örneğin RSA imzası)
      if (this.verifySignature(binaryBuffer, signature)) {
        await this.installUpdate(binaryBuffer);
      }
    }
  }
}
```

---

## 4. Güvenlik Açıklarından Alınan Dersler

### 4.1. Axios RAT Saldırısı

31 Mart 2026'da npm'deki axios paketine (1.14.1 ve 0.30.4) RAT enjekte edilmişti. Bu, tedarik zinciri saldırısıdır.

Alınacak önlemler:

· Bağımlılıkları dondurun: package-lock.json ile sürümleri sabitleyin.
· Düzenli güvenlik taraması: npm audit, snyk veya socket.dev kullanın.
· İzole ortam: Agent'ı bir sandbox (Docker) içinde çalıştırın.
· Ağ izolasyonu: Agent'ın dış ağa çıkmasını sınırlayın (özellikle geliştirme aşamasında).
· İmzalı paketler: npm'de --ignore-scripts ile kurup script'leri manuel inceleyin.

### 4.2. Source Map Sızıntısı

Yukarıda detaylandırıldı. Otomatik kontroller ekleyin: CI/CD'de .map dosyalarının pakette olup olmadığını kontrol eden bir script.

Örnek kontrol:

```bash
# npm pack sonrası kontrol
tar -tf package.tgz | grep '\.map$' && exit 1
```

### 4.3. Gizli Modların Ortaya Çıkması

Sızan kodda KAIROS, BUDDY gibi modların string gizleme yöntemleri (String.fromCharCode) ile saklandığı görüldü. Bu tür gizlemeler security through obscurity olup gerçek güvenlik sağlamaz. Kritik özellikler için:

· Lisanslama ve yetkilendirme kullanın (örneğin, yalnızca belirli API anahtarlarına sahip kullanıcılara açın).
· Derleme sırasında kod ayıklama (dead code elimination) ile kullanılmayan modülleri çıkarın.

---

## 5. Kendi Botunuzu Güvenli Şekilde Dağıtma – Adım Adım

1. Build ve Paketleme
   · TypeScript kaynağınızı derleyin, source map'leri ayrı tutun.
   · Tek bir binary oluşturun (pkg, esbuild veya nexe ile).
   · Binary'yi imzalayın (macOS/Linux: codesign, Windows: signtool).
2. Güvenli Kurulum Scripti
   · HTTPS üzerinden binary ve checksum/signature sunun.
   · Kurulum scriptinde checksum doğrulaması yapın.
   ```bash
   # install.sh
   set -e
   BINARY_URL="https://mycdn.com/myagent"
   CHECKSUM_URL="https://mycdn.com/myagent.sha256"
   curl -fsSL "$BINARY_URL" -o myagent
   curl -fsSL "$CHECKSUM_URL" -o myagent.sha256
   sha256sum -c myagent.sha256
   chmod +x myagent
   sudo mv myagent /usr/local/bin/
   ```
3. Güncelleme Mekanizması
   · Agent içinde periyodik kontrol (isteğe bağlı).
   · Güncelleme binary'sini imzalı olarak indir, hash kontrolü yap, swap.
4. Runtime Güvenlik
   · Kullanıcıdan açık izin almadıkça hassas işlemleri (shell, dosya yazma) engelleyin.
   · İzinleri ~/.myagent/config.json gibi bir dosyada tutun.
   · Agent'ı bir Docker container içinde çalıştırmak isteyenler için önceden hazırlanmış image sunun.
5. Sızıntıya Karşı İzleme
   · Eğer bir sızıntı olursa (örneğin npm'de yanlışlıkla source map yayınlandı), hemen yeni sürüm yayınlayın, eski sürümü npm'den kaldırın ve kullanıcıları uyarın.
   · Bug bounty programı kurarak güvenlik araştırmacılarının sorumlu ifşasını teşvik edin.

---

## 6. Sonuç

Claude Code'un kaynak kod sızıntısı ve eş zamanlı axios saldırısı, AI araçlarının dağıtımında güvenliğin ne kadar kritik olduğunu gösterdi. Kendi botunuzu inşa ederken:

· Dağıtım modelinizi güvenlik odaklı seçin (binary + imza).
· Source map ve bağımlılık yönetimine azami dikkat gösterin.
· Güncelleme mekanizmanızı saldırganların kötüye kullanamayacağı şekilde tasarlayın.
· Toplulukla şeffaf olun: sızıntı durumunda hızlı aksiyon alın ve kullanıcıları bilgilendirin.

Bu beş bölümlük seride, sıfırdan güçlü ve güvenli bir AI ajanı inşa etmek için gereken tüm katmanları (çekirdek döngü, araç sistemi, bellek, özel modlar, dağıtım) adım adım tamamladık. Artık kendi "Claude Code" benzeri aracınızı geliştirebilir, hatta onu daha da ileri taşıyabilirsiniz.

---

Not: Burada paylaşılan kodlar ve stratejiler, sızan Claude Code kaynaklarından birebir kopyalanmamış, ancak orada gözlemlenen mimari ve güvenlik açıklarından ders alınarak oluşturulmuştur.
