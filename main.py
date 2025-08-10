import asyncio
import time
import random
import requests
from typing import Dict, List, Set

print("🚀 Starting RentRadar scraper…")

# ========= SETTINGS =========
WEBHOOK_URL = "https://hook.eu2.make.com/ll7gkhmnr3pd2xgwt2sjvp2fw3gzpbil"  # Make.com webhook

# Rightmove OUTCODE location IDs
OUTCODE_LOCATION_IDS: Dict[str, List[str]] = {
    "L18": ["OUTCODE^1350"],
    "L19": ["OUTCODE^1351"],
    "L23": ["OUTCODE^1356"],
    "L25": ["OUTCODE^1358"],
    "L4":  ["OUTCODE^1374"],
    "CH47": ["OUTCODE^466"],
    "CH48": ["OUTCODE^467"],
    "CH49": ["OUTCODE^468"],
    "CH60": ["OUTCODE^471"],
    "CH61": ["OUTCODE^472"],
    "CH62": ["OUTCODE^473"],
    "CH63": ["OUTCODE^474"],
    "CH64": ["OUTCODE^475"],
}

# Search bounds
MIN_BEDS = 3
MAX_BEDS = 4
MAX_PRICE = 1500  # Mariana cap
MIN_BATHS = 2

# Mariana’s target (profit @ 70%)
TARGET_PROFIT_70 = 1300

# Platform fee %
BOOKING_FEE_PCT = 0.15

# Nightly ADR guidance (by outcode & beds) — your numbers
HARDCODED_NIGHTLY: Dict[str, Dict[int, int]] = {
    "L18": {3: 135, 4: 206},
    "L19": {3: 166, 4: 228},
    "L23": {3: 163, 4: 188},
    "L25": {3: 149, 4: 214},
    "L4":  {3: 139, 4: 186},
    "CH47": {3: 179, 4: 231},
    "CH48": {3: 176, 4: 264},
    "CH49": {3: 160, 4: 253},
    "CH60": {3: 166, 4: 253},
    "CH61": {3: 163, 4: 224},
    "CH62": {3: 182, 4: 242},
    "CH63": {3: 177, 4: 253},
    "CH64": {3: 195, 4: 273},
}

# Estimated monthly bills (council tax+energy+water+broadband+TVL)
EST_BILLS: Dict[str, Dict[int, int]] = {
    "L18": {3: 600, 4: 650},
    "L19": {3: 580, 4: 630},
    "L23": {3: 600, 4: 650},
    "L25": {3: 580, 4: 630},
    "L4":  {3: 550, 4: 600},
    "CH47": {3: 620, 4: 680},
    "CH48": {3: 640, 4: 700},
    "CH49": {3: 600, 4: 660},
    "CH60": {3: 620, 4: 680},
    "CH61": {3: 600, 4: 660},
    "CH62": {3: 600, 4: 660},
    "CH63": {3: 610, 4: 670},
    "CH64": {3: 620, 4: 690},
}

# Property type filters (we’re going after houses, not flats/HMOs)
EXCLUDED_SUBTYPES = {
    "FLAT", "APARTMENT", "MAISONETTE", "STUDIO",
    "FLAT SHARE", "HOUSE SHARE", "ROOM", "NOT SPECIFIED",
}
EXCLUDED_KEYWORDS = {
    "RENT TO BUY", "RENT-TO-BUY", "RENT TO OWN", "RENT2BUY",
    "HOUSE SHARE", "ROOM SHARE", "SHARED", "HMO",
    "ALL BILLS INCLUDED", "BILLS INCLUDED", "INCLUSIVE OF BILLS",
}

# ========= HELPERS =========
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
    return max(0, int(round(net - bills - target)))

def calculate_profits(rent_pcm: int, bedrooms: int, outcode: str):
    nightly_rate = HARDCODED_NIGHTLY.get(outcode, {}).get(bedrooms, 0)
    total_bills = EST_BILLS.get(outcode, {}).get(bedrooms, 600)

    def profit(occ: float) -> int:
        net_income = monthly_net_from_adr(nightly_rate, occ)
        return int(round(net_income - rent_pcm - total_bills))

    return {
        "night_rate": nightly_rate,
        "bills": total_bills,
        "profit_50": profit(0.5),
        "profit_70": profit(0.7),
        "profit_100": profit(1.0),
    }

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
            print(f"⚠️ API request failed for {location_id}: status={resp.status_code}")
            return []
        return resp.json().get("properties", [])
    except Exception as e:
        print(f"⚠️ Exception fetching properties for {location_id}: {e}")
        return []

def filter_properties(properties: List[Dict], outcode: str) -> List[Dict]:
    results = []
    for prop in properties:
        try:
            beds = prop.get("bedrooms")
            baths = prop.get("bathrooms")
            rent = prop.get("price", {}).get("amount")
            subtype = (prop.get("propertySubType") or "House")
            subtype_upper = subtype.upper()
            address = prop.get("displayAddress", "Unknown")
            summary = (prop.get("summary") or "").upper()
            title = (prop.get("propertyTitle") or "").upper()

            if beds is None or rent is None:
                continue
            if not (MIN_BEDS <= beds <= MAX_BEDS):
                continue
            if rent > MAX_PRICE:
                continue
            if baths is not None and baths < MIN_BATHS:
                continue
            if subtype_upper in EXCLUDED_SUBTYPES:
                continue
            haystack = " ".join([address.upper(), summary, title])
            if any(word in haystack for word in EXCLUDED_KEYWORDS):
                continue

            p = calculate_profits(rent, beds, outcode)
            p70 = p["profit_70"]
            meets_target = p70 >= TARGET_PROFIT_70
            score10 = round(max(0, min(10, (p70 / TARGET_PROFIT_70) * 10)), 1)
            rag = "🟢" if meets_target else ("🟡" if p70 >= 0.7 * TARGET_PROFIT_70 else "🔴")

            needed_adr = adr_to_hit_target(TARGET_PROFIT_70, rent, p["bills"], occupancy=0.7)
            needed_rent = rent_to_hit_target(TARGET_PROFIT_70, p["night_rate"], p["bills"], occupancy=0.7)

            # A/B helper message (pre-built so Telegram is simple)
            if meets_target:
                ab_message = "🚀 Already ≥ target at 70% — no changes needed."
            else:
                ab_message = (
                    f"💡 *A/B to hit target @ 70%:*\n"
                    f"• *A:* Raise nightly to *£{needed_adr}*\n"
                    f"• *B:* Negotiate rent to *~£{needed_rent}/mo*"
                )

            listing = {
                "id": prop.get("id"),
                "outcode": outcode,
                "address": address,
                "rent_pcm": rent,
                "bedrooms": beds,
                "bathrooms": baths if baths is not None else "",
                "propertySubType": subtype,
                "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
                "nightly_rate": p["night_rate"],
                "profit_50": p["profit_50"],
                "profit_70": p70,
                "profit_100": p["profit_100"],
                "bills": p["bills"],
                "fees": 15,  # %
                "target_profit_70": TARGET_PROFIT_70,
                "to_green_target_adr": needed_adr,
                "to_green_target_rent": needed_rent,
                "meets_target": meets_target,   # boolean
                "over_by": max(0, p70 - TARGET_PROFIT_70),
                "below_by": max(0, TARGET_PROFIT_70 - p70),
                "score10": score10,
                "rag": rag,
                "ab_message": ab_message,
            }
            results.append(listing)
        except Exception:
            continue
    return results

def preview_message(listing: Dict) -> str:
    # Console preview only
    header = f"🔔 New Rent-to-SA Lead"
    body = (
        f"{header}\n"
        f"📍 {listing['address']}\n"
        f"🏠 {listing['bedrooms']}-bed {listing['propertySubType']} | 🛁 {listing['bathrooms']} baths\n"
        f"💰 Rent: £{listing['rent_pcm']}/mo | Bills: £{listing['bills']}/mo | Fees: {listing['fees']}%\n"
        f"🔗 {listing['url']}\n\n"
        f"📊 Profit (Nightly £{listing['nightly_rate']})\n"
        f"• 50% → £{listing['profit_50']}\n"
        f"• 70% → £{listing['profit_70']}   🎯 Target: £{listing['target_profit_70']}\n"
        f"• 100% → £{listing['profit_100']}\n\n"
        f"{listing['ab_message']}\n"
        f"{listing['rag']} Score: {listing['score10']}/10\n"
    )
    return body

# ========= LOOP =========
async def scrape_once(seen_ids: Set[str]) -> List[Dict]:
    new_listings = []
    for outcode, region_ids in OUTCODE_LOCATION_IDS.items():
        print(f"\n📍 Searching {outcode}…")
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
    print("🟢 Scraper started")
    seen_ids: Set[str] = set()
    while True:
        try:
            print(f"\n⏰ New scrape at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            new_listings = await scrape_once(seen_ids)

            if not new_listings:
                print("ℹ️ No new listings found this cycle.")
            else:
                for listing in new_listings:
                    print("────────────────────────────────────────")
                    print(preview_message(listing))
                    print("────────────────────────────────────────")
                    try:
                        requests.post(WEBHOOK_URL, json=listing, timeout=10)
                        print(f"✅ Sent: {listing['address']} ({listing['outcode']})")
                    except Exception as e:
                        print(f"⚠️ Failed to POST to webhook: {e}")

            sleep_duration = 3600 + random.randint(-300, 300)
            print(f"💤 Sleeping {sleep_duration}s…")
            await asyncio.sleep(sleep_duration)

        except Exception as e:
            print(f"🔥 Error: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
