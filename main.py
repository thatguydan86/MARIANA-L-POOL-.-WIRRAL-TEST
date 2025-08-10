# main.py — RentRadar (Mariana spec: Wirral, Lincoln, Bridgwater; 4-bed, 2-bath, £1500 max)

import asyncio
import time
import random
import requests
from typing import Dict, List, Set, Any

print("🚀 Starting RentRadar (Mariana spec)…")

# ========= Config =========
WEBHOOK_URL = "https://hook.eu2.make.com/qsk78c4p25ii0anm32kien7okkmtbit6"  # <- your Make.com webhook

# Rightmove REGION identifiers from Danny's links
AREAS = {
    "Wirral": {
        "locationIdentifier": "REGION^93365",
        "adr": 196,     # PMI-derived 4-bed ADR
        "council_tax": 198,  # £/mo (Band D approx)
    },
    "Lincoln": {
        "locationIdentifier": "REGION^804",
        "adr": 178,
        "council_tax": 188,
    },
    "Bridgwater": {
        "locationIdentifier": "REGION^212",
        "adr": 205,
        "council_tax": 189,
    },
}

# Utilities baseline (energy + water); updated to more accurate 4-bed estimate
UTILS_BASE_PM = 250  # £/mo

# Precompute total bills per area (council tax + utilities)
for name, cfg in AREAS.items():
    cfg["bills_total"] = int(round(cfg["council_tax"] + UTILS_BASE_PM))

# Search constraints
BEDS_EXACT = 4
MIN_BATHS = 2
MAX_PRICE = 1500

# Profit target (70% occupancy)
GOOD_PROFIT_TARGET = 1300  # £ at 70%
BOOKING_FEE_PCT = 0.15

# Exclusions to force "houses only"
EXCLUDED_SUBTYPES = {
    "FLAT", "APARTMENT", "MAISONETTE", "STUDIO",
    "FLAT SHARE", "HOUSE SHARE", "ROOM", "NOT SPECIFIED", "BUNGALOW"
}
EXCLUDED_KEYWORDS = {
    "RENT TO BUY", "RENT-TO-BUY", "RENT TO OWN", "RENT2BUY",
    "HOUSE SHARE", "ROOM SHARE", "SHARED", "HMO",
    "ALL BILLS INCLUDED", "BILLS INCLUDED", "INCLUSIVE OF BILLS",
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

def calculate_profits(rent_pcm: int, adr: int, bills_total: int) -> Dict[str, int]:
    def profit(occ: float) -> int:
        net_income = monthly_net_from_adr(adr, occ)
        return int(round(net_income - rent_pcm - bills_total))
    return {
        "night_rate": adr,
        "total_bills": bills_total,
        "profit_50": profit(0.5),
        "profit_70": profit(0.7),
        "profit_100": profit(1.0),
    }

# ========= Rightmove fetch =========
def fetch_properties(location_id: str) -> List[Dict[str, Any]]:
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
        "minBedrooms": BEDS_EXACT,
        "maxBedrooms": BEDS_EXACT,
        "maxPrice": MAX_PRICE,
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
def filter_properties(area_name: str, properties: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cfg = AREAS[area_name]
    adr = cfg["adr"]
    bills_total = cfg["bills_total"]
    council_tax = cfg["council_tax"]

    results = []
    for prop in properties:
        try:
            beds = prop.get("bedrooms")
            baths = prop.get("bathrooms") or 0
            rent = prop.get("price", {}).get("amount")
            subtype = (prop.get("propertySubType") or "House").upper()
            address = prop.get("displayAddress", "Unknown")
            summary = (prop.get("summary") or "").upper()
            title = (prop.get("propertyTitle") or "").upper()

            if beds != BEDS_EXACT:
                continue
            if baths < MIN_BATHS:
                continue
            if rent is None or rent > MAX_PRICE:
                continue
            if subtype in EXCLUDED_SUBTYPES:
                continue

            haystack = " ".join([address.upper(), summary, title])
            if any(word in haystack for word in EXCLUDED_KEYWORDS):
                continue

            p = calculate_profits(rent, adr, bills_total)
            p70 = p["profit_70"]
            meets_target = p70 >= GOOD_PROFIT_TARGET

            target_adr = adr_to_hit_target(GOOD_PROFIT_TARGET, rent, bills_total, occupancy=0.7)
            target_rent = rent_to_hit_target(GOOD_PROFIT_TARGET, adr, bills_total, occupancy=0.7)

            ab_lines = []
            if not meets_target and target_adr > adr:
                ab_lines.append(f"• A: Raise nightly to £{target_adr}")
            if not meets_target and target_rent < rent:
                ab_lines.append(f"• B: Negotiate rent to ~£{target_rent}/mo")
            ab_message = "\n".join(ab_lines)

            listing = {
                "id": prop.get("id"),
                "area": area_name,
                "address": address,
                "rent_pcm": rent,
                "bedrooms": beds,
                "bathrooms": baths,
                "propertySubType": prop.get("propertySubType") or "House",
                "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
                "night_rate": p["night_rate"],
                "profit_50": p["profit_50"],
                "profit_70": p70,
                "profit_100": p["profit_100"],
                "bills_total": bills_total,
                "council_tax_monthly": council_tax,
                "utils_base_monthly": UTILS_BASE_PM,
                "rag": "🟢" if meets_target else ("🟡" if p70 >= GOOD_PROFIT_TARGET * 0.7 else "🔴"),
                "score10": round(max(0, min(10, (p70 / GOOD_PROFIT_TARGET) * 10)), 1),
                "hot_deal": "yes" if (p70 - GOOD_PROFIT_TARGET) >= 100 else "no",
                "over_by": max(0, p70 - GOOD_PROFIT_TARGET),
                "below_by": max(0, GOOD_PROFIT_TARGET - p70),
                "to_green_target_adr": target_adr,
                "to_green_target_rent": target_rent,
                "target_profit_70": GOOD_PROFIT_TARGET,
                "meets_target": meets_target,
                "ab_message": ab_message,
            }
            results.append(listing)
        except Exception:
            continue
    return results

def preview_message(listing: Dict[str, Any]) -> str:
    body = (
        f"🔔 New Rent-to-SA Lead ({listing['area']})\n"
        f"📍 {listing['address']}\n"
        f"🏠 {listing['bedrooms']}-bed {listing['propertySubType']} | 🛁 {listing['bathrooms']} baths\n"
        f"💰 Rent: £{listing['rent_pcm']}/mo | Bills: £{listing['bills_total']}/mo (CT £{listing['council_tax_monthly']} + utils £{listing['utils_base_monthly']}) | Fees: 15%\n"
        f"🔗 {listing['url']}\n\n"
        f"📊 Profit (Nightly £{listing['night_rate']})\n"
        f"• 50% → £{listing['profit_50']}\n"
        f"• 70% → £{listing['profit_70']}   🎯 Target: £{listing['target_profit_70']}\n"
        f"• 100% → £{listing['profit_100']}\n\n"
    )
    if not listing["meets_target"] and listing.get("ab_message"):
        body += f"💡 A/B to hit target @ 70%:\n{listing['ab_message']}\n"
    return body

# ========= Scraper loop =========
async def scrape_once(seen_ids: Set[str]) -> List[Dict[str, Any]]:
    new_listings: List[Dict[str, Any]] = []
    for area_name, cfg in AREAS.items():
        print(f"\n📍 Searching {area_name}…")
        raw_props = fetch_properties(cfg["locationIdentifier"])
        filtered = filter_properties(area_name, raw_props)
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
                    requests.post(WEBHOOK_URL, json=listing, timeout=12)
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
