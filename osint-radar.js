const fs = require("fs");
const path = require("path");
require("dotenv").config();

const ALERT_FILE = path.join(__dirname, "alerts.json");
const STATE_FILE = path.join(__dirname, "temp", "osint_state.json");

const MILITARY_CALLSIGN_PATTERNS = [
  /^RCH/i,
  /^SAM/i,
  /^CFC/i,
  /^NATO/i,
  /^BAF/i,
  /^QID/i,
  /^TUAF/i,
  /^FORTE/i,
  /^KING/i,
  /^DUKE/i
];

function loadAlerts() {
  try {
    return JSON.parse(fs.readFileSync(ALERT_FILE, "utf-8"));
  } catch {
    return [];
  }
}

function saveAlerts(alerts) {
  fs.writeFileSync(ALERT_FILE, JSON.stringify(alerts.slice(-200), null, 2), "utf-8");
}

function loadState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf-8"));
  } catch {
    return { recentVelocities: [], recentVerticalRates: [], whalePolls: 0 };
  }
}

function saveState(state) {
  const dir = path.dirname(STATE_FILE);
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf-8");
}

function percentile(values, p) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const idx = Math.min(sorted.length - 1, Math.max(0, Math.floor((p / 100) * sorted.length)));
  return sorted[idx];
}

function isMilitaryCallsign(callsign) {
  const c = String(callsign || "").trim();
  return MILITARY_CALLSIGN_PATTERNS.some((re) => re.test(c));
}

async function fetchOpenSky() {
  const res = await fetch("https://opensky-network.org/api/states/all");
  if (!res.ok) throw new Error(`OpenSky error: ${res.status}`);
  const data = await res.json();
  const states = data.states || [];

  const cleaned = states
    .map((s) => ({
      callsign: (s[1] || "").trim(),
      velocity: Number(s[9]) || 0,
      verticalRate: Math.abs(Number(s[11]) || 0),
      onGround: Boolean(s[8]),
      originCountry: s[2] || "unknown"
    }))
    .filter((s) => !s.onGround && s.velocity > 0);

  const velocities = cleaned.map((s) => s.velocity);
  const verticalRates = cleaned.map((s) => s.verticalRate);

  const speedDynamicThreshold = Math.max(250, percentile(velocities, 92));
  const verticalRateThreshold = Math.max(8, percentile(verticalRates, 90));

  const highVelocityFlights = cleaned.filter((s) => s.velocity >= speedDynamicThreshold);
  const highVerticalFlights = cleaned.filter((s) => s.verticalRate >= verticalRateThreshold);
  const militaryFlights = cleaned.filter((s) => isMilitaryCallsign(s.callsign));

  return {
    flights: states.length,
    speedDynamicThreshold,
    verticalRateThreshold,
    highVelocityCount: highVelocityFlights.length,
    highVerticalCount: highVerticalFlights.length,
    militaryCount: militaryFlights.length,
    sampleMilitary: militaryFlights.slice(0, 5).map((s) => `${s.callsign || "UNKNOWN"}/${s.originCountry}`),
    velocitySeries: velocities.slice(0, 200),
    verticalRateSeries: verticalRates.slice(0, 200)
  };
}

async function fetchWhale() {
  const key = process.env.WHALE_ALERT_API_KEY;
  if (!key) return { skipped: true };
  const res = await fetch(`https://api.whale-alert.io/v1/transactions?api_key=${key}&min_value=1000000&limit=10`);
  if (!res.ok) throw new Error(`WhaleAlert error: ${res.status}`);
  const data = await res.json();
  return { txCount: (data.transactions || []).length };
}

async function runRadar() {
  const alerts = loadAlerts();
  const state = loadState();
  const now = new Date().toISOString();

  try {
    const openSky = await fetchOpenSky();
    state.recentVelocities = [...(state.recentVelocities || []), ...openSky.velocitySeries].slice(-2000);
    state.recentVerticalRates = [...(state.recentVerticalRates || []), ...openSky.verticalRateSeries].slice(-2000);

    if (openSky.highVelocityCount > 20) {
      alerts.push({
        id: `os-${Date.now()}`,
        source: "opensky",
        level: "high",
        message: `Yuksek hizli ucus anomali sayisi: ${openSky.highVelocityCount} (esik: ${openSky.speedDynamicThreshold.toFixed(1)} m/s)`,
        meta: { threshold: openSky.speedDynamicThreshold },
        createdAt: now
      });
    }

    if (openSky.highVerticalCount > 15) {
      alerts.push({
        id: `ov-${Date.now()}`,
        source: "opensky",
        level: "medium",
        message: `Yuksek tirmanis/alcalis anomali sayisi: ${openSky.highVerticalCount} (esik: ${openSky.verticalRateThreshold.toFixed(1)} m/s)`,
        meta: { threshold: openSky.verticalRateThreshold },
        createdAt: now
      });
    }

    if (openSky.militaryCount >= 3) {
      alerts.push({
        id: `om-${Date.now()}`,
        source: "opensky",
        level: "medium",
        message: `Askeri/supheli cagri isareti tespiti: ${openSky.militaryCount}`,
        meta: { sample: openSky.sampleMilitary },
        createdAt: now
      });
    }

    // Key yoksa whale istegini tamamen pas gec; key varsa da 2 turda bir cagir.
    if (process.env.WHALE_ALERT_API_KEY) {
      state.whalePolls = Number(state.whalePolls || 0) + 1;
      if (state.whalePolls % 2 === 0) {
        const whale = await fetchWhale();
        if (!whale.skipped && whale.txCount > 5) {
          alerts.push({
            id: `wh-${Date.now()}`,
            source: "whale-alert",
            level: "medium",
            message: `Son pencerede buyuk kripto transferi: ${whale.txCount}`,
            createdAt: now
          });
        }
      }
    }

    saveAlerts(alerts);
    saveState(state);
    console.log(`[OSINT] tarama tamamlandi ${now}`);
  } catch (err) {
    console.error("[OSINT] hata:", err.message);
  }
}

if (require.main === module) {
  runRadar();
  setInterval(runRadar, 15 * 60 * 1000);
}

module.exports = { runRadar };
