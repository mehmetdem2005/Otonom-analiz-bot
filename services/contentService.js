const TURKISH_STOPWORDS = new Set([
  "ve", "ile", "veya", "ama", "icin", "gibi", "olan", "olarak", "bu", "bir", "da", "de", "mi", "mu", "mu", "ya", "ile", "hem", "en", "son", "ilk", "daha", "cok", "az", "ile", "icin", "uzerine", "uzerinde"
]);

function buildFallbackArticle(topic, persona, liveNewsText, alerts) {
  const alertLines = alerts.length
    ? alerts.map((a, i) => `${i + 1}. [${a.level}] ${a.message}`).join("\n")
    : "Anomali tespit edilmedi.";

  return [
    `# ${topic} - Acil Durum Taslak Raporu`,
    "",
    `## Persona Perspektifi: ${persona}`,
    "Canli model yaniti alinamadigi icin bu metin acil fallback modunda olusturuldu.",
    "",
    "## Son 24 Saat Haber Ozetleri",
    liveNewsText || "Canli haber ozeti alinmadi.",
    "",
    "## OSINT Anomali Notlari",
    alertLines,
    "",
    "## Durum Degerlendirmesi",
    "- Kisa vadede belirsizlik ve bilgi asimetrisi yuksek.",
    "- Operasyonel kararlar kaynak dogrulamasi ile alinmali.",
    "- Dalga etkisi sosyal medya ve piyasa duyarliligi uzerinden buyuyebilir.",
    "",
    "## Eylem Listesi",
    "1. Kaynak dogrulama katmanini guclendir.",
    "2. OSINT alarmlarini periyodik rapora bagla.",
    "3. Dagitim oncesi red-team kontrolu yap."
  ].join("\n");
}

function cleanTextForTTS(text, maxLen = 1800) {
  return String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#*_`>\-]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, maxLen);
}

function summarizeText(text, maxChars = 1500) {
  const t = String(text || "").replace(/\s+/g, " ").trim();
  if (!t) return "";
  const sentences = t.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (sentences.length <= 3) return t.slice(0, maxChars);

  const head = sentences.slice(0, 2).join(" ");
  const middle = sentences[Math.floor(sentences.length / 2)] || "";
  const tail = sentences[sentences.length - 1] || "";
  return `${head} ${middle} ${tail}`.slice(0, maxChars);
}

function extractNumericData(markdown) {
  const matches = [...String(markdown || "").matchAll(/(\d+[\.,]?\d*)\s?(%|milyar|milyon|bin|usd|dolar|tl)?/gi)];
  const values = matches.slice(0, 8).map((m) => Number(String(m[1]).replace(",", ".")));
  const labels = values.map((_, i) => `Veri ${i + 1}`);

  if (!values.length) {
    return {
      labels: ["Ornek A", "Ornek B", "Ornek C"],
      datasets: [12, 8, 15],
      chartType: "bar"
    };
  }

  let chartType = "bar";
  if (values.length >= 5) chartType = "line";
  if (values.length <= 3) chartType = "pie";

  return { labels, datasets: values, chartType };
}

function buildInlineChartPlacements(markdown, extracted) {
  const paragraphs = String(markdown || "")
    .split(/\n\s*\n/)
    .map((item) => item.trim())
    .filter(Boolean);

  const labels = extracted?.labels || [];
  const values = extracted?.datasets || [];
  const placements = [];

  if (!paragraphs.length || !values.length) return placements;

  const slotCount = Math.min(Math.max(1, Math.floor(paragraphs.length / 3)), values.length, 4);
  for (let index = 0; index < slotCount; index += 1) {
    const paragraphIndex = Math.min(paragraphs.length - 1, index * 2 + 1);
    placements.push({
      afterParagraph: paragraphIndex,
      label: labels[index] || `Veri ${index + 1}`,
      value: values[index],
      emphasis: values[index] >= (values[0] || 0) ? "high" : "normal"
    });
  }

  return placements;
}

function personaPalette(persona) {
  const map = {
    analist: ["#7f1d1d", "#111827", "#dc2626", "#ea580c"],
    siber: ["#22c55e", "#0f172a", "#16a34a", "#4ade80"],
    finans: ["#1e3a8a", "#f59e0b", "#2563eb", "#fbbf24"],
    derin: ["#57534e", "#1f2937", "#78716c", "#a8a29e"]
  };
  return map[persona] || map.analist;
}

function chunkForX(text, maxLen = 260) {
  const words = String(text || "").split(/\s+/);
  const chunks = [];
  let current = "";
  for (const word of words) {
    if ((current + " " + word).trim().length > maxLen) {
      if (current.trim()) chunks.push(current.trim());
      current = word;
    } else {
      current = (current + " " + word).trim();
    }
  }
  if (current.trim()) chunks.push(current.trim());
  return chunks.map((c, i) => `${c} (${i + 1}/${chunks.length})`);
}

function buildFallbackComments(article) {
  const excerpt = String(article || "").slice(0, 180);
  return [
    { name: "Analitik Aylin", avatar: "AA", text: `Veri akisinda tutarlilik orta duzeyde. Ozet: ${excerpt}...` },
    { name: "Siber Sarp", avatar: "SS", text: "Kaynak guvenilirligi ve manipule icerik riski icin ek dogrulama gerekir." },
    { name: "Finans Fikret", avatar: "FF", text: "Piyasa etkisi haber yogunluguna bagli hizla fiyatlanabilir; risk primi izlenmeli." },
    { name: "Derin Derya", avatar: "DD", text: "Uzun vadede anlatinin kurumsal guven uzerinde birikimli etkisi olur." }
  ];
}

function buildCommentPrompt(profile, article) {
  const styles = {
    "Analitik Aylin": "Sadece dogrulanabilir veri, yuzde ve trend odagiyla 2-3 cumle.",
    "Siber Sarp": "Saldiri yuzu, risk seviyesi ve kontrol onerisini teknik dilde ver.",
    "Finans Fikret": "Piyasa fiyatlamasi, volatilite ve risk primi uzerinden yorumla.",
    "Derin Derya": "Toplumsal ve jeopolitik ikinci derece etkileri tartis."
  };
  return [
    profile.prompt,
    styles[profile.name] || "Kisa ve oz bir uzman yorumu yaz.",
    `Makale ozeti: ${summarizeText(article, 900)}`,
    "Kurallar: Tekrar eden kaliplar kullanma, somut bir veri noktasi ekle, 220 karakteri asma."
  ].join("\n");
}

function buildFuturePrompt({ topic, horizon, articleText, newsContext }) {
  return [
    "Sen Chronos modunda stratejik ongoru uzmansin.",
    `Konu: ${topic}`,
    `Ufuk: ${horizon}`,
    `Makale ozeti: ${summarizeText(articleText, 900)}`,
    `Haber baglami ozeti: ${summarizeText(newsContext, 900)}`,
    "Cikti formati:",
    "1) En iyi senaryo (sayisal)",
    "2) Baz senaryo (sayisal)",
    "3) En kotu senaryo (sayisal)",
    "Her senaryoda su metrikleri zorunlu ver: enflasyon %, buyume %, savunma harcamasi (milyar $), enerji fiyat endeksi, risk skoru (0-100), etkilenen ulkeler.",
    "Sonunda kisa tablo, 5 maddelik eylem plani ve 3 erken uyari gostergesi ver."
  ].join("\n");
}

function extractKeywordsFromText(text, limit = 8) {
  const words = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9ğüşiöçı\s-]/gi, " ")
    .split(/\s+/)
    .filter((w) => w.length > 3 && !TURKISH_STOPWORDS.has(w));

  const freq = new Map();
  for (const w of words) freq.set(w, (freq.get(w) || 0) + 1);

  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([term]) => term);
}

module.exports = {
  buildFallbackArticle,
  cleanTextForTTS,
  summarizeText,
  extractNumericData,
  personaPalette,
  chunkForX,
  buildFallbackComments,
  buildCommentPrompt,
  buildFuturePrompt,
  extractKeywordsFromText,
  buildInlineChartPlacements
};
