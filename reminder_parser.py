import re
import dateparser
from datetime import datetime, timezone, timedelta

TRIGGER_WORDS = ["remind", "meeting", "call", "appointment",
                 "catch up", "standup", "sync", "doctor",
                 "dentist", "interview", "lunch"]

PRE_REMINDER_DEFAULTS = {
    "meeting": 10, "call": 5, "appointment": 15,
    "doctor": 15, "dentist": 15, "interview": 15, "default": 10,
}

def detect_type(text):
    text = text.lower()
    for key in PRE_REMINDER_DEFAULTS:
        if key in text:
            return key
    return "default"

def parse_reminder(text, user_timezone="UTC"):
    text = text.strip()
    event_type = detect_type(text)
    task_match = re.search(r"\bto\b (.+)$", text, re.IGNORECASE)
    task = task_match.group(1).strip() if task_match else text
    time_text = re.sub(r"remind me", "", text, flags=re.IGNORECASE)
    time_text = re.sub(r"\bto\b.+$", "", time_text, flags=re.IGNORECASE).strip()
    remind_at = dateparser.parse(
        time_text,
        settings={"PREFER_DATES_FROM": "future",
                  "RETURN_AS_TIMEZONE_AWARE": True,
                  "TIMEZONE": user_timezone}
    )
    if not remind_at:
        return task, None, None, event_type
    if remind_at.hour == 0 and remind_at.minute == 0:
        remind_at = remind_at.replace(hour=9, minute=0, second=0)
    pre_mins = PRE_REMINDER_DEFAULTS.get(event_type, 10)
    pre_remind_at = remind_at - timedelta(minutes=pre_mins)
    now = datetime.now(timezone.utc)
    if pre_remind_at <= now:
        pre_remind_at = None
    return task, remind_at, pre_remind_at, event_type
