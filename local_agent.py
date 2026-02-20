"""
local_agent.py
--------------
A minimal, read-only autonomous agent that:
  - Gathers context from local sources (files, calendar feeds, RSS, etc.)
  - Reasons about that context using a local LLM via Ollama
  - Sends reminders/questions to you via Telegram
  - Reads your Telegram replies and incorporates them into the next reasoning cycle

Dependencies:
    pip install requests

Setup:
  1. Install and run Ollama: https://ollama.com
     Pull your model, e.g.: ollama pull mistral
  2. Create a Telegram bot via BotFather (@BotFather in Telegram), get your API token.
  3. Find your Telegram chat ID: message your bot, then visit
     https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
     and look for "chat": {"id": ...}
  4. Fill in the CONFIG section below.
  5. Run: python local_agent.py
"""

import requests
import time
import json
from datetime import datetime
from pathlib import Path
from collections import deque

# ---------------------------------------------------------------------------
# CONFIG — fill these in before running
# ---------------------------------------------------------------------------

TELEGRAM_TOKEN   = "YOUR_BOT_TOKEN_HERE"          # from BotFather
TELEGRAM_CHAT_ID = "YOUR_CHAT_ID_HERE"            # your personal chat ID

OLLAMA_URL       = "http://localhost:11434/api/generate"
OLLAMA_MODEL     = "mistral"                       # or whatever local model you use

POLL_INTERVAL_SECONDS = 300                        # how often the agent runs (5 min default)
MAX_HISTORY_TURNS     = 10                         # rolling dialogue window (pairs of messages)

# Paths/URLs the agent is allowed to READ. Add or remove as you like.
WATCHED_FILES    = [
    # Path.home() / "notes" / "todo.txt",
    # Path.home() / "notes" / "journal.txt",
]

ICAL_URLS        = [
    # "https://calendar.google.com/calendar/ical/your_feed_here/basic.ics",
]

RSS_URLS         = [
    # "https://example.com/feed.rss",
]

# ---------------------------------------------------------------------------
# SYSTEM PROMPT — this constrains the agent's behaviour
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """
You are a read-only assistant monitoring context on behalf of the user.
Your sole job is to notice things in the provided context that are worth
bringing to the user's attention, or to respond helpfully to something
the user said in a previous message.

Rules you must follow:
- You cannot take any actions in the world. You have no tools.
- Your only output is either a SHORT message to send to the user, or
  exactly the single word NOTHING (in capitals) if there is nothing
  worth saying right now.
- Be concise. The user reads these on a mobile phone.
- If the user has replied to a previous message, acknowledge it and
  reason about it before deciding whether to send a new message.
- Never fabricate information that is not in the provided context.
""".strip()

# ---------------------------------------------------------------------------
# CONTEXT GATHERING — all read-only
# ---------------------------------------------------------------------------

def read_watched_files() -> str:
    """Read local text files the user has opted in to monitoring."""
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
    """Fetch and naively extract upcoming event lines from iCal feeds."""
    parts = []
    for url in ICAL_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            # Very lightweight parsing — just pull SUMMARY and DTSTART lines
            lines = r.text.splitlines()
            events = []
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
    """Fetch and extract titles from RSS feeds."""
    parts = []
    for url in RSS_URLS:
        try:
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            # Lightweight extraction of <title> tags inside <item> blocks
            import re
            items = re.findall(r"<item>.*?</item>", r.text, re.DOTALL)
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
    """Assemble all context into a single string to pass to the LLM."""
    sections = [f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M')}"]

    files_ctx = read_watched_files()
    if files_ctx:
        sections.append(files_ctx)

    ical_ctx = fetch_ical_events()
    if ical_ctx:
        sections.append(ical_ctx)

    rss_ctx = fetch_rss_headlines()
    if rss_ctx:
        sections.append(rss_ctx)

    if len(sections) == 1:
        sections.append("(No context sources are configured yet.)")

    return "\n\n".join(sections)

# ---------------------------------------------------------------------------
# TELEGRAM — send and receive
# ---------------------------------------------------------------------------

def telegram_send(text: str) -> None:
    """Send a message to the user via Telegram."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        print(f"[Telegram send error] {e}")


def telegram_get_updates(offset: int) -> tuple[list[dict], int]:
    """
    Poll Telegram for new messages from the user.
    Returns (list of message texts, new offset).
    Only accepts messages from the configured chat ID for safety.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"offset": offset, "timeout": 5}
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[Telegram poll error] {e}")
        return [], offset

    messages = []
    new_offset = offset
    for update in data.get("result", []):
        new_offset = max(new_offset, update["update_id"] + 1)
        msg = update.get("message", {})
        # Only accept messages from the authorised chat
        if str(msg.get("chat", {}).get("id", "")) == str(TELEGRAM_CHAT_ID):
            text = msg.get("text", "").strip()
            if text:
                messages.append(text)

    return messages, new_offset

# ---------------------------------------------------------------------------
# LLM QUERY via Ollama
# ---------------------------------------------------------------------------

def build_prompt(context: str, history: deque, new_user_messages: list[str]) -> str:
    """
    Construct the full prompt string.
    History is a deque of ("agent"|"user", text) tuples.
    """
    parts = [SYSTEM_PROMPT, "\n\n=== CURRENT CONTEXT ===\n", context]

    if history:
        parts.append("\n\n=== CONVERSATION HISTORY ===")
        for role, text in history:
            label = "Agent" if role == "agent" else "User"
            parts.append(f"{label}: {text}")

    if new_user_messages:
        parts.append("\n\n=== NEW USER REPLIES ===")
        for msg in new_user_messages:
            parts.append(f"User: {msg}")

    parts.append("\n\n=== YOUR RESPONSE ===\nRespond with either a concise message to the user, or the single word NOTHING.")
    return "\n".join(parts)


def query_llm(prompt: str) -> str:
    """Send a prompt to the local Ollama instance and return the response text."""
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }
    try:
        r = requests.post(OLLAMA_URL, json=payload, timeout=120)
        r.raise_for_status()
        return r.json().get("response", "").strip()
    except Exception as e:
        print(f"[Ollama error] {e}")
        return "NOTHING"

# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------

def main():
    print(f"Agent starting. Model: {OLLAMA_MODEL}. Poll interval: {POLL_INTERVAL_SECONDS}s.")
    telegram_send("Agent started. I will message you if I notice something worth your attention.")

    history: deque = deque(maxlen=MAX_HISTORY_TURNS * 2)  # each turn = 2 entries
    telegram_offset = 0

    # Drain any old pending Telegram messages on startup so we don't
    # re-process messages from before the agent launched.
    _, telegram_offset = telegram_get_updates(telegram_offset)

    while True:
        try:
            # 1. Collect any replies the user sent since last cycle
            new_user_messages, telegram_offset = telegram_get_updates(telegram_offset)
            for msg in new_user_messages:
                print(f"[User replied] {msg}")
                history.append(("user", msg))

            # 2. Gather read-only context
            context = gather_context()

            # 3. Build prompt and query the LLM
            prompt = build_prompt(context, history, [])
            response = query_llm(prompt)
            print(f"[LLM response] {response[:120]}{'...' if len(response) > 120 else ''}")

            # 4. Send message if the LLM decided to
            if response.upper() != "NOTHING" and response:
                telegram_send(response)
                history.append(("agent", response))

        except Exception as e:
            print(f"[Main loop error] {e}")

        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
