# jevjoke 😂

A joke **rating and categorizing tool** built to test how powerful the **JEV AI model** is —
connected through [OpenRouter](https://openrouter.ai).

## What it does

Paste any joke into the page, hit **Rate this joke**, and JEV returns:

- **Score** — 1 (bone dry) → 10 (very funny)
- **Category** — `Adult` or `General`
- **Foul language** — whether the joke text contains profanity
- **Style** — one of:
  One-Liners · Puns and Wordplay · Dad Jokes · Knock-Knock Jokes ·
  Observational Humor · Riddles · Dark Humor · Slapstick · Deadpan · Anti-Jokes

Every input + output is logged to `logs/joke_ratings.jsonl` (one JSON object per line).

## Run it

Zero dependencies — just Python 3.9+.

```powershell
# 1. set your OpenRouter key (either works):
$env:OPENROUTER_API_KEY = "sk-or-..."
#    …or create a .env file with:  OPENROUTER_API_KEY=sk-or-...

# 2. start the server
py app.py
```

Open <http://localhost:8790>.

### Config

| Env var              | Default               | Purpose                    |
| -------------------- | --------------------- | -------------------------- |
| `OPENROUTER_API_KEY` | — (required)          | OpenRouter API key         |
| `JEV_MODEL`          | `typesafe/jev-router` | OpenRouter model ID        |
| `PORT`               | `8790`                | HTTP port                  |

## Files

- `app.py` — the whole app (stdlib HTTP server + embedded frontend)
- `logs/joke_ratings.jsonl` — append-only rating log (gitignored)

## Log format

```json
{
  "ts": "2026-09-29T14:00:00.000000+00:00",
  "input": "Why did the chicken cross the road? ...",
  "ok": true,
  "output": {"score": 6, "category": "General", "foul_language": false, "style": "Dad Jokes", "reason": "..."},
  "meta": {"model": "typesafe/jev-router", "latency_ms": 1234, "usage": {...}}
}
```
