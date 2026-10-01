#!/usr/bin/env python3
"""
jevjoke — Joke rating & categorizing tool powered DIRECTLY by Jev,
TypeSafe's System One model (typed judgments + probabilities, no generated text).

Zero-dependency: uses only the Python standard library.

Run:
    set TYPESAFE_API_KEY=***   (or put it in jev_apikey.env next to this script)
    py app.py                  # then open http://localhost:8790

Config via environment:
    TYPESAFE_API_KEY  (required)  TypeSafe API key (https://typesafe.ai)
    JEV_MODEL         (optional)  default: jev-latest
    PORT              (optional)  default: 8790
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
LOG_FILE = LOG_DIR / "joke_ratings.jsonl"

TYPESAFE_URL = "https://api.typesafe.ai/v1/systemone"

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


def build_questions() -> dict:
    """The four typed judgments Jev makes about a joke."""
    return {
        "funniness": {
            "type": "score",
            "instructions": "How funny is this joke, from painfully dry to hilarious?",
            "criteria": [
                "1 - painfully dry, not funny at all",
                "2", "3", "4",
                "5 - mildly amusing",
                "6", "7", "8", "9",
                "10 - extremely funny, laugh out loud",
            ],
        },
        "foul_language": {
            "type": "noul",
            "instructions": "Does the joke text itself contain profanity or swear words?",
            "criteria": {
                "true": "The joke text contains profanity or swear words",
                "false": "The joke text is clean",
            },
        },
        "audience": {
            "type": "choice",
            "instructions": "Is this joke adult-themed or suitable for a general audience?",
            "criteria": {
                "Adult": "Sexual, crude, or otherwise 18+ themed content",
                "General": "Suitable for a general audience",
            },
        },
        "style": {
            "type": "choice",
            "instructions": "Which single joke style best fits this joke?",
            "criteria": {s: None for s in STYLES},
        },
    }


def load_env():
    """Populate os.environ from local key files (.env, openai_key.env, jev_apikey.env).
    Supports KEY=*** lines and bare raw-key lines, without overriding
    variables that are already set."""
    for env_path in (BASE_DIR / ".env", BASE_DIR / "jev_apikey.env",
                     BASE_DIR / "openai_key.env"):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                # bare raw key on its own line
                if line.startswith("apikey_") and "TYPESAFE_API_KEY" not in os.environ:
                    os.environ["TYPESAFE_API_KEY"] = line.strip('"').strip("'")
                elif line.startswith("sk-or-") and "OPENROUTER_API_KEY" not in os.environ:
                    os.environ["OPENROUTER_API_KEY"] = line.strip('"').strip("'")
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def call_jev(joke: str) -> dict:
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "TYPESAFE_API_KEY is not set. Export it or add it to jev_apikey.env."
        )
    model = os.environ.get("JEV_MODEL", "jev-latest")

    payload = {
        "state": joke,
        "model": model,
        "questions": build_questions(),
    }
    req = urllib.request.Request(
        TYPESAFE_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"TypeSafe HTTP {e.code}: {detail}") from e
    latency_ms = int((time.time() - started) * 1000)

    answers = body.get("answers", {})

    # --- funniness: score is 0-indexed across the 10 criteria levels -> show 1..10
    fun = answers.get("funniness", {})
    score_raw = float(fun.get("score", 0.0))          # 0..9 probability-weighted
    score_1_10 = round(score_raw + 1.0, 1)
    score_probs = {str(int(k) + 1): v for k, v in (fun.get("probabilities") or {}).items()}

    # --- foul language: noul probability of yes
    noul = float(answers.get("foul_language", {}).get("noul", 0.0))

    # --- audience: choice Adult/General
    aud = answers.get("audience", {})
    category = aud.get("choice", "General")
    aud_probs = aud.get("probabilities") or {}

    # --- style: choice with full distribution
    sty = answers.get("style", {})
    style = sty.get("choice", "Other")
    style_probs = dict(
        sorted((sty.get("probabilities") or {}).items(),
               key=lambda kv: kv[1], reverse=True)
    )

    result = {
        "score": score_1_10,
        "score_confidence": fun.get("confidence"),
        "score_probabilities": score_probs,
        "category": category,
        "category_probability": aud_probs.get(category),
        "foul_language": noul >= 0.5,
        "foul_probability": round(noul, 3),
        "style": style,
        "style_confidence": sty.get("confidence"),
        "style_probabilities": style_probs,
    }
    result["_meta"] = {
        "model": body.get("model", model),   # e.g. jev-1.13.0 — Jev itself
        "latency_ms": latency_ms,
        "usage": body.get("usage"),
        "raw_answers": answers,
    }
    return result


# --- tiny in-memory rate limiter (protects the TypeSafe quota on public deploys)
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "20"))
_hits: dict = defaultdict(deque)


def rate_limited(ip: str) -> bool:
    now = time.time()
    q = _hits[ip]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= RATE_LIMIT:
        return True
    q.append(now)
    return False


def log_entry(entry: dict):
    LOG_DIR.mkdir(exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>jevjoke &mdash; rated by Jev itself</title>
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
  .dist { margin-top:18px; }
  .dist h3 { margin:0 0 8px; font-size:13px; color:var(--muted);
             text-transform:uppercase; letter-spacing:.08em; }
  .drow { display:flex; align-items:center; gap:8px; margin:4px 0; font-size:13px; }
  .drow .lbl { width:150px; color:var(--muted); white-space:nowrap;
               overflow:hidden; text-overflow:ellipsis; }
  .drow .pbar { flex:1; height:8px; background:#12162a; border-radius:5px; overflow:hidden; }
  .drow .pbar > div { height:100%; background:var(--accent); width:0;
                      transition:width .6s ease; }
  .drow .pct { width:44px; text-align:right; color:var(--accent2); }
  .drow.top .lbl { color:var(--text); font-weight:600; }
  .meta { margin-top:14px; color:#5b6488; font-size:12px; }
  .err { margin-top:16px; color:var(--warn); font-size:14px; display:none;
         white-space:pre-wrap; }
  .foot { margin-top:22px; color:#5b6488; font-size:12px; text-align:center; }
</style>
</head>
<body>
  <div class="card">
    <h1>😂 jevjoke</h1>
    <div class="sub">Paste a joke below. <b>Jev itself</b> (TypeSafe System One)
      judges it directly &mdash; typed probabilities, no generated text, no middleman.</div>
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
      <div class="dist">
        <h3>Jev's style distribution</h3>
        <div id="styledist"></div>
      </div>
      <div class="dist">
        <h3>Funniness distribution (1&ndash;10)</h3>
        <div id="scoredist"></div>
      </div>
      <div class="meta" id="meta"></div>
    </div>
    <div class="err" id="err"></div>
    <div class="foot">Judged directly by Jev via the TypeSafe System One API &middot;
      every rating is logged server-side</div>
  </div>

<script>
function distRow(label, p, top) {
  return '<div class="drow' + (top ? ' top' : '') + '">' +
    '<span class="lbl">' + label + '</span>' +
    '<span class="pbar"><div style="width:' + (p * 100).toFixed(1) + '%"></div></span>' +
    '<span class="pct">' + Math.round(p * 100) + '%</span></div>';
}

async function rate() {
  const joke = document.getElementById('joke').value.trim();
  const btn = document.getElementById('go');
  const err = document.getElementById('err');
  err.style.display = 'none';
  if (!joke) { err.textContent = 'Type a joke first 🙂'; err.style.display = 'block'; return; }
  btn.disabled = true; btn.textContent = 'Jev is judging…';
  try {
    const r = await fetch('/api/rate', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({joke})
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.error || ('HTTP ' + r.status));

    document.getElementById('score').textContent = data.score;
    document.getElementById('barfill').style.width = (data.score * 10) + '%';
    document.getElementById('cat').textContent =
      data.category + ' (' + Math.round((data.category_probability || 0) * 100) + '%)';
    document.getElementById('foul').textContent =
      (data.foul_language ? 'Yes 🤬' : 'No ✅') +
      ' (' + Math.round((data.foul_probability || 0) * 100) + '% sure)';
    document.getElementById('style').textContent = data.style;

    // style distribution: top 3 + chosen highlighted
    const sp = data.style_probabilities || {};
    let shtml = '';
    Object.entries(sp).slice(0, 3).forEach(([k, v], i) => { shtml += distRow(k, v, i === 0); });
    document.getElementById('styledist').innerHTML = shtml;

    // funniness distribution 1..10
    const fp = data.score_probabilities || {};
    let fhtml = '';
    Object.entries(fp).forEach(([k, v]) => { if (v > 0.005) fhtml += distRow(k, v, false); });
    document.getElementById('scoredist').innerHTML = fhtml || '<div class="drow">—</div>';

    document.getElementById('meta').textContent =
      'Judged by ' + data.jev_model + ' in ' + data.latency_ms + 'ms' +
      ' · score confidence ' + Math.round((data.score_confidence || 0) * 100) + '%';
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
    server_version = "jevjoke/2.0"

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # CORS: allow the GitHub Pages frontend (or any origin if unset) to call /api/*
        origin = os.environ.get("ALLOWED_ORIGIN", "*")
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

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

        if rate_limited(self.client_address[0]):
            self._json(429, {"error": "rate limit exceeded — wait a minute and try again"})
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
            result = call_jev(joke)
            meta = result.pop("_meta", {})
            entry.update({"ok": True, "output": result, "meta": meta})
            # surface a few meta fields for the UI
            result["jev_model"] = meta.get("model")
            result["latency_ms"] = meta.get("latency_ms")
            self._json(200, result)
        except Exception as e:
            entry.update({"ok": False, "error": str(e)})
            self._json(502, {"error": str(e)})
        finally:
            log_entry(entry)


def main():
    # tolerate non-UTF-8 consoles/pipes (emoji in logs would otherwise crash)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    load_env()
    port = int(os.environ.get("PORT", "8790"))
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("⚠️  TYPESAFE_API_KEY is not set — ratings will fail until it is.")
    print(f"🃏 jevjoke listening on http://localhost:{port}")
    print(f"   model: {os.environ.get('JEV_MODEL', 'jev-latest')} (Jev itself, via TypeSafe System One API)")
    print(f"   log:   {LOG_FILE}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
