# Agent: ALERTS

## Mission
Implement the spending limit checker and email alert system. Runs after BACKEND.

---

## Email Provider Options (all free)

| Provider | Free Tier | Best For |
|---|---|---|
| **Gmail SMTP** | Unlimited (app password) | Easiest if you have Gmail |
| **Resend.com** | 3,000 emails/month | Clean API, custom domain |
| **Brevo (Sendinblue)** | 300 emails/day | Good deliverability |

**Recommended: Gmail SMTP** — zero signup, works immediately.

### Gmail App Password Setup
1. Go to Google Account → Security → 2-Step Verification (must be enabled)
2. Search "App passwords" → Create one named "Finance Tracker"
3. Copy the 16-char password → put in `EMAIL_SMTP_PASSWORD`

---

## `backend/alerts.py`
```python
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from backend.database import connect

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = os.environ["EMAIL_FROM"]
    msg["To"]      = os.environ["EMAIL_TO"]
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_FROM"], os.environ["EMAIL_SMTP_PASSWORD"])
        server.sendmail(os.environ["EMAIL_FROM"], os.environ["EMAIL_TO"], msg.as_string())

def check_and_alert():
    """Check all limits; send emails for 80% and 100% thresholds."""
    month = date.today().strftime("%Y-%m")
    with connect() as conn:
        limits = conn.execute("SELECT * FROM limits").fetchall()

        for lim in limits:
            lim = dict(lim)
            if lim["category"]:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions
                    WHERE amount < 0 AND category = ? AND strftime('%Y-%m', date) = ?
                """, [lim["category"], month]).fetchone()[0]
                label = f"Category: {lim['category'].title()}"
            else:
                spent = conn.execute("""
                    SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions
                    WHERE amount < 0 AND strftime('%Y-%m', date) = ?
                """, [month]).fetchone()[0]
                label = "Total monthly spending"

            pct = spent / lim["amount"] * 100 if lim["amount"] else 0

            for threshold, threshold_label in [(100, "100pct"), (80, "80pct")]:
                if pct >= threshold:
                    already_sent = conn.execute("""
                        SELECT id FROM alerts_sent
                        WHERE limit_id = ? AND threshold = ?
                        AND strftime('%Y-%m', sent_at) = ?
                    """, [lim["id"], threshold_label, month]).fetchone()

                    if not already_sent:
                        _send_limit_alert(label, spent, lim["amount"], pct, threshold)
                        conn.execute("""
                            INSERT INTO alerts_sent (limit_id, threshold) VALUES (?, ?)
                        """, [lim["id"], threshold_label])
                    break  # only send the highest triggered threshold per run

def _send_limit_alert(label: str, spent: float, limit: float, pct: float, threshold: int):
    color = "#e53e3e" if threshold >= 100 else "#d69e2e"
    emoji = "🚨" if threshold >= 100 else "⚠️"
    word  = "EXCEEDED" if threshold >= 100 else "WARNING"

    subject = f"{emoji} Budget {word}: {label} at {pct:.0f}%"
    body = f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background: {color}; color: white; padding: 20px; border-radius: 8px 8px 0 0;">
        <h2 style="margin:0">{emoji} Budget {word}</h2>
    </div>
    <div style="padding: 20px; border: 1px solid #e2e8f0; border-radius: 0 0 8px 8px;">
        <table style="width:100%; border-collapse:collapse">
            <tr><td style="padding:8px 0; color:#718096">Category</td>
                <td style="padding:8px 0; font-weight:bold">{label}</td></tr>
            <tr><td style="padding:8px 0; color:#718096">Spent</td>
                <td style="padding:8px 0; font-weight:bold">€{spent:.2f}</td></tr>
            <tr><td style="padding:8px 0; color:#718096">Limit</td>
                <td style="padding:8px 0">€{limit:.2f}</td></tr>
            <tr><td style="padding:8px 0; color:#718096">Usage</td>
                <td style="padding:8px 0; color:{color}; font-weight:bold">{pct:.1f}%</td></tr>
        </table>
        <div style="margin-top:20px; padding:12px; background:#f7fafc; border-radius:6px">
            <div style="background:#e2e8f0; border-radius:4px; height:12px">
                <div style="background:{color}; width:{min(pct,100):.0f}%; height:12px; border-radius:4px"></div>
            </div>
        </div>
        <p style="color:#718096; margin-top:20px; font-size:14px">
            Open your <a href="http://localhost:8000">finance dashboard</a> to review and adjust limits.
        </p>
    </div>
    </body></html>
    """
    send_email(subject, body)

def send_daily_summary():
    """Optional: send a daily spending summary email."""
    today = date.today().isoformat()
    month = date.today().strftime("%Y-%m")

    with connect() as conn:
        today_total = conn.execute("""
            SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions
            WHERE amount < 0 AND date = ?
        """, [today]).fetchone()[0]

        month_total = conn.execute("""
            SELECT COALESCE(SUM(ABS(amount)), 0) FROM transactions
            WHERE amount < 0 AND strftime('%Y-%m', date) = ?
        """, [month]).fetchone()[0]

        by_category = conn.execute("""
            SELECT category, SUM(ABS(amount)) as total
            FROM transactions
            WHERE amount < 0 AND date = ?
            GROUP BY category ORDER BY total DESC
        """, [today]).fetchall()

    rows = "".join(
        f"<tr><td style='padding:6px 12px'>{r['category']}</td>"
        f"<td style='padding:6px 12px; text-align:right'>€{r['total']:.2f}</td></tr>"
        for r in by_category
    )

    body = f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color:#2d3748">Daily Finance Summary — {today}</h2>
    <p><strong>Today:</strong> €{today_total:.2f} &nbsp;|&nbsp;
       <strong>This month:</strong> €{month_total:.2f}</p>
    <table style="width:100%; border-collapse:collapse; margin-top:16px">
        <thead><tr style="background:#f7fafc">
            <th style="padding:8px 12px; text-align:left">Category</th>
            <th style="padding:8px 12px; text-align:right">Amount</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table>
    </body></html>
    """
    send_email(f"Daily Summary — €{today_total:.2f} spent today", body)
```

---

## Scheduler Additions for Daily/Weekly Digests (optional)

Add to `main.py` startup if you want digests:
```python
from apscheduler.triggers.cron import CronTrigger

# Daily summary at 9pm
scheduler.add_job(send_daily_summary, CronTrigger(hour=21, minute=0))

# Weekly portfolio digest every Monday at 8am
scheduler.add_job(send_weekly_portfolio, CronTrigger(day_of_week="mon", hour=8))
```
