import smtplib
import os
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date
from backend.database import connect

logger = logging.getLogger(__name__)

def send_email(subject: str, html_body: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(os.environ["EMAIL_FROM"], os.environ["EMAIL_SMTP_PASSWORD"])
        server.sendmail(os.environ["EMAIL_FROM"], os.environ["EMAIL_TO"], msg.as_string())


def email_configured() -> bool:
    return all(os.getenv(k) for k in ("EMAIL_FROM", "EMAIL_TO", "EMAIL_SMTP_PASSWORD"))


def send_security_alert(event: str, when: str, ip: str, user_agent: str,
                        detail: str = "", warn: bool = False):
    """Notify the owner of a security-relevant auth event (new sign-in / passkey
    enrolment / lockout). Best-effort: no-ops if e-mail isn't configured and never
    raises — auth flows must not depend on mail delivery. Runs off the request
    thread (see auth.notify)."""
    if not email_configured():
        logger.info("Security event '%s' — e-mail not configured, skipping alert", event)
        return
    from backend import config
    color = "#e53e3e" if warn else "#3b82f6"
    detail_row = (
        f"<tr><td style='padding:8px 0;color:#718096'>Detail</td>"
        f"<td style='padding:8px 0;font-weight:bold'>{detail}</td></tr>" if detail else ""
    )
    body = f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
    <div style="background:{color}; color:white; padding:20px; border-radius:8px 8px 0 0;">
        <h2 style="margin:0">🔐 {event}</h2>
    </div>
    <div style="padding:20px; border:1px solid #e2e8f0; border-radius:0 0 8px 8px;">
        <table style="width:100%; border-collapse:collapse">
            <tr><td style="padding:8px 0; color:#718096">When</td>
                <td style="padding:8px 0; font-weight:bold">{when}</td></tr>
            <tr><td style="padding:8px 0; color:#718096">IP address</td>
                <td style="padding:8px 0; font-weight:bold">{ip}</td></tr>
            <tr><td style="padding:8px 0; color:#718096">Device</td>
                <td style="padding:8px 0">{user_agent or 'unknown'}</td></tr>
            {detail_row}
        </table>
        <p style="color:#718096; margin-top:20px; font-size:14px">
            If this was you, no action is needed. If not, your account is protected by a
            hardware passkey (it cannot be phished), but you should review activity on your
            <a href="{config.RP_ORIGIN}">dashboard</a> and check the server.
        </p>
    </div>
    </body></html>
    """
    try:
        send_email(f"🔐 Finance Tracker — {event}", body)
        logger.info("Security alert e-mailed: %s", event)
    except Exception:
        logger.exception("Failed to send security alert e-mail for '%s'", event)


def check_and_alert():
    """Check all limits; send emails for 80% and 100% thresholds (once per threshold per month)."""
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
                        try:
                            _send_limit_alert(label, spent, lim["amount"], pct, threshold)
                            conn.execute("""
                                INSERT INTO alerts_sent (limit_id, threshold) VALUES (?, ?)
                            """, [lim["id"], threshold_label])
                            logger.info("Alert sent: %s at %.1f%%", label, pct)
                        except Exception:
                            logger.exception("Failed to send alert for %s", label)
                    break  # only send the highest triggered threshold per run

def _send_limit_alert(label: str, spent: float, limit: float, pct: float, threshold: int):
    color = "#e53e3e" if threshold >= 100 else "#d69e2e"
    emoji = "🚨" if threshold >= 100 else "⚠️"
    word = "EXCEEDED" if threshold >= 100 else "WARNING"

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
                <div style="background:{color}; width:{min(pct, 100):.0f}%; height:12px; border-radius:4px"></div>
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
    """Optional daily spending summary email."""
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
        f"<tr><td style='padding:6px 12px'>{r['category'] or 'other'}</td>"
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
    try:
        send_email(f"Daily Summary — €{today_total:.2f} spent today", body)
    except Exception:
        logger.exception("Failed to send daily summary")

def send_weekly_portfolio():
    """Optional weekly portfolio digest email."""
    with connect() as conn:
        rows = conn.execute("""
            SELECT p.* FROM positions p
            INNER JOIN (
                SELECT isin, MAX(fetched_at) as latest
                FROM positions GROUP BY isin
            ) l ON p.isin = l.isin AND p.fetched_at = l.latest
            ORDER BY pl_pct DESC
        """).fetchall()

    positions = [dict(r) for r in rows]
    total_value = sum(p["quantity"] * (p["current_price"] or 0) for p in positions)
    total_pl = sum(p["pl_eur"] or 0 for p in positions)

    pos_rows = "".join(
        f"<tr>"
        f"<td style='padding:6px 12px'>{p['name']}</td>"
        f"<td style='padding:6px 12px; text-align:right; color:{'#10b981' if (p['pl_pct'] or 0) >= 0 else '#ef4444'}'>"
        f"{(p['pl_pct'] or 0):+.2f}%</td>"
        f"<td style='padding:6px 12px; text-align:right; color:{'#10b981' if (p['pl_eur'] or 0) >= 0 else '#ef4444'}'>"
        f"€{(p['pl_eur'] or 0):+.2f}</td>"
        f"</tr>"
        for p in positions
    )

    body = f"""
    <html><body style="font-family: sans-serif; max-width: 600px; margin: 0 auto;">
    <h2 style="color:#2d3748">Weekly Portfolio Digest</h2>
    <p><strong>Total Value:</strong> €{total_value:.2f} &nbsp;|&nbsp;
       <strong>Total P&L:</strong> <span style="color:{'#10b981' if total_pl >= 0 else '#ef4444'}">€{total_pl:+.2f}</span></p>
    <table style="width:100%; border-collapse:collapse; margin-top:16px">
        <thead><tr style="background:#f7fafc">
            <th style="padding:8px 12px; text-align:left">Position</th>
            <th style="padding:8px 12px; text-align:right">P&L %</th>
            <th style="padding:8px 12px; text-align:right">P&L €</th>
        </tr></thead>
        <tbody>{pos_rows}</tbody>
    </table>
    </body></html>
    """
    try:
        send_email(f"Weekly Portfolio — €{total_value:.2f} total value", body)
    except Exception:
        logger.exception("Failed to send weekly portfolio digest")
