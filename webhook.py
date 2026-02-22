import functions_framework
import uuid, os, re
from flask import Request
from pymongo import MongoClient
from twilio.twiml.messaging_response import MessagingResponse
from twilio.rest import Client
from datetime import datetime, timezone, timedelta
import dateparser
from reminder_parser import parse_reminder, TRIGGER_WORDS

client = MongoClient(os.environ["MONGODB_URI"])
db     = client["reminderbot"]
twilio = Client(os.environ["TWILIO_SID"], os.environ["TWILIO_TOKEN"])
WA_FROM = "whatsapp:" + os.environ["TWILIO_WHATSAPP_NUMBER"]

HELP_TEXT = (
    "Here's what you can say:\n\n"
    "Remind me at 3pm to call John\n"
    "Meeting with Sarah at 2:30\n"
    "Doctor appointment tomorrow at 10am\n"
    "Call with team in 20 minutes\n\n"
    "Commands:\n"
    "your day - see today's schedule\n"
    "done / ok - dismiss latest reminder\n"
    "not now - push reminder to a new time\n"
    "cancel [keyword] - cancel a reminder\n"
    "help - show this list"
)

@functions_framework.http
def webhook(request: Request):
    body        = request.form.get("Body", "").strip()
    from_number = request.form.get("From", "")
    resp        = MessagingResponse()
    lower       = body.lower()
    now         = datetime.now(timezone.utc)

    # Session check
    session = db.sessions.find_one({"user_phone": from_number})
    if session:
        if session.get("expires_at") and session["expires_at"] < now:
            db.sessions.delete_one({"user_phone": from_number})
        elif session.get("state") == "awaiting_snooze_time":
            new_time = dateparser.parse(body, settings={
                "PREFER_DATES_FROM": "future",
                "RETURN_AS_TIMEZONE_AWARE": True})
            if not new_time:
                resp.message("Didn't catch that. Try '3pm' or 'in 2 hours'.")
                return str(resp)
            reminder = db.reminders.find_one({"_id": session["reminder_id"]})
            db.reminders.update_one(
                {"_id": session["reminder_id"]},
                {"$set": {"remind_at": new_time, "pre_remind_at": None,
                          "status": "pending",
                          "snooze_count": reminder.get("snooze_count", 0) + 1}})
            db.sessions.delete_one({"user_phone": from_number})
            t = new_time.strftime("%I:%M %p")
            resp.message(f"Got it - reminding you about '{reminder['message']}' at {t}")
            return str(resp)

    # Help
    if lower in ("help", "hi", "hello", "start", "hey"):
        resp.message(HELP_TEXT)
        return str(resp)

    # Acknowledgement
    ack_words = {"ok","done","got it","seen","thanks","noted","ack","yes"}
    if any(w in lower for w in ack_words) and "remind" not in lower:
        reminder = db.reminders.find_one(
            {"user_phone": from_number, "status": {"$in": ["notified","pre_notified"]}},
            sort=[("remind_at", 1)])
        if reminder:
            db.reminders.update_one({"_id": reminder["_id"]}, {"$set": {"status": "acknowledged"}})
            resp.message(f"Done! '{reminder['message']}' marked complete.")
        else:
            resp.message("No active reminders to dismiss.")
        return str(resp)

    # Not now
    if lower in ("not now","skip","push","later","busy"):
        reminder = db.reminders.find_one(
            {"user_phone": from_number, "status": {"$in": ["notified","pre_notified"]}},
            sort=[("remind_at", 1)])
        if reminder:
            expires = now + timedelta(minutes=10)
            db.sessions.update_one(
                {"user_phone": from_number},
                {"$set": {"state": "awaiting_snooze_time",
                          "reminder_id": reminder["_id"],
                          "expires_at": expires}},
                upsert=True)
            resp.message("When should I remind you?\nSay '3pm' or 'in 2 hours'")
        else:
            resp.message("No active reminder to push.")
        return str(resp)

    # Your day
    if lower in ("your day","my day","today","schedule","whats next","what's next"):
        day_end = now.replace(hour=23, minute=59, second=59)
        reminders = list(db.reminders.find(
            {"user_phone": from_number,
             "status": {"$in": ["pending","pre_notified"]},
             "remind_at": {"$gte": now, "$lte": day_end}},
            sort=[("remind_at", 1)]))
        if reminders:
            items = []
            for r in reminders:
                t = r["remind_at"].strftime("%I:%M %p")
                pushed = " (pushed)" if r.get("snooze_count", 0) > 0 else ""
                items.append(f"{t} - {r['message']}{pushed}")
            resp.message("Your day from now:\n\n" + "\n".join(items))
        else:
            resp.message("Nothing else scheduled for today!")
        return str(resp)

    # Cancel
    cancel_match = re.match(r"cancel (.+)", lower)
    if cancel_match:
        keyword = cancel_match.group(1).strip()
        reminders = list(db.reminders.find(
            {"user_phone": from_number, "status": {"$in": ["pending","pre_notified"]}}))
        cancelled = []
        for r in reminders:
            if keyword in r["message"].lower():
                db.reminders.update_one({"_id": r["_id"]}, {"$set": {"status": "cancelled"}})
                cancelled.append(r["message"])
        if cancelled:
            resp.message("Cancelled:\n" + "\n".join(f"- {m}" for m in cancelled))
        else:
            resp.message(f"No reminders found matching '{keyword}'")
        return str(resp)

    # New reminder
    if any(w in lower for w in TRIGGER_WORDS):
        user_tz = os.environ.get("USER_TIMEZONE", "Asia/Kolkata")
        task, remind_at, pre_remind_at, event_type = parse_reminder(body, user_tz)
        if not remind_at:
            resp.message("Couldn't get the time. Try: 'Remind me at 3pm to call John'")
            return str(resp)
        db.reminders.insert_one({
            "user_phone": from_number, "message": task,
            "event_type": event_type, "remind_at": remind_at,
            "pre_remind_at": pre_remind_at, "status": "pending",
            "notified_at": None, "snooze_count": 0,
            "created_at": datetime.now(timezone.utc),
        })
        t = remind_at.strftime("%b %d at %I:%M %p")
        pre = "\nI'll heads-up you 10 mins before too." if pre_remind_at else ""
        resp.message(f"Got it!\n{t}\n{task}{pre}")
        return str(resp)

    resp.message(HELP_TEXT)
    return str(resp)