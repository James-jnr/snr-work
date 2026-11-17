import json
import logging
import re
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import List, Optional, Tuple

import httpx

from . import charts as chartlib
from . import config, db

log = logging.getLogger("notify")


def _telegram(text: str):
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    for i in range(0, len(text), 3900):
        r = httpx.post(url, json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text[i:i + 3900],
                                  "disable_web_page_preview": True}, timeout=15)
        if r.status_code != 200:
            log.error("Telegram error %s %s", r.status_code, r.text[:200])


def _telegram_photos(images: List[Tuple[str, str, bytes]]):
    """images = [(tf, caption, png_bytes)]; several go out as one album, one as a single photo."""
    if not images or not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return
    base = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
    if len(images) == 1:
        tf, cap, png = images[0]
        r = httpx.post(f"{base}/sendPhoto", data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": cap},
                       files={"photo": (f"{tf}.png", png, "image/png")}, timeout=60)
    else:
        media = [{"type": "photo", "media": f"attach://f{i}", "caption": cap}
                 for i, (_, cap, _) in enumerate(images[:10])]
        files = {f"f{i}": (f"{tf}.png", png, "image/png") for i, (tf, _, png) in enumerate(images[:10])}
        r = httpx.post(f"{base}/sendMediaGroup",
                       data={"chat_id": config.TELEGRAM_CHAT_ID, "media": json.dumps(media)},
                       files=files, timeout=90)
    if r.status_code != 200:
        log.error("Telegram photo error %s %s", r.status_code, r.text[:200])


def _email(subject: str, text: str, images: Optional[List[Tuple[str, str, bytes]]] = None):
    if not (config.SMTP_HOST and config.EMAIL_TO):
        return
    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, config.EMAIL_FROM, config.EMAIL_TO
    msg.set_content(text)
    for tf, cap, png in images or []:
        msg.add_attachment(png, maintype="image", subtype="png", filename=f"{cap.replace(' ', '_')}.png")
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as s:
        s.starttls()
        if config.SMTP_USER:
            s.login(config.SMTP_USER, config.SMTP_PASS)
        s.send_message(msg)


def _txt_copy(row: dict, subject: str, text: str) -> str:
    """Write a human-readable copy to SETUPS_DIR/all/. Returns the file name."""
    folder = config.SETUPS_DIR / "all"
    folder.mkdir(parents=True, exist_ok=True)
    ts = datetime.fromtimestamp(row["created_at"], timezone.utc)
    parts = [ts.strftime("%Y-%m-%d_%H%M%S"), row["symbol"] or "system", row["kind"]]
    if row["grade"]:
        parts.append(row["grade"])
    if row["direction"]:
        parts.append(row["direction"])
    parts.append(str(row["id"]))
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", "_".join(parts)) + ".txt"
    (folder / name).write_text(
        f"{subject}\n{ts.strftime('%Y-%m-%d %H:%M:%S')} UTC  (alert #{row['id']})\n\n{text}\n",
        encoding="utf-8")
    return name


def _store(subject: str, text: str, meta: Optional[dict]) -> Optional[dict]:
    """Database row + .txt copy. Never raises: storage trouble must not block delivery."""
    try:
        row = db.insert_alert(subject, text, meta)
    except Exception:
        log.exception("could not save alert to the database")
        return None
    try:
        db.set_txt_file(row["id"], _txt_copy(row, subject, text))
    except Exception:
        log.exception("could not write the .txt copy")
    return row


def _render_images(specs, row: Optional[dict]) -> List[Tuple[str, str, bytes]]:
    """Render chart specs to PNG, save them next to the .txt copy and link them to the alert."""
    out: List[Tuple[str, str, bytes]] = []
    for spec in specs or []:
        try:
            out.append((spec.tf, f"{spec.symbol} {chartlib.TF_NAME[spec.tf]}", chartlib.render(spec)))
        except Exception:
            log.exception("chart render failed for %s", getattr(spec, "tf", "?"))
    if out and row:
        try:
            folder = config.SETUPS_DIR / "charts"
            folder.mkdir(parents=True, exist_ok=True)
            names = []
            for tf, _, png in out:
                name = f"{row['id']}_{tf}.png"
                (folder / name).write_bytes(png)
                names.append({"tf": tf, "file": name})
            db.add_meta(row["id"], {"charts": names})
        except Exception:
            log.exception("could not save chart images")
    return out


def notify(subject: str, text: str, meta: Optional[dict] = None, charts=None):
    """Save the alert, then send to every configured channel; one failing never blocks the others.
    `charts` = optional list of ChartSpec; each is rendered as one image (one timeframe alone)."""
    log.info("ALERT: %s", subject)
    row = _store(subject, text, meta)
    images = _render_images(charts, row)
    calls = [(_telegram, (f"{subject}\n\n{text}",))]
    if images:                       # images are only passed when there are some (keeps old call shapes valid)
        calls.append((_telegram_photos, (images,)))
        calls.append((_email, (subject, text, images)))
    else:
        calls.append((_email, (subject, text)))
    for fn, args in calls:
        try:
            fn(*args)
        except Exception:
            log.exception("delivery failed via %s", fn.__name__)
