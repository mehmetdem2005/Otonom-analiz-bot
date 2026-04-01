/* ---------------------------------------------------------------
   NEXUS-120  |  Advanced Routes
   Arama, analitik, SSE, toplu üretim, karşılaştırma, arşiv yönetimi.
--------------------------------------------------------------- */

const express = require("express");
const path = require("path");
const zlib = require("zlib");

const { computeStats, computeTrends, computePersonaQualityMatrix, computeProductionRate } = require("../services/analyticsService");
const { search, suggestTerms } = require("../services/searchService");
const { callGroq } = require("../services/aiService");
const { buildOntologyGraph } = require("../services/ontologyService");
const { getPreferredModels, getQualityProfile } = require("../services/promptEngine");
const { summarizeText } = require("../services/contentService");
const { info, error } = require("../utils/logger");

/* ---------- SSE Client Registry (in-memory) ----------------- */
const sseClients = new Set();

function broadcastSSE(event, data) {
  const payload = `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
  for (const res of sseClients) {
    try { res.write(payload); } catch (_) { sseClients.delete(res); }
  }
}

/* ---------- Active batch jobs (in-memory) ------------------- */
const batchJobs = new Map();

/* ---------- Helpers ----------------------------------------- */
function wrap(handler) {
  return async (req, res) => {
    try {
      await handler(req, res);
    } catch (err) {
      const status = err.status || 500;
      error("Advanced route hatası", err, { path: req.path });
      res.status(status).json({ error: err.message || "Bilinmeyen hata", path: req.path, status });
    }
  };
}

function httpErr(status, msg) {
  const e = new Error(msg);
  e.status = status;
  return e;
}

function nowId(prefix) { return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`; }

function safe(readJson, file, fallback) {
  const v = readJson(file, fallback);
  return v == null ? fallback : v;
}

/* ---------- Router factory ---------------------------------- */
function createAdvancedRouter(deps) {
  const { ARCHIVE_FILE, ALERT_FILE, DISTRIBUTION_LOG_FILE, readJson, writeJson } = deps;
  const router = express.Router();

  /* ────────────────────────────────────────────────────────────
     GET /api/search
     q, persona, quality, dateFrom, dateTo, limit
  ──────────────────────────────────────────────────────────── */
  router.get("/search", wrap(async (req, res) => {
    const { q = "", persona, quality, dateFrom, dateTo, limit } = req.query;
    const archive = safe(readJson, ARCHIVE_FILE, []);
    const results = search(archive, q, {
      persona,
      quality,
      dateFrom,
      dateTo,
      limit: Math.min(Number(limit) || 20, 100)
    });
    res.json({ query: q, count: results.length, results });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/search/suggest?prefix=
  ──────────────────────────────────────────────────────────── */
  router.get("/search/suggest", wrap(async (req, res) => {
    const { prefix = "" } = req.query;
    const archive = safe(readJson, ARCHIVE_FILE, []);
    res.json({ suggestions: suggestTerms(archive, prefix) });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/analytics
  ──────────────────────────────────────────────────────────── */
  router.get("/analytics", wrap(async (_req, res) => {
    const archive = safe(readJson, ARCHIVE_FILE, []);
    const alerts = safe(readJson, ALERT_FILE, []);
    const distLog = safe(readJson, DISTRIBUTION_LOG_FILE, []);
    const stats = computeStats(archive, alerts, distLog);
    const trends = computeTrends(archive, 15);
    const matrix = computePersonaQualityMatrix(archive);
    const production = computeProductionRate(archive);
    res.json({ stats, trends, matrix, production });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/trends?limit=
  ──────────────────────────────────────────────────────────── */
  router.get("/trends", wrap(async (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 15, 30);
    const archive = safe(readJson, ARCHIVE_FILE, []);
    res.json({ trends: computeTrends(archive, limit) });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/distribution-log
  ──────────────────────────────────────────────────────────── */
  router.get("/distribution-log", wrap(async (req, res) => {
    const limit = Math.min(Number(req.query.limit) || 50, 200);
    const distLog = safe(readJson, DISTRIBUTION_LOG_FILE, []);
    res.json({ log: [...distLog].reverse().slice(0, limit), total: distLog.length });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/models  (model listesi ve sağlık)
  ──────────────────────────────────────────────────────────── */
  router.get("/models", wrap(async (_req, res) => {
    const tiers = {
      fast: getPreferredModels("hizli"),
      balanced: getPreferredModels("dengeli"),
      deep: getPreferredModels("derin")
    };
    res.json({ tiers, groqConfigured: Boolean(process.env.GROQ_API_KEY) });
  }));

  /* ────────────────────────────────────────────────────────────
     POST /api/export  { id?, article?, format: 'json'|'text'|'markdown' }
  ──────────────────────────────────────────────────────────── */
  router.post("/export", wrap(async (req, res) => {
    const { id, article: rawArticle, format = "markdown" } = req.body || {};

    let payload;
    if (id) {
      const archive = safe(readJson, ARCHIVE_FILE, []);
      const entry = archive.find((e) => e.id === id);
      if (!entry) throw httpErr(404, `Arşiv kaydı bulunamadı: ${id}`);
      payload = entry;
    } else if (rawArticle) {
      payload = { article: rawArticle, exportedAt: new Date().toISOString() };
    } else {
      throw httpErr(400, "id veya article gerekli");
    }

    if (format === "json") {
      res.setHeader("Content-Disposition", `attachment; filename="nexus_export_${Date.now()}.json"`);
      res.setHeader("Content-Type", "application/json; charset=utf-8");
      return res.send(JSON.stringify(payload, null, 2));
    }

    if (format === "text") {
      res.setHeader("Content-Disposition", `attachment; filename="nexus_export_${Date.now()}.txt"`);
      res.setHeader("Content-Type", "text/plain; charset=utf-8");
      return res.send(String(payload.article || "").replace(/[#*_`>]/g, ""));
    }

    // Default: markdown
    res.setHeader("Content-Disposition", `attachment; filename="nexus_export_${Date.now()}.md"`);
    res.setHeader("Content-Type", "text/markdown; charset=utf-8");
    const header = [
      `---`,
      `topic: "${payload.topic || ""}"`,
      `persona: "${payload.persona || ""}"`,
      `quality: "${payload.quality || ""}"`,
      `createdAt: "${payload.createdAt || new Date().toISOString()}"`,
      `model: "${payload.modelUsed || "fallback"}"`,
      `---`,
      ""
    ].join("\n");
    return res.send(header + (payload.article || ""));
  }));

  /* ────────────────────────────────────────────────────────────
     POST /api/quick-brief  { topic, persona }
     Hızlı 3 maddelik öz; tek API çağrısı, düşük gecikme.
  ──────────────────────────────────────────────────────────── */
  router.post("/quick-brief", wrap(async (req, res) => {
    const topic = String(req.body?.topic || "").trim();
    const persona = String(req.body?.persona || "analist").trim();
    if (!topic) throw httpErr(400, "topic gerekli");

    const ontology = buildOntologyGraph(topic);
    const domains = (ontology.dominantDomains || []).slice(0, 3).map((d) => d.domain).join(", ") || "genel";
    const messages = [
      {
        role: "system",
        content: `Sen ${persona} perspektifinden 3 maddelik acil öz üreten bir istihbarat asistanısın.`
      },
      {
        role: "user",
        content: [
          `Konu: ${topic}`,
          `Baskin alanlar: ${domains}`,
          "Kural: tam olarak 3 bullet point, her biri 1 cümle, sayı/oran içermeli.",
          "Format: - [madde]"
        ].join("\n")
      }
    ];

    let content;
    try {
      const result = await callGroq(messages, 0.55, getPreferredModels("hizli"));
      content = result.content;
    } catch (err) {
      error("Quick-brief model hatası", err);
      content = [
        `- ${topic} kısa vadede belirsizlik içeriyor; kaynak dogrulama kritik.`,
        `- ${domains} alanında ikincil etkiler fiyatlanmamış olabilir.`,
        `- Erken uyarı sinyalleri için OSINT katmanı aktif tutulmalı.`
      ].join("\n");
    }

    broadcastSSE("quick-brief", { topic, persona, brief: content });
    res.json({ topic, persona, brief: content, ontology: { domains: ontology.dominantDomains?.slice(0, 4) } });
  }));

  /* ────────────────────────────────────────────────────────────
     POST /api/summarize  { text, maxLen }
  ──────────────────────────────────────────────────────────── */
  router.post("/summarize", wrap(async (req, res) => {
    const text = String(req.body?.text || "").trim();
    const maxLen = Math.min(Number(req.body?.maxLen) || 800, 3000);
    if (!text) throw httpErr(400, "text gerekli");

    const messages = [
      { role: "system", content: "Türkçe olarak kısa, net, karar-odaklı özet yaz. Fazla kelime yok." },
      { role: "user", content: `Şu metni ${maxLen} karakterin altında özetle:\n${text.slice(0, 4000)}` }
    ];

    let summary;
    try {
      const result = await callGroq(messages, 0.5, getPreferredModels("hizli"));
      summary = result.content;
    } catch (err) {
      summary = summarizeText(text, maxLen);
    }
    res.json({ summary, charCount: summary.length });
  }));

  /* ────────────────────────────────────────────────────────────
     POST /api/compare  { articleA, articleB, persona }
     İki makaleyi karşılaştırıp sentez üret.
  ──────────────────────────────────────────────────────────── */
  router.post("/compare", wrap(async (req, res) => {
    const articleA = String(req.body?.articleA || "").trim();
    const articleB = String(req.body?.articleB || "").trim();
    const persona = String(req.body?.persona || "analist").trim();
    if (!articleA || !articleB) throw httpErr(400, "articleA ve articleB gerekli");

    const messages = [
      { role: "system", content: `Sen ${persona} olarak iki analiz metnini karşılaştırıyorsun. 3 bölüm: Uyuşan Noktalar / Çelişen Noktalar / Bütünleşik Sonuç.` },
      {
        role: "user",
        content: [
          "## Metin A",
          summarizeText(articleA, 1400),
          "## Metin B",
          summarizeText(articleB, 1400),
          "Yukarıdaki iki metni 3 bölümde karşılaştır."
        ].join("\n\n")
      }
    ];

    let comparison;
    try {
      const result = await callGroq(messages, 0.6, getPreferredModels("dengeli"));
      comparison = result.content;
    } catch (err) {
      comparison = "Karşılaştırma modeli yanıt vermedi; her iki metin de aktif kullanılabilir.";
    }
    res.json({ comparison, persona });
  }));

  /* ────────────────────────────────────────────────────────────
     POST /api/batch  { topics: string[], persona, quality }
     Toplu üretim kuyruğu — arka planda çalışır, ID döner.
  ──────────────────────────────────────────────────────────── */
  router.post("/batch", wrap(async (req, res) => {
    const topics = (req.body?.topics || []).map((t) => String(t).trim()).filter(Boolean).slice(0, 10);
    const persona = String(req.body?.persona || "analist").trim();
    const quality = String(req.body?.quality || "hizli").trim();
    if (!topics.length) throw httpErr(400, "En az 1 konu gerekli");

    const jobId = nowId("batch");
    batchJobs.set(jobId, {
      id: jobId,
      topics,
      persona,
      quality,
      status: "queued",
      total: topics.length,
      done: 0,
      results: [],
      errors: [],
      createdAt: new Date().toISOString(),
      finishedAt: null
    });

    // Async fire-and-forget
    processBatch(jobId, deps).catch((err) => error("Batch işlem hatası", err, { jobId }));
    broadcastSSE("batch-started", { jobId, count: topics.length });

    res.status(202).json({ jobId, topics, status: "queued", total: topics.length });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/batch/:id
  ──────────────────────────────────────────────────────────── */
  router.get("/batch/:id", wrap(async (req, res) => {
    const job = batchJobs.get(req.params.id);
    if (!job) throw httpErr(404, `Batch görev bulunamadı: ${req.params.id}`);
    res.json(job);
  }));

  /* ────────────────────────────────────────────────────────────
     DELETE /api/archive/:id
  ──────────────────────────────────────────────────────────── */
  router.delete("/archive/:id", wrap(async (req, res) => {
    const archive = safe(readJson, ARCHIVE_FILE, []);
    const before = archive.length;
    const filtered = archive.filter((e) => e.id !== req.params.id);
    if (filtered.length === before) throw httpErr(404, `Kayıt bulunamadı: ${req.params.id}`);
    writeJson(ARCHIVE_FILE, filtered);
    broadcastSSE("archive-deleted", { id: req.params.id });
    res.json({ ok: true, deleted: req.params.id, remaining: filtered.length });
  }));

  /* ────────────────────────────────────────────────────────────
     PATCH /api/archive/:id  { pinned, tag }
  ──────────────────────────────────────────────────────────── */
  router.patch("/archive/:id", wrap(async (req, res) => {
    const archive = safe(readJson, ARCHIVE_FILE, []);
    const idx = archive.findIndex((e) => e.id === req.params.id);
    if (idx < 0) throw httpErr(404, `Kayıt bulunamadı: ${req.params.id}`);
    if (req.body.pinned !== undefined) archive[idx].pinned = Boolean(req.body.pinned);
    if (req.body.tag !== undefined) archive[idx].tag = String(req.body.tag).slice(0, 40);
    writeJson(ARCHIVE_FILE, archive);
    broadcastSSE("archive-updated", { id: req.params.id, changes: req.body });
    res.json({ ok: true, entry: archive[idx] });
  }));

  /* ────────────────────────────────────────────────────────────
     GET /api/sse  –  Server-Sent Events stream
  ──────────────────────────────────────────────────────────── */
  router.get("/sse", (req, res) => {
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.setHeader("X-Accel-Buffering", "no");
    res.flushHeaders();

    // Send welcome event
    res.write(`event: connected\ndata: ${JSON.stringify({ clients: sseClients.size + 1, now: new Date().toISOString() })}\n\n`);

    sseClients.add(res);
    info("SSE client bağlandı", { total: sseClients.size });

    // Heartbeat every 20s
    const hb = setInterval(() => {
      try { res.write(`: heartbeat ${new Date().toISOString()}\n\n`); } catch (_) { cleanup(); }
    }, 20000);

    function cleanup() {
      clearInterval(hb);
      sseClients.delete(res);
      info("SSE client ayrıldı", { total: sseClients.size });
    }

    req.on("close", cleanup);
    req.on("error", cleanup);
  });

  return router;
}

/* ---------- Batch processor (runs outside request cycle) ----- */
async function processBatch(jobId, deps) {
  const job = batchJobs.get(jobId);
  if (!job) return;
  job.status = "running";

  const { ARCHIVE_FILE, readJson, writeJson } = deps;

  for (const topic of job.topics) {
    try {
      const messages = [
        { role: "system", content: `Sen ${job.persona} perspektifli hızlı bir analistsin.` },
        { role: "user", content: `${topic} hakkında kısa ama özlü bir analiz notu yaz. Maksimum 400 kelime. Kalite: ${job.quality}.` }
      ];
      const result = await callGroq(messages, getQualityProfile(job.quality).generationTemp, getPreferredModels(job.quality));
      const entry = {
        id: `batch_${Date.now()}_${Math.random().toString(36).slice(2, 5)}`,
        createdAt: new Date().toISOString(),
        topic,
        persona: job.persona,
        quality: job.quality,
        article: result.content,
        generationMode: "ai",
        modelUsed: result.model,
        batchJobId: jobId
      };
      const archive = readJson(ARCHIVE_FILE, []);
      archive.push(entry);
      writeJson(ARCHIVE_FILE, archive);
      job.results.push({ topic, id: entry.id, ok: true });
    } catch (err) {
      job.errors.push({ topic, error: err.message });
      job.results.push({ topic, ok: false, error: err.message });
    }
    job.done += 1;
    broadcastSSE("batch-progress", { jobId, done: job.done, total: job.total, topic });
  }

  job.status = "done";
  job.finishedAt = new Date().toISOString();
  broadcastSSE("batch-done", { jobId, done: job.done, errors: job.errors.length });
}

module.exports = { createAdvancedRouter, broadcastSSE, sseClients };
