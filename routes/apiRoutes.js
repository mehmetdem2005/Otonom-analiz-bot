const express = require("express");
const fs = require("fs");
const path = require("path");

const { callGroq } = require("../services/aiService");
const {
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
} = require("../services/contentService");
const {
  getQualityProfile,
  getPreferredModels,
  buildGenerationMessages,
  buildConsensusMessages
} = require("../services/promptEngine");
const { buildOntologyGraph } = require("../services/ontologyService");
const { listAgents, buildExecutionPlan, buildAgentBriefs } = require("../services/agentOrchestrator");
const { buildVoiceProfile, chunkForXWithVoice, publishThreadToX } = require("../services/distributionService");
const { runProcess } = require("../utils/processRunner");
const { info, error } = require("../utils/logger");

const PERSONAS = {
  analist: "Sen sert, veri odakli ve net cikan bir stratejik analiz yazarisin.",
  siber: "Sen tehdit modelleme bilen, teknik detay ve savunma onceligi yuksek bir siber analiz uzmanisin.",
  finans: "Sen piyasa davranisi, likidite, risk primi ve fiyatlama reflekslerine odaklanan bir finans stratejistisin.",
  derin: "Sen ikinci derece etkiler, toplumsal davranis, jeopolitik yayilim ve anlatilar arasi baglari cikarabilen derin bir analizcisindir."
};

function createHttpError(status, message, details) {
  const err = new Error(message);
  err.status = status;
  err.details = details;
  return err;
}

function ensureArray(value) {
  return Array.isArray(value) ? value : [];
}

function nowId(prefix) {
  return `${prefix}_${Date.now()}`;
}

function safeReadJson(readJson, file, fallback) {
  const data = readJson(file, fallback);
  return data == null ? fallback : data;
}

function getPythonBinary(rootDir) {
  const local = path.join(rootDir, ".venv", "bin", "python");
  if (fs.existsSync(local)) return local;
  return process.env.PYTHON_BIN || "python3";
}

async function fetchJsonFromPython(rootDir, scriptName, args = []) {
  const command = getPythonBinary(rootDir);
  const scriptPath = path.join(rootDir, scriptName);
  const result = await runProcess(command, [scriptPath, ...args], {
    cwd: rootDir,
    env: process.env
  });

  if (result.code !== 0) {
    throw new Error(result.stderr || `${scriptName} calistirilamadi`);
  }

  const text = String(result.stdout || "").trim();
  return text ? JSON.parse(text) : {};
}

async function runTts(rootDir, tempDir, text) {
  const command = getPythonBinary(rootDir);
  const outFile = path.join(tempDir, `${nowId("podcast")}.mp3`);
  const scriptPath = path.join(rootDir, "tts_edge.py");
  const result = await runProcess(command, [scriptPath, cleanTextForTTS(text), outFile], {
    cwd: rootDir,
    env: process.env
  });

  if (result.code !== 0) {
    throw new Error(result.stderr || "TTS basarisiz");
  }

  return outFile;
}

async function callGroqOrNull(messages, temperature, preferredModels) {
  try {
    return await callGroq(messages, temperature, preferredModels);
  } catch (err) {
    error("AI model fallback'a dustu", err);
    return null;
  }
}

async function delay(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function generateCover(prompt) {
  const encoded = encodeURIComponent(prompt);
  const fallbackUrl = `https://image.pollinations.ai/prompt/${encoded}?width=1280&height=720&seed=42&model=flux`;

  if (!process.env.DALL_E_API_KEY) {
    return { imageUrl: fallbackUrl, source: "pollinations", note: "DALL-E anahtari yok; fallback kullanildi." };
  }

  const attempts = [0, 700, 1400];
  let lastError;

  for (const waitMs of attempts) {
    if (waitMs) await delay(waitMs);
    try {
      const response = await fetch("https://api.openai.com/v1/images/generations", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${process.env.DALL_E_API_KEY}`,
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          model: "gpt-image-1",
          prompt,
          size: "1024x1024"
        })
      });

      if (!response.ok) {
        const body = await response.text();
        throw new Error(`DALL-E status=${response.status} body=${body}`);
      }

      const data = await response.json();
      const item = data.data?.[0];
      if (item?.url) {
        return { imageUrl: item.url, source: "dall-e", note: "DALL-E ile olusturuldu." };
      }
      if (item?.b64_json) {
        return { imageUrl: `data:image/png;base64,${item.b64_json}`, source: "dall-e", note: "DALL-E ile olusturuldu." };
      }
      throw new Error("DALL-E bos veri dondurdu");
    } catch (err) {
      lastError = err;
      error("DALL-E denemesi basarisiz", err, { waitMs });
    }
  }

  return {
    imageUrl: fallbackUrl,
    source: "pollinations",
    note: `DALL-E denemeleri basarisiz; fallback kullanildi. ${lastError ? lastError.message : ""}`.trim()
  };
}

async function cacheRemoteFile(tempDir, input, prefix, defaultExtension = ".bin") {
  if (!input) return "";
  if (!/^https?:/i.test(input)) return input;

  const response = await fetch(input);
  if (!response.ok) throw new Error(`Uzak dosya indirilemedi status=${response.status}`);
  const contentType = response.headers.get("content-type") || "";
  const extension = contentType.includes("audio") ? ".mp3" : contentType.includes("image/png") ? ".png" : contentType.includes("image/jpeg") ? ".jpg" : defaultExtension;
  const outFile = path.join(tempDir, `${nowId(prefix)}${extension}`);
  const buf = Buffer.from(await response.arrayBuffer());
  fs.writeFileSync(outFile, buf);
  return outFile;
}

function buildReplyPairs(comments) {
  if (!comments.length) return [];
  return comments.slice(0, 3).map((comment, index) => ({
    from: comments[index].name,
    to: comments[(index + 1) % comments.length].name,
    text: `${comments[index].name}, ${comments[(index + 1) % comments.length].name} yorumundaki kritik noktaya bir ek getirdi.`
  }));
}

function buildPersonaPrompt(persona, qualityProfile) {
  return [
    PERSONAS[persona] || PERSONAS.analist,
    `Kalite modu: ${qualityProfile.name}.`,
    qualityProfile.instruction,
    "Fazla genel yazma. Sayi, risk ve eylem ver."
  ].join(" ");
}

async function generateArticleBundle({ topic, persona, quality, alerts, rootDir, readJson, ARCHIVE_FILE, ALERT_FILE }) {
  const qualityProfile = getQualityProfile(quality);
  const liveNews = await fetchJsonFromPython(rootDir, "live_news_fusion.py", [topic]).catch((err) => ({
    fusion: `Canli haber alinamadi: ${err.message}`,
    items: []
  }));

  const promptAlerts = ensureArray(alerts).slice(-8);
  const ontology = buildOntologyGraph(topic, liveNews.fusion || "");
  const agentPlan = buildExecutionPlan({ topic, persona, quality, ontology });
  const personaPrompt = buildPersonaPrompt(persona, qualityProfile);
  const messages = buildGenerationMessages({
    topic,
    personaPrompt,
    liveNewsText: liveNews.fusion,
    alerts: promptAlerts,
    quality,
    ontology,
    agentPlan
  });

  const aiArticle = await callGroqOrNull(messages, qualityProfile.generationTemp, getPreferredModels(quality));
  const article = aiArticle?.content || buildFallbackArticle(topic, persona, liveNews.fusion, promptAlerts);
  const keywords = extractKeywordsFromText(article, 10);
  const coverPrompt = [
    `${topic} icin premium dergi kapagi`,
    `ana eksenler: ${keywords.slice(0, 6).join(", ")}`,
    "modern editorial, cinematic contrast, strong typography, high detail, no watermark"
  ].join(", ");
  const inlineCharts = buildInlineChartPlacements(article, extractNumericData(article));
  const agentBriefs = buildAgentBriefs({ topic, article, ontology, agentPlan });
  const consensusResponse = await callGroqOrNull(
    buildConsensusMessages({ topic, article, ontology, agentBriefs, quality }),
    0.45,
    getPreferredModels(quality)
  );

  return {
    topic,
    persona,
    quality,
    article,
    coverPrompt,
    keywords,
    liveNews,
    ontology,
    agentPlan,
    agentBriefs,
    inlineCharts,
    consensus: consensusResponse?.content || "Konsensus motoru fallback modunda; temel ajan brifleri kullanilabilir.",
    generationMode: aiArticle ? "ai" : "fallback",
    generationWarning: aiArticle ? null : "Canli model yaniti alinamadi; fallback rapor uretildi.",
    modelUsed: aiArticle?.model || null
  };
}

function createApiRouter(deps) {
  const {
    ARCHIVE_FILE,
    ALERT_FILE,
    DISTRIBUTION_LOG_FILE,
    PROFILE_FILE,
    TEMP_DIR,
    readJson,
    writeJson,
    generateVideo,
    runRadar
  } = deps;

  const rootDir = path.resolve(__dirname, "..");
  const tweetsFile = path.join(rootDir, "tweets.json");
  const router = express.Router();

  const wrap = (handler) => async (req, res) => {
    try {
      await handler(req, res);
    } catch (err) {
      const status = err.status || 500;
      error("API istegi basarisiz", err, { path: req.path, status });
      res.status(status).json({
        error: err.message || "Bilinmeyen hata",
        details: err.details || null,
        path: req.path,
        status
      });
    }
  };

  router.get("/health", wrap(async (_req, res) => {
    res.json({ ok: true, now: new Date().toISOString(), agents: listAgents().length });
  }));

  router.get("/archive", wrap(async (_req, res) => {
    res.json(safeReadJson(readJson, ARCHIVE_FILE, []));
  }));

  router.get("/agents", wrap(async (_req, res) => {
    res.json({ agents: listAgents() });
  }));

  router.post("/ontology", wrap(async (req, res) => {
    const topic = String(req.body?.topic || "").trim();
    const text = String(req.body?.text || "").trim();
    if (!topic && !text) throw createHttpError(400, "topic veya text gerekli");
    res.json(buildOntologyGraph(topic || text.slice(0, 80), text));
  }));

  router.post("/generate", wrap(async (req, res) => {
    const topic = String(req.body?.topic || "").trim();
    const persona = String(req.body?.persona || "analist").trim();
    const quality = String(req.body?.quality || "dengeli").trim();
    if (!topic) throw createHttpError(400, "Konu gerekli");

    const alerts = safeReadJson(readJson, ALERT_FILE, []);
    const bundle = await generateArticleBundle({ topic, persona, quality, alerts, rootDir, readJson, ARCHIVE_FILE, ALERT_FILE });
    const archive = safeReadJson(readJson, ARCHIVE_FILE, []);
    archive.push({
      id: nowId("arc"),
      createdAt: new Date().toISOString(),
      topic,
      persona,
      quality,
      article: bundle.article,
      coverPrompt: bundle.coverPrompt,
      keywords: bundle.keywords,
      ontology: bundle.ontology,
      generationMode: bundle.generationMode,
      modelUsed: bundle.modelUsed
    });
    writeJson(ARCHIVE_FILE, archive);
    res.json(bundle);
  }));

  router.post("/generate-bundle", wrap(async (req, res) => {
    const topic = String(req.body?.topic || "").trim();
    const persona = String(req.body?.persona || "analist").trim();
    const quality = String(req.body?.quality || "dengeli").trim();
    if (!topic) throw createHttpError(400, "Konu gerekli");

    const alerts = safeReadJson(readJson, ALERT_FILE, []);
    const bundle = await generateArticleBundle({ topic, persona, quality, alerts, rootDir, readJson, ARCHIVE_FILE, ALERT_FILE });

    const forecastPrompt = buildFuturePrompt({
      topic,
      horizon: "12 ay",
      articleText: bundle.article,
      newsContext: bundle.liveNews?.fusion || ""
    });
    const futureResponse = await callGroqOrNull([{ role: "user", content: forecastPrompt }], getQualityProfile(quality).futureTemp, getPreferredModels(quality));
    const forecast = futureResponse?.content || [
      "1) En iyi senaryo: Enflasyon %24, buyume %4.1, savunma harcamasi 38 milyar $, enerji endeksi 91, risk skoru 34, etkilenen ulkeler: Turkiye, Almanya, BAE.",
      "2) Baz senaryo: Enflasyon %31, buyume %3.0, savunma harcamasi 34 milyar $, enerji endeksi 104, risk skoru 48, etkilenen ulkeler: Turkiye, Yunanistan, Polonya.",
      "3) En kotu senaryo: Enflasyon %43, buyume %0.8, savunma harcamasi 41 milyar $, enerji endeksi 127, risk skoru 72, etkilenen ulkeler: Turkiye, Ukrayna, Romanya.",
      "Eylem Plani: veri teyidi, tedarik savunmasi, piyasa hedge'i, anlati takibi, uyum denetimi."
    ].join("\n\n");

    const profiles = safeReadJson(readJson, PROFILE_FILE, []);
    const commentTasks = profiles.map((profile) => ({
      profile,
      messages: [{ role: "user", content: buildCommentPrompt(profile, bundle.article) }]
    }));

    const comments = [];
    for (const task of commentTasks) {
      const response = await callGroqOrNull(task.messages, getQualityProfile(quality).commentTemp, getPreferredModels(quality));
      comments.push({
        name: task.profile.name,
        avatar: task.profile.avatar,
        text: response?.content || buildFallbackComments(bundle.article).find((item) => item.name === task.profile.name)?.text || "Yorum olusturulamadi."
      });
    }

    const chart = extractNumericData(`${bundle.article}\n${forecast}`);
    const palette = personaPalette(persona);
    const archive = safeReadJson(readJson, ARCHIVE_FILE, []);
    archive.push({
      id: nowId("arc"),
      createdAt: new Date().toISOString(),
      topic,
      persona,
      quality,
      article: bundle.article,
      future: forecast,
      comments,
      ontology: bundle.ontology,
      agentPlan: bundle.agentPlan,
      generationMode: bundle.generationMode,
      modelUsed: bundle.modelUsed
    });
    writeJson(ARCHIVE_FILE, archive);

    res.json({
      article: bundle,
      future: forecast,
      comments: {
        comments,
        replyPairs: buildReplyPairs(comments)
      },
      chart: {
        labels: chart.labels,
        datasets: chart.datasets,
        chartType: chart.chartType,
        palette
      },
      ontology: bundle.ontology,
      agentPlan: bundle.agentPlan,
      consensus: bundle.consensus
    });
  }));

  router.post("/generate-cover", wrap(async (req, res) => {
    const prompt = String(req.body?.prompt || "").trim();
    if (!prompt) throw createHttpError(400, "Prompt gerekli");
    res.json(await generateCover(prompt));
  }));

  router.post("/podcast", wrap(async (req, res) => {
    const text = String(req.body?.text || "").trim();
    if (!text) throw createHttpError(400, "Podcast icin metin gerekli");
    const outFile = await runTts(rootDir, TEMP_DIR, text);
    res.json({ audioUrl: `/temp/${path.basename(outFile)}` });
  }));

  router.post("/generate-video", wrap(async (req, res) => {
    const title = String(req.body?.title || "").trim() || "Otonom Basyazar";
    const subtitleText = String(req.body?.subtitleText || title).trim();
    const audioPathInput = String(req.body?.audioPath || "").trim();
    if (!audioPathInput) throw createHttpError(400, "audioPath gerekli");

    const imagePath = await cacheRemoteFile(TEMP_DIR, String(req.body?.imagePath || "").trim(), "cover", ".png").catch(() => "");
    const audioPath = await cacheRemoteFile(TEMP_DIR, audioPathInput, "audio", ".mp3");
    const videoFile = await generateVideo({ imagePath, audioPath, title, subtitleText });
    res.json({ videoUrl: `/temp/${path.basename(videoFile)}` });
  }));

  router.post("/generate-future", wrap(async (req, res) => {
    const topic = String(req.body?.topic || "").trim();
    const horizon = String(req.body?.horizon || "12 ay").trim();
    const articleText = String(req.body?.articleText || "").trim();
    const newsContext = String(req.body?.newsContext || "").trim();
    if (!topic && !articleText) throw createHttpError(400, "topic veya articleText gerekli");

    const prompt = buildFuturePrompt({ topic: topic || "Genel gundem", horizon, articleText, newsContext });
    const result = await callGroqOrNull([{ role: "user", content: prompt }], 0.6, getPreferredModels("derin"));
    res.json({ forecast: result?.content || summarizeText(articleText || newsContext, 1200) || "Future report olusturulamadi." });
  }));

  router.post("/chart-data", wrap(async (req, res) => {
    const article = String(req.body?.article || "").trim();
    const persona = String(req.body?.persona || "analist").trim();
    if (!article) throw createHttpError(400, "article gerekli");
    const chart = extractNumericData(article);
    res.json({
      labels: chart.labels,
      datasets: chart.datasets,
      chartType: chart.chartType,
      palette: personaPalette(persona)
    });
  }));

  router.post("/generate-comments", wrap(async (req, res) => {
    const article = String(req.body?.article || "").trim();
    if (!article) throw createHttpError(400, "article gerekli");

    const profiles = safeReadJson(readJson, PROFILE_FILE, []);
    const comments = [];
    for (const profile of profiles) {
      const result = await callGroqOrNull([{ role: "user", content: buildCommentPrompt(profile, article) }], 0.9, getPreferredModels("dengeli"));
      comments.push({
        name: profile.name,
        avatar: profile.avatar,
        text: result?.content || buildFallbackComments(article).find((item) => item.name === profile.name)?.text || "Yorum olusturulamadi."
      });
    }

    res.json({ comments, replies: buildReplyPairs(comments) });
  }));

  router.post("/deep-expand", wrap(async (req, res) => {
    const keyword = String(req.body?.keyword || "").trim();
    const context = String(req.body?.context || "").trim();
    if (!keyword) throw createHttpError(400, "keyword gerekli");
    const ontology = buildOntologyGraph(keyword, context);
    const result = await callGroqOrNull([
      {
        role: "user",
        content: [
          `Kavram: ${keyword}`,
          `Baglam: ${summarizeText(context, 1200)}`,
          `Ontoloji: ${JSON.stringify(ontology)}`,
          "Bu kavrami 4 baslikta ac: tanim, sistemik etkiler, kritik riskler, izlenmesi gereken sinyaller."
        ].join("\n\n")
      }
    ], 0.62, getPreferredModels("derin"));

    res.json({ expanded: result?.content || `${keyword} icin derin aciklama uretilemedi; mevcut baglam: ${summarizeText(context, 600)}` });
  }));

  router.post("/distribute/x", wrap(async (req, res) => {
    const article = String(req.body?.article || "").trim();
    if (!article) throw createHttpError(400, "article gerekli");

    const tweets = safeReadJson(readJson, tweetsFile, []);
    const voiceProfile = buildVoiceProfile(article, tweets);
    const chunks = tweets.length ? chunkForXWithVoice(article, chunkForX, voiceProfile) : chunkForX(article, 250);

    const publishResult = await publishThreadToX(chunks, {
      apiKey: process.env.X_API_KEY,
      apiSecret: process.env.X_API_SECRET,
      accessToken: process.env.X_ACCESS_TOKEN,
      accessSecret: process.env.X_ACCESS_SECRET
    });

    const logEntries = safeReadJson(readJson, DISTRIBUTION_LOG_FILE, []);
    logEntries.push({
      id: nowId("distx"),
      createdAt: new Date().toISOString(),
      channel: "x",
      published: publishResult.published || 0,
      simulated: Boolean(publishResult.simulated),
      dominantTone: voiceProfile.dominantTone,
      motifs: voiceProfile.motifs,
      detail: publishResult
    });
    writeJson(DISTRIBUTION_LOG_FILE, logEntries);

    res.json({ xChunks: chunks.length, chunks, voiceProfile, publishResult });
  }));

  router.post("/distribute/telegram", wrap(async (req, res) => {
    const article = String(req.body?.article || "").trim();
    if (!article) throw createHttpError(400, "article gerekli");

    const token = process.env.TELEGRAM_BOT_TOKEN;
    const chatId = process.env.TELEGRAM_CHAT_ID;
    let result;

    if (!token || !chatId) {
      result = { simulated: true, reason: "Telegram kimlik bilgileri eksik" };
    } else {
      const response = await fetch(`https://api.telegram.org/bot${token}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: chatId, text: summarizeText(article, 3800) })
      });
      const body = await response.json();
      result = { simulated: false, ok: response.ok, body };
    }

    const logEntries = safeReadJson(readJson, DISTRIBUTION_LOG_FILE, []);
    logEntries.push({ id: nowId("disttg"), createdAt: new Date().toISOString(), channel: "telegram", detail: result });
    writeJson(DISTRIBUTION_LOG_FILE, logEntries);

    res.json(result);
  }));

  router.post("/distribute/all", wrap(async (req, res) => {
    const article = String(req.body?.article || "").trim();
    if (!article) throw createHttpError(400, "article gerekli");

    const tweets = safeReadJson(readJson, tweetsFile, []);
    const voiceProfile = buildVoiceProfile(article, tweets);
    const chunks = tweets.length ? chunkForXWithVoice(article, chunkForX, voiceProfile) : chunkForX(article, 250);
    const xResult = await publishThreadToX(chunks, {
      apiKey: process.env.X_API_KEY,
      apiSecret: process.env.X_API_SECRET,
      accessToken: process.env.X_ACCESS_TOKEN,
      accessSecret: process.env.X_ACCESS_SECRET
    });

    let telegramResult = { simulated: true, reason: "Telegram kimlik bilgileri eksik" };
    if (process.env.TELEGRAM_BOT_TOKEN && process.env.TELEGRAM_CHAT_ID) {
      const response = await fetch(`https://api.telegram.org/bot${process.env.TELEGRAM_BOT_TOKEN}/sendMessage`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: process.env.TELEGRAM_CHAT_ID, text: summarizeText(article, 3800) })
      });
      telegramResult = { simulated: false, ok: response.ok, body: await response.json() };
    }

    const logEntries = safeReadJson(readJson, DISTRIBUTION_LOG_FILE, []);
    logEntries.push({
      id: nowId("distall"),
      createdAt: new Date().toISOString(),
      channel: "all",
      voiceProfile,
      x: xResult,
      telegram: telegramResult
    });
    writeJson(DISTRIBUTION_LOG_FILE, logEntries);

    res.json({ xChunks: chunks.length, voiceProfile, x: xResult, telegram: telegramResult });
  }));

  router.post("/osint-scan", wrap(async (_req, res) => {
    await runRadar();
    const alerts = safeReadJson(readJson, ALERT_FILE, []);
    res.json({ alerts: alerts.slice(-20) });
  }));

  router.get("/osint-alerts", wrap(async (_req, res) => {
    res.json({ alerts: safeReadJson(readJson, ALERT_FILE, []).slice(-50) });
  }));

  router.post("/dal10", wrap(async (_req, res) => {
    res.json({ ok: true, status: "reserved", note: "DAL 10 operasyon genisleme alani olarak saklandi." });
  }));

  router.use((req, _res, next) => {
    info("API route erisimi", { method: req.method, path: req.path });
    next();
  });

  return router;
}

module.exports = { createApiRouter };