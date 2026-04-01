const crypto = require("crypto");

function percentEncode(value) {
  return encodeURIComponent(String(value || ""))
    .replace(/[!'()*]/g, (char) => `%${char.charCodeAt(0).toString(16).toUpperCase()}`);
}

function normalizeWords(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^a-z0-9ğüşiöçı\s]/gi, " ")
    .split(/\s+/)
    .filter((word) => word.length > 2);
}

function wordVector(text) {
  const vector = new Map();
  for (const word of normalizeWords(text)) {
    vector.set(word, (vector.get(word) || 0) + 1);
  }
  return vector;
}

function cosineSimilarity(aText, bText) {
  const a = wordVector(aText);
  const b = wordVector(bText);
  const all = new Set([...a.keys(), ...b.keys()]);
  let dot = 0;
  let aNorm = 0;
  let bNorm = 0;
  for (const key of all) {
    const av = a.get(key) || 0;
    const bv = b.get(key) || 0;
    dot += av * bv;
    aNorm += av * av;
    bNorm += bv * bv;
  }
  if (!aNorm || !bNorm) return 0;
  return dot / (Math.sqrt(aNorm) * Math.sqrt(bNorm));
}

function findSimilarTweets(article, tweets, limit = 5) {
  return (tweets || [])
    .map((tweet) => ({ ...tweet, similarity: cosineSimilarity(article, tweet.text) }))
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}

function buildVoiceProfile(article, tweets) {
  const matches = findSimilarTweets(article, tweets, 5);
  return {
    matches,
    dominantTone: matches[0]?.tone || "analitik",
    motifs: [...new Set(matches.flatMap((item) => item.motifs || []))].slice(0, 6)
  };
}

function chunkForXWithVoice(article, chunker, voiceProfile) {
  const chunks = chunker(article, 250);
  return chunks.map((chunk, index) => {
    const motif = voiceProfile?.motifs?.[index % Math.max(1, voiceProfile?.motifs?.length || 1)] || "kritik sinyal";
    return `${chunk}\n\nOdak: ${motif}`;
  });
}

function oauthHeader(method, url, credentials, extraParams = {}) {
  const oauthParams = {
    oauth_consumer_key: credentials.apiKey,
    oauth_nonce: crypto.randomBytes(16).toString("hex"),
    oauth_signature_method: "HMAC-SHA1",
    oauth_timestamp: String(Math.floor(Date.now() / 1000)),
    oauth_token: credentials.accessToken,
    oauth_version: "1.0"
  };

  const params = { ...oauthParams, ...extraParams };
  const normalized = Object.keys(params)
    .sort()
    .map((key) => `${percentEncode(key)}=${percentEncode(params[key])}`)
    .join("&");

  const baseString = [method.toUpperCase(), percentEncode(url), percentEncode(normalized)].join("&");
  const signingKey = `${percentEncode(credentials.apiSecret)}&${percentEncode(credentials.accessSecret)}`;
  const signature = crypto.createHmac("sha1", signingKey).update(baseString).digest("base64");

  const headerParams = { ...oauthParams, oauth_signature: signature };
  return `OAuth ${Object.keys(headerParams)
    .sort()
    .map((key) => `${percentEncode(key)}="${percentEncode(headerParams[key])}"`)
    .join(", ")}`;
}

async function publishThreadToX(chunks, credentials) {
  if (!credentials?.apiKey || !credentials?.apiSecret || !credentials?.accessToken || !credentials?.accessSecret) {
    return { simulated: true, published: 0, reason: "X kimlik bilgileri eksik" };
  }

  const endpoint = "https://api.twitter.com/2/tweets";
  let replyTo = null;
  const tweetIds = [];

  for (const chunk of chunks) {
    const body = replyTo ? { text: chunk, reply: { in_reply_to_tweet_id: replyTo } } : { text: chunk };
    const auth = oauthHeader("POST", endpoint, credentials);
    const response = await fetch(endpoint, {
      method: "POST",
      headers: {
        Authorization: auth,
        "Content-Type": "application/json"
      },
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const detail = await response.text();
      return { simulated: false, published: tweetIds.length, error: `X status=${response.status}`, detail };
    }

    const data = await response.json();
    replyTo = data?.data?.id || null;
    if (replyTo) tweetIds.push(replyTo);
  }

  return { simulated: false, published: tweetIds.length, tweetIds };
}

module.exports = {
  buildVoiceProfile,
  findSimilarTweets,
  chunkForXWithVoice,
  publishThreadToX
};