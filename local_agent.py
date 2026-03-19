"""
local_agent.py
--------------
A minimal, read-only autonomous agent that:
  - Gathers context from local sources (files, calendar feeds, RSS, etc.)
  - Reasons about that context using a local LLM via Ollama
  - Responds to user messages in whatever channel they arrived on (chat loop)
  - Sends proactive hourly reminders/updates to the primary channel (push loop)

Dependencies:
    pip install requests python-dotenv

Setup:
  1. Install and run Ollama: https://ollama.com
     Pull your model, e.g.: ollama pull mistral
  2. Configure your chosen channel (see channels/) and fill in .env (see .env.example).
  3. Run: python local_agent.py
"""

import os
import time
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from collections import deque
import requests
from dotenv import load_dotenv
from channels.telegram import TelegramChannel
from channels.base import BaseChannel

load_dotenv()

from backends import get_backend

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# --- LLM ---
# Supports LLM_BACKENDS (comma-separated priority list) and legacy LLM_BACKEND.
LLM_BACKENDS  = os.environ.get("LLM_BACKENDS") or os.environ.get("LLM_BACKEND", "ollama")
_llm_backend  = get_backend(LLM_BACKENDS)

# --- Push loop: how often to run the proactive check ---
PUSH_INTERVAL_SECONDS = 3600   # 1 hour

# --- History ---
MAX_HISTORY_TURNS = 10         # rolling window (each turn = 1 user + 1 agent entry)

# --- Quiet hours (24-hour local time) — push messages are suppressed in this window ---
# Set both to the same value to disable quiet hours entirely.
QUIET_HOURS_START = 22         # 10 PM
QUIET_HOURS_END   = 8          #  8 AM

# --- Work schedule — controls push message framing, not suppression ---
WORK_DAYS       = {0, 1, 2, 3, 4}  # Monday=0 … Friday=4
WORK_HOUR_START = 9
WORK_HOUR_END   = 17

# --- Data sources ---
WATCHED_FILES = [
    #Path.home() / "iCloudDrive" / "iCloud~md~obsidian" / "Planner" / "TODO.md"
    Path.home() / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents" / "Planner" / "TODO.md"
    # Path.home() / "notes" / "todo.txt",
]

ICAL_URLS = [u for k, u in sorted(os.environ.items()) if k.startswith("ICAL_URL_")]

RSS_URLS = [
    # "https://example.com/feed.rss",
]

# ---------------------------------------------------------------------------
# SYSTEM PROMPTS
# ---------------------------------------------------------------------------

SYSTEM_PROMPT_CHAT = """
You are a concise personal assistant. The user has just sent you a message.
Respond directly and helpfully using the provided context, as well as any 
relevant conversation history.

Rules:
- Always produce a reply.
- Keep replies short — the user reads these on a mobile device.
- Only reference information present in the provided context or history.
  Do not fabricate facts.
- You cannot take actions in the world. You can only compose a text reply.
""".strip()

SYSTEM_PROMPT_PUSH = """
You are a proactive personal assistant, whose role is to remind the user of important tasks.
Pick the single most important item from the current context to remind the user about right now.

{focus_instruction}

Output rules:
- Always output a SHORT message (2–4 sentences).
- Pick one item: an upcoming event, a task, a deadline, or anything else
  that deserves attention. If nothing is urgent, pick whatever is most
  actionable or timely.  Do not assume that either the first or last item 
  you see is the most important.
- Do not repeat something already mentioned in recent conversation history
  unless it is becoming more urgent.
- Never fabricate information not present in the provided context.  Do not
  make assumptions about meetings associated with tasks, or deadline dates
  and times not specifically mentioned.
""".strip()

_FOCUS_WORK = (
    "It is currently a work day during work hours. Focus on work-relevant reminders: "
    "upcoming meetings, deadlines, and tasks in TODO files related to work. "
    "Deprioritize personal or lifestyle topics."
)
_FOCUS_PERSONAL = (
    "It is currently outside work hours. Focus on personal reminders: health, "
    "social commitments, errands, and upcoming non-work calendar events. "
    "Deprioritize work topics."
)

# ---------------------------------------------------------------------------
# SCHEDULE HELPERS
# ---------------------------------------------------------------------------

def is_quiet_hours() -> bool:
    """True if the current local hour falls inside the configured quiet window."""
    hour = datetime.now().hour
    if QUIET_HOURS_START == QUIET_HOURS_END:
        return False
    if QUIET_HOURS_START > QUIET_HOURS_END:    # window spans midnight
        return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END
    return QUIET_HOURS_START <= hour < QUIET_HOURS_END


def is_work_hours() -> bool:
    """True if the current local time is within the configured work schedule."""
    now = datetime.now()
    return now.weekday() in WORK_DAYS and WORK_HOUR_START <= now.hour < WORK_HOUR_END

# ---------------------------------------------------------------------------
# CONTEXT GATHERING — all read-only
# ---------------------------------------------------------------------------

def read_watched_files() -> str:
    parts = []
    for path in WATCHED_FILES:
        p = Path(path)
        if p.exists() and p.is_file():
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
                parts.append(f"--- File: {p.name} ---\n{content.strip()}")
            except Exception as e:
                parts.append(f"--- File: {p.name} [read error: {e}] ---")
    return "\n\n".join(parts)


def _parse_ical_dt(value: str) -> datetime | None:
    """Parse an iCal DTSTART/DTEND value into a naive local datetime."""
    value = value.strip()
    try:
        if len(value) == 8:                        # all-day: 20191217
            return datetime.strptime(value, "%Y%m%d")
        if value.endswith("Z"):                    # UTC: 20191217T150000Z
            dt = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        return datetime.strptime(value[:15], "%Y%m%dT%H%M%S")  # local / TZID
    except Exception:
        return None


def fetch_ical_events() -> str:
    today    = datetime.now().date()
    tomorrow = today + timedelta(days=1)
    cutoff   = today  # include today and tomorrow only

    parts = []
    for url in ICAL_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            lines   = r.text.splitlines()
            events  = []
            current: dict = {}
            for line in lines:
                if line.startswith("BEGIN:VEVENT"):
                    current = {}
                elif line.startswith("SUMMARY:"):
                    current["summary"] = line[8:]
                elif line.startswith("DTSTART"):
                    current["start_raw"] = line.split(":")[-1]
                elif line.startswith("END:VEVENT"):
                    if not current:
                        continue
                    dt = _parse_ical_dt(current.get("start_raw", ""))
                    if dt is None:
                        continue
                    event_date = dt.date()
                    if event_date not in (today, tomorrow):
                        continue
                    label = "Today" if event_date == today else "Tomorrow"
                    # all-day events have no time component
                    if current.get("start_raw", "").isdigit() and len(current["start_raw"]) == 8:
                        time_str = f"{label} (all day)"
                    else:
                        time_str = f"{label} at {dt.strftime('%I:%M %p').lstrip('0')}"
                    events.append(f"  {time_str} — {current.get('summary', '?')}")
            if events:
                parts.append("--- Upcoming calendar events (today & tomorrow) ---\n" + "\n".join(events))
        except Exception as e:
            parts.append(f"--- Calendar feed error: {e} ---")
    return "\n\n".join(parts)


def fetch_rss_headlines() -> str:
    import re
    parts = []
    for url in RSS_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            items  = re.findall(r"<item>.*?</item>", r.text, re.DOTALL)
            titles = []
            for item in items[:10]:
                m = re.search(r"<title>(.*?)</title>", item, re.DOTALL)
                if m:
                    titles.append("  - " + m.group(1).strip())
            if titles:
                parts.append(f"--- RSS: {url} ---\n" + "\n".join(titles))
        except Exception as e:
            parts.append(f"--- RSS feed error ({url}): {e} ---")
    return "\n\n".join(parts)


def gather_context() -> str:
    sections = [f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    for fn in (read_watched_files, fetch_ical_events, fetch_rss_headlines):
        chunk = fn()
        if chunk:
            sections.append(chunk)

    if len(sections) == 1:
        sections.append("(No context sources are configured yet.)")

    return "\n\n".join(sections)

# ---------------------------------------------------------------------------
# PROMPT BUILDERS
# ---------------------------------------------------------------------------

def _history_to_messages(history: deque) -> list[dict]:
    role_map = {"user": "user", "agent": "assistant"}
    return [{"role": role_map[role], "content": text} for role, text in history]


def build_chat_prompt(context: str, history: deque, new_user_messages: list[str]) -> tuple[str, list[dict]]:
    system_prompt = SYSTEM_PROMPT_CHAT + "\n\n=== CURRENT CONTEXT ===\n" + context
    messages = _history_to_messages(history)
    for msg in new_user_messages:
        messages.append({"role": "user", "content": msg})
    return system_prompt, messages


def build_push_prompt(context: str, history: deque) -> tuple[str, list[dict]]:
    focus = _FOCUS_WORK if is_work_hours() else _FOCUS_PERSONAL
    system_prompt = SYSTEM_PROMPT_PUSH.format(focus_instruction=focus) + "\n\n=== CURRENT CONTEXT ===\n" + context
    messages = _history_to_messages(history)
    messages.append({"role": "user", "content": "What should I be aware of right now?"})
    return system_prompt, messages

# ---------------------------------------------------------------------------
# LLM QUERY
# ---------------------------------------------------------------------------

def query_llm(system_prompt: str, messages: list[dict]) -> str | None:
    print(f"[LLM system] {system_prompt[:120]}{'...' if len(system_prompt) > 120 else ''}")
    print(f"[LLM messages] {len(messages)} message(s)")
    return _llm_backend.query(system_prompt, messages)

# ---------------------------------------------------------------------------
# PUSH LOOP (daemon thread)
# ---------------------------------------------------------------------------

PUSH_RETRY_SECONDS = 300   # retry a failed push after 5 minutes


def push_loop(
    primary_channel: BaseChannel,
    history: deque,
    history_lock: threading.Lock,
) -> None:
    """
    Sleeps for PUSH_INTERVAL_SECONDS, then (if not in quiet hours) gathers
    context, asks the LLM whether there is anything worth surfacing, and sends
    a message to the primary channel if so.  On failure, retries after
    PUSH_RETRY_SECONDS rather than waiting the full interval again.
    """
    next_push = time.monotonic() + PUSH_INTERVAL_SECONDS

    while True:
        try:
            if is_quiet_hours():
                print("[Push] Quiet hours — skipping.")
                next_push = time.monotonic() + PUSH_INTERVAL_SECONDS
                continue

            context = gather_context()

            with history_lock:
                snapshot = deque(history, maxlen=history.maxlen)

            system_prompt, messages = build_push_prompt(context, snapshot)
            response = query_llm(system_prompt, messages)

            if not response:
                print("[Push] LLM returned nothing — will retry in "
                      f"{PUSH_RETRY_SECONDS // 60} min.")
                next_push = time.monotonic() + PUSH_RETRY_SECONDS
                continue

            print(f"[Push] {response[:100]}{'...' if len(response) > 100 else ''}")
            primary_channel.send(response)
            with history_lock:
                history.append(("agent", response))
            next_push = time.monotonic() + PUSH_INTERVAL_SECONDS

        except Exception as e:
            print(f"[Push loop error] {e} — will retry in "
                  f"{PUSH_RETRY_SECONDS // 60} min.")
            next_push = time.monotonic() + PUSH_RETRY_SECONDS

        sleep_for = max(0, next_push - time.monotonic())
        time.sleep(sleep_for)

# ---------------------------------------------------------------------------
# CHAT LOOP (main thread)
# ---------------------------------------------------------------------------

def chat_loop(
    channels: list[BaseChannel],
    history: deque,
    history_lock: threading.Lock,
) -> None:
    """
    Polls each channel on its own schedule (channel.poll_interval).
    When a user message arrives, responds on the same channel it came from.
    """
    last_polled = {id(ch): 0.0 for ch in channels}

    while True:
        try:
            now = time.monotonic()
            for channel in channels:
                if now - last_polled[id(channel)] < channel.poll_interval:
                    continue
                last_polled[id(channel)] = now

                new_messages = channel.get_updates()
                if not new_messages:
                    continue

                with history_lock:
                    for msg in new_messages:
                        print(f"[Chat] User ({channel.__class__.__name__}): {msg}")
                        history.append(("user", msg))
                    snapshot = deque(history, maxlen=history.maxlen)

                context  = gather_context()
                system_prompt, messages = build_chat_prompt(context, snapshot, new_messages)
                response = query_llm(system_prompt, messages)
                if not response:
                    print("[Chat] LLM returned nothing — skipping reply.")
                    continue

                print(f"[Chat] Agent: {response[:100]}{'...' if len(response) > 100 else ''}")
                channel.send(response)
                with history_lock:
                    history.append(("agent", response))

        except Exception as e:
            print(f"[Chat loop error] {e}")

        time.sleep(1)

# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    telegram = TelegramChannel()
    telegram.on_startup()

    channels        = [telegram]   # all channels polled by the chat loop
    primary_channel = telegram     # where push notifications are sent

    history      = deque(maxlen=MAX_HISTORY_TURNS * 2)
    history_lock = threading.Lock()

    push_thread = threading.Thread(
        target=push_loop,
        args=(primary_channel, history, history_lock),
        daemon=True,
        name="push-loop",
    )
    push_thread.start()
    poll_summary = ", ".join(
        f"{ch.__class__.__name__}={ch.poll_interval}s" for ch in channels
    )
    print(f"[Main] Agent running. Backends: {LLM_BACKENDS}. "
          f"Chat polls: [{poll_summary}]. Push interval: {PUSH_INTERVAL_SECONDS}s.")

    chat_loop(channels, history, history_lock)


if __name__ == "__main__":
    main()
