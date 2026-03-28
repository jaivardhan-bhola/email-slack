import subprocess
import json
from groq import Groq
from dotenv import load_dotenv
import os

# 🔑 Load env
load_dotenv(override=True)
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ⚙️ Config
MAX_EMAILS = 50
BATCH_SIZE = 6

# 🔹 Fetch emails
cmd = [
    "composio", "execute", "GMAIL_FETCH_EMAILS",
    "-d", json.dumps({
        "query": "newer_than:1d -in:spam",
        "max_results": MAX_EMAILS
    })
]

result = subprocess.run(cmd, capture_output=True, text=True)

# Extract JSON safely
json_start = result.stdout.find("{")
data = json.loads(result.stdout[json_start:])
jsonpath = data["outputFilePath"]

with open(jsonpath, "r") as f:
    data = json.load(f)

emails = data["data"]["messages"]

print(f"📥 Total emails fetched: {len(emails)}")

# 🔹 Light filtering (ONLY obvious spam)
def is_noise(email):
    subject = (email.get("subject") or "").lower()

    hard_spam = [
        "sale", "discount", "offer", "promo",
        "unsubscribe", "free", "win"
    ]

    return any(k in subject for k in hard_spam)

# 🔹 Clean text
def shrink(text, n=800):
    return " ".join((text or "").split())[:n]

# 🔹 Prepare emails
filtered = []
for e in emails:
    if is_noise(e):
        continue

    filtered.append({
        "subject": shrink(e.get("subject", ""), 200),
        "body": shrink(e.get("messageText", ""), 800),
        "sender": e.get("sender", "").split("<")[0].strip()
    })

print(f"🧹 After filtering: {len(filtered)} emails")

important_emails = []

# 🔹 Batch processing with JSON output
for i in range(0, len(filtered), BATCH_SIZE):
    batch = filtered[i:i+BATCH_SIZE]

    prompt = f"""
You are an intelligent email classifier.

For each email, return JSON:

[
  {{"label": "IMPORTANT" or "NOT IMPORTANT", "summary": "..."}}
]

Rules:
- IMPORTANT: requires action, alerts, failures, deadlines, payments, personal messages
- NOT IMPORTANT: promotions, newsletters, ads, job alerts, OTP/login codes

Emails:
{json.dumps(batch, indent=2)}
"""

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=400,
        )

        raw_output = response.choices[0].message.content.strip()

        # 🔥 Extract JSON safely
        json_start = raw_output.find("[")
        json_end = raw_output.rfind("]") + 1
        parsed = json.loads(raw_output[json_start:json_end])

    except Exception as e:
        print("⚠️ LLM error:", e)
        continue

    for item, mail in zip(parsed, batch):
        if item.get("label") == "IMPORTANT":
            important_emails.append({
                "subject": mail["subject"],
                "sender": mail["sender"],
                "summary": item.get("summary", "")
            })

# 🔹 Fallback
if not important_emails:
    print("\n⚠️ No important emails detected.")
    print("Showing top 3 instead:\n")

    for mail in filtered[:3]:
        print(f"- {mail['subject']} ({mail['sender']})")

    exit()

# 🔹 Remove duplicates (important for Vercel spam)
seen = set()
unique_emails = []

for mail in important_emails:
    key = mail["subject"]
    if key not in seen:
        seen.add(key)
        unique_emails.append(mail)

important_emails = unique_emails

# 🔹 Build Slack message
summary_text = "🚀 *Important Emails (Last 24h)*\n"
summary_text += "────────────────────────\n\n"

for i, mail in enumerate(important_emails[:10], 1):
    subject_lower = mail["subject"].lower()

    if any(k in subject_lower for k in ["fail", "error", "alert"]):
        emoji = "🚨"
    elif any(k in subject_lower for k in ["payment", "order"]):
        emoji = "💰"
    else:
        emoji = "📌"

    summary_text += f"{emoji} *{mail['subject']}*\n"
    summary_text += f"   _{mail['summary']}_\n"
    summary_text += f"   👤 {mail['sender']}\n\n"

summary_text += "────────────────────────\n"
summary_text += f"📊 {len(important_emails)} important emails found"

summary_text = summary_text[:3000]

# 🔹 Send to Slack
payload = {
    "channel": "#gmail-summaries",
    "text": summary_text
}

cmd = [
    "composio", "execute", "SLACK_SEND_MESSAGE",
    "-d", json.dumps(payload)
]

result = subprocess.run(cmd, capture_output=True, text=True)

print("\n📤 Slack Response:")
print(result.stdout)
print(result.stderr)
