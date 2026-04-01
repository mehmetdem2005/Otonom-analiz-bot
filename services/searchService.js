/* ---------------------------------------------------------------
   NEXUS-120  |  Search & Filter Service
   Görev: Arşiv üzerinde TF-IDF tabanlı tam-metin arama + filtre.
--------------------------------------------------------------- */

const STOPWORDS = new Set([
  "ve", "ile", "veya", "ama", "için", "gibi", "olan", "olarak", "bu", "bir",
  "da", "de", "mi", "ma", "ya", "hem", "en", "son", "ilk", "daha", "çok",
  "az", "icin", "uzerine", "uzerinde", "icinde", "ise", "idi", "gibi"
]);

/* --- Tokenizer ------------------------------------------------ */
function tokenize(text, removeStop = true) {
  const tokens = String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9ğüşiöçı\s-]/gi, " ")
    .split(/\s+/)
    .filter((w) => w.length >= 3);
  return removeStop ? tokens.filter((w) => !STOPWORDS.has(w)) : tokens;
}

/* --- TF (term frequency for a doc) --------------------------- */
function computeTF(tokens) {
  const freq = new Map();
  for (const t of tokens) freq.set(t, (freq.get(t) || 0) + 1);
  const total = tokens.length || 1;
  const tf = new Map();
  for (const [term, count] of freq) tf.set(term, count / total);
  return tf;
}

/* --- IDF (inverse document frequency across corpus) ---------- */
function computeIDF(corpus) {
  const N = corpus.length || 1;
  const df = new Map();
  for (const tokens of corpus) {
    const seen = new Set(tokens);
    for (const t of seen) df.set(t, (df.get(t) || 0) + 1);
  }
  const idf = new Map();
  for (const [term, count] of df) {
    idf.set(term, Math.log((N + 1) / (count + 1)) + 1);
  }
  return idf;
}

/* --- Build index from archive entries ------------------------ */
function buildIndex(archive = []) {
  const docs = archive.map((entry) => ({
    id: entry.id,
    tokens: tokenize([
      entry.topic || "",
      entry.article || "",
      (entry.keywords || []).join(" ")
    ].join(" "))
  }));

  const corpusTokens = docs.map((d) => d.tokens);
  const idf = computeIDF(corpusTokens);

  const index = docs.map((doc, i) => {
    const tf = computeTF(doc.tokens);
    const tfidf = new Map();
    for (const [term, tfVal] of tf) {
      tfidf.set(term, tfVal * (idf.get(term) || 1));
    }
    return { id: doc.id, archiveIndex: i, tfidf };
  });

  return { index, idf };
}

/* --- Score a document against query -------------------------- */
function scoreDoc(docEntry, queryTokens, idf) {
  let score = 0;
  for (const qt of queryTokens) {
    score += (docEntry.tfidf.get(qt) || 0) * (idf.get(qt) || 0.5);
  }
  return score;
}

/* --- Highlight matches in snippet ---------------------------- */
function makeSnippet(text, queryTokens, maxLen = 240) {
  const safe = String(text || "").replace(/\s+/g, " ").trim();
  let bestStart = 0;
  let bestScore = 0;

  const words = safe.split(" ");
  for (let i = 0; i < words.length - 12; i++) {
    const window = words.slice(i, i + 12).join(" ").toLowerCase();
    const score = queryTokens.filter((qt) => window.includes(qt)).length;
    if (score > bestScore) { bestScore = score; bestStart = i; }
  }

  let snippet = words.slice(bestStart, bestStart + 30).join(" ");
  if (bestStart > 0) snippet = `…${snippet}`;
  if (bestStart + 30 < words.length) snippet += "…";
  return snippet.slice(0, maxLen);
}

/* --- Main search function ------------------------------------ */
function search(archive = [], query = "", filters = {}) {
  const { persona, quality, dateFrom, dateTo, limit = 20 } = filters;
  const queryTokens = tokenize(query, false);

  // Pre-filter
  let candidates = archive;
  if (persona) candidates = candidates.filter((e) => e.persona === persona);
  if (quality) candidates = candidates.filter((e) => e.quality === quality);
  if (dateFrom) {
    const from = new Date(dateFrom).getTime();
    candidates = candidates.filter((e) => new Date(e.createdAt).getTime() >= from);
  }
  if (dateTo) {
    const to = new Date(dateTo).getTime();
    candidates = candidates.filter((e) => new Date(e.createdAt).getTime() <= to);
  }

  if (!queryTokens.length) {
    // No query — return most recent filtered results
    return [...candidates]
      .sort((a, b) => new Date(b.createdAt) - new Date(a.createdAt))
      .slice(0, limit)
      .map((e) => ({
        id: e.id,
        topic: e.topic,
        persona: e.persona,
        quality: e.quality,
        createdAt: e.createdAt,
        generationMode: e.generationMode,
        keywords: e.keywords || [],
        snippet: makeSnippet(e.article || "", [], 200),
        score: 1
      }));
  }

  // Build TF-IDF just on candidates for accuracy
  const { index, idf } = buildIndex(candidates);

  const scored = index.map((docEntry) => ({
    archiveIndex: docEntry.archiveIndex,
    score: scoreDoc(docEntry, queryTokens, idf)
  }));

  return scored
    .filter((s) => s.score > 0)
    .sort((a, b) => b.score - a.score)
    .slice(0, limit)
    .map(({ archiveIndex, score }) => {
      const e = candidates[archiveIndex];
      return {
        id: e.id,
        topic: e.topic,
        persona: e.persona,
        quality: e.quality,
        createdAt: e.createdAt,
        generationMode: e.generationMode,
        keywords: e.keywords || [],
        snippet: makeSnippet(e.article || "", queryTokens, 220),
        score: Number(score.toFixed(4))
      };
    });
}

/* --- Suggest autocomplete terms from archive ---------------- */
function suggestTerms(archive = [], prefix = "", limit = 8) {
  if (!prefix || prefix.length < 2) return [];
  const freq = new Map();
  const p = prefix.toLowerCase();
  for (const entry of archive) {
    for (const kw of (entry.keywords || [])) {
      if (kw.startsWith(p)) freq.set(kw, (freq.get(kw) || 0) + 1);
    }
    const tokens = tokenize(entry.topic || "");
    for (const t of tokens) {
      if (t.startsWith(p)) freq.set(t, (freq.get(t) || 0) + 1);
    }
  }
  return [...freq.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([term]) => term);
}

module.exports = { search, buildIndex, suggestTerms, tokenize };
