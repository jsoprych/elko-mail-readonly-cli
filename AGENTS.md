

```markdown
# AGENTS.md - elko-mail-cli

## Project Overview

**Project Name**: `elko-mail-cli`  
**Version**: 0.1.0  
**Purpose**: A minimal, secure, read-only email fetching CLI designed for feeding clean email data into local LLMs.

**Core Philosophy**:  
- Do one thing extremely well: **fetch emails**.  
- Keep `fetch` pure and record-complete.  
- Excellent support for headless environments and Docker.  
- No bloat, no anonymization, no LLM logic in v0.1.

---

## Command Structure

Only one command in v0.1:

```bash
elko-mail fetch [OPTIONS]
```

---

## Requirements

### Authentication

- **Gmail**: Must use OAuth2 + XOAUTH2 (passwords are disabled).
- Support two OAuth flows:
  - Desktop: Browser-based (`run_local_server`)
  - Headless/Docker: Device Code flow (`run_console`)
- `--headless` flag enables Device Code flow.

**Credential Storage**:

- Path: `~/.config/elko-mail/credentials/<email>.json`
- File permission: `0600`
- One JSON file per email address.

### Supported Providers

- `gmail` (default)
- `imap` (generic)

### Output Formats

- `--format json` (default)
- `--format eml` → one `.eml` file per message
- `--format mbox` → single mbox file

---

## CLI Flags for `fetch`

| Flag             | Description                          | Default                  |
| ---------------- | ------------------------------------ | ------------------------ |
| `--email`, `-e`  | Email address (required)             | —                        |
| `--provider`     | `gmail` or `imap`                    | `gmail`                  |
| `--server`       | IMAP server address                  | `imap.gmail.com` (gmail) |
| `--folder`, `-f` | Mailbox folder                       | `INBOX`                  |
| `--limit`, `-n`  | Maximum number of messages           | `50`                     |
| `--format`       | `json`, `eml`, `mbox`                | `json`                   |
| `--output`, `-o` | Output path                          | `./elko-mail-output`     |
| `--headless`     | Enable Device Code flow (no browser) | `false`                  |
| `--config-dir`   | Custom config directory              | `~/.config/elko-mail`    |

---

## JSON Output Schema (for `--format json`)

```json
{
  "provider": "gmail",
  "folder": "INBOX",
  "fetched_at": "2026-05-09T22:15:00Z",
  "count": 42,
  "messages": [
    {
      "id": "1234567890",
      "raw_size": 28473,
      "fetched_at": "2026-05-09T22:15:00Z"
    }
  ]
}
```

---

## Project Structure

```bash
elko-mail-cli/
├── pyproject.toml
├── README.md
├── AGENTS.md
├── credentials.json              # ← User downloads from Google Cloud
├── src/
│   └── elko_mail_cli/
│       └── __main__.py
├── Dockerfile                    # Nice-to-have
└── .gitignore
```

---

## Non-Goals (v0.1)

- No anonymization / PII handling
- No threading logic
- No attachment extraction
- No password-only auth (except generic IMAP if explicitly requested)
- No LLM commands

---

**Instructions for LLM / Agent**:

You are an expert Python CLI developer.  
Build the complete `elko-mail-cli` project **exactly** according to this specification.  
Prioritize:

- Clean, readable code
- Robust error messages
- Excellent headless + Docker support
- Minimal dependencies

Use `typer` for the CLI and `imaplib` + `google-auth` for the backend.


