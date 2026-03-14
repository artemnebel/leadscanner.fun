from fastapi import FastAPI, HTTPException, Form, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import subprocess
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
from dotenv import load_dotenv
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

from database import User, get_db, init_db

load_dotenv()

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY               = os.getenv("GOOGLE_MAPS_API_KEY")
JWT_SECRET            = os.getenv("JWT_SECRET", "changeme")
JWT_ALGORITHM         = "HS256"
GOOGLE_CLIENT_ID      = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET  = os.getenv("GOOGLE_CLIENT_SECRET")
STRIPE_SECRET_KEY     = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
BASE_URL              = os.getenv("BASE_URL", "http://localhost:8000")
ADMIN_EMAIL           = os.getenv("ADMIN_EMAIL", "artem.nebel07@gmail.com")

PRICE_IDS = {
    "starter":   os.getenv("STRIPE_PRICE_STARTER"),
    "pro":       os.getenv("STRIPE_PRICE_PRO"),
    "business":  os.getenv("STRIPE_PRICE_BUSINESS"),
    "unlimited": os.getenv("STRIPE_PRICE_UNLIMITED"),
}

TIER_LIMITS = {
    "free":      {"type": "leads", "limit": 100},
    "starter":   {"type": "leads", "limit": 500},
    "pro":       {"type": "leads", "limit": 2500},
    "business":  {"type": "leads", "limit": 7500},
    "unlimited": {"type": "leads", "limit": None},
}

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

NEARBY_URL    = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
DETAILS_URL   = "https://maps.googleapis.com/maps/api/place/details/json"
DETAIL_FIELDS = "name,formatted_phone_number,rating,user_ratings_total,website,url,vicinity,geometry"

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

def usage_info(user: User) -> dict:
    if user.email == ADMIN_EMAIL:
        return {"type": "leads", "used": user.leads_used, "limit": None, "tier": "unlimited"}
    cfg = TIER_LIMITS[user.tier]
    return {"type": "leads", "used": user.leads_used, "limit": cfg["limit"], "tier": user.tier}

# ── Page routes ───────────────────────────────────────────────────────────────

def _ctx(request: Request):
    return {"request": request, "version": APP_VERSION}

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

@app.get("/privacy")
async def serve_privacy(request: Request):
    return templates.TemplateResponse("privacy.html", _ctx(request))

@app.get("/terms")
async def serve_terms(request: Request):
    return templates.TemplateResponse("terms.html", _ctx(request))

@app.get("/admin")
async def serve_admin(request: Request):
    return templates.TemplateResponse("admin.html", _ctx(request))

@app.get("/sitemap.xml")
async def serve_sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")

@app.get("/favicon.ico")
async def serve_favicon():
    return FileResponse("static/favicon-48.png", media_type="image/png")

@app.get("/robots.txt")
async def serve_robots():
    return FileResponse("static/robots.txt", media_type="text/plain")

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

@app.get("/api/auth/me")
async def me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return {"email": user.email, "tier": user.tier, "usage": usage_info(user)}

@app.get("/api/admin/users")
async def admin_users(user=Depends(get_current_user), db: Session = Depends(get_db)):
    if not user or user.email != ADMIN_EMAIL:
        raise HTTPException(status_code=403, detail="Forbidden.")
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {
            "email": u.email,
            "tier": u.tier,
            "leads_used": u.leads_used,
            "usage_reset": str(u.usage_reset),
            "created_at": str(u.created_at),
            "has_stripe": bool(u.stripe_customer_id),
            "google": bool(u.google_id),
        }
        for u in users
    ]

@app.get("/api/auth/google")
async def google_login():
    params = (
        f"client_id={GOOGLE_CLIENT_ID}"
        f"&redirect_uri={BASE_URL}/api/auth/google/callback"
        "&response_type=code"
        "&scope=openid%20email%20profile"
        "&access_type=offline"
    )
    return RedirectResponse(f"https://accounts.google.com/o/oauth2/v2/auth?{params}")

@app.get("/api/auth/google/callback")
async def google_callback(code: str, db: Session = Depends(get_db)):
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
            raise HTTPException(status_code=400, detail="Google OAuth failed.")

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

# ── Billing routes ────────────────────────────────────────────────────────────

class CheckoutBody(BaseModel):
    tier: str

@app.post("/api/billing/checkout")
async def create_checkout(
    body: CheckoutBody,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(status_code=401, detail="Login required.")
    price_id = PRICE_IDS.get(body.tier)
    if not price_id:
        raise HTTPException(status_code=400, detail="Invalid tier.")

    if not user.stripe_customer_id:
        customer = stripe.Customer.create(email=user.email)
        user.stripe_customer_id = customer.id
        db.commit()

    session = stripe.checkout.Session.create(
        customer=user.stripe_customer_id,
        payment_method_types=["card"],
        line_items=[{"price": price_id, "quantity": 1}],
        mode="subscription",
        success_url=f"{BASE_URL}/dashboard?upgraded=1",
        cancel_url=f"{BASE_URL}/pricing",
    )
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

    ev_type = event["type"]
    sub = event["data"]["object"]

    if ev_type in ("customer.subscription.updated", "customer.subscription.created"):
        customer_id = sub.get("customer")
        status = sub.get("status")
        price_id = sub["items"]["data"][0]["price"]["id"] if sub.get("items") else None

        tier = "free"
        for t, pid in PRICE_IDS.items():
            if pid == price_id:
                tier = t
                break

        user = db.query(User).filter(User.stripe_customer_id == customer_id).first()
        if user:
            user.tier = tier if status == "active" else "free"
            user.stripe_subscription_id = sub.get("id")
            db.commit()

    elif ev_type == "customer.subscription.deleted":
        customer_id = sub.get("customer")
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

# ── Search ─────────────────────────────────────────────────────────────────────

class SearchRequest(BaseModel):
    category: str
    lat: float
    lng: float
    radius_meters: int


async def get_nearby_places(
    client: httpx.AsyncClient, lat: float, lng: float, radius: int, category: str
) -> list:
    place_ids = []
    params = {
        "location": f"{lat},{lng}",
        "radius": radius,
        "keyword": category,
        "key": API_KEY,
    }

    for page_num in range(3):
        if page_num > 0:
            await asyncio.sleep(2)

        resp = await client.get(NEARBY_URL, params=params)
        data = resp.json()
        status = data.get("status")

        if status == "ZERO_RESULTS":
            break
        if status != "OK":
            error_msg = data.get("error_message", "")
            raise HTTPException(
                status_code=400,
                detail=f"Places API error: {status}" + (f" — {error_msg}" if error_msg else "")
            )

        for place in data.get("results", []):
            place_ids.append(place["place_id"])

        next_token = data.get("next_page_token")
        if not next_token:
            break

        params = {"pagetoken": next_token, "key": API_KEY}

    return place_ids


async def get_place_details(client: httpx.AsyncClient, place_id: str):
    resp = await client.get(
        DETAILS_URL,
        params={"place_id": place_id, "fields": DETAIL_FIELDS, "key": API_KEY},
    )
    data = resp.json()
    if data.get("status") != "OK":
        return None
    return data.get("result")


async def get_details_batch(client: httpx.AsyncClient, place_ids: list, batch_size: int = 10) -> list:
    results = []
    for i in range(0, len(place_ids), batch_size):
        batch = place_ids[i : i + batch_size]
        batch_results = await asyncio.gather(
            *[get_place_details(client, pid) for pid in batch],
            return_exceptions=True,
        )
        for r in batch_results:
            if isinstance(r, dict):
                results.append(r)
    return results


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

    reset_usage_if_needed(user, db)

    # Pre-check: block if user already hit their monthly lead limit
    cfg = TIER_LIMITS["unlimited"] if user.email == ADMIN_EMAIL else TIER_LIMITS.get(user.tier, TIER_LIMITS["free"])
    if cfg["limit"] is not None and user.leads_used >= cfg["limit"]:
        raise HTTPException(status_code=429, detail="LIMIT_REACHED")

    center = {"lat": req.lat, "lng": req.lng}

    async with httpx.AsyncClient(timeout=30.0) as client:
        place_ids = await get_nearby_places(
            client, req.lat, req.lng, req.radius_meters, req.category
        )
        all_details = await get_details_batch(client, place_ids)

        leads = []
        skipped_has_website = 0
        for place in all_details:
            if place.get("website"):
                skipped_has_website += 1
                continue
            if not place.get("formatted_phone_number"):
                continue

            geo = place.get("geometry", {}).get("location", {})
            leads.append(
                {
                    "name": place.get("name", "Unknown"),
                    "phone": place.get("formatted_phone_number", ""),
                    "city": place.get("vicinity", ""),
                    "rating": place.get("rating"),
                    "reviews": place.get("user_ratings_total"),
                    "maps_url": place.get("url", ""),
                    "lat": geo.get("lat"),
                    "lng": geo.get("lng"),
                }
            )

    leads_count = len(leads)

    limit_reached = False
    if cfg["limit"] is not None:
        remaining = cfg["limit"] - user.leads_used
        if remaining <= 0:
            raise HTTPException(status_code=429, detail="LIMIT_REACHED")
        if leads_count > remaining:
            leads = leads[:remaining]
            leads_count = remaining
            limit_reached = True

    user.leads_used += leads_count
    db.commit()

    return {
        "center": center,
        "leads": leads,
        "total_found": len(all_details),
        "skipped_has_website": skipped_has_website,
        "usage": usage_info(user),
        "limit_reached": limit_reached,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
