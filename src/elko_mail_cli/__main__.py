"""elko-mail-cli — minimal, secure, read-only email fetching CLI for local LLMs."""

from __future__ import annotations

import base64
import collections
import email as _email_lib
import getpass
import hashlib
import html.parser
import imaplib
import json
import mailbox
import os
import re
from datetime import datetime, timezone
from email.header import decode_header
from pathlib import Path
from typing import Annotated, Optional

import typer
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

GMAIL_SCOPES = ["https://mail.google.com/"]
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "elko-mail"

app = typer.Typer(
    name="elko-mail",
    help="Read-only email fetching CLI for local LLMs.",
    add_completion=False,
    no_args_is_help=True,
)

# ---------------------------------------------------------------------------
# IMAP envelope parsing
# ---------------------------------------------------------------------------

_RE_UID          = re.compile(r'\bUID\s+(\d+)', re.IGNORECASE)
_RE_FLAGS        = re.compile(r'\bFLAGS\s+\(([^)]*)\)', re.IGNORECASE)
_RE_INTERNALDATE = re.compile(r'\bINTERNALDATE\s+"([^"]+)"', re.IGNORECASE)
_RE_GM_THRID     = re.compile(r'\bX-GM-THRID\s+(\d+)', re.IGNORECASE)
_RE_GM_MSGID     = re.compile(r'\bX-GM-MSGID\s+(\d+)', re.IGNORECASE)
_RE_GM_LABELS    = re.compile(r'\bX-GM-LABELS\s+\(([^)]*)\)', re.IGNORECASE)


def _parse_internaldate(s: str) -> str | None:
    """Convert IMAP INTERNALDATE string to UTC ISO8601."""
    try:
        dt = datetime.strptime(s.strip(), "%d-%b-%Y %H:%M:%S %z")
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def _parse_gm_labels(raw: str) -> list[str]:
    """Parse Gmail labels — handles quoted labels with spaces."""
    return [m.group(1) or m.group(2) for m in re.finditer(r'"([^"]+)"|(\S+)', raw) if m.group(1) or m.group(2)]


def _parse_envelope(envelope: bytes) -> dict:
    """Extract UID, FLAGS, INTERNALDATE, X-GM-* from an IMAP FETCH response line."""
    text = envelope.decode("utf-8", errors="replace")

    uid_m    = _RE_UID.search(text)
    flags_m  = _RE_FLAGS.search(text)
    date_m   = _RE_INTERNALDATE.search(text)
    thrid_m  = _RE_GM_THRID.search(text)
    msgid_m  = _RE_GM_MSGID.search(text)
    labels_m = _RE_GM_LABELS.search(text)

    flags_raw = flags_m.group(1) if flags_m else ""
    flags = [f.strip() for f in flags_raw.split() if f.strip()]

    return {
        "uid":           uid_m.group(1) if uid_m else "",
        "flags":         flags,
        "internal_date": _parse_internaldate(date_m.group(1)) if date_m else None,
        "gm_thrid":      thrid_m.group(1) if thrid_m else None,
        "gm_msgid":      msgid_m.group(1) if msgid_m else None,
        "gm_labels":     _parse_gm_labels(labels_m.group(1)) if labels_m else [],
    }

# ---------------------------------------------------------------------------
# Header decoding
# ---------------------------------------------------------------------------

def _decode_header_value(value: str | None) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


def _all_headers(msg: _email_lib.message.Message) -> dict[str, list[str]]:
    """Return every header as {name: [value, ...]} — nothing silently dropped."""
    result: dict[str, list[str]] = collections.defaultdict(list)
    for k, v in msg.items():
        result[k].append(v)
    return dict(result)

# ---------------------------------------------------------------------------
# Text / HTML processing
# ---------------------------------------------------------------------------

def _normalize_whitespace(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\r?\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _strip_html(raw_html: str) -> str:
    """Convert HTML email body to markdown-flavored plain text.

    Preserves semantic structure LLMs can use: links, images, lists,
    blockquotes (reply chains), bold, headings, and section breaks.
    Drops layout noise: inline styles, table scaffolding, script/style.
    Uses stdlib html.parser only — no external dependencies.
    """

    class _HTMLToText(html.parser.HTMLParser):
        _SKIP    = frozenset({"script", "style", "head"})
        _BLOCK   = frozenset({"p", "div", "section", "article", "header", "footer",
                               "main", "nav", "aside", "table", "tbody", "thead", "tfoot"})
        _HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
        _BOLD    = frozenset({"strong", "b"})

        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self._bufs: list[list[str]] = [[]]
            self._scopes: list[tuple[str, str]] = [("root", "")]
            self._skip_depth = 0
            self._bold_depth = 0
            self._list_stack: list[str] = []
            self._ol_counters: list[int] = []

        def _write(self, text: str) -> None:
            self._bufs[-1].append(text)

        def _push(self, scope: str, data: str = "") -> None:
            self._bufs.append([])
            self._scopes.append((scope, data))

        def _pop(self) -> tuple[str, str, str]:
            content = "".join(self._bufs.pop())
            scope, data = self._scopes.pop()
            return scope, data, content

        def handle_starttag(self, tag: str, attrs: list) -> None:
            if self._skip_depth > 0:
                return
            if tag in self._SKIP:
                self._skip_depth += 1
                return
            d = dict(attrs)
            if tag == "a":
                href = d.get("href", "").strip()
                if href and not href.startswith("#"):
                    self._push("link", href)
                return
            if tag == "img":
                alt = d.get("alt", "").strip()
                if alt:
                    self._write(f"[image: {alt}]")
                return
            if tag in self._BOLD:
                self._bold_depth += 1
                if self._bold_depth == 1:
                    self._write("**")
                return
            if tag == "blockquote":
                self._write("\n")
                self._push("bq")
                return
            if tag == "br":
                self._write("\n")
                return
            if tag == "hr":
                self._write("\n---\n")
                return
            if tag in ("ul", "ol"):
                self._list_stack.append(tag)
                if tag == "ol":
                    self._ol_counters.append(0)
                return
            if tag == "li":
                if self._list_stack and self._list_stack[-1] == "ol":
                    self._ol_counters[-1] += 1
                    self._write(f"\n{self._ol_counters[-1]}. ")
                else:
                    self._write("\n• ")
                return
            if tag in self._HEADINGS:
                self._write(f"\n{'#' * self._HEADINGS[tag]} ")
                return
            if tag in self._BLOCK or tag in ("td", "th"):
                self._write("\n")

        def handle_endtag(self, tag: str) -> None:
            if tag in self._SKIP:
                self._skip_depth = max(0, self._skip_depth - 1)
                return
            if self._skip_depth > 0:
                return
            if tag == "a":
                if len(self._scopes) > 1 and self._scopes[-1][0] == "link":
                    _, href, text = self._pop()
                    text = text.strip()
                    self._write(f"[{text}]({href})" if text else href)
                return
            if tag in self._BOLD:
                self._bold_depth = max(0, self._bold_depth - 1)
                if self._bold_depth == 0:
                    self._write("**")
                return
            if tag == "blockquote":
                if len(self._scopes) > 1 and self._scopes[-1][0] == "bq":
                    _, _, content = self._pop()
                    lines = content.split("\n")
                    quoted = "\n".join(f"> {ln}" if ln.strip() else ">" for ln in lines)
                    self._write(quoted + "\n")
                return
            if tag in ("ul", "ol"):
                if self._list_stack:
                    removed = self._list_stack.pop()
                    if removed == "ol" and self._ol_counters:
                        self._ol_counters.pop()
                self._write("\n")
                return
            if tag in self._HEADINGS or tag in self._BLOCK:
                self._write("\n")

        def handle_data(self, data: str) -> None:
            if self._skip_depth > 0:
                return
            self._write(data)

        def result(self) -> str:
            return "".join(self._bufs[0])

    parser = _HTMLToText()
    parser.feed(raw_html)
    return _normalize_whitespace(parser.result())


def _get_html_body(msg: _email_lib.message.Message) -> str:
    """Return the raw text/html part, or '' if none."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/html" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
    else:
        if msg.get_content_type() == "text/html":
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace")
    return ""


def _make_snippet(body: str, length: int = 200) -> str:
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", body)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6} ", "", text, flags=re.MULTILINE)
    text = re.sub(r"^>+ ?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\[image:[^\]]+\]", "", text, flags=re.MULTILINE)
    text = re.sub(r"^---$", "", text, flags=re.MULTILINE)
    text = _normalize_whitespace(text)
    return text[:length].rstrip()


def _strip_quoted(body: str) -> str:
    lines = body.split("\n")
    result = []
    for line in lines:
        if line.startswith("> ") or line == ">":
            break
        if re.match(r"^On .{10,200} wrote:$", line):
            break
        result.append(line)
    return _normalize_whitespace("\n".join(result))


def _get_text_body(msg: _email_lib.message.Message) -> str:
    html_fallback: str | None = None
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                return _normalize_whitespace(text)
            if ct == "text/html" and html_fallback is None:
                html_fallback = text
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                return _strip_html(text)
            return _normalize_whitespace(text)
    if html_fallback is not None:
        return _strip_html(html_fallback)
    return ""

# ---------------------------------------------------------------------------
# Attachment extraction (with content)
# ---------------------------------------------------------------------------

def _get_attachments(msg: _email_lib.message.Message) -> list[dict]:
    """Extract all attachment and inline parts with full content."""
    parts = []
    if not msg.is_multipart():
        return parts
    for part in msg.walk():
        ct = part.get_content_type()
        cd = str(part.get("Content-Disposition", ""))
        cid = part.get("Content-ID", "").strip()

        is_attachment = "attachment" in cd
        is_inline = ("inline" in cd and bool(cid))  # inline with Content-ID = embedded image

        if not is_attachment and not is_inline:
            continue
        if ct.startswith("multipart/"):
            continue

        filename = _decode_header_value(part.get_filename() or "")
        payload = part.get_payload(decode=True)

        parts.append({
            "filename":       filename,
            "content_type":   ct,
            "size":           len(payload) if payload else 0,
            "content_id":     cid,
            "is_inline":      is_inline,
            "sha256":         hashlib.sha256(payload).hexdigest() if payload else None,
            "content_base64": base64.b64encode(payload).decode("ascii") if payload else None,
        })
    return parts

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _resolve_config_dir(config_dir_str: Optional[str]) -> Path:
    return Path(config_dir_str) if config_dir_str else DEFAULT_CONFIG_DIR


def _token_path(config_dir: Path, email_addr: str) -> Path:
    return config_dir / "credentials" / f"{email_addr}.json"


def _find_client_secrets(config_dir: Path) -> Path:
    candidates = [config_dir / "credentials.json", Path("credentials.json")]
    for p in candidates:
        if p.exists():
            return p
    typer.echo(
        "ERROR: OAuth client secrets not found.\n"
        "Download credentials.json from Google Cloud Console and place it at:\n"
        f"  {candidates[0]}\nor in the current directory.",
        err=True,
    )
    raise typer.Exit(1)


def _load_gmail_credentials(email_addr: str, config_dir: Path, headless: bool) -> Credentials:
    token_path = _token_path(config_dir, email_addr)
    creds: Optional[Credentials] = None

    if token_path.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_path), GMAIL_SCOPES)
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None

    if not creds or not creds.valid:
        secrets_path = _find_client_secrets(config_dir)
        flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), GMAIL_SCOPES)
        if headless:
            flow.redirect_uri = "http://localhost"
            auth_url, _ = flow.authorization_url(prompt="consent")
            typer.echo(f"\nVisit this URL to authorize:\n\n  {auth_url}\n", err=True)
            typer.echo(
                "After authorizing, Google redirects to http://localhost — the browser\n"
                "will show a connection error (that's expected). Copy the 'code' value\n"
                "from the address bar (everything after 'code=' and before '&').",
                err=True,
            )
            code = typer.prompt("Enter the authorization code")
            flow.fetch_token(code=code)
            creds = flow.credentials
        else:
            typer.echo("Opening browser for OAuth2 authorization...", err=True)
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        os.chmod(token_path, 0o600)

    return creds


def _xoauth2_string(email_addr: str, token: str) -> bytes:
    return f"user={email_addr}\x01auth=Bearer {token}\x01\x01".encode()


def _imap_connect_gmail(email_addr: str, creds: Credentials) -> imaplib.IMAP4_SSL:
    if not creds.token:
        creds.refresh(Request())
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.authenticate("XOAUTH2", lambda _: _xoauth2_string(email_addr, creds.token))
    return imap


def _imap_connect_generic(server: str, email_addr: str) -> imaplib.IMAP4_SSL:
    password = getpass.getpass(f"Password for {email_addr} @ {server}: ")
    imap = imaplib.IMAP4_SSL(server)
    imap.login(email_addr, password)
    return imap

# ---------------------------------------------------------------------------
# IMAP fetch — UIDs, FLAGS, INTERNALDATE, Gmail extensions
# ---------------------------------------------------------------------------

def _fetch_messages(
    imap: imaplib.IMAP4_SSL,
    folder: str,
    limit: int,
    is_gmail: bool,
) -> list[dict]:
    """Fetch messages using stable UIDs with full IMAP metadata."""
    typ, data = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        typer.echo(f"ERROR: Cannot select folder '{folder}'.", err=True)
        raise typer.Exit(1)

    # UIDs are stable — sequence numbers shift when messages are expunged
    typ, uid_data = imap.uid("search", None, "ALL")
    if typ != "OK" or not uid_data or not uid_data[0]:
        return []

    all_uids = uid_data[0].split()
    if not all_uids:
        return []

    target_uids = all_uids[-limit:] if limit > 0 else all_uids
    uid_list = b",".join(target_uids)

    # Gmail exposes pre-computed thread IDs, internal message IDs, and labels
    if is_gmail:
        fetch_items = "(UID RFC822 FLAGS INTERNALDATE X-GM-THRID X-GM-MSGID X-GM-LABELS)"
    else:
        fetch_items = "(UID RFC822 FLAGS INTERNALDATE)"

    typ, raw_data = imap.uid("fetch", uid_list, fetch_items)
    if typ != "OK" or not raw_data:
        return []

    results = []
    for item in raw_data:
        if isinstance(item, tuple) and len(item) == 2:
            envelope = _parse_envelope(item[0])
            envelope["raw_bytes"] = item[1]
            results.append(envelope)

    return list(reversed(results))  # newest first

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.command()
def fetch(
    email_addr: Annotated[str, typer.Option("--email", "-e", help="Email address (required).")],
    provider: Annotated[str, typer.Option(help="Provider: gmail or imap.")] = "gmail",
    server: Annotated[str, typer.Option(help="IMAP server address.")] = "",
    folder: Annotated[str, typer.Option("--folder", "-f", help="Mailbox folder.")] = "INBOX",
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max messages to fetch.")] = 50,
    fmt: Annotated[str, typer.Option("--format", help="Output format: json, eml, mbox.")] = "json",
    output: Annotated[str, typer.Option("--output", "-o", help="Output path.")] = "./elko-mail-output",
    headless: Annotated[bool, typer.Option(help="Enable console OAuth2 flow (no browser).")] = False,
    config_dir: Annotated[Optional[str], typer.Option("--config-dir", help="Custom config directory.")] = None,
):
    """Fetch emails from a mailbox."""
    if provider not in ("gmail", "imap"):
        typer.echo(f"ERROR: Unknown provider '{provider}'. Choose gmail or imap.", err=True)
        raise typer.Exit(1)
    if fmt not in ("json", "eml", "mbox"):
        typer.echo(f"ERROR: Unknown format '{fmt}'. Choose json, eml, or mbox.", err=True)
        raise typer.Exit(1)

    cfg_dir = _resolve_config_dir(config_dir)
    out_path = Path(output)
    out_path.mkdir(parents=True, exist_ok=True)

    is_gmail = (provider == "gmail")

    if is_gmail:
        creds = _load_gmail_credentials(email_addr, cfg_dir, headless)
        imap = _imap_connect_gmail(email_addr, creds)
    else:
        if not server:
            typer.echo("ERROR: --server is required for imap provider.", err=True)
            raise typer.Exit(1)
        imap = _imap_connect_generic(server, email_addr)

    try:
        messages_data = _fetch_messages(imap, folder, limit, is_gmail)
    finally:
        try:
            imap.close()
            imap.logout()
        except Exception:
            pass

    fetched_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if fmt == "json":
        messages = []
        for item in messages_data:
            raw_bytes: bytes = item["raw_bytes"]
            msg = _email_lib.message_from_bytes(raw_bytes)
            body = _get_text_body(msg)

            messages.append({
                # --- IMAP metadata (server-authoritative) ---
                "imap_uid":       item["uid"],
                "imap_flags":     item["flags"],
                "internal_date":  item["internal_date"],
                # --- Gmail extensions ---
                "gmail_thread_id":  item["gm_thrid"],
                "gmail_message_id": item["gm_msgid"],
                "gmail_labels":     item["gm_labels"],
                # --- RFC 2822 identity & threading ---
                "message_id":    msg.get("Message-ID", "").strip(),
                "in_reply_to":   msg.get("In-Reply-To", "").strip(),
                "references":    msg.get("References", "").strip(),
                "thread_index":  msg.get("Thread-Index", "").strip(),
                "thread_topic":  _decode_header_value(msg.get("Thread-Topic")),
                # --- Routing ---
                "subject":       _decode_header_value(msg.get("Subject")),
                "from":          _decode_header_value(msg.get("From")),
                "sender":        _decode_header_value(msg.get("Sender")),
                "to":            _decode_header_value(msg.get("To")),
                "cc":            _decode_header_value(msg.get("CC")),
                "bcc":           _decode_header_value(msg.get("BCC")),
                "reply_to":      _decode_header_value(msg.get("Reply-To")),
                "delivered_to":  msg.get("Delivered-To", "").strip(),
                "return_path":   msg.get("Return-Path", "").strip(),
                # --- Timestamps ---
                "date":          msg.get("Date", ""),
                "received":      list(msg.get_all("Received") or []),
                # --- Authentication ---
                "dkim_signature":         "present" if msg.get("DKIM-Signature") else "absent",
                "authentication_results": msg.get("Authentication-Results", "").strip(),
                "received_spf":           msg.get("Received-SPF", "").strip(),
                "arc_authentication_results": msg.get("ARC-Authentication-Results", "").strip(),
                # --- Priority / classification ---
                "importance":          msg.get("Importance", "").strip(),
                "priority":            msg.get("X-Priority", "").strip(),
                "x_mailer":            msg.get("X-Mailer", "").strip(),
                "x_originating_ip":    msg.get("X-Originating-IP", "").strip(),
                "x_spam_score":        msg.get("X-Spam-Score", "").strip(),
                "x_spam_status":       msg.get("X-Spam-Status", "").strip(),
                "list_unsubscribe":    msg.get("List-Unsubscribe", "").strip(),
                "list_id":             msg.get("List-ID", "").strip(),
                "precedence":          msg.get("Precedence", "").strip(),
                "auto_submitted":      msg.get("Auto-Submitted", "").strip(),
                # --- Content ---
                "snippet":         _make_snippet(body),
                "body":            body,
                "body_html":       _get_html_body(msg),
                "stripped_reply":  _strip_quoted(body),
                "attachments":     _get_attachments(msg),
                # --- Complete header dump (nothing silently dropped) ---
                "all_headers":     _all_headers(msg),
                # --- Raw source of truth ---
                "raw_mime":  base64.b64encode(raw_bytes).decode("ascii"),
                "raw_size":  len(raw_bytes),
                "fetched_at": fetched_at,
            })

        result = {
            "provider":   provider,
            "account":    email_addr,
            "folder":     folder,
            "fetched_at": fetched_at,
            "count":      len(messages),
            "messages":   messages,
        }
        out_file = out_path / "messages.json"
        out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        typer.echo(f"Wrote {len(messages)} messages → {out_file}")

    elif fmt == "eml":
        for item in messages_data:
            uid = item["uid"] or "unknown"
            (out_path / f"{uid}.eml").write_bytes(item["raw_bytes"])
        typer.echo(f"Wrote {len(messages_data)} .eml files → {out_path}/")

    elif fmt == "mbox":
        mbox_file = out_path / "messages.mbox"
        mb = mailbox.mbox(str(mbox_file))
        mb.lock()
        try:
            for item in messages_data:
                mb.add(mailbox.mboxMessage(_email_lib.message_from_bytes(item["raw_bytes"])))
            mb.flush()
        finally:
            mb.unlock()
            mb.close()
        typer.echo(f"Wrote {len(messages_data)} messages → {mbox_file}")


@app.command()
def version():
    """Show version."""
    typer.echo("elko-mail-cli 0.2.0")


if __name__ == "__main__":
    app()
