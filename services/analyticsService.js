const path = require("path");

/* ---------------------------------------------------------------
   NEXUS-120  |  Analytics Service
   Görev: Arşiv, uyarı ve dağıtım loglarından istatistik üret.
--------------------------------------------------------------- */

function safeCount(map, key) {
  map.set(key, (map.get(key) || 0) + 1);
}

function topN(map, n = 10) {
  return [...map.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([label, count]) => ({ label, count }));
}

function isoDate(str) {
  if (!str) return null;
  const d = new Date(str);
  return Number.isNaN(d.getTime()) ? null : d.toISOString().slice(0, 10);
}

/* Arşivdeki her kaydı bir kez geçip tüm sayısal özeti çıkar. */
function computeStats(archive = [], alerts = [], distLog = []) {
  const byPersona = new Map();
  const byQuality = new Map();
  const byModel = new Map();
  const byDomain = new Map();
  const keywordFreq = new Map();
  const dailyCounts = new Map();
  const fallbackDays = new Map();

  for (const entry of archive) {
    safeCount(byPersona, entry.persona || "bilinmiyor");
    safeCount(byQuality, entry.quality || "bilinmiyor");
    safeCount(byModel, entry.modelUsed || "fallback");

    const day = isoDate(entry.createdAt);
    if (day) safeCount(dailyCounts, day);
    if (entry.generationMode === "fallback" && day) safeCount(fallbackDays, day);

    for (const domain of (entry.ontology?.dominantDomains || [])) {
      const key = domain.domain;
      byDomain.set(key, (byDomain.get(key) || 0) + (domain.score || 1));
    }

    for (const kw of (entry.keywords || [])) {
      safeCount(keywordFreq, kw);
    }
  }

  // OSINT by level / source
  const alertByLevel = new Map();
  const alertBySource = new Map();
  const alertByDay = new Map();
  for (const a of alerts) {
    safeCount(alertByLevel, a.level || "unknown");
    safeCount(alertBySource, a.source || "unknown");
    const day = isoDate(a.createdAt);
    if (day) safeCount(alertByDay, day);
  }

  // Distribution channels
  const distByChannel = new Map();
  for (const d of distLog) {
    safeCount(distByChannel, d.channel || "unknown");
  }

  // Daily chart (last 30 days)
  const last30 = buildDailySeries(dailyCounts, 30);
  const last30Fallback = buildDailySeries(fallbackDays, 30);

  return {
    totals: {
      articles: archive.length,
      alerts: alerts.length,
      distributions: distLog.length,
      aiArticles: archive.filter((e) => e.generationMode === "ai").length,
      fallbackArticles: archive.filter((e) => e.generationMode === "fallback").length
    },
    byPersona: topN(byPersona, 10),
    byQuality: topN(byQuality, 5),
    byModel: topN(byModel, 8),
    byDomain: topN(byDomain, 10),
    topKeywords: topN(keywordFreq, 20),
    alerts: {
      byLevel: topN(alertByLevel, 5),
      bySource: topN(alertBySource, 5),
      daily: buildDailySeries(alertByDay, 14)
    },
    distribution: {
      byChannel: topN(distByChannel, 5)
    },
    daily: last30,
    dailyFallback: last30Fallback,
    computedAt: new Date().toISOString()
  };
}

/* Keyword trend: en çok geçen kavramların haftalık yoğunluğu */
function computeTrends(archive = [], limit = 15) {
  const windowDays = 7;
  const now = Date.now();
  const recent = archive.filter((e) => {
    const ts = e.createdAt ? new Date(e.createdAt).getTime() : 0;
    return now - ts < windowDays * 86400000;
  });

  const allFreq = new Map();
  const recentFreq = new Map();

  for (const entry of archive) {
    for (const kw of (entry.keywords || [])) safeCount(allFreq, kw);
  }
  for (const entry of recent) {
    for (const kw of (entry.keywords || [])) safeCount(recentFreq, kw);
  }

  return [...recentFreq.entries()]
    .map(([keyword, recentCount]) => {
      const totalCount = allFreq.get(keyword) || recentCount;
      const velocity = archive.length > 0 ? recentCount / (recent.length || 1) : 0;
      const saturation = totalCount / (archive.length || 1);
      return { keyword, recentCount, totalCount, velocity: Number(velocity.toFixed(3)), saturation: Number(saturation.toFixed(3)) };
    })
    .sort((a, b) => b.velocity - a.velocity)
    .slice(0, limit);
}

/* Arşiv büyüme serisi (son N gün için dolgu dahil) */
function buildDailySeries(countMap, days = 30) {
  const series = [];
  const now = new Date();
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(now);
    d.setDate(d.getDate() - i);
    const day = d.toISOString().slice(0, 10);
    series.push({ day, count: countMap.get(day) || 0 });
  }
  return series;
}

/* Persona egzersiz raporu: hangi personanın hangi kalitede ne kadar ürettiği */
function computePersonaQualityMatrix(archive = []) {
  const matrix = {};
  const personas = new Set();
  const qualities = new Set();

  for (const entry of archive) {
    const p = entry.persona || "bilinmiyor";
    const q = entry.quality || "bilinmiyor";
    personas.add(p);
    qualities.add(q);
    if (!matrix[p]) matrix[p] = {};
    matrix[p][q] = (matrix[p][q] || 0) + 1;
  }

  return {
    matrix,
    personas: [...personas],
    qualities: [...qualities]
  };
}

/* Ortalama üretim hızı (makale/gün son 7 gün) */
function computeProductionRate(archive = []) {
  const now = Date.now();
  const window7 = 7 * 86400000;
  const recent = archive.filter((e) => {
    const ts = e.createdAt ? new Date(e.createdAt).getTime() : 0;
    return now - ts < window7;
  });
  return {
    last7Days: recent.length,
    daily: Number((recent.length / 7).toFixed(2)),
    allTime: archive.length
  };
}

module.exports = {
  computeStats,
  computeTrends,
  computePersonaQualityMatrix,
  computeProductionRate,
  buildDailySeries
};
