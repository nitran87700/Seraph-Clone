"""
Guardian alerting module (packaged app version).

Identical logic to server/guardian.py, except CONFIG_FILE and LOG_FILE are
repointed at a writable per-user data directory by main.py at startup, since
a PyInstaller-frozen app's own folder may not be writable (e.g. inside
Program Files or a mounted .app bundle).
"""
import json
import os
import smtplib
import ssl
import time
from email.mime.text import MIMEText

import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(APP_DIR, "guardian_config.json")  # overridden by main.py
LOG_FILE = os.path.join(APP_DIR, "guardian_alerts.log")       # overridden by main.py

DEFAULT_CONFIG = {
    "guardian_name": "",
    "guardian_email": "",
    "guardian_webhook_url": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_username": "",
    "smtp_password": "",
    "from_email": "",
    "cooldown_seconds": 300,
}

_last_alert_time = 0


def get_guardian_config():
    if not os.path.exists(CONFIG_FILE):
        return dict(DEFAULT_CONFIG)
    with open(CONFIG_FILE, "r") as f:
        cfg = json.load(f)
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def set_guardian_config(new_values: dict):
    cfg = get_guardian_config()
    cfg.update({k: v for k, v in new_values.items() if k in DEFAULT_CONFIG})
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)
    return cfg


def _log_alert(message: str):
    with open(LOG_FILE, "a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} | {message}\n")


def _send_email(cfg, subject, body):
    if not (cfg["smtp_host"] and cfg["guardian_email"] and cfg["from_email"]):
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = cfg["from_email"]
    msg["To"] = cfg["guardian_email"]
    context = ssl.create_default_context()
    with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"])) as server:
        server.starttls(context=context)
        if cfg["smtp_username"]:
            server.login(cfg["smtp_username"], cfg["smtp_password"])
        server.sendmail(cfg["from_email"], [cfg["guardian_email"]], msg.as_string())
    return True


def _send_webhook(cfg, text):
    if not cfg["guardian_webhook_url"]:
        return False
    data = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(
        cfg["guardian_webhook_url"], data=data, headers={"Content-Type": "application/json"}
    )
    urllib.request.urlopen(req, timeout=5)
    return True


def send_guardian_alert(reason: str, score: float) -> bool:
    global _last_alert_time
    cfg = get_guardian_config()

    now = time.time()
    if now - _last_alert_time < cfg["cooldown_seconds"]:
        _log_alert(f"SKIPPED (cooldown active) - {reason}")
        return False

    subject = "Seraph Clone - Potential scam activity detected"
    body = (
        f"Hi {cfg['guardian_name'] or 'there'},\n\n"
        f"Seraph Clone detected suspicious activity on a device you are watching as a Guardian.\n\n"
        f"Risk score: {score}/100\n"
        f"Reason: {reason}\n\n"
        f"Consider checking in with them directly.\n"
    )

    sent_any = False
    try:
        if _send_email(cfg, subject, body):
            sent_any = True
    except Exception as e:
        _log_alert(f"EMAIL FAILED - {e}")

    try:
        if _send_webhook(cfg, f"*{subject}*\n{body}"):
            sent_any = True
    except Exception as e:
        _log_alert(f"WEBHOOK FAILED - {e}")

    if sent_any:
        _last_alert_time = now
        _log_alert(f"SENT - {reason}")
    else:
        _last_alert_time = now
        _log_alert(f"NO CHANNEL CONFIGURED (logged only) - {reason}")
        sent_any = True

    return sent_any
