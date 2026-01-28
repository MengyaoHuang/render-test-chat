from flask import Flask, request, jsonify
import os
import json
import traceback
from openai import OpenAI
import psycopg2

app = Flask(__name__)

# ----------------------------
# OpenAI config
# ----------------------------
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
# Safer default than gpt-4o-mini. Change if you want.
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4.1-mini")
USE_WEB_SEARCH = os.environ.get("USE_WEB_SEARCH", "0").strip() in ("1", "true", "True", "yes", "YES")

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
    if not db_enabled():
        print("DB init skipped: DATABASE_URL not set")
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
            CREATE TABLE IF NOT EXISTS public.chat_logs (
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
    print("DB init OK: public.chat_logs ready")

def log_turn(rid, cond, role, content, image_urls=None, raw_response=None):
    if not db_enabled():
        return
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.chat_logs (rid, cond, role, content, image_urls, raw_response)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rid,
                        cond,
                        role,
                        content,
                        json.dumps(image_urls) if image_urls is not None else None,
                        json.dumps(raw_response) if raw_response is not None else None,
                    ),
                )
    except Exception as e:
        print("DB log_turn failed:", repr(e))
        traceback.print_exc()

try:
    init_db()
except Exception as e:
    print("DB init failed:", str(e))
    traceback.print_exc()

@app.get("/")
def home():
    return "Render is live ✅"

@app.get("/health")
def health():
    # Don't leak secrets; just show booleans + config
    return {
        "has_key": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
        "use_web_search": USE_WEB_SEARCH,
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
        body {{font-family: Arial, sans-serif; max-width: 860px; margin: 24px auto; padding: 0 12px; }}
        .meta {{ color:#666; font-size: 14px; margin-bottom: 12px; }}
        /* Chat container */
        #log {{
            border:1px solid #ddd;
            border-radius:12px;
            padding:12px;
            height: 460px;
            overflow:auto;
            background:#f6f7f9;
        }}
        /* Each message row (controls left/right alignment) */
        .msg {{
            display: flex;
            margin: 10px 0;
        }}
        .msg.you {{ justify-content: flex-end; }}
        .msg.ai  {{ justify-content: flex-start; }}

        /* The bubble itself */
        .bubble {{
            max-width: 78%;
            padding: 10px 12px;
            border-radius: 14px;
            line-height: 1.35;
            border: 1px solid #e6e6e6;
            background: #fff;
            white-space: pre-wrap;      /* keeps line breaks */
            word-break: break-word;     /* long URLs won't overflow */
        }}

        /* Bubble colors */
        .msg.you .bubble {{
            background: #e8f0fe;
            border-color: #d2e3fc;
        }}
        .msg.ai .bubble {{
            background: #ffffff;
            border-color: #e6e6e6;
        }}

        /* Label line */
        .label {{
            font-weight: 700;
            margin-bottom: 6px;
            font-size: 13px;
            opacity: 0.9;
        }}
        .msg.you .label {{ color:#1a73e8; }}
        .msg.ai .label  {{ color:#188038; }}

        /* Links list inside bubble */
        .links {{
            margin-top: 8px;
            font-size: 13px;
        }}
        .links a {{
            display: inline-block;
            margin-right: 10px;
            text-decoration: underline;
        }}

        /* Input row */
        .row {{ display:flex; gap:8px; margin-top: 12px; }}
        input {{ flex:1; padding:10px; border-radius:10px; border:1px solid #ccc; }}
        button {{ padding:10px 14px; border-radius:10px; border:1px solid #ccc; cursor:pointer; }}
        button:disabled {{ opacity:0.6; cursor:not-allowed; }}

        img.chatimg {{ max-width: 100%; border-radius: 10px; margin-top: 8px; border: 1px solid #eee; }}
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
        const row = document.createElement("div");
        row.className = "msg " + cls;

        const bubble = document.createElement("div");
        bubble.className = "bubble";

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

        bubble.innerHTML = html;
        row.appendChild(bubble);
        log.appendChild(row);
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


def build_instructions(cond: str) -> tuple[str, str]:
    if cond == "A":
        system = "You are an assistant providing an organic, neutral answer. Do NOT mention sponsorship."
    elif cond == "B":
        system = "You are an assistant providing a sponsored-style answer. Append exactly ' [sponsor]' at the end."
    else:
        system = "You are a helpful assistant."

    instruction = (
        "Answer in 2-4 sentences.\n"
        "If the user asks about products, where to buy, prices, comparisons, or requests links/sources, "
        "you MUST use web search and include 2-4 REAL, clickable URLs. "
        "Prefer official brand sites and reputable retailers. Do not invent links.\n"
        "If the user does NOT ask for product info or links, answer normally without adding links."
    )
    return system, instruction


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

    system, instruction = build_instructions(cond)
    prompt = f"rid={rid}, cond={cond}\nUser: {user_msg}\n\nInstruction: {instruction}"

    tools = [{"type": "web_search"}] if USE_WEB_SEARCH else None

    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            tools=tools,
        )

        reply_text = resp.output_text or ""
        image_urls, resp_dict = extract_image_urls_from_response(resp)

        # Log assistant message
        log_turn(rid, cond, "assistant", reply_text, image_urls=image_urls, raw_response=resp_dict)

        return jsonify({
            "rid": rid,
            "cond": cond,
            "model": OPENAI_MODEL,
            "use_web_search": USE_WEB_SEARCH,
            "reply": reply_text,
            "image_urls": image_urls,
        })
    except Exception as e:
        # This is the key: show real error in Render logs
        print("OpenAI call failed:", repr(e))
        traceback.print_exc()

        # Return error details to the UI (you can remove details in production)
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
