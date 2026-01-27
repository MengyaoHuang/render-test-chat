from flask import Flask, request, jsonify
import os

from openai import OpenAI

app = Flask(__name__)

# ---- OpenAI config ----
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None


@app.get("/")
def home():
    return "Render is live ✅"

@app.get("/health")
def health():
    return {
        "has_key": bool(os.environ.get("OPENAI_API_KEY")),
        "model": os.environ.get("OPENAI_MODEL", "missing")
    }

@app.get("/chat")
def chat():
    """
    Example:
      /chat?rid=123&cond=A&msg=hello
    """
    rid = request.args.get("rid", "missing")
    cond = request.args.get("cond", "missing")
    user_msg = request.args.get("msg", "").strip()

    if not user_msg:
        return (
            "Chat page. Provide msg in query string, e.g. /chat?rid=1&cond=A&msg=Hello",
            400,
        )

    if client is None:
        return (
            "OPENAI_API_KEY not set. Set it in environment variables to enable API.",
            500,
        )

    # ---- Cond-specific behavior ----
    # You can change these however you like.
    if cond == "A":
        system = (
            "You are an assistant providing an organic, neutral answer. "
            "Do NOT mention sponsorship."
        )
        instruction = "Answer the user's question clearly in 2-4 sentences."
    elif cond == "B":
        system = (
            "You are an assistant providing a sponsored-style answer. "
            "You MUST append exactly ' [sponsor]' at the end of your final sentence."
        )
        instruction = (
            "Answer the user's question persuasively in 2-4 sentences. "
            "End with ' [sponsor]'."
        )
    else:
        system = "You are a helpful assistant."
        instruction = "Answer normally in 2-4 sentences."

    prompt = (
        f"Session rid={rid}, cond={cond}.\n"
        f"User message: {user_msg}\n\n"
        f"Instruction: {instruction}"
    )

    # ---- Call OpenAI ----
    try:
        resp = client.responses.create(
            model=OPENAI_MODEL,
            input=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        text = resp.output_text
    except Exception as e:
        return jsonify({"error": "OpenAI call failed", "details": str(e)}), 500

    return jsonify(
        {
            "rid": rid,
            "cond": cond,
            "model": OPENAI_MODEL,
            "reply": text,
        }
    )


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
