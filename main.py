import asyncio
import time
import random
import requests
from typing import Dict, List, Set

print("🚀 Starting RentRadar (Mariana spec)…")

# ========= Config =========
WEBHOOK_URL = "https://hook.eu2.make.com/qsk78c4p25ii0anm32kien7okkmtbit6"

DEBUG_MODE = True   # Toggle verbose debug mode
MIN_RENT = 800      # Minimum acceptable monthly rent

# Location config (Rightmove region IDs, ADR, council tax, utilities)
AREAS: Dict[str, Dict] = {
    "Wirral": {
        "locationIdentifier": "REGION^93365",
        "adr": 196,
        "council_tax": 198,
        "utilities": 245
    },
    "Lincoln": {
        "locationIdentifier": "REGION^804",
        "adr": 178,
        "council_tax": 188,
        "utilities": 240
    },
    "Bridgwater": {
        "locationIdentifier": "REGION^212",
        "adr": 205,
        "council_tax": 189,
        "utilities": 255
    },
}

# Pre-calc total bills per area
for name, cfg in AREAS.items():
    cfg["bills_total"] = cfg["council_tax"] + cfg["utilities"]

# Beds / price limits
MIN_BEDS = 4
MAX_BEDS = 4
MIN_BATHS = 2
MAX_PRICE = 1500

GOOD_PROFIT_TARGET = 1300  # £ at 70% occupancy
BOOKING_FEE_PCT = 0.15

EXCLUDED_SUBTYPES = {
    "FLAT", "APARTMENT", "MAISONETTE", "STUDIO",
    "FLAT SHARE", "HOUSE SHARE", "ROOM", "NOT SPECIFIED",
}
EXCLUDED_KEYWORDS = {
    "RENT TO BUY", "RENT-TO-BUY", "RENT TO OWN", "RENT2BUY",
    "HOUSE SHARE", "ROOM SHARE", "SHARED", "HMO",
    "ALL BILLS INCLUDED", "BILLS INCLUDED", "BILLS INCLUSIVE", "INCLUSIVE OF BILLS",
    "STUDENT"
}

# ========= Helpers =========
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

def calculate_profits(rent_pcm: int, adr: float, bills: int):
    def profit(occ: float) -> int:
        net_income = monthly_net_from_adr(adr, occ)
        return int(round(net_income - rent_pcm - bills))

    return {
        "profit_50": profit(0.5),
        "profit_70": profit(0.7),
        "profit_100": profit(1.0),
    }

# ========= Rightmove fetch =========
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
        "minBathrooms": MIN_BATHS,
    }
    url = "https://www.rightmove.co.uk/api/_search"
    try:
        resp = requests.get(url, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"⚠️ API request failed: status={resp.status_code} for {location_id}")
            return []
        return resp.json().get("properties", [])
    except Exception as e:
        print(f"⚠️ Exception fetching properties: {e}")
        return []

# ========= Filter & enrich =========
def filter_properties(properties: List[Dict], area_name: str, cfg: Dict) -> List[Dict]:
    results = []
    print(f"📊 Found {len(properties)} raw properties in {area_name}")
    for prop in properties:
        address = prop.get("displayAddress", "Unknown")
        rent = prop.get("price", {}).get("amount")
        beds = prop.get("bedrooms")
        baths = prop.get("bathrooms") or 0
        subtype = (prop.get("propertySubType") or "House").upper()
        summary = (prop.get("summary") or "").upper()
        title = (prop.get("propertyTitle") or "").upper()

        # Debug raw
        if DEBUG_MODE:
            print(f"RAW: {address} | £{rent} | {beds} beds | {baths} baths | {subtype}")

        # Filters
        if beds is None or rent is None:
            if DEBUG_MODE: print("  ❌ Skipped: Missing beds or rent")
            continue
        if not (MIN_BEDS <= beds <= MAX_BEDS):
            if DEBUG_MODE: print(f"  ❌ Skipped: Beds out of range ({beds})")
            continue
        if baths < MIN_BATHS:
            if DEBUG_MODE: print(f"  ❌ Skipped: Bathrooms below min ({baths})")
            continue
        if rent < MIN_RENT:
            if DEBUG_MODE: print(f"  ❌ Skipped: Rent below min (£{rent})")
            continue
        if rent > MAX_PRICE:
            if DEBUG_MODE: print(f"  ❌ Skipped: Rent above max (£{rent})")
            continue
        if subtype in EXCLUDED_SUBTYPES:
            if DEBUG_MODE: print(f"  ❌ Skipped: Property subtype '{subtype}'")
            continue
        haystack = " ".join([address.upper(), summary, title])
        if any(word in haystack for word in EXCLUDED_KEYWORDS):
            if DEBUG_MODE: print(f"  ❌ Skipped: Keyword match")
            continue

        # Profit calculation
        p = calculate_profits(rent, cfg["adr"], cfg["bills_total"])
        p70 = p["profit_70"]
        meets_target = p70 >= GOOD_PROFIT_TARGET
        target_adr = adr_to_hit_target(GOOD_PROFIT_TARGET, rent, cfg["bills_total"], occupancy=0.7)
        target_rent = rent_to_hit_target(GOOD_PROFIT_TARGET, cfg["adr"], cfg["bills_total"], occupancy=0.7)

        # Build listing
        listing = {
            "id": prop.get("id"),
            "area": area_name,
            "address": address,
            "rent_pcm": rent,
            "bedrooms": beds,
            "bathrooms": baths,
            "propertySubType": subtype,
            "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
            "night_rate": cfg["adr"],
            "bills": cfg["bills_total"],
            "profit_50": p["profit_50"],
            "profit_70": p70,
            "profit_100": p["profit_100"],
            "target_profit_70": GOOD_PROFIT_TARGET,
            "meets_target": meets_target,
            "to_green_target_adr": target_adr,
            "to_green_target_rent": target_rent
        }
        results.append(listing)
    return results

# ========= Console preview =========
def preview_message(listing: Dict) -> str:
    return (
        f"🔔 New Rent-to-SA Lead ({listing['area']})\n"
        f"📍 {listing['address']}\n"
        f"🏠 {listing['bedrooms']}-bed {listing['propertySubType']} | 🛁 {listing['bathrooms']} baths\n"
        f"💰 Rent: £{listing['rent_pcm']}/mo | Bills: £{listing['bills']}/mo | Fees: 15%\n"
        f"🔗 {listing['url']}\n\n"
        f"📊 Profit (Nightly £{listing['night_rate']})\n"
        f"• 50% → £{listing['profit_50']}\n"
        f"• 70% → £{listing['profit_70']}   🎯 Target: £{listing['target_profit_70']}\n"
        f"• 100% → £{listing['profit_100']}\n"
    )

# ========= Scraper loop =========
async def scrape_once(seen_ids: Set[str]) -> List[Dict]:
    new_listings = []
    for area_name, cfg in AREAS.items():
        print(f"\n📍 Searching {area_name}…")
        raw_props = fetch_properties(cfg["locationIdentifier"])
        filtered = filter_properties(raw_props, area_name, cfg)
        for listing in filtered:
            if listing["id"] in seen_ids:
                continue
            seen_ids.add(listing["id"])
            new_listings.append(listing)
    return new_listings

async def main() -> None:
    print("🚀 Scraper started!")
    seen_ids: Set[str] = set()
    while True:
        try:
            print(f"\n⏰ New scrape at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            new_listings = await scrape_once(seen_ids)
            if not new_listings:
                print("ℹ️ No new listings this run.")
            for listing in new_listings:
                print("────────────────────────────────────────")
                print(preview_message(listing))
                print("────────────────────────────────────────")
                print(f"✅ Sending: {listing['area']} – {listing['address']} – £{listing['rent_pcm']}")
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
