from fastapi import FastAPI, HTTPException, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
import httpx
import asyncio
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("GOOGLE_MAPS_API_KEY")

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

NEARBY_URL = "https://maps.googleapis.com/maps/api/place/nearbysearch/json"
DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
DETAIL_FIELDS = "name,formatted_phone_number,rating,user_ratings_total,website,url,vicinity,geometry"


class SearchRequest(BaseModel):
    category: str
    lat: float
    lng: float
    radius_meters: int


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


@app.post("/api/contact")
async def send_contact(
    name: str = Form(...),
    email: str = Form(...),
    subject: str = Form(...),
    message: str = Form(...),
):
    gmail_user = os.getenv("GMAIL_USER")
    gmail_password = os.getenv("GMAIL_APP_PASSWORD")

    if not gmail_user or not gmail_password:
        raise HTTPException(status_code=500, detail="Email not configured on server.")

    msg = MIMEMultipart()
    msg["From"] = f"Lead Scanner <{gmail_user}>"
    msg["To"] = "artem.nebel07@gmail.com"
    msg["Reply-To"] = f"{name} <{email}>"
    msg["Subject"] = f"[LeadScanner] {subject}"
    body = f"From: {name} <{email}>\n\n{message}\n\n---\nSent via leadscanner.fun"
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_user, gmail_password)
            server.send_message(msg)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"ok": True}


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
            await asyncio.sleep(2)  # Required delay before using next_page_token

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


async def get_place_details(client: httpx.AsyncClient, place_id: str) -> dict | None:
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
async def search_leads(req: SearchRequest):
    if not API_KEY:
        raise HTTPException(status_code=500, detail="GOOGLE_MAPS_API_KEY not set in .env file")

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
                continue  # already has a website — skip
            if not place.get("formatted_phone_number"):
                continue  # no phone number — skip

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

    return {
        "center": center,
        "leads": leads,
        "total_found": len(all_details),
        "skipped_has_website": skipped_has_website,
    }


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
