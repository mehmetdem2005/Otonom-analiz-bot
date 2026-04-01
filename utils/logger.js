function ts() {
  return new Date().toISOString();
}

function info(msg, meta = {}) {
  const body = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : "";
  console.log(`[${ts()}] INFO ${msg}${body}`);
}

function error(msg, err, meta = {}) {
  const detail = err && err.stack ? err.stack : err && err.message ? err.message : String(err || "unknown");
  const body = Object.keys(meta).length ? ` ${JSON.stringify(meta)}` : "";
  console.error(`[${ts()}] ERROR ${msg}: ${detail}${body}`);
}

module.exports = { info, error };
