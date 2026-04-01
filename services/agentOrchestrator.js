const AGENT_REGISTRY = [
  { id: "macro-scout", name: "Macro Scout", domain: "ekonomi", focus: "Makro trendler ve buyume baskilari" },
  { id: "policy-weaver", name: "Policy Weaver", domain: "jeopolitik", focus: "Regulasyon, devlet refleksi ve kamu politikalari" },
  { id: "signal-forge", name: "Signal Forge", domain: "medya", focus: "Erken sinyal ve anomaliler" },
  { id: "risk-sentinel", name: "Risk Sentinel", domain: "finans", focus: "Risk matrisi, soku yayilimi ve korunma adimlari" },
  { id: "supply-lens", name: "Supply Lens", domain: "ekonomi", focus: "Tedarik zinciri ve lojistik etkiler" },
  { id: "threat-mapper", name: "Threat Mapper", domain: "siber", focus: "Tehdit yuzeyi ve istismar zinciri" },
  { id: "narrative-probe", name: "Narrative Probe", domain: "medya", focus: "Anlati savasi ve kamu algisi" },
  { id: "capital-oracle", name: "Capital Oracle", domain: "finans", focus: "Likidite, fiyatlama ve sermaye davranisi" },
  { id: "frontier-engineer", name: "Frontier Engineer", domain: "teknoloji", focus: "Teknoloji mimarisi ve kapasite darboğazlari" },
  { id: "defense-grid", name: "Defense Grid", domain: "savunma", focus: "Savunma doktrini ve caydiricilik" },
  { id: "energy-keeper", name: "Energy Keeper", domain: "enerji", focus: "Arz guvenligi ve enerji fiyati geciskenligi" },
  { id: "civic-monitor", name: "Civic Monitor", domain: "toplum", focus: "Toplumsal tepki, guven ve davranissal degisim" },
  { id: "labor-analyst", name: "Labor Analyst", domain: "toplum", focus: "Istihdam, yetenek donusumu ve verimlilik" },
  { id: "market-maker", name: "Market Maker", domain: "finans", focus: "Piyasa derinligi ve volatilite" },
  { id: "intel-curator", name: "Intel Curator", domain: "jeopolitik", focus: "OSINT ve kaynak birlestirme" },
  { id: "compliance-guard", name: "Compliance Guard", domain: "jeopolitik", focus: "Uyum, denetim ve regülasyon bariyerleri" },
  { id: "infra-watch", name: "Infra Watch", domain: "enerji", focus: "Kritik altyapi kirilganligi" },
  { id: "cloud-auditor", name: "Cloud Auditor", domain: "teknoloji", focus: "Bulut, veri ve hesaplama maliyeti" },
  { id: "psyops-filter", name: "Psyops Filter", domain: "medya", focus: "Manipulasyon paterni ve algi operasyonu" },
  { id: "portfolio-ranger", name: "Portfolio Ranger", domain: "finans", focus: "Portfoy dengesi ve hedge mantigi" },
  { id: "industry-cartographer", name: "Industry Cartographer", domain: "ekonomi", focus: "Sektor haritalama ve yayilim etkisi" },
  { id: "diplomacy-radar", name: "Diplomacy Radar", domain: "jeopolitik", focus: "Bloklasma ve ulke hizalanmalari" },
  { id: "autonomy-bench", name: "Autonomy Bench", domain: "teknoloji", focus: "Ajan kalitesi ve orkestrasyon kabiliyeti" },
  { id: "resilience-architect", name: "Resilience Architect", domain: "savunma", focus: "Dayaniklilik tasarimi ve kurtarma stratejisi" }
];

function listAgents() {
  return AGENT_REGISTRY;
}

function scoreAgent(agent, ontology, persona) {
  let score = 1;
  const domainMatch = (ontology?.dominantDomains || []).find((item) => item.domain === agent.domain);
  if (domainMatch) score += domainMatch.score * 3;
  if (persona === "siber" && agent.domain === "siber") score += 4;
  if (persona === "finans" && agent.domain === "finans") score += 4;
  if (persona === "derin" && ["jeopolitik", "savunma", "medya"].includes(agent.domain)) score += 3;
  return score;
}

function buildExecutionPlan({ topic, persona, quality, ontology }) {
  const ranked = AGENT_REGISTRY
    .map((agent) => ({ ...agent, score: scoreAgent(agent, ontology, persona) }))
    .sort((a, b) => b.score - a.score);

  const selectedAgents = ranked.slice(0, 6);
  const standbyAgents = ranked.slice(6, 14);
  const workflow = [
    { step: "Kaynak ve sinyal tarama", owner: selectedAgents[0]?.name || "Signal Forge" },
    { step: "Alan risklerini haritalama", owner: selectedAgents[1]?.name || "Risk Sentinel" },
    { step: "Karsit gorus ve kirilim analizi", owner: selectedAgents[2]?.name || "Policy Weaver" },
    { step: "Dagitim / operasyon tavsiyesi", owner: selectedAgents[3]?.name || "Narrative Probe" }
  ];

  return {
    topic,
    persona,
    quality,
    selectedAgents,
    standbyAgents,
    workflow
  };
}

function buildAgentBriefs({ topic, article, ontology, agentPlan }) {
  const coreRisk = ontology?.riskFactors?.[0]?.risk || "Belirsizlik dagilimi yuksek";
  const firstGap = ontology?.knowledgeGaps?.[0] || "Nicel teyit gerekli";
  return (agentPlan?.selectedAgents || []).map((agent, index) => ({
    id: agent.id,
    name: agent.name,
    domain: agent.domain,
    brief: `${agent.name}, ${topic} basliginda ${agent.focus.toLowerCase()} ekseninden bakiyor. Baskin risk: ${coreRisk}. Kritik acik: ${firstGap}. Oncelik puani: ${Math.max(60, 88 - index * 4)}/100.`,
    action: `${agent.focus} icin ${ontology?.dominantDomains?.[0]?.domain || agent.domain} baglamli veri toplama ve karar notu hazirla.`
  }));
}

module.exports = {
  AGENT_REGISTRY,
  listAgents,
  buildExecutionPlan,
  buildAgentBriefs
};