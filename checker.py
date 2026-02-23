import functions_framework
import os
from pymongo import MongoClient
from twilio.rest import Client
from datetime import datetime, timezone, timedelta

client  = MongoClient(os.environ["MONGODB_URI"])
db      = client["reminderbot"]
twilio  = Client(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"])
WA_FROM   = "whatsapp:" + os.environ["TWILIO_WHATSAPP_NUMBER"]
CALL_FROM = os.environ["TWILIO_CALL_NUMBER"]
ESCALATION_WAIT_MINS = 5

@functions_framework.http
def checker(request):
    now = datetime.now(timezone.utc)

    # 1. Pre-reminders
    for r in db.reminders.find(
        {"status": "pending", "pre_remind_at": {"$lte": now, "$ne": None}}):
        t = r["remind_at"].strftime("%I:%M %p")
        twilio.messages.create(
            from_=WA_FROM, to=r["user_phone"],
            body=f"Heads up! '{r['message']}' is at {t}.\n"
                 f"Reply 'done' if sorted, or 'not now' to push it.")
        db.reminders.update_one({"_id": r["_id"]}, {"$set": {"status": "pre_notified"}})

    # 2. Main reminders
    for r in db.reminders.find(
        {"status": {"$in": ["pending","pre_notified"]}, "remind_at": {"$lte": now}}):
        twilio.messages.create(
            from_=WA_FROM, to=r["user_phone"],
            body=f"Reminder now: {r['message']}\n"
                 f"Reply 'done' - I'll call in {ESCALATION_WAIT_MINS} mins if not.")
        db.reminders.update_one(
            {"_id": r["_id"]},
            {"$set": {"status": "notified", "notified_at": now}})

    # 3. Escalation
    cutoff = now - timedelta(minutes=ESCALATION_WAIT_MINS)
    for r in db.reminders.find(
        {"status": "notified", "notified_at": {"$lte": cutoff}}):
        hour = now.hour
        if hour >= 21 or hour < 7:
            continue
        phone = r["user_phone"].replace("whatsapp:", "")
        twilio.messages.create(
            from_=WA_FROM, to=r["user_phone"],
            body=f"No response - calling you now about:\n{r['message']}")
        twilio.calls.create(
            to=phone, from_=CALL_FROM,
         twiml='<Response><Say voice="alice">Hi, your reminder bot here. You need to: ' + r["message"] + '. I repeat: ' + r["message"] + '. Please reply done on WhatsApp.</Say></Response>')
        db.reminders.update_one({"_id": r["_id"]}, {"$set": {"status": "called"}})

    return "OK", 200