# jevjoke 😂

A joke **rating and categorizing tool** powered **directly by Jev** — TypeSafe's
flagship System One model — via the [TypeSafe System One API](https://docs.typesafe.ai).

No generated text, no routing to other models: Jev returns **typed judgments with
probabilities**, which is exactly what this page shows. Built to test how powerful
Jev itself is.

## What it does

Paste any joke, hit **Rate this joke**, and Jev returns four typed judgments:

- **Score** — a `score` question: probability-weighted funniness from 1 (bone dry)
  to 10 (very funny), with the full 1–10 probability distribution
- **Category** — a `choice` question: `Adult` or `General`, with probabilities
- **Foul language** — a `noul` question: probability the joke text contains profanity
- **Style** — a `choice` question over:
  One-Liners · Puns and Wordplay · Dad Jokes · Knock-Knock Jokes ·
  Observational Humor · Riddles · Dark Humor · Slapstick · Deadpan · Anti-Jokes —
  with the full probability distribution

Every input + raw answer is logged to `logs/joke_ratings.jsonl`.

> Note: Jev does not generate text, so there is deliberately **no written "reason"**.
> (The v1 of this app went through OpenRouter's `typesafe/jev-router`, which routes
> to an upstream text model — that text was never Jev's. See git history.)

## Run it

Zero dependencies — just Python 3.9+.

```powershell
# 1. set your TypeSafe API key (either works):
$env:TYPESAFE_API_KEY = "apikey_..."
#    …or create jev_apikey.env with the raw key on one line

# 2. start the server
py app.py
```

Open <http://localhost:8790>.

### Config

| Env var            | Default      | Purpose               |
| ------------------ | ------------ | --------------------- |
| `TYPESAFE_API_KEY` | — (required) | TypeSafe API key      |
| `JEV_MODEL`        | `jev-latest` | TypeSafe model alias  |
| `PORT`             | `8790`       | HTTP port             |

## API

`POST /api/rate` with `{"joke": "..."}` →

```json
{
  "score": 5.2,
  "score_confidence": 0.55,
  "score_probabilities": {"1": 0.01, "2": 0.07, "...": "..."},
  "category": "General",
  "category_probability": 0.66,
  "foul_language": false,
  "foul_probability": 0.07,
  "style": "Deadpan",
  "style_probabilities": {"Deadpan": 0.55, "One-Liners": 0.13, "...": "..."},
  "jev_model": "jev-1.13.0",
  "latency_ms": 296
}
```

## Files

- `app.py` — the whole app (stdlib HTTP server + embedded frontend)
- `logs/joke_ratings.jsonl` — append-only rating log (gitignored)
