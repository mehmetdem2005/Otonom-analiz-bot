const { error } = require("../utils/logger");

const DEFAULT_MODELS = [
  process.env.GROQ_MODEL,
  "openai/gpt-oss-120b",
  "llama-3.3-70b-versatile",
  "llama-3.1-70b-versatile"
].filter(Boolean);

async function callGroq(messages, temperature = 0.7, preferredModels = DEFAULT_MODELS) {
  if (!process.env.GROQ_API_KEY) {
    throw new Error("GROQ_API_KEY tanimli degil");
  }

  let lastErr;
  for (const model of preferredModels) {
    try {
      const response = await fetch("https://api.groq.com/openai/v1/chat/completions", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${process.env.GROQ_API_KEY}`
        },
        body: JSON.stringify({ model, temperature, messages })
      });

      if (!response.ok) {
        const body = await response.text();
        throw new Error(`Groq model=${model} status=${response.status} body=${body}`);
      }

      const data = await response.json();
      const content = data.choices?.[0]?.message?.content?.trim();
      if (!content) throw new Error(`Groq bos yanit model=${model}`);
      return { content, model };
    } catch (err) {
      lastErr = err;
      error("Groq model denemesi basarisiz", err, { model });
    }
  }

  throw lastErr || new Error("Groq cagri hatasi");
}

module.exports = { callGroq, DEFAULT_MODELS };
