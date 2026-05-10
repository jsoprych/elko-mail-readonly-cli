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

## Credentials

There are two credential files. You create one manually (once); the other is generated automatically on first run.

### File 1 — OAuth client secrets (`credentials.json`)

This file identifies *your Google Cloud application* to Google. You create it once and never touch it again.

**Steps:**

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a project (or pick an existing one).
2. In the left menu: **APIs & Services → Library** — search for **Gmail API** and click **Enable**.
3. **APIs & Services → OAuth consent screen**
   - Choose **External** (or Internal if you're on Google Workspace).
   - Fill in App name (anything, e.g. `elko-mail`), support email, and developer email. Save.
   - Under **Scopes**, add `https://mail.google.com/` (or click "Add or remove scopes" and search for Gmail).
   - Under **Test users**, add the Gmail address(es) you'll be fetching from. Save.
4. **APIs & Services → Credentials → + Create Credentials → OAuth 2.0 Client ID**
   - Application type: **Desktop app**
   - Name: anything (e.g. `elko-mail-desktop`)
   - Click **Create**
5. Click **Download JSON** on the newly created client ID.
6. Save the downloaded file as:

```
~/.config/elko-mail/credentials.json
```

```bash
mkdir -p ~/.config/elko-mail
mv ~/Downloads/client_secret_*.json ~/.config/elko-mail/credentials.json
chmod 600 ~/.config/elko-mail/credentials.json
```

> **Headless / Docker alternative:** place `credentials.json` in the current working directory instead of `~/.config/elko-mail/`.

---

### File 2 — OAuth token (`~/.config/elko-mail/credentials/<email>.json`)

This file is created automatically on first run. It holds the access + refresh token for a specific email address. You never create or edit it by hand.

**On first run** the CLI will either:
- Open your browser to complete the Google sign-in (default), or
- Print a URL to visit manually and prompt for an authorization code (`--headless`)

Once you approve access, the token is saved:

```
~/.config/elko-mail/credentials/you@gmail.com.json   ← permissions: 0600
```

On every subsequent run the token is loaded from disk and silently refreshed when it expires. You only re-authenticate if you delete the file or revoke access in your Google account.

---

### Summary

| File | What it is | Who creates it | Where it lives |
|------|-----------|---------------|----------------|
| `credentials.json` | OAuth client secrets (app identity) | You — downloaded from Google Cloud | `~/.config/elko-mail/credentials.json` |
| `<email>.json` | OAuth token (your sign-in session) | CLI — auto-generated on first run | `~/.config/elko-mail/credentials/<email>.json` |

---

### Generic IMAP

No files needed. The CLI prompts for your password at runtime (not stored anywhere).

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
