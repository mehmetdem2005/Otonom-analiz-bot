const DOMAIN_KEYWORDS = {
  ekonomi: ["ekonomi", "enflasyon", "buyume", "faiz", "issizlik", "uretim", "sanayi", "ticaret", "verimlilik"],
  finans: ["borsa", "hisse", "tahvil", "portfoy", "sermaye", "likidite", "volatilite", "prim", "kredi"],
  siber: ["siber", "zararli", "fidye", "acik", "zafiyet", "saldiri", "tehdit", "kimlik avi", "ag"],
  jeopolitik: ["jeopolitik", "sinir", "ittifak", "bolge", "savunma", "diplomasi", "kriz", "devlet", "strateji"],
  enerji: ["enerji", "dogalgaz", "petrol", "yenilenebilir", "elektrik", "sebek", "santral", "batarya", "fiyat"],
  teknoloji: ["yapay zeka", "model", "veri", "bulut", "robotik", "otomasyon", "yazilim", "cip", "hesaplama"],
  medya: ["medya", "anlati", "algoritma", "sosyal medya", "viral", "icerik", "gundem", "etkilesim", "kanaat"],
  toplum: ["toplum", "egitim", "goc", "istihdam", "davranis", "algı", "guven", "sendika", "tuketici"],
  savunma: ["savunma", "insansiz", "savunma sanayi", "radar", "hava savunma", "doktrin", "lojistik", "cephane", "tatbikat"]
};

function tokenize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9ğüşiöçı\s-]/gi, " ")
    .split(/\s+/)
    .filter(Boolean);
}

function scoreDomains(tokens) {
  return Object.entries(DOMAIN_KEYWORDS)
    .map(([domain, keywords]) => {
      const score = keywords.reduce((acc, keyword) => {
        const parts = keyword.split(/\s+/);
        if (parts.length === 1) {
          return acc + tokens.filter((token) => token === keyword).length;
        }
        const joined = tokens.join(" ");
        return acc + (joined.includes(keyword) ? 2 : 0);
      }, 0);
      return { domain, score };
    })
    .filter((item) => item.score > 0)
    .sort((a, b) => b.score - a.score);
}

function topTerms(tokens, limit = 12) {
  const stop = new Set(["ve", "ile", "icin", "olan", "bir", "ama", "gibi", "daha", "cok", "kadar", "yani", "olan", "uzerinde"]);
  const freq = new Map();
  for (const token of tokens) {
    if (token.length < 4 || stop.has(token)) continue;
    freq.set(token, (freq.get(token) || 0) + 1);
  }
  return [...freq.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit).map(([term, count]) => ({ term, count }));
}

function inferRiskFactors(dominantDomains) {
  const map = {
    ekonomi: "Talep daralmasi, maliyet baskisi ve verimlilik acigi",
    finans: "Likidite sikismasi, risk primi ve fiyatlama soku",
    siber: "Zafiyet zinciri, tedarik zinciri acigi ve veri sizmasi",
    jeopolitik: "Bloklasma, tedarik kesintisi ve diplomatik kirilim",
    enerji: "Fiyat soku, arz kesintisi ve sebekede kirilganlik",
    teknoloji: "Model yanliligi, hesaplama darboğazi ve veri bagimliligi",
    medya: "Anlati savasi, bilgi manipülasyonu ve guven erozyonu",
    toplum: "Guven kaybi, davranissal kutuplasma ve yetenek uyumsuzlugu",
    savunma: "Lojistik yipranma, doktrin gecikmesi ve caydiricilik asimi"
  };
  return dominantDomains.slice(0, 4).map((item) => ({ domain: item.domain, risk: map[item.domain] || "Belirsizlik kaynakli ikincil riskler" }));
}

function inferKnowledgeGaps(tokens, dominantDomains) {
  const joined = tokens.join(" ");
  const gaps = [];
  if (!/oran|yuzde|milyar|milyon|puan/.test(joined)) gaps.push("Sayisal referans ve metrikler yetersiz");
  if (!/12 ay|6 ay|1 yil|2030|2026|2027/.test(joined)) gaps.push("Zaman ufku net degil");
  if (dominantDomains.length < 2) gaps.push("Capraz alan etkileri zayif temsil ediliyor");
  if (!/turkiye|avrupa|abd|cin|rusya|orta dogu/.test(joined)) gaps.push("Cografi baglam eksik");
  return gaps;
}

function buildOntologyGraph(topic, text = "") {
  const tokens = tokenize(`${topic} ${text}`);
  const dominantDomains = scoreDomains(tokens);
  const concepts = topTerms(tokens, 16);
  const nodes = [
    { id: "topic", label: topic, type: "topic", weight: 10 },
    ...dominantDomains.slice(0, 6).map((item) => ({ id: `domain:${item.domain}`, label: item.domain, type: "domain", weight: item.score + 4 })),
    ...concepts.slice(0, 10).map((item) => ({ id: `concept:${item.term}`, label: item.term, type: "concept", weight: item.count + 1 }))
  ];

  const edges = [];
  for (const domain of dominantDomains.slice(0, 6)) {
    edges.push({ source: "topic", target: `domain:${domain.domain}`, label: "etki" });
  }

  for (const concept of concepts.slice(0, 10)) {
    const match = dominantDomains.find((domain) => DOMAIN_KEYWORDS[domain.domain].some((keyword) => concept.term.includes(keyword.split(" ")[0]) || keyword.includes(concept.term)));
    edges.push({
      source: match ? `domain:${match.domain}` : "topic",
      target: `concept:${concept.term}`,
      label: match ? "kavramsal bag" : "yardimci sinyal"
    });
  }

  return {
    topic,
    dominantDomains,
    concepts,
    riskFactors: inferRiskFactors(dominantDomains),
    knowledgeGaps: inferKnowledgeGaps(tokens, dominantDomains),
    nodes,
    edges
  };
}

module.exports = {
  DOMAIN_KEYWORDS,
  buildOntologyGraph
};