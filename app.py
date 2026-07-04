import os
import base64
import json
import re
import requests
from datetime import datetime
import pytz

from flask import Flask, request
from twilio.twiml.messaging_response import MessagingResponse
import redis
import openai

app = Flask(__name__)

# === CONFIG ===
TWILIO_SID = os.getenv("TWILIO_SID")
TWILIO_TOKEN = os.getenv("TWILIO_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_KEY")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
PORT = int(os.getenv("PORT", "8000"))

openai.api_key = OPENAI_KEY
r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
KENYA_TZ = pytz.timezone('Africa/Nairobi')

DAILY_LIMIT = 5
ONBOARDING_MSG = """Hey! 👋 I'm your KCSE Math Tutor

Stuck on homework? Send me ANY math question and I'll teach you step-by-step.

You get 5 free questions every day 📚
Need unlimited? Reply UPGRADE for KES 49/week

Try me now: Send a photo of any Form 1-4 math question 👇"""

DAILY_LIMIT_MSG = """Free limit reached for today ⏰

You've used all 5 free questions. Resets at 8:00am tomorrow.

Want unlimited now?
Reply UPGRADE for KES 49/week → M-Pesa prompt sent instantly"""

# === REDIS HELPERS ===
def get_today_key(phone):
    today = datetime.now(KENYA_TZ).strftime('%Y-%m-%d')
    return f"user:{phone}:questions:{today}"

def check_daily_limit(phone):
    count = int(r.get(get_today_key(phone)) or 0)
    return count < DAILY_LIMIT, DAILY_LIMIT - count

def use_question(phone):
    key = get_today_key(phone)
    r.incr(key)
    r.expire(key, 86400)

def save_message(phone, role, content):
    key = f"user:{phone}:history"
    message = {"role": role, "content": content}
    r.rpush(key, json.dumps(message))
    r.ltrim(key, -10, -1)
    r.expire(key, 3600)

def get_history(phone):
    key = f"user:{phone}:history"
    messages = r.lrange(key, 0, -1)
    return [json.loads(m) for m in messages]

# === IMAGE + AI HELPERS ===
def download_image_from_twilio(media_url):
    response = requests.get(media_url, auth=(TWILIO_SID, TWILIO_TOKEN))
    return response.content

def image_to_base64(image_bytes):
    return base64.b64encode(image_bytes).decode('utf-8')

def parse_gpt_vision_output(gpt_text):
    result = {"question": "", "form": "Form 3", "topic": "Algebra"}
    lines = gpt_text.strip().split('\n')

    for line in lines:
        line_lower = line.strip().lower()
        if "question" in line_lower and ":" in line:
            result["question"] = line.split(":", 1)[1].strip()
        elif "form" in line_lower and ":" in line:
            form_match = re.search(r'form\s*(\d)', line_lower)
            if form_match:
                result["form"] = f"Form {form_match.group(1)}"
        elif "topic" in line_lower and ":" in line:
            result["topic"] = line.split(":", 1)[1].strip().title()

    if not result["question"]:
        result["question"] = gpt_text[:200]

    result["topic_info"] = f"{result['form']} - {result['topic']}"
    return result

def read_math_from_image(media_url):
    image_bytes = download_image_from_twilio(media_url)
    base64_image = image_to_base64(image_bytes)

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "system",
                "content": "You are a KCSE math question reader. Output EXACTLY:\n1. Exact question text: [question]\n2. Form level: Form [1-4]\n3. Topic name: [topic]\nIf handwriting is unclear, output: UNCLEAR"
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Read this math question"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}", "detail": "high"}}
                ]
            }
        ],
        max_tokens=200,
        temperature=0.1
    )

    gpt_output = response.choices[0].message.content
    if "UNCLEAR" in gpt_output.upper():
        return {"error": "UNCLEAR"}
    return parse_gpt_vision_output(gpt_output)

def tutor_step_by_step(phone, user_input, is_new_question=False):
    history = get_history(phone)
    current_topic = r.get(f"user:{phone}:current_topic") or "Math"

    messages = [
        {"role": "system", "content": "You are a KCSE math tutor for Kenyan students. Use Socratic method: 1 step at a time, 3 lines max, no final answer. Ask 1 question. Be encouraging. Use simple English."}
    ]
    messages.extend(history)

    if is_new_question:
        prompt = f"New question. Topic: {current_topic}. Question: {user_input}. Give only Step 1 to start solving."
    else:
        prompt = f"Student reply: {user_input}. Continue to next step or confirm if correct and give next step."

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=messages + [{"role": "user", "content": prompt}],
        max_tokens=150
    )

    ai_reply = response.choices[0].message.content
    save_message(phone, "user", user_input)
    save_message(phone, "assistant", ai_reply)
    return ai_reply

# === WEBHOOK ===
@app.route("/whatsapp", methods=['POST'])
def whatsapp_webhook():
    incoming_msg = request.form.get('Body', '').strip()
    phone = request.form.get('From', '').replace('whatsapp:', '').replace('+', '')
    media_url = request.form.get('MediaUrl0')

    resp = MessagingResponse()
    msg = resp.message()

    # Onboarding for first message
    if not r.exists(f"user:{phone}:seen"):
        r.setex(f"user:{phone}:seen", 86400 * 30, "1")
        msg.body(ONBOARDING_MSG)
        return str(resp)

    # Check daily limit
    can_ask, left = check_daily_limit(phone)
    if not can_ask:
        msg.body(DAILY_LIMIT_MSG)
        return str(resp)

    try:
        current_topic = r.get(f"user:{phone}:current_topic")
        is_new_question = media_url is not None or current_topic is None

        if media_url:
            result = read_math_from_image(media_url)
            if "error" in result:
                msg.body("Photo is blurry 😅 Can you retake with better light? Write question clearly.")
                return str(resp)

            question_text = result["question"]
            topic_info = result["topic_info"]
            r.setex(f"user:{phone}:current_topic", 1800, topic_info)
            r.delete(f"user:{phone}:history")
            reply = tutor_step_by_step(phone, question_text, is_new_question=True)
            msg.body(f"Got it: {topic_info} ✅\n{reply}\n\nQuestions left: {left - 1}")
        else:
            if incoming_msg.upper() == "UPGRADE":
                msg.body("M-Pesa STK coming soon! For now, 5 free Qs reset at 8am daily.")
                return str(resp)

            reply = tutor_step_by_step(phone, incoming_msg, is_new_question=False)
            msg.body(f"{reply}\n\nQuestions left: {left - 1}")

        use_question(phone)

    except Exception as e:
        print(f"Error: {e}")
        msg.body("Something went wrong. Try sending photo again or type question.")

    return str(resp)

@app.route("/", methods=['GET'])
def health():
    return "KCSE Math Bot is running ✅"

if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=PORT)
