const express = require("express");
const cors = require("cors");
const fs = require("fs");
const path = require("path");
require("dotenv").config();

const schedule = require("node-schedule");

const { generateVideo } = require("./video-generator");
const { runRadar } = require("./osint-radar");
const { readJson, writeJson } = require("./utils/jsonStore");
const { createApiRouter } = require("./routes/apiRoutes");
const { createAdvancedRouter, broadcastSSE } = require("./routes/advancedRoutes");
const { info, error } = require("./utils/logger");

const app = express();
const PORT = Number(process.env.PORT || 3000);

const ARCHIVE_FILE = path.join(__dirname, "archive.json");
const ALERT_FILE = path.join(__dirname, "alerts.json");
const DISTRIBUTION_LOG_FILE = path.join(__dirname, "distribution-log.json");
const PROFILE_FILE = path.join(__dirname, "comment-profiles.json");
const TEMP_DIR = path.join(__dirname, "temp");
const PUBLIC_DIR = path.join(__dirname, "public");

// --- Ensure directories ---
for (const dir of [TEMP_DIR]) {
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
}

// --- Shared router dependencies ---
const routerDeps = {
  ARCHIVE_FILE,
  ALERT_FILE,
  DISTRIBUTION_LOG_FILE,
  PROFILE_FILE,
  TEMP_DIR,
  readJson,
  writeJson,
  generateVideo,
  runRadar
};

// --- Middleware ---
app.use(cors({
  origin: (origin, callback) => {
    // Origin yoksa same-origin ya da non-browser istek — izin ver
    if (!origin) return callback(null, true);
    // Ekstra izinli origin'ler .env'den okunur
    const extra = (process.env.CORS_ALLOWED_ORIGINS || "").split(",").filter(Boolean);
    const isLocal = /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/.test(origin);
    const isCodespace = /\.github\.dev$/.test(origin) || /\.app\.github\.dev$/.test(origin);
    if (isLocal || isCodespace || extra.some((o) => origin.startsWith(o.trim()))) {
      return callback(null, true);
    }
    return callback(null, false);
  },
  credentials: true,
}));
app.use(express.json({ limit: "4mb" }));
app.use(express.static(PUBLIC_DIR));
app.use("/temp", express.static(TEMP_DIR));

// --- Request logger (structured) ---
app.use((req, _res, next) => {
  if (!req.path.startsWith("/api/sse") && !req.path.startsWith("/temp")) {
    info("HTTP", { method: req.method, path: req.path });
  }
  next();
});

// --- API Routers ---
app.use("/api", createApiRouter(routerDeps));
app.use("/api", createAdvancedRouter(routerDeps));

// --- SPA catch-all: her non-API, non-static yol index.html döner ---
app.get("*", (req, res, next) => {
  if (req.path.startsWith("/api") || req.path.startsWith("/temp")) return next();
  const indexPath = path.join(PUBLIC_DIR, "index.html");
  if (!fs.existsSync(indexPath)) return next();
  res.sendFile(indexPath);
});

// --- 404 JSON handler ---
app.use((req, res) => {
  res.status(404).json({ error: "Kaynak bulunamadı", path: req.path, status: 404 });
});

// --- Global error handler ---
app.use((err, req, res, _next) => {
  error("Express hata yakalayıcı", err, { path: req.path });
  const status = err.status || 500;
  res.status(status).json({ error: err.message || "Sunucu hatası", status });
});

// --- OSINT Scheduler: her 15 dakika ---
schedule.scheduleJob("*/15 * * * *", async () => {
  try {
    info("Zamanlanmış OSINT tarama başladı");
    await runRadar();
    const alerts = readJson(ALERT_FILE, []);
    const recent = alerts.filter((a) => {
      const ts = a.createdAt ? new Date(a.createdAt).getTime() : 0;
      return Date.now() - ts < 16 * 60 * 1000;
    });
    if (recent.length) {
      broadcastSSE("osint-alerts", { alerts: recent.slice(-5) });
      info("OSINT tarama: yeni alarm yayını", { count: recent.length });
    }
  } catch (err) {
    error("Zamanlanmış OSINT hatası", err);
  }
});

// --- Heartbeat broadcast: her 30 saniye ---
schedule.scheduleJob("*/30 * * * * *", () => {
  const archive = readJson(ARCHIVE_FILE, []);
  const alerts = readJson(ALERT_FILE, []);
  broadcastSSE("heartbeat", {
    now: new Date().toISOString(),
    archiveCount: archive.length,
    alertCount: alerts.length
  });
});

// --- Sunucu başlatma ---
const server = app.listen(PORT, () => {
  info("Sunucu calisiyor", { url: `http://localhost:${PORT}`, pid: process.pid });
});

// --- Graceful shutdown ---
function gracefulShutdown(signal) {
  info(`${signal} alındı — sunucu kapatılıyor`);
  server.close(() => {
    info("Sunucu kapandı");
    process.exit(0);
  });
  setTimeout(() => process.exit(1), 8000);
}
process.on("SIGTERM", () => gracefulShutdown("SIGTERM"));
process.on("SIGINT", () => gracefulShutdown("SIGINT"));
