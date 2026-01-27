from flask import Flask, request, jsonify
import os
from openai import OpenAI

app = Flask(__name__)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

@app.get("/")
def home():
    return "Render is live ✅"

@app.get("/health")
def health():
    return {"has_key": bool(OPENAI_API_KEY), "model": OPENAI_MODEL}

# 1) Serve a real chat page (HTML) so users can type
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
        body {{ font-family: Arial, sans-serif; max-width: 820px; margin: 24px auto; padding: 0 12px; }}
        .meta {{ color:#666; font-size: 14px; margin-bottom: 12px; }}
        #log {{ border:1px solid #ddd; border-radius:10px; padding:12px; height: 420px; overflow:auto; background:#fafafa; }}
        .msg {{ margin: 10px 0; }}
        .you b {{ color:#1a73e8; }}
        .ai b {{ color:#188038; }}
        .row {{ display:flex; gap:8px; margin-top: 12px; }}
        input {{ flex:1; padding:10px; border-radius:10px; border:1px solid #ccc; }}
        button {{ padding:10px 14px; border-radius:10px; border:1px solid #ccc; cursor:pointer; }}
        button:disabled {{ opacity:0.6; cursor:not-allowed; }}
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

      function addLine(cls, label, text) {{
        const div = document.createElement("div");
        div.className = "msg " + cls;
        div.innerHTML = "<b>" + label + ":</b> " + text.replace(/</g,"&lt;").replace(/>/g,"&gt;");
        log.appendChild(div);
        log.scrollTop = log.scrollHeight;
      }}

      async function send() {{
        const text = msgBox.value.trim();
        if (!text) return;

        addLine("you", "You", text);
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
            addLine("ai", "AI (error)", data.error || "Request failed");
          }} else {{
            addLine("ai", "AI", data.reply || "");
          }}
        }} catch (e) {{
          addLine("ai", "AI (error)", String(e));
        }} finally {{
          sendBtn.disabled = false;
          msgBox.focus();
        }}
      }}

      sendBtn.onclick = send;
      msgBox.addEventListener("keydown", (e) => {{
        if (e.key === "Enter") send();
      }});

      addLine("ai", "AI", "Hi! You can start chatting now.");
      msgBox.focus();
    </script>
    </body>
    </html>
    """

# 2) API endpoint that actually calls OpenAI
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
        return jsonify({"rid": rid, "cond": cond, "model": OPENAI_MODEL, "reply": resp.output_text})
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
