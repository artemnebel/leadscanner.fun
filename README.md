# Website Leads Finder

Local tool to find businesses without a website using the Google Places API — potential clients for web design services.

## Features
- Search by business category within a draggable radius on the map
- Filters out businesses that already have a website
- Accumulates leads across multiple searches with deduplication
- Sortable/filterable leads table with CSV export

## Setup
1. Get a Google Maps API key with the **Places API** enabled
2. Create a `.env` file in the project root:
   ```
   GOOGLE_MAPS_API_KEY=your_key_here
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
4. Run:
   ```bash
   python main.py
   ```
5. Open [http://localhost:8000](http://localhost:8000)
