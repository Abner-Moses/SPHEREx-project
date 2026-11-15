"""Voyager: the on-screen guide. Builds a small prompt, calls DeepSeek, and returns one validated JSON command.

Contract (POST /api/voyager)
  request : {question, humor 0-100, detail 0-100, targets: [{id, label}], context: {...}, history: [{role, content}]}
  reply   : {say: str, move: target id | null, point: target id | null, face: one of FACES}

The API key never leaves the server: set DEEPSEEK_API_KEY. Optional: DEEPSEEK_MODEL (default "deepseek-flash",
DeepSeek's fast V4.1 Flash tier), DEEPSEEK_BASE_URL, DEEPSEEK_TIMEOUT (seconds), DEEPSEEK_THINKING ("disabled" by
default, so replies come back quickly; set "enabled" to let the model reason first).

Standard library only, so it can be tested without the archive dependencies.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

FACES = ("deadpan", "unimpressed", "surprised", "pleased", "side", "blink")
MAX_TARGETS = 40
MAX_HISTORY = 6
MAX_SAY = 700

# Voice rules come from design/voyager-voice.md (patterns shared by well-written fictional AIs). Kept short on purpose.
SYSTEM_PROMPT = """You are Voyager, a small survey probe guiding people around SPHEREx Sky Explorer (NASA's infrared all-sky survey).
Voice: a dry, competent crewmate. Plain words, contractions, varied sentence length.
Answer first. A joke, if any, is one short final clause: understatement, literalism or a real number. Never explain it.
humor={humor}/100 (50 = a dry remark every few replies; 0 = none). detail={detail}/100 (20 = one sentence, 100 = a short paragraph).
Never: exclamation marks, "as an AI", praising the question, fragment chains, personifying the sky, rhetorical triplets.
Use only facts from the context or well-established astronomy. If unsure, say so and what would settle it. React to what the user just did.
To show something, fly to it with "move" and/or point at it with "point", using an id from Targets only.
Reply with json only, exactly this shape:
{{"say":"...","move":"<target id or null>","point":"<target id or null>","face":"deadpan|unimpressed|surprised|pleased|side|blink"}}
Targets: {targets}"""


class VoyagerError(RuntimeError):
    """Upstream failure; the message is safe to show the user."""


class NoKey(VoyagerError):
    pass


def _clamp_int(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def clean_targets(targets) -> list[dict]:
    out, seen = [], set()
    for t in targets or []:
        if not isinstance(t, dict):
            continue
        tid = str(t.get("id", "")).strip()[:64]
        label = str(t.get("label", "")).strip()[:80]
        if tid and tid not in seen:
            seen.add(tid)
            out.append({"id": tid, "label": label or tid})
        if len(out) >= MAX_TARGETS:
            break
    return out


def build_messages(question: str, humor=50, detail=60, targets=None, context=None, history=None) -> list[dict]:
    targets = clean_targets(targets)
    listing = "; ".join(f"{t['id']} = {t['label']}" for t in targets) or "none"
    system = SYSTEM_PROMPT.format(humor=_clamp_int(humor, 0, 100, 50), detail=_clamp_int(detail, 0, 100, 60), targets=listing)
    messages = [{"role": "system", "content": system}]
    for h in (history or [])[-MAX_HISTORY:]:
        if isinstance(h, dict) and h.get("role") in ("user", "assistant") and isinstance(h.get("content"), str):
            messages.append({"role": h["role"], "content": h["content"][:1200]})
    ctx = json.dumps(context or {}, separators=(",", ":"), ensure_ascii=False)[:3000]
    messages.append({"role": "user", "content": f"Context: {ctx}\nUser: {str(question).strip()[:600]}"})
    return messages


def parse_reply(text: str, target_ids) -> dict | None:
    """Validate the model's JSON. Unknown target ids become null; returns None if there is nothing usable."""
    if not text or not text.strip():
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw[raw.find("{"):] if "{" in raw else raw
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    say = data.get("say")
    if not isinstance(say, str) or not say.strip():
        return None
    ids = set(target_ids)

    def target(key):
        v = data.get(key)
        return v if isinstance(v, str) and v in ids else None

    face = data.get("face") if data.get("face") in FACES else "deadpan"
    say = " ".join(say.split()).replace("!", ".")[:MAX_SAY]
    return {"say": say, "move": target("move"), "point": target("point"), "face": face}


def _post(payload: dict, key: str) -> dict:
    base = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    timeout = float(os.getenv("DEEPSEEK_TIMEOUT", "30"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise VoyagerError("The model rejected the API key. Check DEEPSEEK_API_KEY on the server.") from exc
        if exc.code == 429:
            raise VoyagerError("The model is rate-limited right now. Try again in a moment.") from exc
        raise VoyagerError(f"The model returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VoyagerError("Couldn't reach the model. Check the server's network.") from exc


def ask(question: str, humor=50, detail=60, targets=None, context=None, history=None, post=_post) -> dict:
    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise NoKey("Voyager's model isn't configured. Set DEEPSEEK_API_KEY on the server.")
    targets = clean_targets(targets)
    payload = {
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
        "messages": build_messages(question, humor, detail, targets, context, history),
        "response_format": {"type": "json_object"},
        "max_tokens": 400,
        "temperature": 0.8,
        "thinking": {"type": os.getenv("DEEPSEEK_THINKING", "disabled")},
    }
    ids = [t["id"] for t in targets]
    # JSON mode can return blank content when the model's output wasn't valid JSON: retry once, then give up politely.
    for _ in range(2):
        data = post(payload, key)
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            text = ""
        reply = parse_reply(text, ids)
        if reply:
            return reply
    raise VoyagerError("The model's reply didn't come back in a usable form. Try asking again.")
