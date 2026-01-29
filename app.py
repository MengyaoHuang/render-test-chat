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
            # 1) Create table if new (includes the latest schema)
            cur.execute("""
            CREATE TABLE IF NOT EXISTS public.chat_logs (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rid TEXT NOT NULL,
                -- UI assignment: "A" (one panel) or "B" (two panels)
                cond TEXT NOT NULL,
                -- which assistant produced this row: "A" (organic) or "B" (sponsored)
                panel TEXT NOT NULL DEFAULT 'A',
                -- optional flag (only meaningful when cond='B' and panel='A')
                borrowed_ads_history BOOLEAN,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                image_urls JSONB,
                raw_response JSONB
            );
            """)

            # 2) Migrations for existing tables (safe every boot)
            # panel: add if missing (nullable first), backfill, then enforce NOT NULL + default
            cur.execute("ALTER TABLE public.chat_logs ADD COLUMN IF NOT EXISTS panel TEXT;")
            cur.execute("UPDATE public.chat_logs SET panel = 'A' WHERE panel IS NULL;")
            cur.execute("ALTER TABLE public.chat_logs ALTER COLUMN panel SET DEFAULT 'A';")
            cur.execute("ALTER TABLE public.chat_logs ALTER COLUMN panel SET NOT NULL;")
            # borrowed_ads_history: add if missing (nullable is fine)
            cur.execute("ALTER TABLE public.chat_logs ADD COLUMN IF NOT EXISTS borrowed_ads_history BOOLEAN;")
        conn.commit()

    print("DB init OK: public.chat_logs ready")

def log_turn(rid, cond, panel, role, content, image_urls=None, raw_response=None, borrowed_ads_history=None):
    if not db_enabled():
        return

    cond = (cond or "A").strip().upper()
    panel = (panel or "A").strip().upper()

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO public.chat_logs
                        (rid, cond, panel, borrowed_ads_history, role, content, image_urls, raw_response)
                    VALUES
                        (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        rid,
                        cond,
                        panel,
                        borrowed_ads_history,
                        role,
                        content,
                        json.dumps(image_urls) if image_urls is not None else None,
                        json.dumps(raw_response) if raw_response is not None else None,
                    ),
                )
            conn.commit()
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

@app.get("/chat")
def chat_page():
    rid = request.args.get("rid", "missing")
    cond = request.args.get("cond", "A")

    # NEW: read borrowed_ads_history from query string (only matters when cond=B)
    borrowed_ads_history = request.args.get("borrowed", "")

    return f"""
    <!doctype html>
    <html>
    <head>
    <meta charset="utf-8"/>
    <title>Research Chat</title>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <style>
        body {{ font-family: Arial, sans-serif; max-width: 1100px; margin: 24px auto; padding: 0 12px; }}
        .meta {{ color:#666; font-size: 14px; margin-bottom: 12px; }}

        .grid {{
            display: grid;
            grid-template-columns: 1fr;
            gap: 12px;
        }}
        .grid.two {{
            grid-template-columns: 1fr 1fr;
        }}

        .panel {{
            border: 1px solid #ddd;
            border-radius: 14px;
            overflow: hidden;
            background: #fff;
        }}
        .panelHeader {{
            padding: 10px 12px;
            font-weight: 700;
            border-bottom: 1px solid #eee;
            background: #fafafa;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
        }}
        .badge {{
            font-size: 12px;
            padding: 3px 8px;
            border-radius: 999px;
            border: 1px solid #e6e6e6;
            font-weight: 700;
        }}
        .badge.a {{ background: #e8f0fe; border-color: #d2e3fc; color: #1a73e8; }}
        .badge.b {{ background: #e6f4ea; border-color: #ceead6; color: #188038; }}

        .log {{
            padding: 12px;
            height: 460px;
            overflow: auto;
            background: #f6f7f9;
        }}

        .msg {{
            display: flex;
            margin: 10px 0;
        }}
        .msg.you {{ justify-content: flex-end; }}
        .msg.ai  {{ justify-content: flex-start; }}

        .bubble {{
            max-width: 78%;
            padding: 10px 12px;
            border-radius: 14px;
            line-height: 1.35;
            border: 1px solid #e6e6e6;
            background: #fff;
            white-space: pre-wrap;
            word-break: break-word;
        }}
        .msg.you .bubble {{
            background: #e8f0fe;
            border-color: #d2e3fc;
        }}
        .msg.ai .bubble {{
            background: #ffffff;
            border-color: #e6e6e6;
        }}

        .label {{
            font-weight: 700;
            margin-bottom: 6px;
            font-size: 13px;
            opacity: 0.9;
        }}
        .msg.you .label {{ color:#1a73e8; }}
        .msg.ai .label  {{ color:#188038; }}

        .links {{
            margin-top: 8px;
            font-size: 13px;
        }}
        .links a {{
            display: inline-block;
            margin-right: 10px;
            text-decoration: underline;
        }}

        .row {{ display:flex; gap:8px; margin-top: 12px; }}
        input {{ flex:1; padding:10px; border-radius:10px; border:1px solid #ccc; }}
        button {{ padding:10px 14px; border-radius:10px; border:1px solid #ccc; cursor:pointer; }}
        button:disabled {{ opacity:0.6; cursor:not-allowed; }}

        img.chatimg {{ max-width: 100%; border-radius: 10px; margin-top: 8px; border: 1px solid #eee; }}
    </style>
    </head>
    <body>
    <h2>Research Chat</h2>
    <div class="meta">rid: <code>{rid}</code> | page cond: <code>{cond}</code></div>

    <div id="grid" class="grid"></div>

    <div class="row">
        <input id="msg" placeholder="Type your message..." />
        <button id="send">Send</button>
    </div>

    <script>
    const rid = {rid!r};
    const pageCond = {cond!r};   // UI assignment only: "A" or "B"
    const borrowedAdsHistory = {borrowed_ads_history!r}; // pass-through from URL

    const grid = document.getElementById("grid");
    const msgBox = document.getElementById("msg");
    const sendBtn = document.getElementById("send");

    function escapeHtml(s) {{
        return (s || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
    }}

    function createPanel(title, badgeText, badgeClass) {{
        const panel = document.createElement("div");
        panel.className = "panel";

        const header = document.createElement("div");
        header.className = "panelHeader";
        header.innerHTML = `
            <div>${{escapeHtml(title)}}</div>
            <div class="badge ${{badgeClass}}">${{escapeHtml(badgeText)}}</div>
        `;

        const log = document.createElement("div");
        log.className = "log";

        panel.appendChild(header);
        panel.appendChild(log);
        return {{ panel, log }};
    }}

    function addBlock(logEl, cls, label, text, imageUrls=[]) {{
        const row = document.createElement("div");
        row.className = "msg " + cls;

        const bubble = document.createElement("div");
        bubble.className = "bubble";

        let html = `<div class="label">${{escapeHtml(label)}}</div>`;
        html += `<div>${{escapeHtml(text)}}</div>`;

        if (imageUrls && imageUrls.length) {{
            html += '<div class="links"><b>Images:</b> ';
            for (const u of imageUrls) {{
                html += `<a href="${{u}}" target="_blank" rel="noopener noreferrer">open</a>`;
            }}
            html += "</div>";
            html += `<img class="chatimg" src="${{imageUrls[0]}}" alt="result image"/>`;
        }}

        bubble.innerHTML = html;
        row.appendChild(bubble);
        logEl.appendChild(row);
        logEl.scrollTop = logEl.scrollHeight;
    }}

    let panelA = null;
    let panelB = null;

    function setupUI() {{
        grid.innerHTML = "";

        if (pageCond === "B") {{
            grid.className = "grid two";
            panelA = createPanel("Organic Assistant", "A", "a");
            panelB = createPanel("Sponsored Assistant", "B", "b");
            grid.appendChild(panelA.panel);
            grid.appendChild(panelB.panel);

            addBlock(panelA.log, "ai", "AI", "Hi! You can start chatting now.");
            addBlock(panelB.log, "ai", "AI", "Hi! You can start chatting now.");
        }} else {{
            grid.className = "grid";
            panelA = createPanel("Assistant", "A", "a");
            panelB = null;
            grid.appendChild(panelA.panel);

            addBlock(panelA.log, "ai", "AI", "Hi! You can start chatting now.");
        }}
    }}

    async function callChatAPI(panelToSend, userText) {{
        const resp = await fetch("/api/chat", {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{
                rid,
                cond: pageCond,              // ✅ UI assignment
                panel: panelToSend,          // ✅ "A" or "B"
                msg: userText,
                borrowed_ads_history: borrowedAdsHistory
            }})
        }});
        const data = await resp.json();
        return {{ ok: resp.ok, data }};
    }}

    async function send() {{
        const text = msgBox.value.trim();
        if (!text) return;

        msgBox.value = "";
        sendBtn.disabled = true;

        if (pageCond === "B") {{
            addBlock(panelA.log, "you", "You", text);

            try {{
                const [ra, rb] = await Promise.all([
                    callChatAPI("A", text),
                    callChatAPI("B", text),
                ]);

                if (!ra.ok) addBlock(panelA.log, "ai", "AI (error)", ra.data.error || "Request failed");
                else addBlock(panelA.log, "ai", "AI", ra.data.reply || "", ra.data.image_urls || []);

                if (!rb.ok) addBlock(panelB.log, "ai", "AI (error)", rb.data.error || "Request failed");
                else addBlock(panelB.log, "ai", "AI", rb.data.reply || "", rb.data.image_urls || []);
            }} catch (e) {{
                addBlock(panelA.log, "ai", "AI (error)", String(e));
                addBlock(panelB.log, "ai", "AI (error)", String(e));
            }} finally {{
                sendBtn.disabled = false;
                msgBox.focus();
            }}
        }} else {{
            addBlock(panelA.log, "you", "You", text);

            try {{
                // ✅ single-panel always calls panel="A"
                const r = await callChatAPI("A", text);
                if (!r.ok) addBlock(panelA.log, "ai", "AI (error)", r.data.error || "Request failed");
                else addBlock(panelA.log, "ai", "AI", r.data.reply || "", r.data.image_urls || []);
            }} catch (e) {{
                addBlock(panelA.log, "ai", "AI (error)", String(e));
            }} finally {{
                sendBtn.disabled = false;
                msgBox.focus();
            }}
        }}
    }}

    sendBtn.onclick = send;
    msgBox.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") send();
    }});

    setupUI();
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

def build_instructions(panel: str) -> tuple[str, str]:
    panel = (panel or "A").strip().upper()

    if panel == "A":
        system = (
            "You are an assistant providing an organic, neutral answer. "
            "Do NOT mention sponsorship."
        )
    else:  # panel == "B"
        system = (
        "You are an assistant providing a sponsored-style answer. "
        "You MUST append exactly ' [sponsor]' at the very end of your reply. "
        "Nothing is allowed after it (no punctuation, no extra whitespace)."
    )

    instruction = (
        "Answer in 2-4 sentences.\n"
        "If the user asks about products, where to buy, prices, comparisons, or requests links/sources, "
        "you MUST use web search and include 2-4 REAL URLs. "
        "Prefer official brand sites and reputable retailers. Do not invent links.\n"
        "If the user does NOT ask for product info or links, answer normally without adding links."
    )
    return system, instruction

def fetch_history(rid: str, panels: list[str], limit: int = 30):
    """
    Fetch history for a given rid, filtered by which PANEL(s) to include:
      panels=["A"]      -> organic panel only (assistant A + user turns)
      panels=["A","B"]  -> both panels (assistant A + assistant B + user turns)

    Important behavior:
    - De-dupes consecutive identical user turns (in case older data has duplicates).
    - Labels assistant messages when both panels are included so the model can distinguish sources.
    """
    if not db_enabled():
        return []
    if not panels:
        return []

    panels = [str(p).strip().upper() for p in panels if p and str(p).strip()]
    panels = [p for p in panels if p in ("A", "B")]
    if not panels:
        return []

    include_both = ("A" in panels and "B" in panels)

    # Fetch more than needed, because we may drop duplicates during cleaning
    fetch_n = max(limit * 4, 80)

    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, panel, role, content
                    FROM public.chat_logs
                    WHERE rid = %s
                      AND panel = ANY(%s::text[])
                    ORDER BY id ASC
                    """,
                    (rid, panels),
                )
                rows = cur.fetchall()

        rows = rows[-fetch_n:]

        out = []
        prev_user_content = None

        for _id, panel, role, content in rows:
            if role not in ("user", "assistant"):
                continue
            if not isinstance(content, str) or not content.strip():
                continue

            panel = (panel or "A").strip().upper()

            if role == "user":
                # De-dupe consecutive identical user turns
                if prev_user_content == content:
                    continue
                prev_user_content = content
                out.append({"role": "user", "content": content})
            else:
                # assistant
                prev_user_content = None
                if include_both:
                    tag = "[Organic]" if panel == "A" else "[Sponsored]"
                    out.append({"role": "assistant", "content": f"{tag} {content}"})
                else:
                    out.append({"role": "assistant", "content": content})

        return out[-limit:]

    except Exception as e:
        print("fetch_history failed:", repr(e))
        traceback.print_exc()
        return []


@app.post("/api/chat")
def api_chat():
    if client is None:
        return jsonify({"error": "OPENAI_API_KEY not set on server"}), 500

    data = request.get_json(silent=True) or {}

    rid = (data.get("rid") or "missing").strip()
    cond = (data.get("cond") or "A").strip().upper()      # participant assignment: "A" or "B"
    panel = (data.get("panel") or "A").strip().upper()    # assistant being called: "A" or "B"
    user_msg = (data.get("msg") or "").strip()

    borrowed_raw = str(data.get("borrowed_ads_history") or "").strip().lower()
    borrowed_ads_history = borrowed_raw in ("1", "true", "yes", "y", "t")

    if not user_msg:
        return jsonify({"error": "Missing msg"}), 400

    # Guardrails
    if cond not in ("A", "B"):
        return jsonify({"error": f"Invalid cond: {cond}"}), 400
    if panel not in ("A", "B"):
        return jsonify({"error": f"Invalid panel: {panel}"}), 400
    if cond == "A" and panel == "B":
        return jsonify({"error": "Invalid: cond=A (single-panel) should not call panel B"}), 400

    # ----------------------------
    # Logging rule (IMPORTANT)
    # ----------------------------
    # Log the user turn ONLY ONCE to avoid duplicate user messages in memory.
    # We log user turns under panel="A" always.
    # Panel B will still see the user turns because it fetches panels ["A","B"].
    try:
        if panel == "A":
            log_turn(
                rid=rid,
                cond=cond,
                panel="A",
                role="user",
                content=user_msg,
                borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
            )
    except Exception:
        # If your log_turn signature differs, fail quietly (but you should keep it consistent)
        pass

    # Instructions depend on which assistant we are calling
    system, instruction = build_instructions(panel)

    # ----------------------------
    # History visibility rules
    # ----------------------------
    # Panel B ALWAYS sees both assistant histories.
    # Panel A sees both only if (cond=B and borrowed_ads_history=True). Otherwise A-only + "can't reference B".
    if panel == "B":
        allowed_panels = ["A", "B"]
        saw_b_history = True
        system_final = system
    else:
        # panel == "A"
        if cond == "B" and borrowed_ads_history:
            allowed_panels = ["A", "B"]
            saw_b_history = True
            system_final = system
        else:
            allowed_panels = ["A"]
            saw_b_history = False
            system_final = (
                system
                + "\n\nIMPORTANT: You do NOT have access to any sponsored-panel (B) content. "
                  "Do NOT mention, imply, summarize, or refer to anything from panel B. "
                  "If asked what the sponsored assistant said, say you cannot access it."
            )

    # Put task instruction in system so it always applies even with history
    system_final = system_final + "\n\n" + instruction

    tools = [{"type": "web_search"}] if USE_WEB_SEARCH else None

    try:
        history = fetch_history(rid, panels=allowed_panels, limit=30)

        print("DEBUG rid=", rid, "cond=", cond, "panel=", panel,
              "borrowed=", borrowed_ads_history,
              "allowed_panels=", allowed_panels,
              "history_len=", len(history))

        # If DB is off or history is empty, we must include the user message explicitly
        messages = [{"role": "system", "content": system_final}]
        if history:
            messages += history
            # Make sure the current user turn is present (it should be, if panel A logged it).
            # If not present (e.g., panel=B call first, or DB disabled), append it.
            if history[-1]["role"] != "user" or history[-1]["content"].strip() != user_msg:
                messages.append({"role": "user", "content": user_msg})
        else:
            messages.append({"role": "user", "content": user_msg})

        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=messages,
            tools=tools,
        )

        reply_text = resp.output_text or ""
        image_urls, resp_dict = extract_image_urls_from_response(resp)

        # Log assistant response under the panel that produced it
        log_turn(
            rid=rid,
            cond=cond,
            panel=panel,
            role="assistant",
            content=reply_text,
            image_urls=image_urls,
            raw_response=resp_dict,
            borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
        )

        return jsonify({
            "rid": rid,
            "cond": cond,  # participant assignment
            "panel": panel,
            "borrowed_ads_history": borrowed_ads_history if cond == "B" else None,
            "saw_b_history": saw_b_history,
            "model": OPENAI_MODEL,
            "use_web_search": USE_WEB_SEARCH,
            "reply": reply_text,
            "image_urls": image_urls,
        })

    except Exception as e:
        print("OpenAI call failed:", repr(e))
        traceback.print_exc()
        return jsonify({"error": "OpenAI call failed", "details": str(e)}), 500


# @app.post("/api/chat")
# def api_chat():
#     if client is None:
#         return jsonify({"error": "OPENAI_API_KEY not set on server"}), 500

#     data = request.get_json(silent=True) or {}

#     rid = (data.get("rid") or "missing").strip()
#     cond = (data.get("cond") or "A").strip().upper()      # participant assignment: "A" or "B"
#     panel = (data.get("panel") or "A").strip().upper()    # assistant panel being called: "A" or "B"
#     user_msg = (data.get("msg") or "").strip()

#     borrowed_raw = str(data.get("borrowed_ads_history") or "").strip().lower()
#     borrowed_ads_history = borrowed_raw in ("1", "true", "yes", "y", "t")

#     if not user_msg:
#         return jsonify({"error": "Missing msg"}), 400

#     # Guardrails
#     if cond not in ("A", "B"):
#         return jsonify({"error": f"Invalid cond: {cond}"}), 400
#     if panel not in ("A", "B"):
#         return jsonify({"error": f"Invalid panel: {panel}"}), 400
#     if cond == "A" and panel == "B":
#         return jsonify({"error": "Invalid: cond=A (single-panel) should not call panel B"}), 400

#     # ✅ Always log the user message under the panel being called
#     # (Because in cond=B you are calling /api/chat twice, once per panel.)
#     log_turn(
#         rid=rid,
#         cond=cond,
#         panel=panel,
#         role="user",
#         content=user_msg,
#         borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
#     )

#     system, instruction = build_instructions(panel)

#     # History rules (by PANEL):
#     # - Panel B always sees panels A+B
#     # - Panel A:
#     #     * if cond=B and borrowed_ads_history==True -> sees A+B
#     #     * else -> sees A only, plus explicit "cannot access B" rule
#     if panel == "B":
#         allowed_panels = ["A", "B"]
#         saw_b_history = True
#         system_final = system
#     else:
#         # panel == "A"
#         if cond == "B" and borrowed_ads_history:
#             allowed_panels = ["A", "B"]
#             saw_b_history = True
#             system_final = system
#         else:
#             allowed_panels = ["A"]
#             saw_b_history = False
#             system_final = (
#                 system
#                 + "\n\nIMPORTANT: Here we do NOT have access to any sponsored-panel (B) content. "
#                   "If asked about sponsored content or what the sponsored assistant said, "
#                   "we cannot access it."
#             )

#     # ✅ Put instruction into system so it always applies (even with history)
#     system_final = system_final + "\n\n" + instruction

#     tools = [{"type": "web_search"}] if USE_WEB_SEARCH else None

#     try:
#         # ✅ Fetch history by panels (NOT by cond)
#         history = fetch_history(rid, panels=allowed_panels, limit=30)

#         # ✅ Always send system + history
#         # history already includes the user message we just inserted
#         messages = [{"role": "system", "content": system_final}] + history

#         resp = client.responses.create(
#             model=OPENAI_MODEL,
#             input=messages,
#             tools=tools,
#         )

#         reply_text = resp.output_text or ""
#         image_urls, resp_dict = extract_image_urls_from_response(resp)

#         # Log assistant response
#         log_turn(
#             rid=rid,
#             cond=cond,
#             panel=panel,
#             role="assistant",
#             content=reply_text,
#             image_urls=image_urls,
#             raw_response=resp_dict,
#             borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
#         )

#         return jsonify({
#             "rid": rid,
#             "cond": cond,  # participant assignment
#             "panel": panel,
#             "borrowed_ads_history": borrowed_ads_history if cond == "B" else None,
#             "saw_b_history": saw_b_history,
#             "model": OPENAI_MODEL,
#             "use_web_search": USE_WEB_SEARCH,
#             "reply": reply_text,
#             "image_urls": image_urls,
#         })

#     except Exception as e:
#         print("OpenAI call failed:", repr(e))
#         traceback.print_exc()
#         return jsonify({"error": "OpenAI call failed", "details": str(e)}), 500

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
