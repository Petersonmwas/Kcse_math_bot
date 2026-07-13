```python
from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import os
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "kcse123")

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    if request.method == "GET":
        if request.args.get("hub.verify_token") == VERIFY_TOKEN:
            return request.args.get("hub.challenge")
        return "Verification failed", 403

    resp = MessagingResponse()
    msg = request.form.get("Body", "").lower()
    
    if "hi" in msg:
        resp.message("Welcome back 🔥 I'm your KCSE Math Tutor. Send me a math question or a photo of a problem!")
    else:
        resp.message(f"You said: {msg}. I'm ready to help with KCSE math!")
    
    return str(resp)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
```
