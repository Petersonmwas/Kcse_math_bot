from flask import Flask, request, jsonify
import os
import openai
import json
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "kcse123")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    # Verification for Meta
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        return "Verification failed", 403

    # Handle incoming messages
    if request.method == "POST":
        data = request.get_json()

        try:
            # Get message from WhatsApp
            message = data["entry"][0]["changes"][0]["value"]["messages"][0]
            from_number = message["from"]
            msg_body = message["text"]["body"]

            print(f"Received: {msg_body} from {from_number}")

            # Simple reply for now - we will add GPT + Sheets next
            reply_text = f"Hello! You said: {msg_body}\n\nI'm your KCSE Math Tutor 🔥 Send me a math question."

        except Exception as e:
            print(f"Error: {e}")
            reply_text = "Sorry, I couldn't process that."

        # WhatsApp Cloud API expects 200 OK
        return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
