const QUALITY_PROFILES = {
  hizli: {
    name: "Hizli",
    instruction: "Kisa, net, gereksiz tekrar icermeyen ve hizli karar aldiran bir cikti uret.",
    generationTemp: 0.84,
    commentTemp: 0.94,
    futureTemp: 0.8,
    modelTier: "fast"
  },
  dengeli: {
    name: "Dengeli",
    instruction: "Analitik derinlik ile akici okunabilirlik arasinda denge kur.",
    generationTemp: 0.72,
    commentTemp: 0.88,
    futureTemp: 0.7,
    modelTier: "balanced"
  },
  derin: {
    name: "Derin",
    instruction: "Karsit gorus, ikinci derece etkiler ve stratejik kirilimlarla derin analiz yaz.",
    generationTemp: 0.62,
    commentTemp: 0.8,
    futureTemp: 0.6,
    modelTier: "deep"
  }
};

const MODEL_TIERS = {
  fast: [
    process.env.GROQ_MODEL_FAST,
    process.env.GROQ_MODEL,
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile"
  ].filter(Boolean),
  balanced: [
    process.env.GROQ_MODEL_BALANCED,
    process.env.GROQ_MODEL,
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile"
  ].filter(Boolean),
  deep: [
    process.env.GROQ_MODEL_DEEP,
    process.env.GROQ_MODEL,
    "openai/gpt-oss-120b",
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile"
  ].filter(Boolean)
};

function getQualityProfile(quality = "dengeli") {
  return QUALITY_PROFILES[quality] || QUALITY_PROFILES.dengeli;
}

function getPreferredModels(quality = "dengeli") {
  const profile = getQualityProfile(quality);
  return MODEL_TIERS[profile.modelTier] || MODEL_TIERS.balanced;
}

function buildPromptContract({ quality, ontology, agentPlan }) {
  return [
    `Kalite Profili: ${quality}`,
    getQualityProfile(quality).instruction,
    `Baskin alanlar: ${(ontology?.dominantDomains || []).map((item) => `${item.domain}(${item.score})`).join(", ") || "belirsiz"}`,
    `Aktif ajanlar: ${(agentPlan?.selectedAgents || []).map((agent) => agent.name).join(", ") || "temel analiz"}`,
    "Yanit kurallari:",
    "- Soyut slogan yazma; olcu, olasilik ve etkiler ver.",
    "- Varsayimlari etiketle; kesin olmayan kismi kesinmis gibi yazma.",
    "- Her ana iddianin operasyonel sonucunu belirt.",
    "- Uretken ama disiplinli ol; fantezi veya asiri iddiali bos cikarim yapma.",
    "- Markdown kullan ve karar verdirici netlikte yaz."
  ].join("\n");
}

function buildGenerationMessages({ topic, personaPrompt, liveNewsText, alerts, quality, ontology, agentPlan }) {
  return [
    { role: "system", content: personaPrompt },
    {
      role: "user",
      content: [
        `Konu: ${topic}`,
        `Canli haber ozeti: ${liveNewsText || "yok"}`,
        `OSINT anomali ozetleri: ${JSON.stringify(alerts || [])}`,
        `Ontoloji baglami: ${JSON.stringify({ domains: ontology?.dominantDomains || [], risks: ontology?.riskFactors || [], gaps: ontology?.knowledgeGaps || [] })}`,
        `Ajan gorev plani: ${JSON.stringify(agentPlan?.workflow || [])}`,
        buildPromptContract({ quality, ontology, agentPlan }),
        "Zorunlu cikti formati:",
        "# Baslik",
        "## Yonetici Ozeti",
        "## Kritik Bulgular",
        "## Sistemik Etkiler",
        "## Risk Matrisi",
        "## Karsit Goruler",
        "## Eylem Plani",
        "## Erken Uyari Gostergeleri"
      ].join("\n\n")
    }
  ];
}

function buildConsensusMessages({ topic, article, ontology, agentBriefs, quality }) {
  return [
    {
      role: "system",
      content: "Sen bir orkestrasyon katmanisin. Farkli uzman ajanlar arasindaki ortak noktayi, celiskileri ve operasyonel onceligi sentezlersin. Cikti: Konsensus, Ayrisma, Sonraki Adimlar."
    },
    {
      role: "user",
      content: [
        `Konu: ${topic}`,
        `Kalite: ${quality}`,
        `Makale ozeti: ${String(article || "").slice(0, 1800)}`,
        `Ontoloji: ${JSON.stringify(ontology || {})}`,
        `Ajan gorusleri: ${JSON.stringify(agentBriefs || [])}`,
        "3 bolumlu kisa ama kuvvetli sentez yaz: Konsensus / Ayrisma / Sonraki Adimlar."
      ].join("\n\n")
    }
  ];
}

module.exports = {
  QUALITY_PROFILES,
  MODEL_TIERS,
  getQualityProfile,
  getPreferredModels,
  buildPromptContract,
  buildGenerationMessages,
  buildConsensusMessages
};