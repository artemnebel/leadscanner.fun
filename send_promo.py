#!/usr/bin/env python3
"""
Founding-user promo email for Lead Scanner.

Usage:
    python send_promo.py                 # TEST: sends only to TEST_RECIPIENT
    python send_promo.py --live          # LIVE: previews the free-user list (dry run)
    python send_promo.py --live --yes    # LIVE: actually sends to every free user in the CSV

The LIVE blast to arbitrary recipients requires a *verified domain* in Resend.
The default 'onboarding@resend.dev' sender only delivers to your own verified
address, which is why the TEST goes to TEST_RECIPIENT and works today.
"""

import argparse
import csv
import os
import sys
import time

import httpx
from dotenv import load_dotenv

from database import SessionLocal, User

load_dotenv()
load_dotenv(".env.local", override=True)

# ── Config ────────────────────────────────────────────────────────────────────
FROM_ADDR       = "Lead Scanner <artem@leadscanner.fun>"   # verified domain sender
REPLY_TO        = "artem.nebel07@gmail.com"                # replies land in your Gmail
TEST_RECIPIENT  = "artem.nebel07@gmail.com"
PRICING_URL     = "https://leadscanner.fun/pricing"
UNSUB_MAILTO    = "mailto:artem.nebel07@gmail.com?subject=unsubscribe"
CSV_PATH        = "Leadscanner Customers - Leadscanner Customers.csv"
SUBJECT         = "You're one of our first 1,000: lock in Pro at $49/mo before it goes up"
PRICE           = "$49"

# Brand palette (from static/style.css)
BG_FRAME   = "#050505"
BG_CARD    = "#0a0a0a"
GREEN      = "#33ff00"
GREEN_DIM  = "#2a6a2a"
GREEN_DARK = "#123512"
AMBER      = "#ffb000"
BODY_TEXT  = "#c8d6c8"
MUTED      = "#7f9a7f"
FONT       = "'JetBrains Mono','Fira Code','Courier New',Courier,monospace"


def build_html() -> str:
    return f"""\
<!DOCTYPE html>
<html lang="en" xmlns="http://www.w3.org/1999/xhtml">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="X-UA-Compatible" content="IE=edge">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  <title>Lead Scanner</title>
  <style>
    @media only screen and (max-width:620px) {{
      .container {{ width:100% !important; }}
      .outer    {{ padding:18px 8px !important; }}
      .px       {{ padding-left:22px !important; padding-right:22px !important; }}
      .cardpx   {{ padding-left:18px !important; padding-right:18px !important; }}
      .logo     {{ font-size:17px !important; }}
      .hero     {{ font-size:22px !important; }}
      .price    {{ font-size:32px !important; }}
      .cta      {{ padding-left:24px !important; padding-right:24px !important; font-size:15px !important; }}
    }}
  </style>
</head>
<body style="margin:0;padding:0;background:{BG_FRAME};-webkit-text-size-adjust:100%;-ms-text-size-adjust:100%;">
  <!-- preheader (hidden preview text) -->
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{BG_FRAME};font-size:1px;line-height:1px;">
    As one of our first 1,000 users you can lock in Pro at today's {PRICE}/mo before the price goes up.
  </div>

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{BG_FRAME}" style="background:{BG_FRAME};">
    <tr>
      <td align="center" class="outer" style="padding:32px 16px;">

        <table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" class="container" style="width:600px;max-width:600px;background:{BG_CARD};border:1px solid {GREEN_DIM};">

          <!-- terminal title bar -->
          <tr>
            <td style="padding:12px 20px;border-bottom:1px solid {GREEN_DARK};font-family:{FONT};font-size:12px;color:{MUTED};letter-spacing:1px;">
              <span style="color:#ff5f56;">&#9679;</span>
              <span style="color:{AMBER};">&#9679;</span>
              <span style="color:{GREEN};">&#9679;</span>
              &nbsp;&nbsp;leadscanner.fun &middot; /promo
            </td>
          </tr>

          <!-- logo -->
          <tr>
            <td class="px" style="padding:34px 40px 8px 40px;font-family:{FONT};">
              <span class="logo" style="color:{GREEN};font-size:22px;font-weight:bold;letter-spacing:1px;">&#9608;&nbsp;&gt;_ LEAD_SCANNER.EXE</span>
            </td>
          </tr>

          <!-- badge -->
          <tr>
            <td class="px" style="padding:14px 40px 0 40px;font-family:{FONT};">
              <span style="display:inline-block;color:{AMBER};font-size:12px;font-weight:bold;letter-spacing:2px;border:1px solid {AMBER};padding:6px 12px;">
                // FOUNDING USER &middot; FIRST 1,000
              </span>
            </td>
          </tr>

          <!-- headline -->
          <tr>
            <td class="px" style="padding:22px 40px 0 40px;font-family:{FONT};">
              <div class="hero" style="color:#ffffff;font-size:26px;line-height:1.3;font-weight:bold;">
                You were here early.
              </div>
            </td>
          </tr>

          <!-- body -->
          <tr>
            <td class="px" style="padding:18px 40px 0 40px;font-family:{FONT};color:{BODY_TEXT};font-size:15px;line-height:1.7;">
              <p style="margin:0 0 16px 0;">
                You're one of the <strong style="color:{GREEN};">first 1,000 people</strong> to use Lead Scanner,
                back when it was just a tool I built because I wished it existed when I freelanced.
              </p>
              <p style="margin:0 0 16px 0;">
                Demand has grown enough that the free tier now pauses at busy times, and Pro pricing
                is going up soon. Before it does, I wanted to give the people who showed up first a
                chance to lock in today's rate.
              </p>
            </td>
          </tr>

          <!-- offer card -->
          <tr>
            <td class="px" style="padding:22px 40px 0 40px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border:1px solid {GREEN_DIM};background:#071007;">
                <tr>
                  <td class="cardpx" style="padding:24px 26px;font-family:{FONT};">
                    <div style="color:{GREEN};font-size:15px;font-weight:bold;letter-spacing:1px;">PRO &#9889;</div>
                    <div style="margin:8px 0 4px 0;">
                      <span class="price" style="color:#ffffff;font-size:38px;font-weight:bold;">{PRICE}</span>
                      <span style="color:{MUTED};font-size:15px;"> / month</span>
                    </div>
                    <div style="color:{AMBER};font-size:12px;letter-spacing:1px;margin-bottom:16px;">LOCKED-IN RATE &middot; PRICE GOES UP SOON</div>
                    <div style="border-top:1px solid {GREEN_DARK};padding-top:16px;color:{BODY_TEXT};font-size:14px;line-height:2;">
                      <div>&gt; Priority access: unlimited scans, no waiting</div>
                      <div>&gt; 15&#8202;mi scan radius (3&#215; the free range)</div>
                      <div>&gt; Multi-scan: sweep several areas at once</div>
                      <div>&gt; Unlimited client portal: call, notes, status</div>
                      <div>&gt; Export to CSV / JSON / XLSX</div>
                    </div>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <!-- CTA button (bulletproof, square = on-brand) -->
          <tr>
            <td class="px" style="padding:28px 40px 8px 40px;" align="center">
              <table role="presentation" cellpadding="0" cellspacing="0" border="0">
                <tr>
                  <td bgcolor="{GREEN}" style="background:{GREEN};">
                    <a href="{PRICING_URL}" target="_blank" class="cta"
                       style="display:inline-block;padding:16px 40px;font-family:{FONT};font-size:16px;font-weight:bold;color:#03210b;text-decoration:none;letter-spacing:1px;">
                      [ &#9889; LOCK IN {PRICE}/MO ]
                    </a>
                  </td>
                </tr>
              </table>
            </td>
          </tr>

          <tr>
            <td class="px" style="padding:0 40px;font-family:{FONT};" align="center">
              <div style="color:{MUTED};font-size:12px;">Cancel anytime. One won client usually pays for years of Pro.</div>
            </td>
          </tr>

          <!-- sign-off -->
          <tr>
            <td class="px" style="padding:30px 40px 0 40px;font-family:{FONT};color:{BODY_TEXT};font-size:14px;line-height:1.7;">
              <div style="border-top:1px solid {GREEN_DARK};padding-top:22px;">
                Thanks for being here from the start,<br>
                <span style="color:{GREEN};">Artem, Lead Scanner</span>
              </div>
            </td>
          </tr>

          <!-- footer -->
          <tr>
            <td class="px" style="padding:26px 40px 34px 40px;font-family:{FONT};color:{MUTED};font-size:11px;line-height:1.8;border-top:1px solid {GREEN_DARK};margin-top:20px;">
              <div>&gt;_ <a href="https://leadscanner.fun" target="_blank" style="color:{MUTED};text-decoration:underline;">leadscanner.fun</a></div>
              <div>You're receiving this because you have a free Lead Scanner account.</div>
              <div><a href="{UNSUB_MAILTO}" style="color:{MUTED};text-decoration:underline;">Unsubscribe</a></div>
            </td>
          </tr>

        </table>

      </td>
    </tr>
  </table>
</body>
</html>
"""


def build_text() -> str:
    return f"""\
>_ LEAD_SCANNER.EXE

// FOUNDING USER · FIRST 1,000

You were here early.

You're one of the first 1,000 people to use Lead Scanner, back when it was
just a tool I built because I wished it existed when I freelanced.

Demand has grown enough that the free tier now pauses at busy times, and Pro
pricing is going up soon. Before it does, I wanted to give the people who
showed up first a chance to lock in today's rate.

PRO: {PRICE}/month  (locked-in rate; price goes up soon)
  > Priority access: unlimited scans, no waiting
  > 15mi scan radius (3x the free range)
  > Multi-scan: sweep several areas at once
  > Unlimited client portal: call, notes, status
  > Export to CSV / JSON / XLSX

Lock in {PRICE}/mo:  {PRICING_URL}

Cancel anytime. One won client usually pays for years of Pro.

Thanks for being here from the start,
Artem, Lead Scanner

leadscanner.fun
You're receiving this because you have a free Lead Scanner account.
Unsubscribe: {UNSUB_MAILTO}
"""


def send(to_list, api_key):
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "from": FROM_ADDR,
            "to": to_list,
            "reply_to": REPLY_TO,
            "subject": SUBJECT,
            "html": build_html(),
            "text": build_text(),
        },
        timeout=30,
    )
    return resp


def free_users_from_csv(path):
    emails = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("email") or "").strip()
            tier  = (row.get("tier") or "").strip().lower()
            paid  = (row.get("paid_stripe") or "").strip().lower()
            if email and tier == "free" and paid == "no":
                emails.append(email)
    # de-dupe, preserve order
    seen, out = set(), []
    for e in emails:
        k = e.lower()
        if k not in seen:
            seen.add(k)
            out.append(e)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="target all free users in the CSV")
    ap.add_argument("--yes", action="store_true", help="actually send the live blast (otherwise dry-run)")
    ap.add_argument("--resend-all", action="store_true",
                    help="also send to users already marked promo_sent_at (default: skip them)")
    args = ap.parse_args()

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        sys.exit("RESEND_API_KEY not set (check .env).")

    if not args.live:
        print(f"TEST -> sending to {TEST_RECIPIENT} ...")
        r = send([TEST_RECIPIENT], api_key)
        print(f"status={r.status_code} body={r.text}")
        return

    recipients = free_users_from_csv(CSV_PATH)
    print(f"LIVE: {len(recipients)} free users found in {CSV_PATH}")

    # Suppression: skip anyone already recorded as sent (promo_sent_at set) so a
    # re-send only reaches people who missed the first blast. The authoritative
    # "sent" mark is written by reconcile_promo.py from Resend's delivery logs.
    if not args.resend_all:
        db = SessionLocal()
        try:
            already = {
                (e or "").lower()
                for (e,) in db.query(User.email).filter(User.promo_sent_at.isnot(None)).all()
            }
        finally:
            db.close()
        before = len(recipients)
        recipients = [e for e in recipients if e.lower() not in already]
        print(f"Skipping {before - len(recipients)} already-sent (promo_sent_at set); "
              f"{len(recipients)} remaining. Use --resend-all to override.")

    if not args.yes:
        print("Dry run - re-run with --yes to actually send.")
        for e in recipients[:10]:
            print(f"  {e}")
        if len(recipients) > 10:
            print(f"  ... and {len(recipients) - 10} more")
        return

    # NOTE: requires a verified sending domain in Resend, or delivery will fail
    # for addresses other than your own verified address.
    sent, failed = 0, 0
    for e in recipients:
        r = send([e], api_key)
        ok = r.status_code == 200
        sent += ok
        failed += (not ok)
        print(f"  {'OK ' if ok else 'ERR'} {e} ({r.status_code})")
        time.sleep(0.6)  # stay under Resend rate limits
    print(f"\nDone. sent={sent} failed={failed}")


if __name__ == "__main__":
    main()
