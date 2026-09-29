#!/usr/bin/env python3
"""
jevjoke — Joke rating & categorizing tool powered by the JEV AI model via OpenRouter.

Zero-dependency: uses only the Python standard library.

Run:
    set OPENROUTER_API_KEY=sk-or-...   (or put it in a .env file next to this script)
    py app.py                          # then open http://localhost:8787

Config via environment:
    OPENROUTER_API_KEY  (required)  OpenRouter API key
    JEV_MODEL           (optional)  default: typesafe/jev-router
    PORT                (optional)  default: 8790
"""

import json
import os
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "joke_ratings.jsonl"

STYLES = [
    "One-Liners",
    "Puns and Wordplay",
    "Dad Jokes",
    "Knock-Knock Jokes",
    "Observational Humor",
    "Riddles",
    "Dark Humor",
    "Slapstick",
    "Deadpan",
    "Anti-Jokes",
]

CATEGORIES = ["Adult", "General"]

SYSTEM_PROMPT = """You are JEV-Judge, a precise joke analysis engine.
Analyze the joke the user provides and respond with ONLY a single JSON object
(no markdown, no code fences, no extra text) with exactly these keys:

{
  "score": <integer 1-10, where 1 = painfully dry / not funny and 10 = extremely funny>,
  "category": <"Adult" or "General">,
  "foul_language": <true or false>,
  "style": <exactly one of: One-Liners | Puns and Wordplay | Dad Jokes | Knock-Knock Jokes | Observational Humor | Riddles | Dark Humor | Slapstick | Deadpan | Anti-Jokes>,
  "reason": <one short sentence justifying the score>
}

Rules:
- "Adult" means sexual, crude, or otherwise 18+ themed content; otherwise "General".
- "foul_language" is true only if the joke text itself contains profanity/swear words.
- Pick the single best-fitting style from the list, even if none is perfect."""


def load_env():
    """Populate os.environ from a local .env file (KEY=VALUE lines), without
    overriding variables that are already set."""
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def call_openrouter(joke: str) -> dict:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Export it or add it to a .env file."
        )
    model = os.environ.get("JEV_MODEL", "typesafe/jev-router")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": joke},
        ],
        "temperature": 0.2,
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/alexyapsl/jevjoke",
            "X-Title": "jevjoke",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"OpenRouter HTTP {e.code}: {detail}") from e
    latency_ms = int((time.time() - started) * 1000)

    content = (
        body.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
    ).strip()
    result = parse_result(content)
    result["_meta"] = {
        "model": model,
        "latency_ms": latency_ms,
        "usage": body.get("usage"),
        "raw_content": content,
    }
    return result


def parse_result(content: str) -> dict:
    """Extract and validate the JSON verdict from the model's reply."""
    text = content.strip()
    # Strip markdown code fences if present
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    # Find the first {...} block if extra text surrounds it
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Model did not return valid JSON. Raw reply: {content[:300]}") from e

    # Normalize / validate
    score = data.get("score")
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        raise RuntimeError(f"Model returned an invalid score: {score!r}")
    score = max(1, min(10, score))

    category = str(data.get("category", "")).strip().capitalize()
    if category not in CATEGORIES:
        category = "General"

    foul = data.get("foul_language")
    if isinstance(foul, str):
        foul = foul.strip().lower() in ("true", "yes", "1")
    foul = bool(foul)

    style = str(data.get("style", "")).strip()
    matched = next((s for s in STYLES if s.lower() == style.lower()), None)
    if not matched:
        # fuzzy: compare on alphanumerics only, allow containment either way
        # (e.g. "knock knock" -> "Knock-Knock Jokes")
        norm = re.sub(r"[^a-z0-9]", "", style.lower())
        if norm:
            matched = next(
                (
                    s
                    for s in STYLES
                    if norm in re.sub(r"[^a-z0-9]", "", s.lower())
                    or re.sub(r"[^a-z0-9]", "", s.lower()) in norm
                ),
                None,
            )
    style = matched or "Other"

    reason = str(data.get("reason", "")).strip()[:500]

    return {
        "score": score,
        "category": category,
        "foul_language": foul,
        "style": style,
        "reason": reason,
    }


def log_entry(entry: dict):
    LOG_DIR.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jevjoke &mdash; JEV Joke Rater</title>
<style>
  :root { --bg:#0f1220; --card:#1a1f35; --accent:#7c5cff; --accent2:#00d4ff;
          --text:#e8eaf6; --muted:#9aa3c7; --good:#2ecc71; --warn:#ff5c7a; }
  * { box-sizing:border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:radial-gradient(1200px 600px at 20% 0%, #232a4d 0%, var(--bg) 60%);
         font-family:"Segoe UI", system-ui, sans-serif; color:var(--text); padding:24px; }
  .card { width:100%; max-width:640px; background:var(--card); border-radius:16px;
          padding:32px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  h1 { margin:0 0 4px; font-size:28px;
       background:linear-gradient(90deg,var(--accent),var(--accent2));
       -webkit-background-clip:text; background-clip:text; color:transparent; }
  .sub { color:var(--muted); font-size:14px; margin-bottom:20px; }
  textarea { width:100%; min-height:120px; resize:vertical; border-radius:10px;
             border:1px solid #2c3454; background:#12162a; color:var(--text);
             padding:14px; font-size:15px; font-family:inherit; }
  textarea:focus { outline:2px solid var(--accent); }
  button { margin-top:14px; width:100%; padding:14px; border:0; border-radius:10px;
           background:linear-gradient(90deg,var(--accent),var(--accent2));
           color:#fff; font-size:16px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.5; cursor:wait; }
  .result { margin-top:22px; display:none; }
  .score-row { display:flex; align-items:baseline; gap:10px; }
  .score { font-size:52px; font-weight:800; }
  .score small { font-size:20px; color:var(--muted); font-weight:400; }
  .bar { flex:1; height:10px; background:#12162a; border-radius:6px; overflow:hidden; }
  .bar > div { height:100%; background:linear-gradient(90deg,var(--warn),#ffd166,var(--good));
               width:0; transition:width .6s ease; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin-top:16px; }
  .chip { background:#12162a; border:1px solid #2c3454; border-radius:999px;
          padding:6px 14px; font-size:13px; }
  .chip b { color:var(--accent2); }
  .reason { margin-top:14px; color:var(--muted); font-size:14px; font-style:italic; }
  .err { margin-top:16px; color:var(--warn); font-size:14px; display:none;
         white-space:pre-wrap; }
  .foot { margin-top:22px; color:#5b6488; font-size:12px; text-align:center; }
</style>
</head>
<body>
  <div class="card">
    <h1>😂 jevjoke</h1>
    <div class="sub">Paste a joke below. The <b>JEV</b> model (via OpenRouter) will score it
      from bone-dry (1) to hilarious (10) and categorize it.</div>
    <textarea id="joke" placeholder="Why did the chicken cross the road? ..."></textarea>
    <button id="go" onclick="rate()">Rate this joke</button>

    <div class="result" id="result">
      <div class="score-row">
        <div class="score"><span id="score">-</span><small>/10</small></div>
        <div class="bar"><div id="barfill"></div></div>
      </div>
      <div class="chips">
        <span class="chip">Category: <b id="cat"></b></span>
        <span class="chip">Foul language: <b id="foul"></b></span>
        <span class="chip">Style: <b id="style"></b></span>
      </div>
      <div class="reason" id="reason"></div>
    </div>
    <div class="err" id="err"></div>
    <div class="foot">Powered by JEV via OpenRouter &middot; every rating is logged server-side</div>
  </div>

<script>
async function rate() {
  const joke = document.getElementById('joke').value.trim();
  const btn = document.getElementById('go');
  const err = document.getElementById('err');
  err.style.display = 'none';
  if (!joke) { err.textContent = 'Type a joke first 🙂'; err.style.display = 'block'; return; }
  btn.disabled = true; btn.textContent = 'Judging…';
  try {
    const r = await fetch('/api/rate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({joke})
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));
    document.getElementById('score').textContent = data.score;
    document.getElementById('barfill').style.width = (data.score * 10) + '%';
    document.getElementById('cat').textContent = data.category;
    document.getElementById('foul').textContent = data.foul_language ? 'Yes 🤬' : 'No ✅';
    document.getElementById('style').textContent = data.style;
    document.getElementById('reason').textContent = data.reason || '';
    document.getElementById('result').style.display = 'block';
  } catch (e) {
    err.textContent = 'Error: ' + e.message;
    err.style.display = 'block';
  } finally {
    btn.disabled = false; btn.textContent = 'Rate this joke';
  }
}
document.getElementById('joke').addEventListener('keydown', e => {
  if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') rate();
});
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "jevjoke/1.0"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: dict):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def log_message(self, fmt, *args):  # quieter logs
        print(f"[{datetime.now():%H:%M:%S}] {self.address_string()} {fmt % args}")

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/healthz":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/rate":
            self._json(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, json.JSONDecodeError):
            self._json(400, {"error": "invalid JSON body"})
            return

        joke = str(payload.get("joke", "")).strip()
        if not joke:
            self._json(400, {"error": "joke is required"})
            return
        if len(joke) > 8000:
            self._json(400, {"error": "joke is too long (max 8000 chars)"})
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "input": joke,
        }
        try:
            result = call_openrouter(joke)
            meta = result.pop("_meta", {})
            entry.update({"ok": True, "output": result, "meta": meta})
            self._json(200, result)
        except Exception as e:
            entry.update({"ok": False, "error": str(e)})
            self._json(502, {"error": str(e)})
        finally:
            log_entry(entry)


def main():
    load_env()
    port = int(os.environ.get("PORT", "8790"))
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("⚠️  OPENROUTER_API_KEY is not set — ratings will fail until it is.")
    print(f"🃏 jevjoke listening on http://localhost:{port}")
    print(f"   model: {os.environ.get('JEV_MODEL', 'typesafe/jev-router')}")
    print(f"   log:   {LOG_FILE}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
