import asyncio
import time
import random
import requests
from typing import Dict, List, Set

print("🚀 Starting RentRadar scraper…")

WEBHOOK_URL = "https://hook.eu2.make.com/ll7gkhmnr3pd2xgwt2sjvp2fw3gzpbil"  # Make.com webhook

# ---- Search regions (Rightmove OUTCODE IDs) ----
OUTCODE_LOCATION_IDS: Dict[str, List[str]] = {
    "L18": ["OUTCODE^1350"],
    "L19": ["OUTCODE^1351"],
    "L23": ["OUTCODE^1356"],
    "L25": ["OUTCODE^1358"],
    "L4":  ["OUTCODE^1374"],
    # Wirral (nicer pockets)
    "CH47": ["OUTCODE^466"],
    "CH48": ["OUTCODE^467"],
    "CH49": ["OUTCODE^468"],
    "CH60": ["OUTCODE^471"],
    "CH61": ["OUTCODE^472"],
    "CH62": ["OUTCODE^473"],
    "CH63": ["OUTCODE^474"],
    "CH64": ["OUTCODE^475"],
}

MIN_BEDS = 3
MAX_BEDS = 4
MAX_PRICE = 1500       # Mariana’s cap
MIN_BATHS = 2          # must have 2+ bathrooms

# ---- ADR by postcode & bed (your numbers) ----
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

# ---- Bills (simple for now; can be made postcode-specific later) ----
BILLS_ESTIMATE = {3: 550, 4: 600}

# ---- Target at 70% occupancy ----
GOOD_PROFIT_TARGET = 1300     # Mariana’s new target
BOOKING_FEE_PCT = 0.15

# Listing content filters
EXCLUDED_SUBTYPES = {
    "FLAT", "APARTMENT", "MAISONETTE", "STUDIO",
    "FLAT SHARE", "HOUSE SHARE", "ROOM", "NOT SPECIFIED",
}
EXCLUDED_KEYWORDS = {
    "RENT TO BUY", "RENT-TO-BUY", "RENT TO OWN", "RENT2BUY",
    "HOUSE SHARE", "ROOM SHARE", "SHARED", "HMO",
    "ALL BILLS INCLUDED", "BILLS INCLUDED", "INCLUSIVE OF BILLS",
}

# ------------------ Maths helpers ------------------ #
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

# ------------------ Profit calculation ------------------ #
def calculate_profits(rent_pcm: int, bedrooms: int, outcode: str):
    nightly_rate = HARDCODED_NIGHTLY.get(outcode, {}).get(bedrooms, 0)
    total_bills = BILLS_ESTIMATE.get(bedrooms, 550)

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

# ------------------ Rightmove API fetch ------------------ #
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

# ------------------ Filter & enrich ------------------ #
def filter_properties(properties: List[Dict], outcode: str) -> List[Dict]:
    results = []
    for prop in properties:
        try:
            beds = prop.get("bedrooms")
            rent = prop.get("price", {}).get("amount")
            baths = prop.get("bathrooms", 0)
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

            # Profit maths
            p = calculate_profits(rent, beds, outcode)
            p70 = p["profit_70"]
            diff = p70 - GOOD_PROFIT_TARGET

            rag = "🟢" if p70 >= GOOD_PROFIT_TARGET else ("🟡" if p70 >= GOOD_PROFIT_TARGET * 0.7 else "🔴")
            score10 = round(max(0, min(10, (p70 / GOOD_PROFIT_TARGET) * 10)), 1)

            # A/B targets to go green (only meaningful if below target)
            target_adr = adr_to_hit_target(GOOD_PROFIT_TARGET, rent, p["total_bills"], occupancy=0.7)
            target_rent = rent_to_hit_target(GOOD_PROFIT_TARGET, p["night_rate"], p["total_bills"], occupancy=0.7)

            listing = {
                "id": prop.get("id"),
                "outcode": outcode,
                "address": address,
                "rent_pcm": rent,
                "bedrooms": beds,
                "bathrooms": baths,
                "propertySubType": subtype,
                "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
                "night_rate": p["night_rate"],
                "profit_50": p["profit_50"],
                "profit_70": p70,
                "profit_100": p["profit_100"],
                "bills": p["total_bills"],
                "rag": rag,
                "score10": score10,
                "over_by": max(0, diff),
                "below_by": max(0, -diff),
                "to_green_target_adr": target_adr,
                "to_green_target_rent": target_rent,
                "target_profit_70": GOOD_PROFIT_TARGET,
                # NEW: boolean for Make.com conditions
                "meets_target": (p70 >= GOOD_PROFIT_TARGET),
            }
            results.append(listing)
        except Exception as e:
            print(f"⚠️ Skipped a property due to error: {e}")
            continue
    return results

# ------------------ Optional: console preview ------------------ #
def preview_message(listing: Dict) -> str:
    header = "🔔 New Rent-to-SA Lead"
    body = (
        f"{header}\n"
        f"📍 {listing['address']}\n"
        f"🏠 {listing['bedrooms']}-bed {listing['propertySubType']}  |  🛁 {listing.get('bathrooms','?')} baths\n"
        f"💰 Rent: £{listing['rent_pcm']}/mo | Bills: £{listing['bills']}/mo | Fees: 15%\n"
        f"🔗 {listing['url']}\n\n"
        f"📊 Profit (Nightly £{listing['night_rate']})\n"
        f"• 50% → £{listing['profit_50']}\n"
        f"• 70% → £{listing['profit_70']}   🎯 Target: £{listing['target_profit_70']}\n"
        f"• 100% → £{listing['profit_100']}\n\n"
    )
    if not listing["meets_target"]:
        body += (
            f"{listing['rag']} Score: {listing['score10']}/10\n"
            f"💡 A/B @70% — ADR→£{listing['to_green_target_adr']}, Rent→~£{listing['to_green_target_rent']}/mo\n"
        )
    else:
        body += "🚀 Already ≥ target at 70% — no changes needed.\n"
    return body

# ------------------ Scraper loop ------------------ #
async def scrape_once(seen_ids: Set[str]) -> List[Dict]:
    new_listings = []
    total_checked = 0
    total_after_filters = 0

    for outcode, region_ids in OUTCODE_LOCATION_IDS.items():
        print(f"\n📍 Searching outcode {outcode}…")
        for region_id in region_ids:
            raw_props = fetch_properties(region_id)
            total_checked += len(raw_props)
            filtered = filter_properties(raw_props, outcode)
            total_after_filters += len(filtered)
            for listing in filtered:
                if listing["id"] in seen_ids:
                    continue
                seen_ids.add(listing["id"])
                new_listings.append(listing)

    print(f"🔎 Checked {total_checked} raw; {total_after_filters} passed filters; {len(new_listings)} new to send.")
    return new_listings

async def main() -> None:
    print("🚀 Scraper started!")
    seen_ids: Set[str] = set()

    while True:
        try:
            print(f"\n⏰ New scrape at {time.strftime('%Y-%m-%d %H:%M:%S')}")
            new_listings = await scrape_once(seen_ids)

            if not new_listings:
                print("ℹ️ No new listings matched the criteria this run.")
            else:
                for listing in new_listings:
                    # console preview
                    print("────────────────────────────────────────")
                    print(preview_message(listing))
                    print("────────────────────────────────────────")
                    print(f"✅ Sending: {listing['address']} – £{listing['rent_pcm']} – {listing['bedrooms']} beds")
                    try:
                        # Send structured payload to Make (Telegram template formats it)
                        requests.post(WEBHOOK_URL, json=listing, timeout=10)
                    except Exception as e:
                        print(f"⚠️ Failed to POST to webhook: {e}")

            # Sleep ~1h ±5m
            sleep_duration = 3600 + random.randint(-300, 300)
            print(f"💤 Sleeping for {sleep_duration} seconds…")
            await asyncio.sleep(sleep_duration)

        except Exception as e:
            print(f"🔥 Error in main loop: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
