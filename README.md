# elko-mail-cli

A minimal, secure, read-only email fetching CLI designed for feeding clean email data into local LLMs.

## Philosophy

- Does one thing extremely well: **fetch emails**
- Pure, record-complete fetch — every field, every message
- Excellent support for headless environments and Docker
- No bloat, no anonymization, no LLM logic in v0.1

## Install

```bash
pip install .
```

## Setup: Gmail OAuth2

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (type: Desktop App)
3. Download the JSON and save it as `~/.config/elko-mail/credentials.json`

On first run, the CLI will open a browser (or print a URL for headless/Docker) to complete the OAuth flow. The resulting token is saved at `~/.config/elko-mail/credentials/<email>.json` with `0600` permissions and auto-refreshed on subsequent runs.

## Usage

```bash
elko-mail fetch [OPTIONS]
```

| Flag | Description | Default |
|------|-------------|---------|
| `--email`, `-e` | Email address **(required)** | — |
| `--provider` | `gmail` or `imap` | `gmail` |
| `--server` | IMAP server address | `imap.gmail.com` |
| `--folder`, `-f` | Mailbox folder | `INBOX` |
| `--limit`, `-n` | Max messages to fetch | `50` |
| `--format` | `json`, `eml`, or `mbox` | `json` |
| `--output`, `-o` | Output path | `./elko-mail-output` |
| `--headless` | Console OAuth2 flow — no browser | `false` |
| `--config-dir` | Custom config directory | `~/.config/elko-mail` |

### Examples

```bash
# Fetch 50 most recent inbox messages as JSON
elko-mail fetch --email you@gmail.com

# Fetch 100 messages as individual .eml files
elko-mail fetch --email you@gmail.com --format eml --limit 100

# Fetch Sent folder as mbox
elko-mail fetch --email you@gmail.com --format mbox --folder Sent

# Headless / server (no browser)
elko-mail fetch --email you@gmail.com --headless

# Generic IMAP server
elko-mail fetch --email you@work.com --provider imap --server mail.work.com
```

## Output Formats

### `--format json` (default)

Single `messages.json` in the output directory:

```json
{
  "provider": "gmail",
  "folder": "INBOX",
  "fetched_at": "2026-05-09T22:15:00Z",
  "count": 42,
  "messages": [
    {
      "id": "1234",
      "subject": "Hello",
      "from": "sender@example.com",
      "to": "you@gmail.com",
      "date": "Fri, 09 May 2026 10:00:00 +0000",
      "body": "Plain-text body...",
      "raw_size": 28473,
      "fetched_at": "2026-05-09T22:15:00Z"
    }
  ]
}
```

### `--format eml`

One `<id>.eml` file per message in the output directory — raw RFC 822, byte-for-byte from the server.

### `--format mbox`

Single `messages.mbox` file in the output directory.

## Docker

```bash
docker build -t elko-mail .

# First run — headless OAuth2 (copy/paste URL into browser, paste code back)
docker run -it \
  -v ~/.config/elko-mail:/root/.config/elko-mail \
  -v $(pwd)/elko-mail-output:/app/elko-mail-output \
  elko-mail fetch --email you@gmail.com --headless

# Subsequent runs (token cached, no interaction needed)
docker run --rm \
  -v ~/.config/elko-mail:/root/.config/elko-mail \
  -v $(pwd)/elko-mail-output:/app/elko-mail-output \
  elko-mail fetch --email you@gmail.com
```

## Non-Goals (v0.1)

- No anonymization / PII handling
- No threading logic
- No attachment extraction
- No LLM commands
