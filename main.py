from fastapi import FastAPI, HTTPException, Form, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import httpx
import asyncio
import os
import stripe
from datetime import date
from dotenv import load_dotenv
from jose import JWTError, jwt
from passlib.context import CryptContext
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

PRICE_IDS = {
    "starter":   os.getenv("STRIPE_PRICE_STARTER"),
    "pro":       os.getenv("STRIPE_PRICE_PRO"),
    "business":  os.getenv("STRIPE_PRICE_BUSINESS"),
    "unlimited": os.getenv("STRIPE_PRICE_UNLIMITED"),
}

TIER_LIMITS = {
    "free":      {"type": "leads", "limit": 25},
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
app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
init_db()

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer  = HTTPBearer(auto_error=False)

# ── Auth helpers ─────────────────────────────────────────────────────────────

def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)

def verify_password(pw: str, hashed: str) -> bool:
    return pwd_ctx.verify(pw, hashed)

def create_jwt(user_id: str) -> str:
    return jwt.encode({"sub": user_id}, JWT_SECRET, algorithm=JWT_ALGORITHM)

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
    cfg = TIER_LIMITS[user.tier]
    return {"type": "leads", "used": user.leads_used, "limit": cfg["limit"], "tier": user.tier}

# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/")
async def serve_index():
    return FileResponse("static/index.html")

@app.get("/about")
async def serve_about():
    return FileResponse("static/about.html")

@app.get("/how-it-works")
async def serve_how_it_works():
    return FileResponse("static/how-it-works.html")

@app.get("/cold-calling")
async def serve_cold_calling():
    return FileResponse("static/cold-calling.html")

@app.get("/resources")
async def serve_resources():
    return FileResponse("static/resources.html")

@app.get("/contact")
async def serve_contact():
    return FileResponse("static/contact.html")

@app.get("/login")
async def serve_login():
    return FileResponse("static/login.html")

@app.get("/signup")
async def serve_signup():
    return FileResponse("static/signup.html")

@app.get("/pricing")
async def serve_pricing():
    return FileResponse("static/pricing.html")

@app.get("/dashboard")
async def serve_dashboard():
    return FileResponse("static/dashboard.html")

@app.get("/sitemap.xml")
async def serve_sitemap():
    return FileResponse("static/sitemap.xml", media_type="application/xml")

@app.get("/favicon.ico")
async def serve_favicon():
    return FileResponse("static/favicon-48.png", media_type="image/png")

# ── Contact form ──────────────────────────────────────────────────────────────

@app.post("/api/contact")
async def send_contact(
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
):
    resend_key = os.getenv("RESEND_API_KEY")
    if not resend_key:
        raise HTTPException(status_code=500, detail="Email not configured on server.")

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {resend_key}"},
            json={
                "from": "Lead Scanner <onboarding@resend.dev>",
                "to": ["artem.nebel07@gmail.com"],
                "reply_to": f"{name} <{email}>",
                "subject": f"[LeadScanner] {subject}",
                "text": f"From: {name} <{email}>\n\n{message}\n\n---\nSent via leadscanner.fun",
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
async def register(body: AuthBody, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = User(email=body.email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"token": create_jwt(user.id), "user": {"email": user.email, "tier": user.tier}}

@app.post("/api/auth/login")
async def login(body: AuthBody, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if not user or not user.password_hash or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return {"token": create_jwt(user.id), "user": {"email": user.email, "tier": user.tier}}

@app.get("/api/auth/me")
async def me(user=Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    return {"email": user.email, "tier": user.tier, "usage": usage_info(user)}

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

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    else:
        import json
        event = json.loads(payload)

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
async def search_leads(
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
    cfg = TIER_LIMITS.get(user.tier, TIER_LIMITS["free"])
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

    if cfg["limit"] is not None and user.leads_used + leads_count > cfg["limit"]:
        raise HTTPException(status_code=429, detail="LIMIT_REACHED")
    user.leads_used += leads_count
    db.commit()

    return {
        "center": center,
        "leads": leads,
        "total_found": len(all_details),
        "skipped_has_website": skipped_has_website,
        "usage": usage_info(user),
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
