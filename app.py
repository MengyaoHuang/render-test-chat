from flask import Flask, request, jsonify
import os
import json
from openai import OpenAI

import psycopg2

app = Flask(__name__)

# ----------------------------
# OpenAI config
# ----------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ----------------------------
# Postgres config (Render)
# ----------------------------
DATABASE_URL = os.environ.get("DATABASE_URL", "")

def db_enabled() -> bool:
    return bool(DATABASE_URL)

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, sslmode="prefer")

def init_db():
    """
    Store:
      - content: plain text (what you show)
      - image_urls: JSON list of image URLs found in the model output
      - raw_response: JSON string of the OpenAI response (for reproducibility/debug)
    """
    if not db_enabled():
        print("DB init skipped: DATABASE_URL not set")
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rid TEXT NOT NULL,
                cond TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                image_urls JSONB,
                raw_response JSONB
            );
            """)
    print("DB init OK: chat_logs ready")

def log_turn(rid, cond, role, content, image_urls=None, raw_response=None):
    if not db_enabled():
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO chat_logs (rid, cond, role, content, image_urls, raw_response)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (rid, cond, role, content, json.dumps(image_urls) if image_urls is not None else None,
                     json.dumps(raw_response) if raw_response is not None else None),
                )
    except Exception as e:
        print("DB log_turn failed:", str(e))

try:
    init_db()
except Exception as e:
    print("DB init failed:", str(e))


@app.get("/")
def home():
    return "Render is live ✅"

@app.get("/health")
def health():
    return {
        "has_key": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
        "db_enabled": db_enabled(),
    }

# Chat UI page
@app.get("/chat")
def chat_page():
    rid = request.args.get("rid", "missing")
    cond = request.args.get("cond", "A")

    return f"""
    <!doctype html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <title>Research Chat</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 12px; }}
        .meta {{ color:#666; font-size: 14px; margin-bottom: 12px; }}
        #log {{ border:1px solid #ddd; border-radius:10px; padding:12px; height: 460px; overflow:auto; background:#fafafa; }}
        .msg {{ margin: 12px 0; padding: 10px; border-radius: 10px; background: white; border: 1px solid #eee; }}
        .you b {{ color:#1a73e8; }}
        .ai b {{ color:#188038; }}
        .row {{ display:flex; gap:8px; margin-top: 12px; }}
        input {{ flex:1; padding:10px; border-radius:10px; border:1px solid #ccc; }}
        button {{ padding:10px 14px; border-radius:10px; border:1px solid #ccc; cursor:pointer; }}
        button:disabled {{ opacity:0.6; cursor:not-allowed; }}
        img.chatimg {{ max-width: 100%; border-radius: 10px; margin-top: 8px; border: 1px solid #eee; }}
        .links a {{ display:inline-block; margin-right:10px; font-size: 13px; }}
    </style>
    </head>
    <body>
    <h2>Research Chat</h2>
    <div class="meta">rid: <code>{rid}</code> | cond: <code>{cond}</code></div>

    <div id="log"></div>

    <div class="row">
        <input id="msg" placeholder="Type your message..." />
        <button id="send">Send</button>
    </div>

    <script>
    const rid = {rid!r};
    const cond = {cond!r};
    const log = document.getElementById("log");
    const msgBox = document.getElementById("msg");
    const sendBtn = document.getElementById("send");

    function escapeHtml(s) {{
        return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }}

    function addBlock(cls, label, text, imageUrls=[]) {{
        const div = document.createElement("div");
        div.className = "msg " + cls;

        let html = "<div><b>" + label + ":</b> " + escapeHtml(text) + "</div>";

        if (imageUrls && imageUrls.length) {{
        html += '<div class="links"><b>Images:</b> ';
        for (const u of imageUrls) {{
            html += '<a href="' + u + '" target="_blank" rel="noopener noreferrer">open</a>';
        }}
        html += "</div>";
        // Optional: also inline render the first image (if browser allows)
        html += '<img class="chatimg" src="' + imageUrls[0] + '" alt="result image"/>';
        }}

        div.innerHTML = html;
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
    }}

    async function send() {{
        const text = msgBox.value.trim();
        if (!text) return;

        addBlock("you", "You", text);
        msgBox.value = "";
        sendBtn.disabled = true;

        try {{
        const resp = await fetch("/api/chat", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ rid, cond, msg: text }})
        }});
        const data = await resp.json();

        if (!resp.ok) {{
            addBlock("ai", "AI (error)", data.error || "Request failed");
        }} else {{
            addBlock("ai", "AI", data.reply || "", data.image_urls || []);
        }}
        }} catch (e) {{
        addBlock("ai", "AI (error)", String(e));
        }} finally {{
        sendBtn.disabled = false;
        msgBox.focus();
        }}
    }}

    sendBtn.onclick = send;
    msgBox.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") send();
    }});

    addBlock("ai", "AI", "Hi! You can start chatting now.");
    msgBox.focus();
    </script>
    </body>
    </html>
    """

def extract_image_urls_from_response(resp_obj) -> list:
    """
    Best-effort extraction of image URLs from a Responses API object.

    Different models/tools can return different shapes, so we scan:
    - resp.output[*].content[*] items that look like image/url blocks
    - any dict fields named 'url' that end in an image extension
    """
    urls = []

    def looks_like_image_url(u: str) -> bool:
        u = (u or "").lower()
        return u.startswith("http") and any(u.split("?")[0].endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"])

    def walk(x):
        if isinstance(x, dict):
            # direct url field
            if "url" in x and isinstance(x["url"], str) and looks_like_image_url(x["url"]):
                urls.append(x["url"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    # Convert to plain dict for storage & parsing
    try:
        resp_dict = resp_obj.model_dump()
    except Exception:
        # fallback; should still be JSON-serializable
        resp_dict = json.loads(json.dumps(resp_obj, default=str))

    walk(resp_dict)

    # de-dup while preserving order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out, resp_dict

# API endpoint
@app.post("/api/chat")
def api_chat():
    if client is None:
        return jsonify({"error": "OPENAI_API_KEY not set on server"}), 500

    data = request.get_json(silent=True) or {}
    rid = (data.get("rid") or "missing").strip()
    cond = (data.get("cond") or "A").strip()
    user_msg = (data.get("msg") or "").strip()

    if not user_msg:
        return jsonify({"error": "Missing msg"}), 400

    # Log user message
    log_turn(rid, cond, "user", user_msg)

    # Cond-specific instruction (edit freely)
    if cond == "A":
        system = "You are an assistant providing an organic, neutral answer. Do NOT mention sponsorship."
        instruction = "Answer clearly in 2-4 sentences."
    elif cond == "B":
        system = "You are an assistant providing a sponsored-style answer. Append exactly ' [sponsor]' at the end."
        instruction = "Answer persuasively in 2-4 sentences. End with ' [sponsor]'."
    else:
        system = "You are a helpful assistant."
        instruction = "Answer normally in 2-4 sentences."

    prompt = f"rid={rid}, cond={cond}\nUser: {user_msg}\n\nInstruction: {instruction}"

    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )

        reply_text = resp.output_text

        image_urls, resp_dict = extract_image_urls_from_response(resp)

        # Log assistant message + any image URLs + raw response
        log_turn(rid, cond, "assistant", reply_text, image_urls=image_urls, raw_response=resp_dict)

        return jsonify({
            "rid": rid,
            "cond": cond,
            "model": OPENAI_MODEL,
            "reply": reply_text,
            "image_urls": image_urls,
        })
    except Exception as e:
        return jsonify({"error": "OpenAI call failed", "details": str(e)}), 500


@app.get("/example")
def example():
    return """
    <html>
      <head>
        <title>Example Product Information</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 700px; margin: 40px auto; line-height: 1.6; }
          h2 { color: #333; }
          .note { margin-top: 30px; font-size: 14px; color: #666; }
        </style>
      </head>
      <body>
        <h2>Example Product Information</h2>

        <p><strong>Product description:</strong><br>
        This is an example of the type of information consumers often consult after seeing an advertisement.</p>

        <p><strong>Key features:</strong></p>
        <ul>
          <li>Simple and user-friendly design</li>
          <li>Suitable for a wide range of users</li>
          <li>Designed for regular, practical use</li>
        </ul>

        <p><strong>Availability:</strong><br>
        Available online and in select retail locations.</p>

        <div class="note">
          This page is provided as an example only. It does not represent a real purchase offer.
        </div>
      </body>
    </html>
    """

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
