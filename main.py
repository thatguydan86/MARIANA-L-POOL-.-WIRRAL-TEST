import asyncio
import time
import random
import re
import os
import requests
from typing import Dict, List, Set

print("🚀 Starting RentRadar – Mariana (Liverpool + Wirral)…")

WEBHOOK_URL = "https://hook.eu2.make.com/qsk78c4p25ii0anm32kien7okkmtbit6"  # Make webhook

# ---------- Rightmove OUTCODE location IDs (from your URLs) ----------
OUTCODE_LOCATION_IDS: Dict[str, List[str]] = {
    # Liverpool
    "L18": ["OUTCODE^1350"],
    "L19": ["OUTCODE^1351"],
    "L23": ["OUTCODE^1356"],
    "L25": ["OUTCODE^1358"],
    "L4":  ["OUTCODE^1374"],

    # Wirral (nicer areas)
    "CH47": ["OUTCODE^466"],   # Hoylake, Meols
    "CH48": ["OUTCODE^467"],   # West Kirby, Caldy
    "CH60": ["OUTCODE^471"],   # Heswall, Gayton
    "CH61": ["OUTCODE^472"],   # Irby, Thingwall, Pensby
    "CH49": ["OUTCODE^468"],   # Greasby, Upton (better pockets)
    "CH62": ["OUTCODE^473"],   # Port Sunlight, Bromborough
    "CH63": ["OUTCODE^474"],   # Bebington, Spital, Storeton
    "CH64": ["OUTCODE^475"],   # Parkgate, Neston, Willaston
}

# ---------- Search criteria (Mariana) ----------
MIN_BEDS = 3
MAX_BEDS = 4
MAX_PRICE = 1500
MIN_BATHROOMS = 2          # hard filter
BOOKING_FEE_PCT = 0.15

# Bills & target (env overrideable)
TOTAL_BILLS_ESTIMATE = {3: 550, 4: 600}
GOOD_PROFIT_TARGET = int(os.getenv("TARGET_70", "1000"))   # profit target at 70%
HOT_DEAL_EXTRA     = int(os.getenv("HOT_DEAL_EXTRA", "100"))

# ---------- ADR assumptions per area (from your list) ----------
HARDCODED_NIGHTLY = {
    # Liverpool
    "L18": {3: 135, 4: 206},
    "L19": {3: 166, 4: 228},
    "L23": {3: 163, 4: 188},
    "L25": {3: 149, 4: 214},
    "L4":  {3: 139, 4: 186},

    # Wirral
    "CH47": {3: 179, 4: 231},
    "CH48": {3: 176, 4: 264},
    "CH49": {3: 160, 4: 253},
    "CH60": {3: 166, 4: 253},
    "CH61": {3: 163, 4: 224},
    "CH62": {3: 182, 4: 242},
    "CH63": {3: 177, 4: 253},
    "CH64": {3: 195, 4: 273},  # assumed from your final line
}

# Exclude flats/rooms/HMO etc.
EXCLUDED_SUBTYPES = {
    "FLAT", "APARTMENT", "MAISONETTE", "STUDIO",
    "FLAT SHARE", "HOUSE SHARE", "ROOM", "NOT SPECIFIED",
}
EXCLUDED_KEYWORDS = {
    "HOUSE SHARE", "ROOM SHARE", "SHARED", "HMO",
    "ALL BILLS INCLUDED", "BILLS INCLUDED", "INCLUSIVE OF BILLS",
    "STUDENTS ONLY", "STUDENT HOUSE", "ROOMS", "ROOM ONLY",
}

# ---------- Maths helpers ----------
def monthly_net_from_adr(adr: float, occupancy: float) -> float:
    gross = adr * occupancy * 30
    return gross * (1 - BOOKING_FEE_PCT)

def adr_to_hit_target(target: float, rent: float, bills: float, occupancy: float = 0.7) -> int:
    net_needed = target + rent + bills
    denom = occupancy * 30 * (1 - BOOKING_FEE_PCT)
    if denom <= 0:
        return 0
    return int(round(net_needed / denom))

def rent_to_hit_target(target: float, adr: float, bills: float, occupancy: float = 0.7) -> int:
    net = monthly_net_from_adr(adr, occupancy)
    return int(round(net - bills - target))

def calculate_profits(rent_pcm: int, bedrooms: int, outcode: str):
    nightly_rate = HARDCODED_NIGHTLY.get(outcode, {}).get(bedrooms, 0)
    total_bills = TOTAL_BILLS_ESTIMATE.get(bedrooms, 550)

    def profit(occ: float) -> int:
        net_income = monthly_net_from_adr(nightly_rate, occ)
        return int(round(net_income - rent_pcm - total_bills))

    return {
        "night_rate": nightly_rate,
        "total_bills": total_bills,
        "profit_50": profit(0.5),
        "profit_70": profit(0.7),
        "profit_100": profit(1.0),
    }

# ---------- Rightmove fetch ----------
def fetch_properties(location_id: str) -> List[Dict]:
    params = {
        "locationIdentifier": location_id,
        "numberOfPropertiesPerPage": 24,
        "radius": 0.0,
        "index": 0,
        "channel": "RENT",
        "currencyCode": "GBP",
        "includeSSTC": "false",
        "sortType": 6,
        "viewType": "LIST",
        "minBedrooms": MIN_BEDS,
        "maxBedrooms": MAX_BEDS,
        "maxPrice": MAX_PRICE,
        "propertyTypes": "detached,semi-detached,terraced",
        # Rightmove API doesn’t support minBathrooms—so we post-filter below
    }
    url = "https://www.rightmove.co.uk/api/_search"
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"⚠️ API request failed for {location_id}: status={resp.status_code}")
            return []
        return resp.json().get("properties", [])
    except Exception as e:
        print(f"⚠️ Exception fetching properties for {location_id}: {e}")
        return []

# ---------- Bathrooms filter ----------
_2BATH_PAT = re.compile(r"\b(2\s*bath(?:room)?s?|two\s*bath(?:room)?s?)\b", re.I)

def has_min_bathrooms(prop: Dict, summary_texts: str) -> bool:
    baths = prop.get("bathrooms")
    if isinstance(baths, int):
        return baths >= MIN_BATHROOMS
    return bool(_2BATH_PAT.search(summary_texts))

# ---------- Filter & enrich ----------
def filter_properties(properties: List[Dict], outcode: str) -> List[Dict]:
    results = []
    for prop in properties:
        try:
            beds = prop.get("bedrooms")
            rent = prop.get("price", {}).get("amount")
            subtype = (prop.get("propertySubType") or "House")
            subtype_upper = subtype.upper()
            address = prop.get("displayAddress", "Unknown")
            summary = (prop.get("summary") or "")
            title = (prop.get("propertyTitle") or "")
            haystack = " ".join([address, summary, title]).upper()

            if beds is None or rent is None:
                continue
            if not (MIN_BEDS <= beds <= MAX_BEDS):
                continue
            if rent > MAX_PRICE:
                continue
            if subtype_upper in EXCLUDED_SUBTYPES:
                continue
            if any(word in haystack for word in EXCLUDED_KEYWORDS):
                continue
            if not has_min_bathrooms(prop, summary + " " + title):
                continue

            p = calculate_profits(rent, beds, outcode)
            p70 = p["profit_70"]
            diff = p70 - GOOD_PROFIT_TARGET

            rag = "🟢" if p70 >= GOOD_PROFIT_TARGET else ("🟡" if p70 >= GOOD_PROFIT_TARGET * 0.7 else "🔴")
            score10 = round(max(0, min(10, (p70 / GOOD_PROFIT_TARGET) * 10)), 1)
            target_adr = adr_to_hit_target(GOOD_PROFIT_TARGET, rent, p["total_bills"], occupancy=0.7)
            target_rent = rent_to_hit_target(GOOD_PROFIT_TARGET, p["night_rate"], p["total_bills"], occupancy=0.7)
            hot_deal = "yes" if diff >= HOT_DEAL_EXTRA else "no"

            listing = {
                "id": prop.get("id"),
                "address": address,
                "rent_pcm": rent,
                "bedrooms": beds,
                "bathrooms": prop.get("bathrooms"),
                "propertySubType": subtype,
                "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
                "night_rate": p["night_rate"],
                "profit_50": p["profit_50"],
                "profit_70": p70,
                "profit_100": p["profit_100"],
                "bills": p["total_bills"],
                "rag": rag,
                "score10": score10,
                "hot_deal": hot_deal,
                "over_by": max(0, diff),
                "below_by": max(0, -diff),
                "to_green_target_adr": target_adr,
                "to_green_target_rent": target_rent,
                "target_profit_70": GOOD_PROFIT_TARGET,
            }
            results.append(listing)
        except Exception:
            continue
    return results

# ---------- Scraper loop ----------
async def scrape_once(seen_ids: Set[str]) -> List[Dict]:
    new_listings = []
    for outcode, region_ids in OUTCODE_LOCATION_IDS.items():
        print(f"\n📍 Searching outcode {outcode}…")
        for region_id in region_ids:
            raw_props = fetch_properties(region_id)
            filtered = filter_properties(raw_props, outcode)
            for listing in filtered:
                if listing["id"] in seen_ids:
                    continue
                seen_ids.add(listing["id"])
                new_listings.append(listing)
    return new_listings

async def main() -> None:
    print("🚀 Scraper started for Mariana!")
    seen_ids: Set[str] = set()
    while True:
        try:
            print(f"\n⏰ New scrape at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            new_listings = await scrape_once(seen_ids)
            for listing in new_listings:
                # Console preview
                print("────────────────────────────────────────")
                print(listing)
                print("────────────────────────────────────────")
                print(f"✅ Sending: {listing['address']} – £{listing['rent_pcm']} – {listing['bedrooms']} beds")
                try:
                    requests.post(WEBHOOK_URL, json=listing, timeout=10)
                except Exception as e:
                    print(f"⚠️ Failed to POST to webhook: {e}")

            sleep_duration = 3600 + random.randint(-300, 300)
            print(f"💤 Sleeping for {sleep_duration} seconds…")
            await asyncio.sleep(sleep_duration)

        except Exception as e:
            print(f"🔥 Error: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
