#!/usr/bin/env python3
import asyncio
import os
import sys


async def run_tts(text: str, out_path: str):
    try:
        import edge_tts
    except Exception as exc:
        raise RuntimeError("edge-tts paketi kurulu degil") from exc

    voice = os.environ.get("EDGE_TTS_VOICE", "tr-TR-AhmetNeural")
    communicate = edge_tts.Communicate(text=text, voice=voice)
    await communicate.save(out_path)


def main():
    if len(sys.argv) < 3:
        print("Kullanim: python3 tts_edge.py <metin> <cikti.mp3>", file=sys.stderr)
        sys.exit(1)

    text = sys.argv[1]
    out_path = sys.argv[2]

    try:
        asyncio.run(run_tts(text, out_path))
        print(out_path)
    except Exception as exc:
        print(f"TTS hata: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
