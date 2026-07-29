from fastapi import FastAPI, HTTPException, Form, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import math
import subprocess
import json
import hashlib
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from pydantic import BaseModel
import httpx
import asyncio
import os
import stripe
from datetime import date, datetime, timezone, timedelta
import re
from urllib.parse import urlencode, quote
import secrets
from dotenv import load_dotenv
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session
from sqlalchemy import text, func

from database import User, PlacesCache, SearchLog, SavedClient, get_db, init_db
from send_promo import (
    build_html as build_promo_html,
    build_text as build_promo_text,
    SUBJECT as PROMO_SUBJECT,
    FROM_ADDR as PROMO_FROM_ADDR,
    REPLY_TO as PROMO_REPLY_TO,
)

load_dotenv()
load_dotenv(".env.local", override=True)  # local overrides — not committed

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY               = os.getenv("GOOGLE_MAPS_API_KEY")
JWT_SECRET            = os.getenv("JWT_SECRET", "changeme")
JWT_ALGORITHM         = "HS256"
GOOGLE_CLIENT_ID      = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET  = os.getenv("GOOGLE_CLIENT_SECRET")
FACEBOOK_APP_ID       = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET   = os.getenv("FACEBOOK_APP_SECRET")
GITHUB_CLIENT_ID      = os.getenv("GITHUB_CLIENT_ID")
GITHUB_CLIENT_SECRET  = os.getenv("GITHUB_CLIENT_SECRET")
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
BASE_URL              = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL           = os.getenv("ADMIN_EMAIL", "artem.nebel07@gmail.com")
DEFAULT_SCAN_CAP      = int(os.getenv("DEFAULT_SCAN_CAP", "50"))  # hidden lifetime scan cap per user

# Legacy subscription price IDs — kept so existing subscribers stay grandfathered.
PRICE_IDS = {
    "starter":   os.getenv("STRIPE_PRICE_STARTER"),
    "pro":       os.getenv("STRIPE_PRICE_PRO"),
    "business":  os.getenv("STRIPE_PRICE_BUSINESS"),
    "unlimited": os.getenv("STRIPE_PRICE_UNLIMITED"),
}

# New Pro subscription prices (monthly + annual). Both grant tier="pro".
PRO_PRICE_MONTHLY = os.getenv("STRIPE_PRICE_PRO_MONTHLY")
PRO_PRICE_ANNUAL  = os.getenv("STRIPE_PRICE_PRO_ANNUAL")

# All subscription price IDs → tier, used by the Stripe webhook. Legacy tiers keep
# their names; the new Pro monthly/annual prices both map to "pro".
SUBSCRIPTION_PRICE_TO_TIER = {pid: tier for tier, pid in PRICE_IDS.items() if pid}
for _pid in (PRO_PRICE_MONTHLY, PRO_PRICE_ANNUAL):
    if _pid:
        SUBSCRIPTION_PRICE_TO_TIER[_pid] = "pro"

# ── Catalogue ──────────────────────────────────────────────────────────────
# One-time lead packs, exactly as sold on /pricing.
PACK_PRICE_IDS = {
    "solo":     os.getenv("STRIPE_PRICE_PACK_SOLO"),       # $15
    "business": os.getenv("STRIPE_PRICE_PACK_BUSINESS"),   # $49
    "scale":    os.getenv("STRIPE_PRICE_PACK_SCALE"),      # $89
}
PACK_LEADS = {"solo": 100, "business": 500, "scale": 1000}

# price_id -> leads granted, used by the webhook.
PACK_CREDITS = {pid: PACK_LEADS[name] for name, pid in PACK_PRICE_IDS.items() if pid}
# Retired packs, folded in so a webhook replay for an older purchase still
# resolves. STRIPE_PRICE_PACK_BUSINESS is deliberately absent here: that name is
# now the $49/500 pack above, so point it at the new Stripe price.
for _pid, _leads in {
    os.getenv("STRIPE_PRICE_PACK_MINI"):    100,
    os.getenv("STRIPE_PRICE_PACK_STARTER"): 300,
    os.getenv("STRIPE_PRICE_PACK_PRO"):     650,
    os.getenv("STRIPE_PRICE_PACK_BULK"):   2600,
}.items():
    if _pid and _pid not in PACK_CREDITS:
        PACK_CREDITS[_pid] = _leads

# Recurring lead plans, as sold on /pricing. Each renewal tops the balance up.
AGENCY_PRICE_IDS = {
    "agency":      os.getenv("STRIPE_PRICE_AGENCY"),       # $20/mo
    "agency-plus": os.getenv("STRIPE_PRICE_AGENCY_PLUS"),  # $39/mo
}
AGENCY_LEADS = {"agency": 300, "agency-plus": 700}
AGENCY_PRICE_TO_LEADS = {pid: AGENCY_LEADS[n] for n, pid in AGENCY_PRICE_IDS.items() if pid}
ROLLOVER_MONTHS = 3   # unused leads roll over up to this many months' worth

# ── Plan gating ────────────────────────────────────────────────────────────
# Everyone now scans against a lead balance. The only uncapped accounts are the
# admin and anyone already on a legacy paid tier, who stay grandfathered.
FREE_RADIUS_M     = 8_000     # ~5mi
PRO_RADIUS_M      = 24_140    # ~15mi
FREE_PORTAL_LIMIT = 5
PAID_TIERS = {"pro", "starter", "business", "unlimited"}

# The old per-tier monthly caps are gone with the allotment logic: grandfathered
# paid tiers are uncapped and everyone else spends from lead_credits.

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
# Text Search field mask. Phone is in Enterprise tier; rating/userRatingCount push
# the call to Enterprise + Atmosphere ($40/1K vs $35/1K) — a flat ~$5/1K calls more,
# regardless of how many places each call returns.
# nextPageToken must be named in the mask or the API omits it and pagination
# silently stops after the first 20 results, whatever max_pages says.
FIELD_MASK        = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.googleMapsUri,places.location,places.businessStatus,"
    "places.websiteUri,"
    "places.nationalPhoneNumber,places.rating,places.userRatingCount,"
    "nextPageToken"
)

# ── App ──────────────────────────────────────────────────────────────────────
def _get_version():
    try:
        return subprocess.check_output(
            ['git', 'rev-parse', '--short', 'HEAD'],
            stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return os.environ.get('RENDER_GIT_COMMIT', 'v1')[:7]

APP_VERSION = _get_version()

limiter = Limiter(key_func=get_remote_address)
app = FastAPI()
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="static")
init_db()

bearer  = HTTPBearer(auto_error=False)

# ── Auth helpers ─────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw[:72].encode(), bcrypt.gensalt()).decode()

def verify_password(pw: str, hashed: str) -> bool:
    return bcrypt.checkpw(pw[:72].encode(), hashed.encode())

def create_jwt(user_id: str) -> str:
    exp = datetime.now(timezone.utc) + timedelta(days=30)
    return jwt.encode({"sub": user_id, "exp": exp}, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_jwt(token: str):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None

def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(bearer),
    db: Session = Depends(get_db),
):
    if not creds:
        return None
    user_id = decode_jwt(creds.credentials)
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()

def reset_usage_if_needed(user: User, db: Session):
    today = date.today()
    if user.usage_reset.month != today.month or user.usage_reset.year != today.year:
        user.scans_used = 0
        user.leads_used = 0
        user.usage_reset = today
        db.commit()

def has_unlimited_leads(user: User) -> bool:
    """Accounts that scan without drawing down a balance.

    Only the admin and anyone already sitting on a legacy paid tier. Everyone
    else, new or old, now spends leads from lead_credits."""
    if user is None:
        return False
    return user.email == ADMIN_EMAIL or (user.tier in PAID_TIERS)

def available_leads(user: User) -> int | None:
    """Leads the user can still scan right now. None = uncapped."""
    if has_unlimited_leads(user):
        return None
    return max(0, user.lead_credits or 0)

def consume_leads(user: User, count: int) -> None:
    """Charge N delivered leads to the user.

    leads_used is incremented for everyone because the admin dashboard reports
    on it. The balance is only drawn down for metered accounts."""
    user.leads_used = (user.leads_used or 0) + count
    if has_unlimited_leads(user):
        return
    user.lead_credits = max(0, (user.lead_credits or 0) - count)

def grant_leads(user: User, count: int, cap_months: int | None = None) -> int:
    """Add leads to the balance, optionally capping it at cap_months' worth.

    Used by the recurring plans so unused leads roll over but do not pile up
    forever. Returns the number actually added."""
    before = user.lead_credits or 0
    after = before + count
    if cap_months is not None:
        after = min(after, count * cap_months)
    user.lead_credits = max(before, after)   # never take leads away
    return user.lead_credits - before

def usage_info(user: User) -> dict:
    available = available_leads(user)
    return {
        "type": "leads",
        "tier": user.tier,
        "free_used": user.leads_used or 0,
        "free_limit": None,
        "credits": user.lead_credits or 0,
        "available": available,                     # None = uncapped
        "used": user.leads_used or 0,               # legacy field, kept for old UI
        "limit": None,                              # legacy field, kept for old UI
    }

# ── Plan gating ────────────────────────────────────────────────────────────────

def is_pro(user: User) -> bool:
    """True if the user has any paid/elevated plan. Admin is always Pro."""
    if user is None:
        return False
    return user.email == ADMIN_EMAIL or (user.tier in PAID_TIERS)

def plan_features(user: User) -> dict:
    """Single source of truth for what a user's plan unlocks."""
    pro = is_pro(user)
    return {
        "pro": pro,
        "max_radius_m": PRO_RADIUS_M if pro else FREE_RADIUS_M,
        "portal_limit": None if pro else FREE_PORTAL_LIMIT,      # None = uncapped
    }

def plan_payload(user: User) -> dict:
    """Plan info for the frontend. There is no longer a rolling scan window:
    scanning is limited by the lead balance alone."""
    feats = plan_features(user)
    return {
        "pro": feats["pro"],
        "max_radius_m": feats["max_radius_m"],
        "portal_limit": feats["portal_limit"],
        "leads_available": available_leads(user),   # None = uncapped
        # Retired fields, still sent so older cached clients do not break.
        "daily_scan_limit": None,
        "daily_remaining": None,
        "daily_reset": None,
    }

# ── Page routes ───────────────────────────────────────────────────────────────

def _ctx(request: Request):
    # facebook_enabled gates the "Continue with Facebook" button in templates so it
    # only shows once the app's FB credentials are configured (e.g. on Render). Keeps
    # a broken button off production while Facebook setup/verification is pending.
    return {
        "request": request,
        "version": APP_VERSION,
        "facebook_enabled": bool(FACEBOOK_APP_ID and FACEBOOK_APP_SECRET),
        "github_enabled": bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET),
    }

@app.get("/")
async def serve_index(request: Request):
    return templates.TemplateResponse("index.html", _ctx(request))

@app.get("/about")
async def serve_about(request: Request):
    return templates.TemplateResponse("about.html", _ctx(request))

@app.get("/how-it-works")
async def serve_how_it_works(request: Request):
    return templates.TemplateResponse("how-it-works.html", _ctx(request))

@app.get("/cold-calling")
async def serve_cold_calling(request: Request):
    return templates.TemplateResponse("cold-calling.html", _ctx(request))

@app.get("/resources")
async def serve_resources(request: Request):
    return templates.TemplateResponse("resources.html", _ctx(request))

@app.get("/contact")
async def serve_contact(request: Request):
    return templates.TemplateResponse("contact.html", _ctx(request))

@app.get("/login")
async def serve_login(request: Request):
    return templates.TemplateResponse("login.html", _ctx(request))

@app.get("/signup")
async def serve_signup(request: Request):
    return templates.TemplateResponse("signup.html", _ctx(request))

@app.get("/pricing")
async def serve_pricing(request: Request):
    return templates.TemplateResponse("pricing.html", _ctx(request))

@app.get("/dashboard")
async def serve_dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", _ctx(request))

@app.get("/clients")
async def serve_clients(request: Request):
    return templates.TemplateResponse("clients.html", _ctx(request))

@app.get("/forgot-password")
async def serve_forgot_password(request: Request):
    return templates.TemplateResponse("forgot-password.html", _ctx(request))

@app.get("/reset-password")
async def serve_reset_password(request: Request):
    return templates.TemplateResponse("reset-password.html", _ctx(request))

@app.get("/privacy")
async def serve_privacy(request: Request):
    return templates.TemplateResponse("privacy.html", _ctx(request))

@app.get("/terms")
async def serve_terms(request: Request):
    return templates.TemplateResponse("terms.html", _ctx(request))

@app.get("/data-deletion")
async def serve_data_deletion(request: Request):
    return templates.TemplateResponse("data-deletion.html", _ctx(request))

@app.get("/admin")
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", _ctx(request))

@app.get("/admin/users")
async def serve_admin_users(request: Request):
    return templates.TemplateResponse("admin-users.html", _ctx(request))

@app.get("/admin/user")
async def serve_admin_user_detail(request: Request):
    return templates.TemplateResponse("admin-user-detail.html", _ctx(request))

@app.api_route("/sitemap.xml", methods=["GET", "HEAD"])
async def serve_sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")

@app.api_route("/favicon.ico", methods=["GET", "HEAD"])
async def serve_favicon():
    return FileResponse("static/favicon-48.png", media_type="image/png")

@app.api_route("/robots.txt", methods=["GET", "HEAD"])
async def serve_robots():
    return FileResponse("static/robots.txt", media_type="text/plain")

@app.api_route("/llms.txt", methods=["GET", "HEAD"])
async def serve_llms_txt():
    return FileResponse("static/llms.txt", media_type="text/plain")

# ── Contact form ──────────────────────────────────────────────────────────────

def _strip_headers(s: str) -> str:
    """Remove newlines and carriage returns to prevent email header injection."""
    return re.sub(r'[\r\n]+', ' ', s).strip()

@app.post("/api/contact")
async def send_contact(
    name: str = Form(..., max_length=100),
    email: str = Form(..., max_length=254),
    subject: str = Form(..., max_length=200),
    message: str = Form(..., max_length=5000),
):
    resend_key = os.getenv("RESEND_API_KEY")
    if not resend_key:
        raise HTTPException(status_code=500, detail="Email not configured on server.")

    safe_name    = _strip_headers(name)
    safe_email   = _strip_headers(email)
    safe_subject = _strip_headers(subject)

    if not re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', safe_email):
        raise HTTPException(status_code=400, detail="Invalid email address.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={
                "from": "Lead Scanner <onboarding@resend.dev>",
                "to": [os.getenv("CONTACT_EMAIL", "artem.nebel07@gmail.com")],
                "reply_to": f"{safe_name} <{safe_email}>",
                "subject": f"[LeadScanner] {safe_subject}",
                "text": f"From: {safe_name} <{safe_email}>\n\n{message}\n\n---\nSent via leadscanner.fun",
            },
        )

    if resp.status_code != 200:
        raise HTTPException(status_code=500, detail=resp.text)

    return {"ok": True}

# ── Auth routes ───────────────────────────────────────────────────────────────

class AuthBody(BaseModel):
    email: str
    password: str

@app.post("/api/auth/register")
@limiter.limit("10/minute")
async def register(request: Request, body: AuthBody, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_jwt(user.id), "user": {"email": user.email, "tier": user.tier}}

@app.post("/api/auth/login")
@limiter.limit("20/minute")
async def login(request: Request, body: AuthBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if user and not user.password_hash:
        raise HTTPException(status_code=401, detail="GOOGLE_ACCOUNT")
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return {"token": create_jwt(user.id), "user": {"email": user.email, "tier": user.tier}}

class ForgotPasswordBody(BaseModel):
    email: str

class ResetPasswordBody(BaseModel):
    token: str
    password: str

@app.post("/api/auth/forgot-password")
@limiter.limit("5/minute")
async def forgot_password(request: Request, body: ForgotPasswordBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    # Always return 200 to avoid leaking which emails are registered
    if not user:
        return {"ok": True}

    token = secrets.token_urlsafe(32)
    user.reset_token = token
    user.reset_token_expires = datetime.now(timezone.utc) + timedelta(hours=1)
    db.commit()

    reset_url = f"{BASE_URL}/reset-password?token={token}"
    # Always log the link so it's available in server console during local dev
    print(f"\n[PASSWORD RESET] {user.email} → {reset_url}\n", flush=True)

    resend_key = os.getenv("RESEND_API_KEY")
    if resend_key:
        # onboarding@resend.dev can only deliver to verified addresses.
        # Send to the user's email directly; also CC admin so it arrives
        # even when the recipient hasn't verified with Resend.
        recipients = [user.email]
        if user.email != ADMIN_EMAIL:
            recipients.append(ADMIN_EMAIL)
        async with httpx.AsyncClient() as client:
            r = await client.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {resend_key}"},
                json={
                    "from": "Lead Scanner <onboarding@resend.dev>",
                    "to": recipients,
                    "subject": f"Password reset for {user.email}",
                    "text": (
                        f"Password reset requested for: {user.email}\n\n"
                        f"Reset link (expires in 1 hour):\n{reset_url}\n\n"
                        "If you didn't request this, ignore this email."
                    ),
                },
            )
        print(f"[RESEND] status={r.status_code} body={r.text}", flush=True)

    return {"ok": True}

@app.post("/api/auth/reset-password")
@limiter.limit("10/minute")
async def reset_password(request: Request, body: ResetPasswordBody, db: Session = Depends(get_db)):
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    user = db.query(User).filter(User.reset_token == body.token).first()
    if not user or not user.reset_token_expires:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link.")
    expires = user.reset_token_expires
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires:
        raise HTTPException(status_code=400, detail="Reset link has expired. Please request a new one.")
    user.password_hash = hash_password(body.password)
    user.reset_token = None
    user.reset_token_expires = None
    db.commit()
    return {"ok": True}

class SetPasswordBody(BaseModel):
    password: str
    current_password: str | None = None

@app.post("/api/auth/set-password")
@limiter.limit("10/minute")
async def set_password(
    request: Request,
    body: SetPasswordBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Let a logged-in user set (or change) their password. Google-only accounts
    have no password yet, so they can set one directly to enable email + password
    login. Accounts that already have a password must confirm the current one."""
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    if user.password_hash:
        if not body.current_password or not verify_password(body.current_password, user.password_hash):
            raise HTTPException(status_code=400, detail="Current password is incorrect.")
    user.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True, "has_password": True}

@app.get("/api/healthz")
async def healthz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        return {"ok": True, "db": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"db error: {e}")

@app.get("/api/auth/me")
async def me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    is_admin = user.email == ADMIN_EMAIL
    effective_tier = "unlimited" if is_admin else user.tier
    return {
        "email": user.email,
        "tier": effective_tier,
        "is_admin": is_admin,
        "has_password": bool(user.password_hash),  # false for Google-only accounts
        "google": bool(user.google_id),
        "plan": plan_payload(user),
        "usage": usage_info(user),
    }

@app.get("/api/admin/users")
async def admin_users(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    users = db.query(User).order_by(User.created_at.desc()).all()
    # One aggregate over the search log gives per-user engagement signals for the table.
    agg = {
        email: {
            "search_count": cnt or 0,
            "last_active": str(last) if last else None,
            "leads_found": int(total or 0),
        }
        for email, cnt, last, total in db.query(
            SearchLog.user_email,
            func.count(SearchLog.id),
            func.max(SearchLog.created_at),
            func.sum(SearchLog.results),
        ).group_by(SearchLog.user_email).all()
    }
    return [
        {
            "email": u.email,
            "tier": u.tier,
            "leads_used": u.leads_used,
            "scans_used": u.scans_used or 0,
            "total_scans": u.total_scans or 0,
            "scan_limit": u.scan_limit if u.scan_limit is not None else DEFAULT_SCAN_CAP,
            "lead_credits": u.lead_credits or 0,
            "usage_reset": str(u.usage_reset),
            "created_at": str(u.created_at),
            "promo_sent_at": str(u.promo_sent_at) if u.promo_sent_at else None,
            "promo_status": u.promo_status,
            "has_stripe": bool(u.stripe_customer_id),
            "google": bool(u.google_id),
            "search_count": agg.get(u.email, {}).get("search_count", 0),
            "last_active": agg.get(u.email, {}).get("last_active"),
            "leads_found": agg.get(u.email, {}).get("leads_found", 0),
        }
        for u in users
    ]

@app.get("/api/admin/user-detail")
async def admin_user_detail(email: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    target = db.query(User).filter(User.email == email).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    rows = (
        db.query(SearchLog)
        .filter(SearchLog.user_email == email)
        .order_by(SearchLog.created_at.desc())
        .limit(1000)
        .all()
    )

    # Favorite categories (case-insensitive) + distinct active days.
    cat_counts: dict[str, dict] = {}
    active_days: set[str] = set()
    total_results = 0
    for r in rows:
        total_results += r.results or 0
        if r.created_at:
            active_days.add(str(r.created_at)[:10])
        c = (r.category or "").strip()
        if c:
            key = c.lower()
            if key not in cat_counts:
                cat_counts[key] = {"category": c, "count": 0}
            cat_counts[key]["count"] += 1
    top_categories = sorted(cat_counts.values(), key=lambda x: x["count"], reverse=True)

    dates = [r.created_at for r in rows if r.created_at]

    return {
        "user": {
            "email": target.email,
            "tier": target.tier,
            "scans_used": target.scans_used or 0,
            "total_scans": target.total_scans or 0,
            "scan_limit": target.scan_limit if target.scan_limit is not None else DEFAULT_SCAN_CAP,
            "leads_used": target.leads_used or 0,
            "lead_credits": target.lead_credits or 0,
            "usage_reset": str(target.usage_reset),
            "created_at": str(target.created_at),
            "google": bool(target.google_id),
            "has_stripe": bool(target.stripe_customer_id),
        },
        "stats": {
            "total_searches": len(rows),
            "first_seen": str(min(dates)) if dates else None,
            "last_active": str(max(dates)) if dates else None,
            "active_days": len(active_days),
            "total_results": total_results,
            "top_categories": top_categories,
        },
        "searches": [
            {
                "category": r.category,
                "radius_meters": r.radius_meters,
                "results": r.results,
                "lat": r.lat,
                "lng": r.lng,
                "created_at": str(r.created_at),
            }
            for r in rows
        ],
    }

@app.post("/api/admin/set-scan-limit")
async def admin_set_scan_limit(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    body = await request.json()
    email = (body.get("email") or "").strip()
    try:
        scan_limit = int(body.get("scan_limit"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="scan_limit must be an integer.")
    if scan_limit < 0:
        raise HTTPException(status_code=400, detail="scan_limit must be >= 0.")
    target = db.query(User).filter(User.email == email).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    target.scan_limit = scan_limit
    db.commit()
    return {"ok": True, "email": target.email, "scan_limit": target.scan_limit}

# last_event values that mean the recipient actually got the email
PROMO_RECEIVED_EVENTS = {"delivered", "sent", "opened", "clicked"}

@app.post("/api/admin/reconcile-promo")
async def admin_reconcile_promo(user=Depends(get_current_user), db: Session = Depends(get_db)):
    """Read Resend's delivery log and stamp users.promo_sent_at for everyone who
    actually received the founding-user promo, so we never re-email them. A 200
    from the send API is not proof of delivery — this is the source of truth."""
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    key = os.getenv("RESEND_READ_API_KEY") or os.getenv("RESEND_API_KEY")
    if not key:
        raise HTTPException(status_code=400, detail="RESEND_READ_API_KEY not set in the server environment.")

    # Page through the sent-email log, keeping only the promo sends.
    promo, after = [], None
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(20):  # safety cap: 20 pages x 100
            params = {"limit": 100}
            if after:
                params["after"] = after
            r = await client.get(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {key}"},
                params=params,
            )
            if r.status_code == 401:
                raise HTTPException(status_code=400,
                    detail="Resend key is not read-capable — needs a Full-access key.")
            if r.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Resend list failed: {r.status_code}")
            body = r.json()
            data = body.get("data", [])
            if not data:
                break
            for e in data:
                subj = (e.get("subject") or "").lower()
                if "first 1,000" in subj or "first 1000" in subj:
                    promo.append(e)
            if not body.get("has_more"):
                break
            after = data[-1].get("id")

    # newest status + timestamp per recipient (log is newest-first)
    status_by_email, when_by_email = {}, {}
    for e in promo:
        ev = (e.get("last_event") or "").lower()
        created = e.get("created_at")
        to = e.get("to")
        addrs = to if isinstance(to, list) else ([to] if to else [])
        for a in addrs:
            a = str(a).strip().lower()
            status_by_email.setdefault(a, ev)
            if a not in when_by_email and created:
                when_by_email[a] = created

    received_set = {a for a, ev in status_by_email.items() if ev in PROMO_RECEIVED_EVENTS}
    all_recipients = list(status_by_email.keys())

    updated, already = 0, 0
    if all_recipients:
        for u in db.query(User).filter(func.lower(User.email).in_(all_recipients)).all():
            key = u.email.lower()
            u.promo_status = status_by_email.get(key)  # always reflect the latest status
            if key in received_set:
                if u.promo_sent_at is None:
                    ts = when_by_email.get(key)
                    dt = None
                    if ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).replace(tzinfo=None)
                        except Exception:
                            dt = None
                    u.promo_sent_at = dt or datetime.utcnow()
                    updated += 1
                else:
                    already += 1
        db.commit()

    not_received = sorted(a for a, ev in status_by_email.items() if ev not in PROMO_RECEIVED_EVENTS)
    return {
        "promo_emails_scanned": len(promo),
        "received": len(received_set),
        "users_updated": updated,
        "already_marked": already,
        "not_received": [{"email": a, "status": status_by_email[a]} for a in not_received],
    }

class DeleteUserBody(BaseModel):
    email: str

@app.post("/api/admin/delete-user")
async def admin_delete_user(
    body: DeleteUserBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Permanently delete a user and purge their saved clients and search logs so
    nothing is left orphaned. Does not touch Stripe — an active subscription must
    be cancelled there separately."""
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    email = (body.email or "").strip()
    if not email:
        raise HTTPException(status_code=400, detail="Email required.")
    target = db.query(User).filter(User.email == email).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    if target.email == ADMIN_EMAIL:
        raise HTTPException(status_code=400, detail="Cannot delete the admin account.")

    clients_deleted = (
        db.query(SavedClient)
        .filter(SavedClient.user_id == target.id)
        .delete(synchronize_session=False)
    )
    logs_deleted = (
        db.query(SearchLog)
        .filter(SearchLog.user_email == target.email)
        .delete(synchronize_session=False)
    )
    db.delete(target)
    db.commit()
    return {
        "ok": True,
        "email": email,
        "saved_clients_deleted": clients_deleted,
        "search_logs_deleted": logs_deleted,
    }

class SendPromoBody(BaseModel):
    emails: list[str]

@app.post("/api/admin/send-promo")
async def admin_send_promo(
    body: SendPromoBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Send the founding-user promo email to a hand-picked list of users and stamp
    promo_sent_at on each successful send so they're suppressed from future blasts.
    A 200 from Resend only means the send was accepted — reconcile-promo later
    corrects promo_status from the delivery log."""
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        raise HTTPException(status_code=400, detail="RESEND_API_KEY not set in the server environment.")

    # de-dupe (case-insensitive), preserve order
    seen, recipients = set(), []
    for e in body.emails:
        e = (e or "").strip()
        k = e.lower()
        if e and k not in seen:
            seen.add(k)
            recipients.append(e)
    if not recipients:
        raise HTTPException(status_code=400, detail="No recipients selected.")

    html_body = build_promo_html()
    text_body = build_promo_text()
    sent, failed, results = 0, 0, []
    async with httpx.AsyncClient(timeout=30) as client:
        for e in recipients:
            ok, err = False, None
            try:
                r = await client.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "from": PROMO_FROM_ADDR,
                        "to": [e],
                        "reply_to": PROMO_REPLY_TO,
                        "subject": PROMO_SUBJECT,
                        "html": html_body,
                        "text": text_body,
                    },
                )
                ok = r.status_code == 200
                if not ok:
                    err = f"HTTP {r.status_code}"
            except Exception as ex:
                err = str(ex)
            if ok:
                sent += 1
                target = db.query(User).filter(func.lower(User.email) == e.lower()).first()
                if target:
                    target.promo_sent_at = datetime.utcnow()
                    target.promo_status = "sent"
                results.append({"email": e, "ok": True})
            else:
                failed += 1
                results.append({"email": e, "ok": False, "error": err})
            await asyncio.sleep(0.4)  # stay under Resend's rate limit
    db.commit()
    return {"sent": sent, "failed": failed, "results": results}

@app.get("/api/admin/stats")
async def admin_stats(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    days = 30
    start = date.today() - timedelta(days=days - 1)

    def daily(rows):
        by_day = {str(d)[:10]: int(c or 0) for d, c in rows}
        return [
            {"date": str(start + timedelta(days=i)), "count": by_day.get(str(start + timedelta(days=i)), 0)}
            for i in range(days)
        ]

    signups = db.query(func.date(User.created_at), func.count(User.id)) \
        .filter(User.created_at >= start).group_by(func.date(User.created_at)).all()
    searches = db.query(func.date(SearchLog.created_at), func.count(SearchLog.id)) \
        .filter(SearchLog.created_at >= start).group_by(func.date(SearchLog.created_at)).all()

    return {"signups_by_day": daily(signups), "searches_by_day": daily(searches)}

@app.get("/api/admin/searches")
async def admin_searches(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    rows = db.query(SearchLog).order_by(SearchLog.created_at.desc()).limit(300).all()
    return [
        {
            "email": r.user_email,
            "category": r.category,
            "lat": r.lat,
            "lng": r.lng,
            "radius_meters": r.radius_meters,
            "results": r.results,
            "created_at": str(r.created_at),
        }
        for r in rows
    ]

@app.get("/api/auth/google")
async def google_login():
    redirect_uri = f"{BASE_URL}/api/auth/google/callback"
    params = urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
    })
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_callback(
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error or not code:
        return RedirectResponse("/?auth_error=google_failed")
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": f"{BASE_URL}/api/auth/google/callback",
                "grant_type": "authorization_code",
            },
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            return RedirectResponse(f"/?auth_error=google_failed")

        userinfo_resp = await client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        userinfo = userinfo_resp.json()

    email = userinfo.get("email")
    g_id  = userinfo.get("sub")

    if not email:
        raise HTTPException(status_code=400, detail="No email from Google.")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, google_id=g_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.google_id:
        user.google_id = g_id
        db.commit()

    jwt_token = create_jwt(user.id)
    return RedirectResponse(f"/?token={jwt_token}")

# ── Facebook OAuth ─────────────────────────────────────────────────────────────
FACEBOOK_OAUTH_VERSION = "v19.0"

@app.get("/api/auth/facebook")
async def facebook_login():
    if not FACEBOOK_APP_ID or not FACEBOOK_APP_SECRET:
        raise HTTPException(status_code=500, detail="Facebook login not configured.")
    redirect_uri = f"{BASE_URL}/api/auth/facebook/callback"
    params = urlencode({
        "client_id": FACEBOOK_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "email public_profile",
    })
    return RedirectResponse(
        f"https://www.facebook.com/{FACEBOOK_OAUTH_VERSION}/dialog/oauth?{params}"
    )

@app.get("/api/auth/facebook/callback")
async def facebook_callback(
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error or not code:
        return RedirectResponse("/?auth_error=facebook_failed")
    redirect_uri = f"{BASE_URL}/api/auth/facebook/callback"
    async with httpx.AsyncClient() as client:
        token_resp = await client.get(
            f"https://graph.facebook.com/{FACEBOOK_OAUTH_VERSION}/oauth/access_token",
            params={
                "client_id": FACEBOOK_APP_ID,
                "client_secret": FACEBOOK_APP_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse("/?auth_error=facebook_failed")

        userinfo_resp = await client.get(
            "https://graph.facebook.com/me",
            params={"fields": "id,name,email", "access_token": access_token},
        )
        userinfo = userinfo_resp.json()

    fb_id = userinfo.get("id")
    email = userinfo.get("email")
    if not fb_id:
        return RedirectResponse("/?auth_error=facebook_failed")
    # Facebook doesn't always return an email (no email on the account, or the user
    # unchecked the email permission). We need one to create/link the account.
    if not email:
        return RedirectResponse("/?auth_error=facebook_no_email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, facebook_id=fb_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.facebook_id:
        user.facebook_id = fb_id
        db.commit()

    jwt_token = create_jwt(user.id)
    return RedirectResponse(f"/?token={jwt_token}")

# ── GitHub OAuth ───────────────────────────────────────────────────────────────
GITHUB_API_HEADERS = {"Accept": "application/json", "User-Agent": "LeadScanner"}

@app.get("/api/auth/github")
async def github_login():
    if not GITHUB_CLIENT_ID or not GITHUB_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="GitHub login not configured.")
    redirect_uri = f"{BASE_URL}/api/auth/github/callback"
    params = urlencode({
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": "read:user user:email",
        "allow_signup": "true",
    })
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")

@app.get("/api/auth/github/callback")
async def github_callback(
    code: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if error or not code:
        return RedirectResponse("/?auth_error=github_failed")
    redirect_uri = f"{BASE_URL}/api/auth/github/callback"
    async with httpx.AsyncClient() as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            headers=GITHUB_API_HEADERS,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
                "redirect_uri": redirect_uri,
            },
        )
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse("/?auth_error=github_failed")

        auth_headers = {**GITHUB_API_HEADERS, "Authorization": f"Bearer {access_token}"}
        gh = (await client.get("https://api.github.com/user", headers=auth_headers)).json()
        gh_id = str(gh.get("id")) if gh.get("id") is not None else None
        email = gh.get("email")
        # GitHub omits the email when it's set to private — fetch the verified primary.
        if not email:
            try:
                emails = (await client.get("https://api.github.com/user/emails", headers=auth_headers)).json()
            except Exception:
                emails = []
            if isinstance(emails, list):
                chosen = (next((e for e in emails if e.get("primary") and e.get("verified")), None)
                          or next((e for e in emails if e.get("verified")), None))
                if chosen:
                    email = chosen.get("email")

    if not gh_id:
        return RedirectResponse("/?auth_error=github_failed")
    if not email:
        return RedirectResponse("/?auth_error=github_no_email")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, github_id=gh_id)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.github_id:
        user.github_id = gh_id
        db.commit()

    jwt_token = create_jwt(user.id)
    return RedirectResponse(f"/?token={jwt_token}")

# ── Billing routes ────────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    pack: str | None = None
    tier: str | None = None  # legacy field name from older clients; treated as pack

@app.post("/api/billing/checkout")
async def create_checkout(
    body: CheckoutBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")

    pack_name = (body.pack or body.tier or "").lower()
    price_id = PACK_PRICE_IDS.get(pack_name)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid pack.")

    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="payment",
        success_url=f"{BASE_URL}/dashboard?credits_added=1",
        cancel_url=f"{BASE_URL}/pricing",
        metadata={"user_id": user.id, "pack": pack_name},
    )
    return {"url": session.url}

class SubscribeBody(BaseModel):
    plan: str | None = "agency"  # "agency" | "agency-plus"

@app.post("/api/billing/subscribe")
async def create_subscription(
    body: SubscribeBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a recurring lead plan checkout. Each renewal tops the user's lead
    balance up via the invoice.paid webhook. The old Pro tier is no longer sold;
    its price IDs stay wired only so existing subscribers keep working."""
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    if not STRIPE_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Payments not configured.")

    plan = (body.plan or "agency").lower()
    price_id = AGENCY_PRICE_IDS.get(plan)
    if not price_id:
        raise HTTPException(status_code=400, detail="Plan not configured.")

    def _create_session(customer_id: str):
        return stripe.checkout.Session.create(
            customer=customer_id,
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=f"{BASE_URL}/dashboard?upgraded=1&plan={plan}&sid={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{BASE_URL}/pricing",
            metadata={"user_id": user.id, "plan": plan},
        )

    try:
        if not user.stripe_customer_id:
            customer = stripe.Customer.create(email=user.email)
            user.stripe_customer_id = customer.id
            db.commit()
        try:
            session = _create_session(user.stripe_customer_id)
        except stripe.InvalidRequestError as e:
            # A stripe_customer_id saved under a different mode/account (e.g. a live
            # customer while using a test key) 404s here — recreate it once and retry.
            if "customer" in str(e).lower():
                customer = stripe.Customer.create(email=user.email)
                user.stripe_customer_id = customer.id
                db.commit()
                session = _create_session(user.stripe_customer_id)
            else:
                raise
    except stripe.StripeError as e:
        # Surface Stripe's actual message (e.g. "No such price … a similar object
        # exists in live mode …") instead of a generic 500 → "Network error".
        msg = getattr(e, "user_message", None) or str(e)
        raise HTTPException(status_code=400, detail=f"Stripe error: {msg}")

    return {"url": session.url}

@app.post("/api/billing/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()
    sig = request.headers.get("stripe-signature", "")

    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=500, detail="Webhook secret not configured.")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")

    # Convert to plain dict — newer Stripe SDK's StripeObject doesn't expose dict.get
    event_dict = event.to_dict() if hasattr(event, "to_dict") else event
    ev_type = event_dict["type"]
    obj = event_dict["data"]["object"]

    # ── New pricing: one-time credit pack purchases ─────────────────────────
    if ev_type == "checkout.session.completed":
        # Pro subscription checkout: grant the plan immediately (don't wait for the
        # separate customer.subscription.created event).
        if obj.get("mode") == "subscription":
            metadata = obj.get("metadata") or {}
            user_id = metadata.get("user_id")
            customer_id = obj.get("customer")
            user = None
            if user_id:
                user = db.query(User).filter(User.id == user_id).first()
            if not user and customer_id:
                user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
            if user:
                user.tier = "pro"
                user.stripe_subscription_id = obj.get("subscription")
                db.commit()
                print(f"[webhook] pro subscription activated for {user.email}", flush=True)
            return {"ok": True}

        if obj.get("payment_status") != "paid":
            return {"ok": True}

        metadata = obj.get("metadata") or {}
        user_id = metadata.get("user_id")
        pack_name = (metadata.get("pack") or "").lower()
        customer_id = obj.get("customer")

        # Resolve credits: prefer pack name from metadata, fall back to price_id lookup.
        credits_to_add = 0
        price_id = PACK_PRICE_IDS.get(pack_name)
        if price_id and price_id in PACK_CREDITS:
            credits_to_add = PACK_CREDITS[price_id]
        else:
            try:
                items_obj = stripe.checkout.Session.list_line_items(obj["id"], limit=10)
                items = items_obj.to_dict() if hasattr(items_obj, "to_dict") else items_obj
                for item in items.get("data", []):
                    pid = (item.get("price") or {}).get("id")
                    if pid in PACK_CREDITS:
                        credits_to_add += PACK_CREDITS[pid] * (item.get("quantity") or 1)
            except Exception as e:
                print(f"[webhook] failed to list line items: {e}", flush=True)

        if credits_to_add <= 0:
            return {"ok": True}

        user = None
        if user_id:
            user = db.query(User).filter(User.id == user_id).first()
        if not user and customer_id:
            user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.lead_credits = (user.lead_credits or 0) + credits_to_add
            db.commit()
            print(f"[webhook] granted {credits_to_add} credits to {user.email}", flush=True)

    # ── Recurring lead plans: every paid invoice tops the balance up ─────────
    # invoice.paid fires for the first charge and every renewal, so this is the
    # single place leads are granted. Stripe retries webhooks, so the invoice id
    # is recorded and replays are ignored rather than granting twice.
    elif ev_type in ("invoice.paid", "invoice.payment_succeeded"):
        customer_id = obj.get("customer")
        invoice_id = obj.get("id")
        lines = (obj.get("lines") or {}).get("data") or []
        leads_to_grant = 0
        for line in lines:
            pid = ((line.get("price") or {}).get("id")
                   or ((line.get("pricing") or {}).get("price_details") or {}).get("price"))
            if pid in AGENCY_PRICE_TO_LEADS:
                leads_to_grant += AGENCY_PRICE_TO_LEADS[pid] * (line.get("quantity") or 1)

        if leads_to_grant > 0 and customer_id:
            user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
            if user and invoice_id and user.last_invoice_id == invoice_id:
                print(f"[webhook] invoice {invoice_id} already granted, skipping", flush=True)
                return {"ok": True}
            if user:
                added = grant_leads(user, leads_to_grant, cap_months=ROLLOVER_MONTHS)
                user.last_invoice_id = invoice_id
                db.commit()
                print(f"[webhook] granted {added} leads to {user.email} "
                      f"(invoice {invoice_id}, balance {user.lead_credits})", flush=True)

    # ── Legacy subscription events (grandfathered subscribers only) ──────────
    elif ev_type in ("customer.subscription.updated", "customer.subscription.created"):
        customer_id = obj.get("customer")
        status = obj.get("status")
        price_id = obj["items"]["data"][0]["price"]["id"] if obj.get("items") else None

        tier = SUBSCRIPTION_PRICE_TO_TIER.get(price_id, "free")

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.tier = tier if status == "active" else "free"
            user.stripe_subscription_id = obj.get("id")
            db.commit()

    elif ev_type == "customer.subscription.deleted":
        customer_id = obj.get("customer")
        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.tier = "free"
            user.stripe_subscription_id = None
            db.commit()

    return {"ok": True}

@app.get("/api/billing/portal")
async def billing_portal(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    if not user.stripe_customer_id:
        raise HTTPException(status_code=400, detail="No billing account found.")

    session = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=f"{BASE_URL}/dashboard",
    )
    return {"url": session.url}

# ── Client portal (saved leads / mini-CRM) ─────────────────────────────────────

VALID_CLIENT_STATUSES = {"new", "contacted", "interested", "won", "lost"}

class SaveClientBody(BaseModel):
    business_name: str
    phone: str | None = ""
    city: str | None = ""
    maps_url: str | None = ""
    rating: float | None = None
    reviews: int | None = None

class UpdateClientBody(BaseModel):
    status: str | None = None
    notes: str | None = None

def _client_to_dict(c: SavedClient) -> dict:
    return {
        "id": c.id,
        "business_name": c.business_name,
        "phone": c.phone,
        "city": c.city,
        "maps_url": c.maps_url,
        "rating": c.rating,
        "reviews": c.reviews,
        "status": c.status,
        "notes": c.notes or "",
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }

@app.get("/api/clients")
async def list_clients(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    rows = (
        db.query(SavedClient)
        .filter(SavedClient.user_id == user.id)
        .order_by(SavedClient.created_at.desc())
        .all()
    )
    return {"clients": [_client_to_dict(c) for c in rows], "plan": plan_payload(user)}

@app.post("/api/clients")
async def save_client(body: SaveClientBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")

    # Skip duplicates (same business already saved by this user).
    if body.maps_url:
        existing = (
            db.query(SavedClient)
            .filter(SavedClient.user_id == user.id, SavedClient.maps_url == body.maps_url)
            .first()
        )
        if existing:
            return {"client": _client_to_dict(existing), "duplicate": True}

    # Free plan is capped at FREE_PORTAL_LIMIT saved clients (a taste of the CRM);
    # Pro is uncapped.
    limit = plan_features(user)["portal_limit"]
    if limit is not None:
        count = db.query(SavedClient).filter(SavedClient.user_id == user.id).count()
        if count >= limit:
            raise HTTPException(status_code=403, detail={"code": "PORTAL_LIMIT_REACHED", "limit": limit})

    client = SavedClient(
        user_id=user.id,
        business_name=body.business_name,
        phone=body.phone or "",
        city=body.city or "",
        maps_url=body.maps_url or "",
        rating=body.rating,
        reviews=body.reviews,
        status="new",
        notes="",
    )
    db.add(client)
    db.commit()
    db.refresh(client)
    return {"client": _client_to_dict(client)}

@app.patch("/api/clients/{client_id}")
async def update_client(client_id: str, body: UpdateClientBody, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    client = (
        db.query(SavedClient)
        .filter(SavedClient.id == client_id, SavedClient.user_id == user.id)
        .first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    if body.status is not None:
        if body.status not in VALID_CLIENT_STATUSES:
            raise HTTPException(status_code=400, detail="Invalid status.")
        client.status = body.status
    if body.notes is not None:
        client.notes = body.notes[:5000]
    db.commit()
    db.refresh(client)
    return {"client": _client_to_dict(client)}

@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str, user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    client = (
        db.query(SavedClient)
        .filter(SavedClient.id == client_id, SavedClient.user_id == user.id)
        .first()
    )
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    db.delete(client)
    db.commit()
    return {"ok": True}

def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres."""
    R = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _generate_sub_circles(
    lat: float,
    lng: float,
    total_radius_m: float,
    sub_radius_m: float = 8_000.0,
) -> list[tuple[float, float]]:
    """
    Tile the search area with a hexagonal grid of sub-circles so every part of
    a large radius gets its own focused query (overcoming the 60-result API cap).
    Default sub-circle radius is 8 km; for areas <= one sub-circle a single
    center is used. Pass a smaller sub_radius_m to tile a small area densely —
    the 60-result cap is per query, not per area, so finer tiles return more.
    """
    SUB_R = float(sub_radius_m)  # metres — 8 km gives good local density by default

    if total_radius_m <= SUB_R:
        return [(lat, lng)]

    # Hex-grid spacing with 15 % overlap so no gaps at circle edges
    row_step_m = SUB_R * math.sqrt(3) * 0.85
    col_step_m = SUB_R * 2.0 * 0.85

    m_per_lat = 111_320.0
    m_per_lng = 111_320.0 * math.cos(math.radians(lat))

    max_i = math.ceil(total_radius_m / row_step_m)
    max_j = math.ceil(total_radius_m / col_step_m)

    centers: list[tuple[float, float]] = []
    for i in range(-max_i, max_i + 1):
        dlat_m = i * row_step_m
        hex_offset_m = col_step_m * 0.5 if i % 2 != 0 else 0.0
        for j in range(-max_j - 1, max_j + 2):
            dlng_m = j * col_step_m + hex_offset_m
            if math.sqrt(dlat_m ** 2 + dlng_m ** 2) <= total_radius_m:
                centers.append((
                    lat + dlat_m / m_per_lat,
                    lng + dlng_m / m_per_lng,
                ))

    return centers


# ── Search ─────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    category: str
    lat: float
    lng: float
    radius_meters: int



# ── Text Search cache ────────────────────────────────────────────────────────
# Cache Google Text Search results so repeat / overlapping scans don't re-pay.
# Business website status changes slowly, so a long TTL is safe.
CACHE_TTL = timedelta(days=30)


def _cache_key(lat: float, lng: float, radius: int, category: str, max_pages: int) -> str:
    # Round coords to ~110 m so near-identical scans share an entry.
    raw = f"{category.strip().lower()}|{round(lat, 3)}|{round(lng, 3)}|{radius}|{max_pages}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _cache_get(db: Session, key: str):
    row = db.query(PlacesCache).filter(PlacesCache.key == key).first()
    if row and row.created_at and (datetime.utcnow() - row.created_at) < CACHE_TTL:
        try:
            return json.loads(row.payload)
        except Exception:
            return None
    return None


def _cache_put(db: Session, key: str, places: list) -> None:
    try:
        row = db.query(PlacesCache).filter(PlacesCache.key == key).first()
        if row:
            row.payload = json.dumps(places)
            row.created_at = datetime.utcnow()
        else:
            db.add(PlacesCache(key=key, payload=json.dumps(places), created_at=datetime.utcnow()))
        db.commit()
    except Exception:
        db.rollback()


async def get_nearby_places(
    client: httpx.AsyncClient,
    db: Session,
    lat: float,
    lng: float,
    radius: int,
    category: str,
    max_pages: int = 3,
) -> list:
    key = _cache_key(lat, lng, radius, category, max_pages)
    cached = _cache_get(db, key)
    if cached is not None:
        return cached

    places = []
    page_token = None

    for _page in range(max_pages):
        body: dict = {
            "textQuery": category,
            "locationBias": {
                "circle": {
                    "center": {"latitude": lat, "longitude": lng},
                    "radius": float(radius),
                }
            },
            "maxResultCount": 20,
            "rankPreference": "DISTANCE",  # surface nearby locals, not just popular chains
        }
        if page_token:
            body["pageToken"] = page_token

        resp = await client.post(
            PLACES_SEARCH_URL,
            json=body,
            headers={
                "X-Goog-Api-Key": API_KEY,
                "X-Goog-FieldMask": FIELD_MASK,
            },
        )
        data = resp.json()

        if resp.status_code != 200:
            err = data.get("error", {})
            raise HTTPException(
                status_code=400,
                detail=f"Places API error: {err.get('message', 'Unknown error')}",
            )

        results = data.get("places", [])
        if not results:
            break

        places.extend(results)

        page_token = data.get("nextPageToken")
        if not page_token:
            break

        # No inter-page delay: the legacy Places API needed ~2s for a page token
        # to become valid, but on Places API (New) the token works immediately
        # and returns a non-overlapping page.

    _cache_put(db, key, places)
    return places


@app.post("/api/search")
@limiter.limit("15/minute")
async def search_leads(
    request: Request,
    req: SearchRequest,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(status_code=401, detail="LOGIN_REQUIRED")
    if not API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY not set in .env file")

    feats = plan_features(user)

    # Per-plan radius cap, enforced server-side (defends crafted requests that bypass
    # the slider). Free ~5mi, Pro ~15mi. A wide scan tiles into many 8km sub-circles,
    # each a paid Places call, so the cap directly bounds worst-case cost. Silent clamp.
    max_radius = feats["max_radius_m"]
    if req.radius_meters > max_radius:
        req.radius_meters = max_radius

    reset_usage_if_needed(user, db)

    # The rolling 24h scan window is gone. Scanning is bounded by the lead
    # balance alone, so a user with leads can scan as often as they like.
    available_before = available_leads(user)
    if available_before is not None and available_before <= 0:
        raise HTTPException(status_code=429, detail="LIMIT_REACHED")

    center = {"lat": req.lat, "lng": req.lng}

    sub_circles = _generate_sub_circles(req.lat, req.lng, req.radius_meters)
    is_grid = len(sub_circles) > 1
    # For grid mode use 8 km sub-circles and 2 pages each; single mode uses full radius + 3 pages
    sub_radius = 8_000 if is_grid else req.radius_meters
    sub_pages = 2 if is_grid else 3

    _search_sem = asyncio.Semaphore(5)  # max 5 concurrent sub-circle requests

    async with httpx.AsyncClient(timeout=60.0) as client:

        async def _fetch_sub(slat: float, slng: float) -> list:
            async with _search_sem:
                try:
                    return await get_nearby_places(
                        client, db, slat, slng, sub_radius, req.category, max_pages=sub_pages
                    )
                except Exception:
                    return []

        sub_results = await asyncio.gather(*[_fetch_sub(slat, slng) for slat, slng in sub_circles])

        seen_ids: set[str] = set()
        all_places: list = []
        for batch in sub_results:
            for place in batch:
                pid = place.get("id")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    all_places.append(place)

        # Filter to businesses without a website. We trust Text Search's websiteUri
        # field — no per-business Place Details confirmation (that was the dominant cost).
        leads = []
        skipped_has_website = 0
        skipped_no_contact = 0
        for place in all_places:
            if place.get("businessStatus") == "CLOSED_PERMANENTLY":
                continue
            if place.get("websiteUri"):
                skipped_has_website += 1
                continue
            phone = place.get("nationalPhoneNumber", "") or ""
            review_count = place.get("userRatingCount") or 0
            # Require BOTH a phone and at least one review — drop anything missing either signal.
            if not phone or review_count == 0:
                skipped_no_contact += 1
                continue
            geo = place.get("location", {})
            leads.append(
                {
                    "name": place.get("displayName", {}).get("text", "Unknown"),
                    "city": place.get("formattedAddress", ""),
                    "maps_url": place.get("googleMapsUri", ""),
                    "lat": geo.get("latitude"),
                    "lng": geo.get("longitude"),
                    "phone": phone,
                    "rating": place.get("rating"),
                    "reviews": place.get("userRatingCount"),
                }
            )

    leads_count = len(leads)

    limit_reached = False
    available_now = available_leads(user)
    if available_now is not None:
        if available_now <= 0:
            raise HTTPException(status_code=429, detail="LIMIT_REACHED")
        if leads_count > available_now:
            leads = leads[:available_now]
            leads_count = available_now
            limit_reached = True

    user.scans_used = (user.scans_used or 0) + 1  # one /api/search call = one scan (analytics)
    user.total_scans = (user.total_scans or 0) + 1  # lifetime counter (analytics)
    user.daily_scans = (user.daily_scans or 0) + 1  # analytics only; no longer gates anything
    consume_leads(user, leads_count)
    # Log the query for the admin panel. Analytics only — never fail a search over it.
    try:
        db.add(SearchLog(
            user_email=user.email,
            category=req.category,
            lat=req.lat,
            lng=req.lng,
            radius_meters=req.radius_meters,
            results=leads_count,
        ))
    except Exception:
        pass
    db.commit()

    return {
        "center": center,
        "leads": leads,
        "total_found": len(all_places),
        "skipped_has_website": skipped_has_website,
        "skipped_no_contact": skipped_no_contact,
        "usage": usage_info(user),
        "plan": plan_payload(user),
        "limit_reached": limit_reached,
    }


# ── Landing-page demo search ──────────────────────────────────────────────────
# Public (no auth) but bounded by a per-IP allowance of DEMO_SCAN_LIMIT scans
# plus a burst rate limit. Within a scan the demo is deliberately NOT thinned:
# it tiles the full 5-mile disc and pulls every page of every tile, because a
# rich free result set is what sells the paid plan. Spend is bounded by the
# per-IP scan cap and the 30-day Places cache, not by holding results back.

DEMO_RADIUS_M = 8_047    # fixed 5 miles — matches the Free plan's radius.

# The Places API caps any single query at 60 results (3 pages of 20), so one
# call over the whole disc can never return more than 60 no matter how dense
# the area. Tiling into smaller sub-circles gives each tile its own 60-result
# budget: DEMO_SUB_RADIUS_M of 4km yields 7 hex tiles over the 5-mile disc,
# raising the raw ceiling from 60 to ~420 before filtering.
#
# Cost note: an uncached demo scan is len(tiles) * DEMO_MAX_PAGES Places calls
# (7 * 3 = 21 today). Repeat scans of the same area+category are free via the
# 30-day cache. Lower DEMO_SUB_RADIUS_M for more coverage at proportionally
# more spend; raise it toward DEMO_RADIUS_M to collapse back to a single call.
DEMO_SUB_RADIUS_M = 4_000
DEMO_MAX_PAGES    = 3      # 3 = every page the Places API will hand out
DEMO_CONCURRENCY  = 8      # all tiles in flight at once keeps the demo snappy

# How many leads the demo hands over in full. Everything past this ships as a
# bare coordinate: the map still shows how much was found, but the names and
# numbers stay behind the signup. Enforced here rather than in the page so the
# withheld details never reach the browser at all.
DEMO_VISIBLE_LEADS = 3

# Demo scans per IP before the signup wall. One free look, then sign up and buy
# leads. Set DEMO_SCAN_LIMIT=0 in the env to lift the cap entirely — that is how
# local dev scans without hitting the wall, and .env.docker does exactly that.
DEMO_SCAN_LIMIT        = int(os.getenv("DEMO_SCAN_LIMIT", "1"))
DEMO_SCAN_WINDOW_HOURS = 24    # rolling window the allowance refills over

# IP -> timestamps of that IP's demo scans, trimmed to the rolling window.
# Deliberately in-process: no migration, and a restart just hands out fresh
# allowances. This is a friction gate for a public demo, not a security
# control — X-Forwarded-For is client-supplied and therefore spoofable.
_demo_scan_hits: dict[str, list[datetime]] = {}


def _client_ip(request: Request) -> str:
    """Real client IP behind Render's proxy.

    get_remote_address() reads request.client.host, which on Render is the
    edge proxy — every visitor would share one bucket and the demo would shut
    off globally after 3 scans. Prefer the leftmost X-Forwarded-For hop.
    """
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        first = fwd.split(",")[0].strip()
        if first:
            return first
    return request.headers.get("x-real-ip") or get_remote_address(request) or "unknown"


def _demo_scans_remaining(ip: str) -> int:
    """Scans left for this IP, pruning hits that aged out of the window."""
    cutoff = datetime.utcnow() - timedelta(hours=DEMO_SCAN_WINDOW_HOURS)
    hits = [t for t in _demo_scan_hits.get(ip, []) if t > cutoff]
    if hits:
        _demo_scan_hits[ip] = hits
    else:
        _demo_scan_hits.pop(ip, None)
    return max(0, DEMO_SCAN_LIMIT - len(hits))


class DemoSearchRequest(BaseModel):
    category: str
    lat: float
    lng: float


@app.post("/api/demo-search")
@limiter.limit("10/minute")   # burst guard only; DEMO_SCAN_LIMIT is the real cap
async def demo_search(request: Request, req: DemoSearchRequest, db: Session = Depends(get_db)):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY not set in .env file")

    ip = _client_ip(request)
    if DEMO_SCAN_LIMIT > 0 and _demo_scans_remaining(ip) <= 0:
        raise HTTPException(status_code=429, detail="DEMO_LIMIT_REACHED")

    category = req.category.strip()[:60] or "plumbers"
    if not (-90 <= req.lat <= 90 and -180 <= req.lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")

    # Tile the disc and pull every page of every tile, all through the shared
    # 30-day cache. One slow tile must not sink the whole scan, so a failing
    # sub-circle contributes nothing rather than raising.
    tiles = _generate_sub_circles(req.lat, req.lng, DEMO_RADIUS_M, DEMO_SUB_RADIUS_M)
    sem = asyncio.Semaphore(DEMO_CONCURRENCY)

    async with httpx.AsyncClient(timeout=60.0) as client:

        async def _fetch_tile(tlat: float, tlng: float) -> list:
            async with sem:
                try:
                    return await get_nearby_places(
                        client, db, tlat, tlng, DEMO_SUB_RADIUS_M, category,
                        max_pages=DEMO_MAX_PAGES,
                    )
                except Exception:
                    return []

        batches = await asyncio.gather(*[_fetch_tile(t[0], t[1]) for t in tiles])

    # Overlapping tiles return the same business more than once — dedupe by
    # place id, same as the real grid scan.
    seen_ids: set[str] = set()
    places: list = []
    for batch in batches:
        for place in batch:
            pid = place.get("id")
            if pid and pid in seen_ids:
                continue
            if pid:
                seen_ids.add(pid)
            places.append(place)

    # Same lead filter as the real scan: open, no website, phone + reviews.
    leads = []
    for place in places:
        if place.get("businessStatus") == "CLOSED_PERMANENTLY":
            continue
        if place.get("websiteUri"):
            continue
        phone = place.get("nationalPhoneNumber", "") or ""
        review_count = place.get("userRatingCount") or 0
        if not phone or review_count == 0:
            continue
        geo = place.get("location", {})
        # Places uses locationBias (a soft hint), so edge tiles pull in results
        # from outside the disc. Hold the demo to the 5 miles the page promises.
        plat, plng = geo.get("latitude"), geo.get("longitude")
        if plat is None or plng is None:
            continue
        dist = _haversine_m(req.lat, req.lng, plat, plng)
        if dist > DEMO_RADIUS_M:
            continue
        leads.append(
            (dist, {
                "name": place.get("displayName", {}).get("text", "Unknown"),
                "city": place.get("formattedAddress", ""),
                "phone": phone,
                "maps_url": place.get("googleMapsUri", ""),
                "rating": place.get("rating"),
                "reviews": review_count,
                "lat": geo.get("latitude"),
                "lng": geo.get("longitude"),
            })
        )

    # Tiles come back in grid order, which reads as random. Nearest-first makes
    # the list feel like a sweep outward from the visitor's pin.
    leads.sort(key=lambda pair: pair[0])
    leads = [lead for _, lead in leads]

    total = len(leads)

    # Split into what the page may show and what it may only plot. Locked
    # entries carry coordinates and nothing else, so the withheld names,
    # phones and Maps links are never sent to the browser.
    visible = leads[:DEMO_VISIBLE_LEADS]
    locked = [
        {"lat": lead["lat"], "lng": lead["lng"], "locked": True}
        for lead in leads[DEMO_VISIBLE_LEADS:]
    ]

    # Burn one of this IP's demo scans. Charged only once the scan actually
    # succeeded, so a Places outage never costs the visitor an attempt.
    if DEMO_SCAN_LIMIT > 0:
        _demo_scan_hits.setdefault(ip, []).append(datetime.utcnow())

    # Log for the admin panel like a normal search — analytics only.
    try:
        db.add(SearchLog(
            user_email="(demo)",
            category=category,
            lat=req.lat,
            lng=req.lng,
            radius_meters=DEMO_RADIUS_M,
            results=total,
        ))
        db.commit()
    except Exception:
        db.rollback()

    return {
        "leads": visible + locked,
        "total": total,
        "visible": len(visible),
        "locked": len(locked),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
