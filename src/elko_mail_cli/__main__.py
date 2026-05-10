"""elko-mail-cli — minimal, secure, read-only email fetching CLI for local LLMs."""

from __future__ import annotations

import email as _email_lib
import getpass
import imaplib
import json
import mailbox
import os
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


def _get_text_body(msg: _email_lib.message.Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                return payload.decode(charset, errors="replace") if payload else ""
    else:
        payload = msg.get_payload(decode=True)
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace") if payload else ""
    return ""


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
        f"  {candidates[0]}\n"
        "or in the current directory.",
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
            auth_url, _ = flow.authorization_url(prompt="consent")
            typer.echo(f"\nVisit this URL to authorize:\n\n  {auth_url}\n", err=True)
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


def _fetch_raw_messages(imap: imaplib.IMAP4_SSL, folder: str, limit: int) -> list[tuple[str, bytes]]:
    typ, _ = imap.select(f'"{folder}"', readonly=True)
    if typ != "OK":
        typer.echo(f"ERROR: Cannot select folder '{folder}'.", err=True)
        raise typer.Exit(1)

    typ, data = imap.search(None, "ALL")
    if typ != "OK":
        typer.echo("ERROR: Mailbox search failed.", err=True)
        raise typer.Exit(1)

    uids = data[0].split()
    if limit > 0:
        uids = uids[-limit:]
    uids = list(reversed(uids))

    results: list[tuple[str, bytes]] = []
    for uid in uids:
        typ, raw = imap.fetch(uid, "(RFC822)")
        if typ != "OK" or not raw or raw[0] is None:
            continue
        results.append((uid.decode(), raw[0][1]))

    return results


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

    if provider == "gmail":
        creds = _load_gmail_credentials(email_addr, cfg_dir, headless)
        imap = _imap_connect_gmail(email_addr, creds)
    else:
        if not server:
            typer.echo("ERROR: --server is required for imap provider.", err=True)
            raise typer.Exit(1)
        imap = _imap_connect_generic(server, email_addr)

    try:
        raw_messages = _fetch_raw_messages(imap, folder, limit)
    finally:
        try:
            imap.close()
            imap.logout()
        except Exception:
            pass

    fetched_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    if fmt == "json":
        messages = []
        for uid, raw_bytes in raw_messages:
            msg = _email_lib.message_from_bytes(raw_bytes)
            messages.append({
                "id": uid,
                "subject": _decode_header_value(msg.get("Subject")),
                "from": _decode_header_value(msg.get("From")),
                "to": _decode_header_value(msg.get("To")),
                "date": msg.get("Date", ""),
                "body": _get_text_body(msg),
                "raw_size": len(raw_bytes),
                "fetched_at": fetched_at,
            })
        result = {
            "provider": provider,
            "folder": folder,
            "fetched_at": fetched_at,
            "count": len(messages),
            "messages": messages,
        }
        out_file = out_path / "messages.json"
        out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        typer.echo(f"Wrote {len(messages)} messages → {out_file}")

    elif fmt == "eml":
        for uid, raw_bytes in raw_messages:
            (out_path / f"{uid}.eml").write_bytes(raw_bytes)
        typer.echo(f"Wrote {len(raw_messages)} .eml files → {out_path}/")

    elif fmt == "mbox":
        mbox_file = out_path / "messages.mbox"
        mb = mailbox.mbox(str(mbox_file))
        mb.lock()
        try:
            for _, raw_bytes in raw_messages:
                mb.add(mailbox.mboxMessage(_email_lib.message_from_bytes(raw_bytes)))
            mb.flush()
        finally:
            mb.unlock()
            mb.close()
        typer.echo(f"Wrote {len(raw_messages)} messages → {mbox_file}")


@app.command()
def version():
    """Show version."""
    typer.echo("elko-mail-cli 0.1.0")


if __name__ == "__main__":
    app()
