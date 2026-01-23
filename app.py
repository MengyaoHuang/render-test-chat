from flask import Flask, request
import os

app = Flask(__name__)

@app.get("/")
def home():
    return "Render is live ✅"

@app.get("/chat")
def chat():
    rid = request.args.get("rid", "missing")
    cond = request.args.get("cond", "missing")
    return f"Chat page. rid={rid}, cond={cond}"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
