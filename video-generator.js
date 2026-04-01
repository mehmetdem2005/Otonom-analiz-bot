const ffmpeg = require("fluent-ffmpeg");
const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");
const ffmpegStatic = require("ffmpeg-static");

function resolveFfmpegPath() {
  if (process.env.FFMPEG_PATH) return process.env.FFMPEG_PATH;
  if (ffmpegStatic) return ffmpegStatic;
  const probe = spawnSync("which", ["ffmpeg"], { encoding: "utf-8" });
  if (probe.status === 0 && probe.stdout.trim()) return probe.stdout.trim();
  return null;
}

const ffmpegPath = resolveFfmpegPath();
if (ffmpegPath) {
  ffmpeg.setFfmpegPath(ffmpegPath);
}
const FFMPEG_READY = Boolean(ffmpegPath);

function ensureTemp() {
  const dir = path.join(__dirname, "temp");
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
  return dir;
}

function formatSrtTime(totalSeconds) {
  const clamped = Math.max(0, totalSeconds);
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  const seconds = Math.floor(clamped % 60);
  const millis = Math.round((clamped - Math.floor(clamped)) * 1000);
  const pad = (value, size = 2) => String(value).padStart(size, "0");
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)},${pad(millis, 3)}`;
}

function splitSubtitleSegments(text, limit = 6) {
  const safeText = String(text || "")
    .slice(0, 1400)
    .replace(/\s+/g, " ")
    .trim();

  if (!safeText) return ["Otonom Basyazar"];

  const sentences = safeText.split(/(?<=[.!?])\s+/).filter(Boolean);
  if (sentences.length <= limit) return sentences;

  const chunkSize = Math.ceil(sentences.length / limit);
  const segments = [];
  for (let index = 0; index < sentences.length; index += chunkSize) {
    segments.push(sentences.slice(index, index + chunkSize).join(" "));
  }
  return segments;
}

function createSubtitleSrt(text, outFile, totalDuration = 12) {
  const segments = splitSubtitleSegments(text, 6);
  const weights = segments.map((segment) => Math.max(1, segment.length));
  const totalWeight = weights.reduce((sum, value) => sum + value, 0);
  let cursor = 0;

  const srt = segments.map((segment, index) => {
    const share = weights[index] / totalWeight;
    const duration = Math.max(1.2, totalDuration * share);
    const start = cursor;
    const end = index === segments.length - 1 ? totalDuration : Math.min(totalDuration, cursor + duration);
    cursor = end;
    return [
      String(index + 1),
      `${formatSrtTime(start)} --> ${formatSrtTime(end)}`,
      segment,
      ""
    ].join("\n");
  }).join("\n");

  fs.writeFileSync(outFile, srt, "utf-8");
}

function createFallbackPpm(outFile, width = 1280, height = 720, rgb = [15, 23, 42]) {
  const header = `P6\n${width} ${height}\n255\n`;
  const pixelCount = width * height;
  const body = Buffer.alloc(pixelCount * 3);
  for (let i = 0; i < pixelCount; i += 1) {
    const idx = i * 3;
    body[idx] = rgb[0];
    body[idx + 1] = rgb[1];
    body[idx + 2] = rgb[2];
  }
  fs.writeFileSync(outFile, Buffer.concat([Buffer.from(header, "ascii"), body]));
}

function probeAudioDuration(audioPath) {
  return new Promise((resolve) => {
    ffmpeg.ffprobe(audioPath, (err, metadata) => {
      if (err) return resolve(12);
      const duration = Number(metadata?.format?.duration || 12);
      resolve(Number.isFinite(duration) && duration > 0 ? duration : 12);
    });
  });
}

async function generateVideo({ imagePath, audioPath, title, subtitleText }) {
  if (!FFMPEG_READY) {
    throw new Error("ffmpeg bulunamadi. PATH veya FFMPEG_PATH ayari gerekli");
  }

  const tempDir = ensureTemp();
  const outFile = path.join(tempDir, `video_${Date.now()}.mp4`);
  const srtPath = path.join(tempDir, `subtitle_${Date.now()}.srt`);
  const fallbackImagePath = path.join(tempDir, `fallback_${Date.now()}.ppm`);
  const audioDuration = await probeAudioDuration(audioPath);
  createSubtitleSrt(subtitleText || title, srtPath, Math.max(6, audioDuration));

  let chosenImage = imagePath;
  if (!chosenImage || !fs.existsSync(chosenImage)) {
    createFallbackPpm(fallbackImagePath, 1280, 720, [15, 23, 42]);
    chosenImage = fallbackImagePath;
  }

  return new Promise((resolve, reject) => {
    ffmpeg()
      .addInput(chosenImage)
      .loop(12)
      .addInput(audioPath)
      .videoCodec("libx264")
      .audioCodec("aac")
      .outputOptions([
        "-shortest",
        "-pix_fmt yuv420p",
        `-vf subtitles=${srtPath}`
      ])
      .on("end", () => resolve(outFile))
      .on("error", (err) => reject(err))
      .save(outFile);
  });
}

module.exports = { generateVideo };
