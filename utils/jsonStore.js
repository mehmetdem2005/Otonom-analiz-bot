const fs = require("fs");

function readJson(file, fallback) {
  try {
    return JSON.parse(fs.readFileSync(file, "utf-8"));
  } catch {
    return fallback;
  }
}

function writeJson(file, data) {
  try {
    fs.writeFileSync(file, JSON.stringify(data, null, 2), "utf-8");
  } catch (err) {
    console.error(`[writeJson] ${file} yazma hatas\u0131: ${err.message}`);
    throw err;
  }
}

module.exports = { readJson, writeJson };
