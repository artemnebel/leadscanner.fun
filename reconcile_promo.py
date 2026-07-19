#!/usr/bin/env python3
"""
Reconcile the founding-user promo blast against Resend's ACTUAL delivery logs,
and record who received it in the database (users.promo_sent_at) so we never
re-spam them.

Why this exists: a 200 from the send API only means Resend *accepted* the
request. The free plan caps delivery at 100 emails/day, so some of the 143
sends may not have gone out. Resend's logs are the only source of truth.

Requires a READ-capable Resend API key — the app's send-only key returns 401 on
GET /emails. Put a full-access key in .env or .env.local as RESEND_READ_API_KEY.

    python reconcile_promo.py            # dry run: report who got it / who didn't
    python reconcile_promo.py --write    # also set users.promo_sent_at for delivered
"""
import argparse
import os
import sys
from collections import Counter
from datetime import datetime

import httpx
from dotenv import load_dotenv

import send_promo
from database import SessionLocal, User, init_db

load_dotenv()
load_dotenv(".env.local", override=True)

# last_event buckets
RECEIVED = {"delivered", "sent", "opened", "clicked"}          # got the email
PENDING  = {"queued", "scheduled", "delivery_delayed"}          # not confirmed yet
FAILED   = {"bounced", "complained", "canceled", "failed"}      # did not arrive


def get_key():
    return os.getenv("RESEND_READ_API_KEY") or os.getenv("RESEND_API_KEY")


def is_promo(e):
    subj = (e.get("subject") or "").lower()
    return "first 1,000" in subj or "first 1000" in subj


def norm_to(e):
    to = e.get("to")
    if isinstance(to, list):
        return [str(t).strip().lower() for t in to]
    if isinstance(to, str):
        return [to.strip().lower()]
    return []


def fetch_promo_emails(key):
    """Page through GET /emails (newest first) and keep the promo sends."""
    H = {"Authorization": f"Bearer {key}"}
    out, after = [], None
    for _ in range(20):  # safety cap: 20 pages x 100 = 2000 emails
        params = {"limit": 100}
        if after:
            params["after"] = after
        r = httpx.get("https://api.resend.com/emails", headers=H, params=params, timeout=30)
        if r.status_code != 200:
            sys.exit(f"Resend list failed: {r.status_code} {r.text}")
        body = r.json()
        data = body.get("data", [])
        if not data:
            break
        out.extend(e for e in data if is_promo(e))
        if not body.get("has_more"):
            break
        after = data[-1].get("id")
    return out


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="set users.promo_sent_at for delivered recipients")
    args = ap.parse_args()

    key = get_key()
    if not key:
        sys.exit("No Resend key found. Set RESEND_READ_API_KEY in .env / .env.local.")

    # who we attempted to email (the exact blast list)
    targets = [e.lower() for e in send_promo.free_users_from_csv(send_promo.CSV_PATH)]
    target_set = set(targets)

    emails = fetch_promo_emails(key)
    if not emails:
        sys.exit("No promo emails found in Resend logs. Is the key read-capable?")

    # newest status per recipient (list is newest-first, so first seen wins)
    status_by_email, when_by_email = {}, {}
    for e in emails:
        ev = (e.get("last_event") or "").lower()
        created = e.get("created_at")
        for addr in norm_to(e):
            status_by_email.setdefault(addr, ev)
            if addr not in when_by_email and created:
                when_by_email[addr] = created

    received = {a for a, ev in status_by_email.items() if ev in RECEIVED}
    got = sorted(target_set & received)
    missing = sorted(target_set - received)
    breakdown = Counter(status_by_email.get(a, "not_found") for a in targets)

    print(f"Promo emails in Resend logs : {len(emails)}")
    print(f"Targets (attempted)         : {len(targets)}")
    print(f"Confirmed RECEIVED          : {len(got)}")
    print(f"NOT received                : {len(missing)}")
    print("\nStatus breakdown (across the target list):")
    for ev, n in breakdown.most_common():
        tag = "  <- got it" if ev in RECEIVED else ("  <- pending" if ev in PENDING else "  <- did NOT arrive")
        print(f"  {ev:12} {n:4}{tag}")
    print("\n--- DID NOT receive (re-send candidates) ---")
    for a in missing:
        print(f"  {a}  [{status_by_email.get(a, 'not_found')}]")

    if not args.write:
        print("\nDry run. Re-run with --write to record promo_sent_at for the received users.")
        return

    init_db()  # ensure the promo_sent_at column exists
    db = SessionLocal()
    try:
        users = {u.email.lower(): u for u in db.query(User).all()}
        updated = 0
        for addr in got:
            u = users.get(addr)
            if not u:
                continue
            u.promo_sent_at = parse_ts(when_by_email.get(addr)) or datetime.utcnow()
            updated += 1
        db.commit()
        print(f"\nDB updated: promo_sent_at set for {updated} users.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
