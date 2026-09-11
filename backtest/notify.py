"""Email notification for finished runs. Stdlib only.

Two paths, tried in order: a local `sendmail` binary, else SMTP to
localhost:25. No credentials, no third-party services — if neither is
available, `send` raises a plain RuntimeError and the caller (bt.py
remote) reports it as a warning without failing the run.

Configure the sender with --sender (default bt@localhost).
"""

from __future__ import annotations

import shutil
import smtplib
import subprocess
from email.message import EmailMessage


def build_message(to: str, subject: str, body: str,
                  sender: str = "bt@localhost") -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


def describe(to: str) -> str:
    if shutil.which("sendmail"):
        return f"email {to} via sendmail"
    return f"email {to} via SMTP localhost:25"


def send(to: str, subject: str, body: str,
         sender: str = "bt@localhost") -> str:
    msg = build_message(to, subject, body, sender)
    sm = shutil.which("sendmail")
    if sm:
        subprocess.run([sm, "-t"], input=bytes(msg), check=True)
        return f"notified {to} via sendmail"
    try:
        with smtplib.SMTP("localhost", 25, timeout=10) as s:
            s.send_message(msg)
    except OSError as e:
        raise RuntimeError(
            f"no sendmail binary and no SMTP on localhost:25 ({e}); "
            f"install sendmail/msmtp or run an MTA") from e
    return f"notified {to} via SMTP localhost:25"
