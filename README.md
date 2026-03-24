# passive_agent

A lightweight personal assistant that watches local files, calendar feeds, and RSS sources, then sends you proactive reminders and responds to your chat messages via Telegram and/or Slack. It runs entirely on your machine and routes push notifications to whichever channel fits the time of day.

---

## Quick start

**Requirements:** Python 3.11+, pip

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and fill in the config
cp .env.example .env
# Edit .env with your tokens (see Channels below)

# Run
python local_agent.py
```

---

## Channels

### Telegram

1. Open Telegram and start a chat with **@BotFather**.
2. Send `/newbot`, follow the prompts, and copy the bot token it gives you.
3. Start a conversation with your new bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat ID in the response JSON.
4. Add to `.env`:
   ```
   TELEGRAM_TOKEN=your_bot_token_from_botfather
   TELEGRAM_CHAT_ID=your_telegram_chat_id
   ```

Telegram is required — it is the fallback channel for all push notifications outside work hours and for all chat messages when Slack is not configured.

---

### Slack

Slack is optional. When `SLACK_BOT_TOKEN` plus either `SLACK_CHANNEL_ID` or `SLACK_USER_ID` are set, Slack is activated as a second chat channel and receives push notifications during work hours.

You can use Slack in **channel mode** (bot posts in a shared channel) or **DM mode** (bot sends you a private direct message). DM mode is recommended for personal use.

#### 1. Create the app

1. Go to **https://api.slack.com/apps** and click **Create New App → From scratch**.
2. Name the app (e.g. `passive_agent`) and select your target workspace.

#### 2. Add Bot Token Scopes

In the left sidebar, go to **OAuth & Permissions → Bot Token Scopes** and add the scopes for your chosen mode:

**DM mode** (recommended):

| Scope | Purpose |
|---|---|
| `chat:write` | Post messages |
| `im:history` | Read DM history (to receive your replies) |
| `im:read` | View DM info |
| `im:write` | Open the DM conversation on first run |

**Channel mode** (public channel):

| Scope | Purpose |
|---|---|
| `chat:write` | Post messages |
| `channels:history` | Read message history |
| `channels:read` | Look up channel info |

> **Private channel?** Replace the two `channels:*` scopes with `groups:history` and `groups:read`.

#### 3. Install the app and copy the token

1. Still on **OAuth & Permissions**, click **Install to Workspace** and approve.
2. Copy the **Bot User OAuth Token** (starts with `xoxb-…`) — this is your `SLACK_BOT_TOKEN`.

#### 4. Configure your target

**DM mode** — find your Slack member ID:
- Click your name in the sidebar → **Profile** → the **⋮** menu → **Copy member ID** (starts with `U`).
- Set this as `SLACK_USER_ID`. The bot will open a DM with you automatically on first run. No channel invite needed.

**Channel mode** — find the channel ID:
- Right-click the target channel → **View channel details** → copy the ID at the bottom (starts with `C`).
- Set this as `SLACK_CHANNEL_ID`.
- Then invite the bot: in the channel, run `/invite @YourBotName` (**required** — without it calls fail with `not_in_channel`).

#### 5. Add to `.env`

DM mode:
```
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_USER_ID=U0123456789
```

Channel mode:
```
SLACK_BOT_TOKEN=xoxb-your-token-here
SLACK_CHANNEL_ID=C0123456789
```

#### Routing behavior

| Time | Push notifications | Chat messages |
|---|---|---|
| Work hours (Mon–Fri, 9 am–3 pm) | Slack | Both channels |
| Outside work hours | Telegram | Both channels |

Work hours are configurable — see the Configuration reference below.

---

### Discord

Discord is optional. When both `DISCORD_BOT_TOKEN` and `DISCORD_CHANNEL_ID` are set, Discord is activated as a chat channel. It polls for new messages and replies in the same channel. Push notifications are not routed to Discord (they go to Slack during work hours and Telegram otherwise).

#### 1. Create the bot

1. Go to **https://discord.com/developers/applications** and click **New Application**.
2. Name the app, then go to **Bot** in the left sidebar.
3. Click **Reset Token**, confirm, and copy the token — this is your `DISCORD_BOT_TOKEN`.
4. Under **Privileged Gateway Intents**, enable **Message Content Intent** (required to read message text).

#### 2. Set bot permissions

In the left sidebar, go to **OAuth2 → URL Generator**:

- Under **Scopes**, check `bot`.
- Under **Bot Permissions**, check:
  - `Read Messages / View Channels`
  - `Send Messages`
  - `Read Message History`

Copy the generated URL, open it in your browser, and invite the bot to your server.

#### 3. Get the Channel ID

1. In Discord, open **User Settings → Advanced** and enable **Developer Mode**.
2. Right-click the target channel → **Copy Channel ID**.

This is your `DISCORD_CHANNEL_ID`.

#### 4. Add to `.env`

```
DISCORD_BOT_TOKEN=your_discord_bot_token
DISCORD_CHANNEL_ID=123456789012345678
```

---

## Data sources

### Watched files

Add local file paths to the `WATCHED_FILES` list in `local_agent.py`. The agent reads each file on every context-gathering pass and includes the contents in its LLM prompt.

```python
WATCHED_FILES = [
    "E:/Sync/Planner/TODO.md",
]
```

### iCal feeds

Add one or more calendar feed URLs to `.env` using numbered keys:

```
ICAL_URL_1=https://calendar.google.com/calendar/ical/you%40gmail.com/public/basic.ics
ICAL_URL_2=https://...
```

The agent surfaces events from today and tomorrow only.

### RSS feeds

Add feed URLs to the `RSS_URLS` list in `local_agent.py`:

```python
RSS_URLS = [
    "https://example.com/feed.rss",
]
```

---

## Configuration reference

Key constants at the top of `local_agent.py`:

| Constant | Default | Description |
|---|---|---|
| `PUSH_INTERVAL_SECONDS` | `3600` | How often the agent sends a proactive reminder (seconds) |
| `MAX_HISTORY_TURNS` | `10` | Rolling conversation window kept in memory |
| `QUIET_HOURS_START` | `22` | Hour (24h) when push notifications stop |
| `QUIET_HOURS_END` | `8` | Hour (24h) when push notifications resume |
| `WORK_DAYS` | `{0,1,2,3,4}` | Weekdays considered work days (Monday=0) |
| `WORK_HOUR_START` | `9` | Start of work hours (24h) — controls Slack routing |
| `WORK_HOUR_END` | `15` | End of work hours (24h) — controls Slack routing |
| `WATCHED_FILES` | `[]` | Local file paths to include in context |
| `RSS_URLS` | `[]` | RSS feed URLs to include in context |

### LLM backends

Set `LLM_BACKENDS` in `.env` to a comma-separated priority list. The agent tries each in order until one succeeds:

```
LLM_BACKENDS=publicai,claude,ollama
```

Supported backends: `ollama` (local), `claude` (Anthropic API), `publicai`.
