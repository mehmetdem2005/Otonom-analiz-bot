#!/usr/bin/env python3
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    import requests
except Exception:
    requests = None

SERPER_URL = "https://google.serper.dev/news"


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def summarize(items):
    lines = []
    for i, item in enumerate(items[:7], start=1):
        title = clean_text(item.get("title", "Baslik yok"))
        source = clean_text(item.get("source", "Bilinmiyor"))
        date = clean_text(item.get("date", ""))
        lines.append(f"{i}. {title} ({source}) {date}")
    return "\n".join(lines)


def fetch_news(query: str, api_key: str):
    if not requests:
        return []
    payload = {"q": query, "gl": "tr", "hl": "tr"}
    headers = {"X-API-KEY": api_key, "Content-Type": "application/json"}
    response = requests.post(SERPER_URL, headers=headers, json=payload, timeout=20)
    response.raise_for_status()
    data = response.json()
    return data.get("news", [])


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "turkiye gundem son 24 saat"
    api_key = os.environ.get("SERPER_API_KEY", "")

    output = {
        "query": query,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "items": [],
        "fusion": ""
    }

    if not api_key:
        output["fusion"] = "SERPER_API_KEY tanimli degil. Canli haber yerine yalnizca yerel uretim kullanilacak."
        print(json.dumps(output, ensure_ascii=False))
        return

    try:
        items = fetch_news(query, api_key)
        output["items"] = items[:10]
        output["fusion"] = summarize(items)
    except Exception as exc:
        output["fusion"] = f"Canli haber toplanamadi: {exc}"

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()
