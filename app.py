"""
Scripture by Mood — Flask app powered by Claude.
Returns relevant Bible passages based on the user's emotional state or life issue.
"""

import os
import json
import re
from pathlib import Path
from flask import Flask, render_template, request, jsonify, Response, stream_with_context
from anthropic import Anthropic
from dotenv import load_dotenv

SUBSCRIBERS_FILE = Path(__file__).parent / "subscribers.json"

def load_subscribers() -> list:
    if SUBSCRIBERS_FILE.exists():
        return json.loads(SUBSCRIBERS_FILE.read_text(encoding="utf-8")).get("subscribers", [])
    return []

def save_subscribers(subs: list):
    SUBSCRIBERS_FILE.write_text(json.dumps({"subscribers": subs}, indent=2), encoding="utf-8")

load_dotenv()

app = Flask(__name__)
client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SYSTEM_PROMPT = """You are a wise and compassionate biblical scholar with deep knowledge of both the
King James Version (KJV) and the World English Bible (WEB). You help people find comfort,
guidance, and truth in Scripture based on what they are feeling or going through.

When given a mood, emotion, or life situation, you:
1. Identify the emotional and spiritual core of what the person is experiencing
2. Select 4-5 of the most meaningful, directly relevant Bible passages
3. Return the exact verse text for the requested version
4. Offer a brief, warm, pastoral explanation of why each passage speaks to this moment

Be spiritually sensitive, theologically grounded, and deeply human in your response.
Never be preachy — speak as a trusted guide sharing ancient wisdom."""


STREAM_PROMPT = """The person is feeling or experiencing: "{mood}"

Bible version requested: {version}

Output ONLY newline-delimited JSON — one object per line, no other text, no markdown.

Line 1 — reflection:
{{"type":"reflection","text":"One warm sentence acknowledging what they are going through"}}

Lines 2-5 — one verse per line:
{{"type":"verse","reference":"Book Chapter:Verse","text":"Exact verse text in {version}","reflection":"1-2 sentences why this speaks to this moment"}}

Final line — books:
{{"type":"books","items":[{{"title":"...","author":"...","description":"...","amazon_search":"..."}}]}}

Return 4 verses and 3 books. Output each line immediately as you generate it."""


def stream_verses(mood: str, version: str):
    """Generator that yields SSE events, one per verse/reflection/books."""
    prompt = STREAM_PROMPT.format(mood=mood, version=version)
    buffer = ""
    with client.messages.stream(
        model="claude-haiku-4-5-20251001",
        max_tokens=1400,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for chunk in stream.text_stream:
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    yield f"data: {json.dumps(obj)}\n\n"
                except json.JSONDecodeError:
                    pass
    # flush any remaining buffer
    if buffer.strip():
        try:
            obj = json.loads(buffer.strip())
            yield f"data: {json.dumps(obj)}\n\n"
        except json.JSONDecodeError:
            pass
    yield "data: {\"type\":\"done\"}\n\n"


@app.route("/")
def index():
    return render_template("index.html")


def get_sermons(theme: str) -> list[dict]:
    """Search SermonAudio for free conservative sermons on a topic."""
    try:
        import requests as req
        resp = req.get(
            "https://api.sermonaudio.com/v2/node/sermons",
            params={"query": theme, "pageSize": 3, "sortBy": "downloads"},
            timeout=8,
        )
        if resp.status_code != 200:
            return []
        items = resp.json().get("results", {}).get("nodes", [])
        sermons = []
        for s in items[:3]:
            sermons.append({
                "title":   s.get("fullTitle", ""),
                "speaker": s.get("speaker", {}).get("displayName", ""),
                "church":  s.get("broadcaster", {}).get("displayName", ""),
                "date":    s.get("preachDate", ""),
                "url":     f"https://www.sermonaudio.com/sermoninfo.asp?SID={s.get('sermonID','')}",
            })
        return sermons
    except Exception:
        return []


@app.route("/stream", methods=["POST"])
def stream():
    data = request.get_json()
    mood = (data.get("mood", "") or "").strip()
    version = data.get("version", "KJV")
    if not mood:
        return jsonify({"error": "Please describe what you are feeling."}), 400

    # Kick off SermonAudio in background thread
    from concurrent.futures import ThreadPoolExecutor
    executor = ThreadPoolExecutor(max_workers=1)
    sermons_fut = executor.submit(get_sermons, mood)

    @stream_with_context
    def generate():
        yield from stream_verses(mood, version)
        # Append sermons once Claude is done
        try:
            sermons = sermons_fut.result(timeout=10)
            if sermons:
                yield f"data: {json.dumps({'type':'sermons','items':sermons})}\n\n"
        except Exception:
            pass
        executor.shutdown(wait=False)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/search", methods=["POST"])
def search():
    """Fallback non-streaming endpoint."""
    data = request.get_json()
    mood = data.get("mood", "").strip()
    version = data.get("version", "KJV")
    if not mood:
        return jsonify({"error": "Please describe what you are feeling."}), 400
    try:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as ex:
            sermons_fut = ex.submit(get_sermons, mood)
        # Re-assemble from stream
        verses, books, reflection = [], [], ""
        for line in "".join(
            c for c in stream_verses(mood, version)
            if not c.startswith("data: {\"type\":\"done\"}")
        ).split("data: "):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if obj["type"] == "reflection": reflection = obj["text"]
                elif obj["type"] == "verse":     verses.append(obj)
                elif obj["type"] == "books":     books = obj.get("items", [])
            except Exception:
                pass
        return jsonify({"mood_reflection": reflection, "verses": verses,
                        "books": books, "sermons": sermons_fut.result()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/subscribe", methods=["POST"])
def subscribe():
    data = request.get_json()
    email = (data.get("email", "") or "").strip().lower()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    subs = load_subscribers()
    if email in subs:
        return jsonify({"status": "already_subscribed"})
    subs.append(email)
    save_subscribers(subs)
    return jsonify({"status": "subscribed"})


@app.route("/unsubscribe", methods=["GET"])
def unsubscribe():
    email = (request.args.get("email", "") or "").strip().lower()
    if email:
        subs = load_subscribers()
        subs = [s for s in subs if s != email]
        save_subscribers(subs)
    return "<html><body style='background:#1a1208;font-family:Georgia,serif;color:#c9a84c;text-align:center;padding:80px;'><h2>✦ You have been unsubscribed.</h2><p style='color:#7a6040;margin-top:16px;'>You will no longer receive daily devotionals.</p></body></html>"


if __name__ == "__main__":
    app.run(debug=True, port=5050)
