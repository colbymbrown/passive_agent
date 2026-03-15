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
from datetime import datetime
from pathlib import Path
from collections import deque
import requests
from dotenv import load_dotenv
from channels.telegram import TelegramChannel
from channels.base import BaseChannel

load_dotenv()

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

# --- LLM ---
OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "MichelRosselli/apertus"

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
    Path.home() / "iCloudDrive" / "iCloud~md~obsidian" / "Planner" / "TODO.md"
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
Respond directly and helpfully using the provided context (files, calendar,
feeds, current time) and any relevant conversation history.

Rules:
- Always produce a reply. Never output NOTHING.
- Keep replies short — the user reads these on a mobile device.
- Only reference information present in the provided context or history.
  Do not fabricate facts.
- You cannot take actions in the world. You can only compose a text reply.
""".strip()

SYSTEM_PROMPT_PUSH = """
You are a proactive personal assistant monitoring context on behalf of the user.
Decide whether there is anything in the current context worth interrupting the
user about right now.

{focus_instruction}

Output rules:
- If there is something genuinely worth surfacing (an upcoming event, an
  overdue task, an important update), output a SHORT message (2–4 sentences).
  The user reads this on a mobile phone.
- If there is nothing important, output exactly: NOTHING
- Do not repeat something already mentioned in recent conversation history
  unless circumstances have materially changed.
- Never fabricate information not present in the provided context.
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


def fetch_ical_events() -> str:
    parts = []
    for url in ICAL_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            lines   = r.text.splitlines()
            events  = []
            current = {}
            for line in lines:
                if line.startswith("BEGIN:VEVENT"):
                    current = {}
                elif line.startswith("SUMMARY:"):
                    current["summary"] = line[8:]
                elif line.startswith("DTSTART"):
                    current["start"] = line.split(":")[-1]
                elif line.startswith("END:VEVENT"):
                    if current:
                        events.append(f"  {current.get('start','?')} — {current.get('summary','?')}")
            if events:
                parts.append("--- Calendar events ---\n" + "\n".join(events[:20]))
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

def _history_block(history: deque) -> str:
    if not history:
        return ""
    lines = ["\n\n=== CONVERSATION HISTORY ==="]
    for role, text in history:
        lines.append(f"{'Agent' if role == 'agent' else 'User'}: {text}")
    return "\n".join(lines)


def build_chat_prompt(context: str, history: deque, new_user_messages: list[str]) -> str:
    parts = [SYSTEM_PROMPT_CHAT, "\n\n=== CURRENT CONTEXT ===\n", context]
    parts.append(_history_block(history))
    if new_user_messages:
        parts.append("\n\n=== NEW USER MESSAGES ===")
        for msg in new_user_messages:
            parts.append(f"User: {msg}")
    parts.append("\n\n=== YOUR RESPONSE ===")
    return "\n".join(parts)


def build_push_prompt(context: str, history: deque) -> str:
    focus = _FOCUS_WORK if is_work_hours() else _FOCUS_PERSONAL
    system = SYSTEM_PROMPT_PUSH.format(focus_instruction=focus)
    parts  = [system, "\n\n=== CURRENT CONTEXT ===\n", context]
    parts.append(_history_block(history))
    parts.append("\n\n=== YOUR RESPONSE ===\nOutput a short message for the user, or NOTHING.")
    return "\n".join(parts)

# ---------------------------------------------------------------------------
# LLM QUERY via Ollama
# ---------------------------------------------------------------------------

def query_llm(prompt: str) -> str:
    payload = {"model": OLLAMA_MODEL, "prompt": prompt, "stream": False}
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        print(f"[Ollama error] {e}")
        return "NOTHING"

# ---------------------------------------------------------------------------
# PUSH LOOP (daemon thread)
# ---------------------------------------------------------------------------

def push_loop(
    primary_channel: BaseChannel,
    history: deque,
    history_lock: threading.Lock,
) -> None:
    """
    Sleeps for PUSH_INTERVAL_SECONDS, then (if not in quiet hours) gathers
    context, asks the LLM whether there is anything worth surfacing, and sends
    a message to the primary channel if so.
    """
    while True:
        time.sleep(PUSH_INTERVAL_SECONDS)
        try:
            if is_quiet_hours():
                print("[Push] Quiet hours — skipping.")
                continue

            context = gather_context()

            with history_lock:
                snapshot = deque(history, maxlen=history.maxlen)

            prompt   = build_push_prompt(context, snapshot)
            response = query_llm(prompt)
            print(f"[Push] {response[:100]}{'...' if len(response) > 100 else ''}")

            if response.strip().upper() != "NOTHING" and response.strip():
                primary_channel.send(response)
                with history_lock:
                    history.append(("agent", response))

        except Exception as e:
            print(f"[Push loop error] {e}")

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
                prompt   = build_chat_prompt(context, snapshot, new_messages)
                response = query_llm(prompt)
                print(f"[Chat] Agent: {response[:100]}{'...' if len(response) > 100 else ''}")

                if response.strip():
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
    print(f"[Main] Agent running. Model: {OLLAMA_MODEL}. "
          f"Chat polls: [{poll_summary}]. Push interval: {PUSH_INTERVAL_SECONDS}s.")

    chat_loop(channels, history, history_lock)


if __name__ == "__main__":
    main()
