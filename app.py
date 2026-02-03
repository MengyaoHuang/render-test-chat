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
    # Render typically supports sslmode=require; prefer keeps it flexible.
    return psycopg2.connect(DATABASE_URL, sslmode="prefer")


def init_db():
    if not db_enabled():
        print("DB init skipped: DATABASE_URL not set")
        return

    with get_db() as conn:
        with conn.cursor() as cur:
            # Create table if missing (latest schema)
            cur.execute(
                """
            CREATE TABLE IF NOT EXISTS public.chat_logs (
                id BIGSERIAL PRIMARY KEY,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                rid TEXT NOT NULL,
                cond TEXT NOT NULL,
                panel TEXT NOT NULL DEFAULT 'A',
                borrowed_ads_history BOOLEAN,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                image_urls JSONB,
                raw_response JSONB
            );
            """
            )

            # Migrations (safe on every boot)
            cur.execute("ALTER TABLE public.chat_logs ADD COLUMN IF NOT EXISTS panel TEXT;")
            cur.execute("UPDATE public.chat_logs SET panel = 'A' WHERE panel IS NULL;")
            cur.execute("ALTER TABLE public.chat_logs ALTER COLUMN panel SET DEFAULT 'A';")
            cur.execute("ALTER TABLE public.chat_logs ALTER COLUMN panel SET NOT NULL;")

            cur.execute("ALTER TABLE public.chat_logs ADD COLUMN IF NOT EXISTS borrowed_ads_history BOOLEAN;")

        conn.commit()

    print("DB init OK: public.chat_logs ready")


def log_turn(
    rid,
    cond,
    panel,
    role,
    content,
    image_urls=None,
    raw_response=None,
    borrowed_ads_history=None,
):
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
    return {
        "has_key": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
        "use_web_search": USE_WEB_SEARCH,
        "db_enabled": db_enabled(),
    }


# ----------------------------
# Chat UI
# ----------------------------
@app.get("/chat")
def chat_page():
    rid = request.args.get("rid", "missing")
    cond = request.args.get("cond", "A")  # "A" single panel, "B" two-panel (organic + sponsor banner)

    borrowed_ads_history = request.args.get("borrowed", "")  # pass-through (optional)

    return f"""
<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>Research Chat</title>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<style>
  body {{ font-family: Arial, sans-serif; max-width: 1300px; margin: 24px auto; padding: 0 12px; }}
  .meta {{ color:#666; font-size: 14px; margin-bottom: 12px; }}

  .grid {{
    display: grid;
    grid-template-columns: 1fr;
    gap: 12px;
    align-items: start;
  }}
  .grid.two {{ grid-template-columns: 3fr 2fr; }}     /* ~60% / 40% */

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
    height: 560px;
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

  /* ------- Sponsored banner styles (slightly lighter dark) ------- */
    .panel.banner {{
    border: 1px solid rgba(255,255,255,0.08);
    background: #111827; /* lighter than #0b1220 */
    }}
    .panel.banner .panelHeader {{
    background: #111827;
    border-bottom: 1px solid rgba(255,255,255,0.10);
    color: #f3f4f6;
    }}
    .panel.banner .badge {{
    border-color: rgba(255,255,255,0.16);
    color: #f3f4f6;
    background: rgba(255,255,255,0.08);
    }}
    .log.bannerLog {{
    background: #111827;
    color: #f3f4f6;
    }}
    .sponsorHint {{
    font-size: 12px;
    color: rgba(243,244,246,0.75);
    margin-top: 2px;
    font-weight: 500;
    line-height: 1.35;
    }}
    .sCard {{
    border: 1px solid rgba(255,255,255,0.12);
    background: rgba(255,255,255,0.08); /* slightly brighter cards */
    border-radius: 12px;
    padding: 10px 10px;
    margin: 10px 0;
    }}
    .sTitle {{
    font-weight: 800;
    font-size: 13px;
    margin-bottom: 6px;
    }}
    .sWhy {{
    font-size: 12.5px;
    color: rgba(243,244,246,0.82);
    margin-bottom: 8px;
    line-height: 1.35;
    }}
    .sCtaRow {{
    display: flex;
    gap: 8px;
    align-items: center;
    flex-wrap: wrap;
    }}
    .sBtn {{
    display: inline-block;
    font-size: 12px;
    font-weight: 800;
    padding: 6px 10px;
    border-radius: 999px;
    border: 1px solid rgba(255,255,255,0.18);
    background: rgba(255,255,255,0.14); /* brighter CTA chip */
    color: #f3f4f6;
    text-decoration: none;
    }}
    .sLink {{
    font-size: 12px;
    color: rgba(147,197,253,0.95);
    text-decoration: underline;
    }}

    /* ------- NEW: Sponsor sections grouped by triggering question ------- */
    .sSection {{
      border-top: 1px solid rgba(255,255,255,0.10);
      margin-top: 10px;
      padding-top: 10px;
    }}
    .sSectionHeader {{
      font-size: 12px;
      font-weight: 800;
      color: rgba(243,244,246,0.92);
      margin-bottom: 8px;
      line-height: 1.35;
    }}
    .sSectionHeader .q {{
      font-weight: 700;
      color: rgba(243,244,246,0.78);
    }}
    .sTime {{
      font-size: 11px;
      color: rgba(243,244,246,0.55);
      margin-top: 2px;
      font-weight: 600;
    }}

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
  const pageCond = {cond!r};   // "A" or "B"
  const borrowedAdsHistory = {borrowed_ads_history!r};

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

  // -------- NEW: Sponsor results are grouped into sections per user question --------
  function nowTimeStr() {{
    const d = new Date();
    return d.toLocaleTimeString([], {{ hour: "2-digit", minute: "2-digit" }});
  }}

  function removeSponsorHint(logEl) {{
    const hint = logEl.querySelector(".sponsorHint");
    if (hint) hint.remove();
  }}

  // Keep last K sponsor sections (not cards)
  function trimSponsorSections(logEl, keepSections = 6) {{
    const sections = Array.from(logEl.querySelectorAll(".sSection"));
    if (sections.length > keepSections) {{
      for (let i = 0; i < sections.length - keepSections; i++) sections[i].remove();
    }}
  }}

  function addSponsorSection(logEl, userText, items) {{
    if (!items || !items.length) return;

    const section = document.createElement("div");
    section.className = "sSection";

    const header = document.createElement("div");
    header.className = "sSectionHeader";

    const q = (userText || "").trim();
    const qShort = q.length > 120 ? q.slice(0, 117) + "..." : q;

    header.innerHTML = `
        <div>Sponsored results <span class="q">for: "${{escapeHtml(qShort)}}"</span></div>
        <div class="sTime">${{escapeHtml(nowTimeStr())}}</div>
    `;

    section.appendChild(header);

    for (const it of items) {{
        const card = document.createElement("div");
        card.className = "sCard";

        // Use ONLY the first URL (no separate "link" anchors)
        const primaryUrl =
        (it && Array.isArray(it.urls) && it.urls.length && String(it.urls[0]).trim())
            ? String(it.urls[0]).trim()
            : "";

        const ctaText = escapeHtml(it && it.cta ? it.cta : "Learn more");

        // CTA pill becomes the clickable link when URL exists
        const ctaHtml = primaryUrl
        ? `<a class="sBtn" href="${{primaryUrl}}" target="_blank" rel="noopener noreferrer">${{ctaText}}</a>`
        : `<span class="sBtn" aria-disabled="true" style="opacity:0.7; cursor:default;">${{ctaText}}</span>`;

        card.innerHTML = `
        <div class="sTitle">${{escapeHtml(it && it.title ? it.title : "")}}</div>
        <div class="sWhy">${{escapeHtml(it && it.why ? it.why : "")}}</div>
        <div class="sCtaRow">
            ${{ctaHtml}}
        </div>
        `;

        section.appendChild(card);
    }}

    logEl.appendChild(section);
    trimSponsorSections(logEl, 6);
    logEl.scrollTop = logEl.scrollHeight;
    }}


  let panelA = null;
  let panelB = null;

  function setupUI() {{
    grid.innerHTML = "";

    if (pageCond === "B") {{
      grid.className = "grid two";

      panelA = createPanel("Organic Assistant", "A", "a");
      panelB = createPanel("Sponsored", "Ad", "b");

      panelB.panel.classList.add("banner");
      panelB.log.classList.add("bannerLog");

      grid.appendChild(panelA.panel);
      grid.appendChild(panelB.panel);

      addBlock(panelA.log, "ai", "AI", "Hi! You can start chatting now.");

      panelB.log.innerHTML = `
        <div class="sponsorHint">
          Recommendations may appear when relevant.
        </div>
      `;
    }} else {{
      grid.className = "grid";
      panelA = createPanel("Assistant", "A", "a");
      panelB = null;
      grid.appendChild(panelA.panel);

      addBlock(panelA.log, "ai", "AI", "Hi! You can start chatting now.");
    }}
  }}

  async function callChatAPI(userText) {{
    const resp = await fetch("/api/chat", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        rid,
        cond: pageCond,
        panel: "A",
        msg: userText,
        borrowed_ads_history: borrowedAdsHistory
      }})
    }});
    const data = await resp.json();
    return {{ ok: resp.ok, data }};
  }}

  async function callSponsorAPI(userText) {{
    const resp = await fetch("/api/sponsor", {{
      method: "POST",
      headers: {{ "Content-Type": "application/json" }},
      body: JSON.stringify({{
        rid,
        cond: pageCond,
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

    addBlock(panelA.log, "you", "You", text);

    try {{
      if (pageCond === "B") {{
        const [ra, rs] = await Promise.all([
          callChatAPI(text),
          callSponsorAPI(text),
        ]);

        if (!ra.ok) addBlock(panelA.log, "ai", "AI (error)", ra.data.error || "Request failed");
        else addBlock(panelA.log, "ai", "AI", ra.data.reply || "", ra.data.image_urls || []);

        // NEW: show sponsor results as a section tied to this user message
        if (rs.ok && rs.data && rs.data.show) {{
          removeSponsorHint(panelB.log);
          addSponsorSection(panelB.log, text, rs.data.items || []);
        }}
      }} else {{
        const r = await callChatAPI(text);
        if (!r.ok) addBlock(panelA.log, "ai", "AI (error)", r.data.error || "Request failed");
        else addBlock(panelA.log, "ai", "AI", r.data.reply || "", r.data.image_urls || []);
      }}
    }} catch (e) {{
      addBlock(panelA.log, "ai", "AI (error)", String(e));
    }} finally {{
      sendBtn.disabled = false;
      msgBox.focus();
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


# ----------------------------
# Helpers
# ----------------------------
def extract_image_urls_from_response(resp_obj) -> tuple[list, dict]:
    """
    Best-effort extraction of image URLs from a Responses API object.
    """
    urls = []

    def looks_like_image_url(u: str) -> bool:
        u = (u or "").lower()
        return u.startswith("http") and any(
            u.split("?")[0].endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp", ".gif"]
        )

    def walk(x):
        if isinstance(x, dict):
            if "url" in x and isinstance(x["url"], str) and looks_like_image_url(x["url"]):
                urls.append(x["url"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    try:
        resp_dict = resp_obj.model_dump()
    except Exception:
        resp_dict = json.loads(json.dumps(resp_obj, default=str))

    walk(resp_dict)

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
            "Do NOT mention [Organic] anywhere."
        )
    else:
        # This chat-panel B mode is no longer used by the UI (we use /api/sponsor),
        # but keep it for backwards compatibility.
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


def build_sponsor_system() -> str:
    return (
        "You are a sponsored recommendation banner for a research chat UI.\n"
        "Your job: decide whether it is appropriate to show 1-3 sponsored recommendations "
        "based on the user's latest message.\n\n"
        "Rules:\n"
        "- If the user's message does NOT imply shopping intent, product research, services, tools, "
        "or anything that reasonably maps to a recommendation, respond with EXACT JSON:\n"
        '  {"show": false, "items": []}\n'
        "- If it IS appropriate, respond with EXACT JSON:\n"
        '  {"show": true, "items": [ ... ] }\n'
        "- Each item must be SHORT and banner-like.\n"
        "- Do NOT write prose outside JSON. Do NOT wrap in markdown.\n"
        "- Max 3 items.\n"
        "- Prefer including URLs only when you are confident they are real; otherwise omit urls.\n\n"
        "JSON schema:\n"
        "{\n"
        '  "show": boolean,\n'
        '  "items": [\n'
        "    {\n"
        '      "title": string,\n'
        '      "why": string,\n'
        '      "cta": string,\n'
        '      "urls": [string]\n'
        "    }\n"
        "  ]\n"
        "}\n"
    )


def fetch_history(rid: str, panels: list[str], limit: int = 30):
    """
    Fetch history for a given rid, filtered by which PANEL(s) to include.
    - If panels includes both A and B, assistant messages are tagged [Organic]/[Sponsored] so the model can distinguish.
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
                if prev_user_content == content:
                    continue
                prev_user_content = content
                out.append({"role": "user", "content": content})
            else:
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


# ----------------------------
# API: Organic assistant chat
# ----------------------------
@app.post("/api/chat")
def api_chat():
    if client is None:
        return jsonify({"error": "OPENAI_API_KEY not set on server"}), 500

    data = request.get_json(silent=True) or {}

    rid = (data.get("rid") or "missing").strip()
    cond = (data.get("cond") or "A").strip().upper()
    panel = (data.get("panel") or "A").strip().upper()  # UI should always send "A" now
    user_msg = (data.get("msg") or "").strip()

    borrowed_raw = str(data.get("borrowed_ads_history") or "").strip().lower()
    borrowed_ads_history = borrowed_raw in ("1", "true", "yes", "y", "t")

    if not user_msg:
        return jsonify({"error": "Missing msg"}), 400

    if cond not in ("A", "B"):
        return jsonify({"error": f"Invalid cond: {cond}"}), 400
    if panel not in ("A", "B"):
        return jsonify({"error": f"Invalid panel: {panel}"}), 400
    if panel != "A":
        # This UI version no longer uses panel B chat. Keep it strict to avoid confusion.
        return jsonify({"error": "This UI only supports panel='A' for /api/chat. Use /api/sponsor for ads."}), 400

    # Log the user turn ONLY ONCE
    try:
        log_turn(
            rid=rid,
            cond=cond,
            panel="A",
            role="user",
            content=user_msg,
            borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
        )
    except Exception:
        pass

    system, instruction = build_instructions("A")

    # Panel A history visibility:
    # - default: only A
    # - if you set borrowed_ads_history and want A to see B too, flip allowed_panels to ["A","B"]
    if cond == "B" and borrowed_ads_history:
        allowed_panels = ["A", "B"]
        system_final = system + "\n\n" + instruction
        system_final += (
            "\n\nNOTE: You may see sponsored history tagged [Sponsored]. "
            "Do not mention sponsorship unless the user explicitly asks."
        )
    else:
        allowed_panels = ["A"]
        system_final = system + "\n\n" + instruction
        system_final += (
            "\n\nIMPORTANT: You do NOT have access to any sponsored banner content. "
            "Do NOT mention, imply, summarize, or refer to anything from the sponsored side."
            "If asked what the sponsored assistant said, say you cannot access it."
        )

    tools = [{"type": "web_search"}] if USE_WEB_SEARCH else None

    try:
        history = fetch_history(rid, panels=allowed_panels, limit=30)

        print(
            "DEBUG /api/chat rid=",
            rid,
            "cond=",
            cond,
            "panel=",
            panel,
            "borrowed=",
            borrowed_ads_history,
            "allowed_panels=",
            allowed_panels,
            "history_len=",
            len(history),
        )

        messages = [{"role": "system", "content": system_final}]
        if history:
            messages += history
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

        log_turn(
            rid=rid,
            cond=cond,
            panel="A",
            role="assistant",
            content=reply_text,
            image_urls=image_urls,
            raw_response=resp_dict,
            borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
        )

        return jsonify(
            {
                "rid": rid,
                "cond": cond,
                "panel": "A",
                "borrowed_ads_history": borrowed_ads_history if cond == "B" else None,
                "model": OPENAI_MODEL,
                "use_web_search": USE_WEB_SEARCH,
                "reply": reply_text,
                "image_urls": image_urls,
            }
        )

    except Exception as e:
        print("OpenAI call failed:", repr(e))
        traceback.print_exc()
        return jsonify({"error": "OpenAI call failed", "details": str(e)}), 500


# ----------------------------
# API: Sponsored banner (selective, JSON-only)
# ----------------------------
@app.post("/api/sponsor")
def api_sponsor():
    if client is None:
        return jsonify({"error": "OPENAI_API_KEY not set on server"}), 500

    data = request.get_json(silent=True) or {}
    rid = (data.get("rid") or "missing").strip()
    cond = (data.get("cond") or "A").strip().upper()
    user_msg = (data.get("msg") or "").strip()

    borrowed_raw = str(data.get("borrowed_ads_history") or "").strip().lower()
    borrowed_ads_history = borrowed_raw in ("1", "true", "yes", "y", "t")

    if cond not in ("A", "B"):
        return jsonify({"error": f"Invalid cond: {cond}"}), 400
    if not user_msg:
        return jsonify({"error": "Missing msg"}), 400

    # Banner should be lightweight; only look at organic history by default.
    allowed_panels = ["A"]
    history = fetch_history(rid, panels=allowed_panels, limit=20)

    system_final = build_sponsor_system()

    messages = [{"role": "system", "content": system_final}]
    if history:
        messages += history
        if history[-1]["role"] != "user" or history[-1]["content"].strip() != user_msg:
            messages.append({"role": "user", "content": user_msg})
    else:
        messages.append({"role": "user", "content": user_msg})

    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=messages,
            tools=None,  # keep banner cheap + deterministic
        )

        raw = (resp.output_text or "").strip()

        # Parse JSON safely
        try:
            payload = json.loads(raw)
        except Exception:
            payload = {"show": False, "items": []}

        show = bool(payload.get("show"))
        items = payload.get("items") or []
        if not isinstance(items, list):
            items = []

        clean_items = []
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            title = str(it.get("title") or "").strip()
            why = str(it.get("why") or "").strip()
            cta = str(it.get("cta") or "").strip()
            urls = it.get("urls") or []
            if not isinstance(urls, list):
                urls = []
            urls = [str(u).strip() for u in urls[:2] if isinstance(u, str) and u.strip()]

            if title and why and cta:
                clean_items.append({"title": title, "why": why, "cta": cta, "urls": urls})

        out = {"show": bool(show and clean_items), "items": clean_items}

        # Optional: log sponsor outputs as panel B assistant (stored as JSON string)
        try:
            log_turn(
                rid=rid,
                cond=cond,
                panel="B",
                role="assistant",
                content=json.dumps(out, ensure_ascii=False),
                raw_response={"sponsor_raw_text": raw},
                borrowed_ads_history=borrowed_ads_history if cond == "B" else None,
            )
        except Exception:
            pass

        return jsonify(out)

    except Exception as e:
        print("Sponsor call failed:", repr(e))
        traceback.print_exc()
        # Banner failure should not break the chat experience
        return jsonify({"show": False, "items": [], "error": "Sponsor call failed", "details": str(e)}), 200


# ----------------------------
# Example page (unchanged)
# ----------------------------
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
