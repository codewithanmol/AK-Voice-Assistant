"""
AK Mailer
---------
Send emails from AK using SMTP (TLS/SSL).
Supports Gmail App Password, Outlook, Proton/Custom SMTP.
"""

from __future__ import annotations

import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import List, Optional

from AK import load_config


class Mailer:
    def __init__(self, cfg: dict | None = None):
        if cfg is None:
            cfg = load_config()
        self.cfg = cfg.get("email", {}) or {}
        self.smtp_host = self.cfg.get("smtp_host") or "smtp.gmail.com"
        self.smtp_port = int(self.cfg.get("smtp_port") or 587)
        self.smtp_user = (self.cfg.get("smtp_user") or "").strip()
        self.smtp_pass = (self.cfg.get("smtp_pass") or "").strip()
        self.from_addr = (self.cfg.get("from_addr") or self.smtp_user).strip()
        self.from_name = (self.cfg.get("from_name") or "AK Assistant").strip()
        self.use_ssl = bool(self.cfg.get("use_ssl", False))
        self.enabled = bool(self.cfg.get("enabled", True)) and self.smtp_user and self.smtp_pass

    def configured(self) -> bool:
        return self.enabled

    def status(self) -> str:
        if not self.enabled:
            return "Email nahi configure hai. config.json me email section fill karo."
        return f"Email ready - {self.from_addr} se bhej sakta hu."

    def send(self, to: str | List[str], subject: str, body: str, html: bool = False, attachments: Optional[List[str]] = None) -> tuple[bool, str]:
        if not self.enabled:
            return False, "Email configure nahi hai. Pehle config.json me SMTP details daal do."

        if isinstance(to, str):
            recipients = [t.strip() for t in to.replace(";", ",").split(",") if t.strip()]
        else:
            recipients = [t.strip() for t in to if t.strip()]

        if not recipients:
            return False, "Kisko email bhejna hai ye batao."

        if not subject.strip():
            subject = "Message from AK"

        try:
            msg = MIMEMultipart()
            msg["From"] = f"{self.from_name} <{self.from_addr}>"
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject.strip()

            if html:
                msg.attach(MIMEText(body, "html", "utf-8"))
            else:
                msg.attach(MIMEText(body, "plain", "utf-8"))

            if attachments:
                for path in attachments:
                    if not os.path.isfile(path):
                        continue
                    try:
                        with open(path, "rb") as f:
                            part = MIMEBase("application", "octet-stream")
                            part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition", f'attachment; filename="{os.path.basename(path)}"')
                        msg.attach(part)
                    except Exception:
                        continue

            context = ssl.create_default_context()
            if self.use_ssl or self.smtp_port == 465:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, context=context) as server:
                    server.login(self.smtp_user, self.smtp_pass)
                    server.sendmail(self.from_addr, recipients, msg.as_string())
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                    server.ehlo()
                    server.starttls(context=context)
                    server.ehlo()
                    server.login(self.smtp_user, self.smtp_pass)
                    server.sendmail(self.from_addr, recipients, msg.as_string())

            return True, f"Email bhej diya - {', '.join(recipients)} ko"
        except Exception as e:
            return False, f"Email bhejne me error: {e}"
