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
      "message_id": "<CABcde123@mail.gmail.com>",
      "in_reply_to": "<CABcde122@mail.gmail.com>",
      "subject": "Re: Hello",
      "from": "sender@example.com",
      "to": "you@gmail.com",
      "cc": "other@example.com",
      "reply_to": "",
      "date": "Fri, 09 May 2026 10:00:00 +0000",
      "snippet": "First 200 characters of body, markdown stripped, clean preview...",
      "body": "Converted markdown text — links, lists, blockquotes, bold preserved...",
      "body_html": "<html>...raw HTML source as received...</html>",
      "stripped_reply": "Just the new reply text, quoted history removed.",
      "attachments": [
        {"filename": "invoice.pdf", "content_type": "application/pdf", "size": 45231}
      ],
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

## Design Notes

*This section documents architecture decisions for the blog series on safely processing email with local LLMs.*

### Body Extraction: HTML → LLM Text

Most email bodies arrive as HTML. elko-mail converts them to clean, structured plain text using a **zero-dependency stdlib parser** (`html.parser`). No BeautifulSoup, no html2text — processing untrusted content with fewer dependencies means a smaller attack surface.

The parser emits **markdown-flavored output** because LLMs are trained extensively on markdown and handle its structure natively. Flat unstructured text loses signal; markdown preserves it without adding tokens the LLM can't interpret.

Seven HTML constructs are preserved with semantic fidelity:

| HTML | Output | Why |
|------|--------|-----|
| `<a href="url">text</a>` | `[text](url)` | The URL is often the entire payload of notification emails |
| `<img alt="desc">` | `[image: desc]` | Alt text is the only machine-readable image description |
| `<ul> / <li>` | `• item` | Lists encode enumeration; flat text loses the structure |
| `<ol> / <li>` | `1. item` | Ordered lists encode sequence and priority |
| `<blockquote>` | `> quoted text` | Email reply chains live in nested blockquotes — the conversational thread is critical context for an LLM |
| `<strong>`, `<b>` | `**text**` | Bold marks calls to action, warnings, and key terms |
| `<h1>`–`<h6>` | `# text`, `## text` … | Newsletter heading hierarchy gives the LLM document structure |
| `<hr>` | `---` | Section breaks delineate content regions |

**What is deliberately dropped:** inline styles, CSS classes, `<script>`, `<style>`, and HTML table layout structure. Email tables are almost universally layout grids — not data tables — so emitting ASCII table markup would add noise, not structure. Table cell content (`<td>`, `<th>`) is still extracted as text separated by newlines.

**Whitespace normalization** runs as a final pass: consecutive spaces/tabs collapse to one, `\r\n` is normalized, runs of three or more blank lines collapse to two, and leading/trailing whitespace is stripped. HTML uses whitespace for visual layout; normalizing it recovers semantic density without losing meaning.

**Both representations, always:** Every message carries `body` (converted markdown text) and `body_html` (raw HTML source). `body` is what you feed the LLM. `body_html` is what you use to debug the conversion, audit what the sender transmitted, or hand off to a pipeline that does its own parsing. No flag needed — complete data is the default.

**Snippet:** `snippet` strips markdown markers from `body` and returns the first 200 characters — clean prose suitable for a triage pass. Feed an LLM 50 snippets to decide which full bodies to load; avoid paying context cost for irrelevant messages.

**Stripped reply:** `stripped_reply` uses a heuristic to isolate just the new text in a reply, dropping quoted history. It stops at the first `> ` blockquote line or `On ... wrote:` separator. Handles the common cases; edge cases (inline replies, non-standard quoting) will include some quoted text. Full reply-chain reconstruction via `message_id` / `in_reply_to` is a separate problem.

**Threading headers:** `message_id` and `in_reply_to` are the raw RFC 2822 values. Reconstruct threads by grouping `in_reply_to` → `message_id` chains client-side.

**Attachment manifest:** `attachments` lists every attachment's filename, MIME type, and size in bytes. Content is never fetched — elko-mail is read-only and attachment payloads are rarely useful to LLMs raw.

**Additional headers:** `cc` and `reply_to` are decoded the same way as `from`/`to`. `bcc` is omitted — MTAs strip it before delivery and it is almost never present in received messages.

**Parser design:** The `_HTMLToText` class uses a **buffer stack** to handle nested scopes. Each `<a>` and `<blockquote>` pushes a new output frame; closing the tag pops the frame, formats the collected content (e.g. `[text](url)`, `> quoted lines`), and writes it to the parent frame. This cleanly handles links inside blockquotes, bold inside links, and other arbitrary nesting without special-casing every combination.

### Read-Only Safety

The IMAP connection is opened with `readonly=True`, which issues an IMAP `EXAMINE` command instead of `SELECT`. This prevents any write operations — flag changes, moves, deletes — **at the protocol level**, regardless of what the OAuth token permits.

The OAuth scope `https://mail.google.com/` is required for XOAUTH2 IMAP authentication (the narrower `gmail.readonly` scope only covers the Gmail HTTP API, not raw IMAP). Defense-in-depth comes from the `EXAMINE` command and from the fact that elko-mail never issues any IMAP mutating commands (`STORE`, `COPY`, `EXPUNGE`, etc.).

Tokens are stored at `~/.config/elko-mail/credentials/<email>.json` with `chmod 600`. The OAuth client secrets (`credentials.json`) should similarly be `chmod 600` and never committed to version control.

## Non-Goals (v0.1)

- No anonymization / PII handling
- No threading logic
- No attachment extraction
- No LLM commands
