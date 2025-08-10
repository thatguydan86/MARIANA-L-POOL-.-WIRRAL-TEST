import asyncio
import time
import random
import requests
from typing import Dict, List, Set

print("🚀 Starting RentRadar scraper…")

WEBHOOK_URL = "https://hook.eu2.make.com/ll7gkhmnr3pd2xgwt2sjvp2fw3gzpbil"

# ---- Areas (Rightmove outcodes) ----
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

MIN_BEDS = 3
MAX_BEDS = 4
MAX_PRICE = 1500  # Mariana constraint

# ---- Nightly rates (ADR) you provided ----
HARDCODED_NIGHTLY = {
    "L18": {3:135, 4:206},
    "L19": {3:166, 4:228},
    "L23": {3:163, 4:188},
    "L25": {3:149, 4:214},
    "L4":  {3:139, 4:186},
    "CH47":{3:179, 4:231},
    "CH48":{3:176, 4:264},
    "CH49":{3:160, 4:253},
    "CH60":{3:166, 4:253},
    "CH61":{3:163, 4:224},
    "CH62":{3:182, 4:242},
    "CH63":{3:177, 4:253},
    "CH64":{3:195, 4:273},
}

# ---- Bills (estimates) — 3-bed / 4-bed per outcode ----
BILLS_EST = {
    "L18": {"3":600, "4":650},
    "L19": {"3":550, "4":600},
    "L23": {"3":575, "4":625},
    "L25": {"3":575, "4":625},
    "L4":  {"3":550, "4":600},
    "CH47":{"3":600, "4":650},
    "CH48":{"3":600, "4":650},
    "CH49":{"3":575, "4":625},
    "CH60":{"3":600, "4":650},
    "CH61":{"3":575, "4":625},
    "CH62":{"3":575, "4":625},
    "CH63":{"3":575, "4":625},
    "CH64":{"3":600, "4":650},
}

TARGET_PROFIT_70 = 1300  # ✅ New target

BOOKING_FEE_PCT = 0.15

EXCLUDED_SUBTYPES = {
    "FLAT","APARTMENT","MAISONETTE","STUDIO",
    "FLAT SHARE","HOUSE SHARE","ROOM","NOT SPECIFIED",
}
EXCLUDED_KEYWORDS = {
    "RENT TO BUY","RENT-TO-BUY","RENT TO OWN","RENT2BUY",
    "HOUSE SHARE","ROOM SHARE","SHARED","HMO",
    "ALL BILLS INCLUDED","BILLS INCLUDED","INCLUSIVE OF BILLS",
}

# ------------------ Maths helpers ------------------ #
def monthly_net_from_adr(adr: float, occupancy: float) -> float:
    gross = adr * occupancy * 30
    return gross * (1 - BOOKING_FEE_PCT)

def adr_to_hit_target(target: float, rent: float, bills: float, occ: float = 0.7) -> int:
    net_needed = target + rent + bills
    denom = occ * 30 * (1 - BOOKING_FEE_PCT)
    return int(round(net_needed / denom)) if denom > 0 else 0

def rent_to_hit_target(target: float, adr: float, bills: float, occ: float = 0.7) -> int:
    net = monthly_net_from_adr(adr, occ)
    return int(round(net - bills - target))

def calculate_profits(rent_pcm: int, bedrooms: int, outcode: str):
    nightly_rate = HARDCODED_NIGHTLY.get(outcode, {}).get(bedrooms, 0)
    bills = BILLS_EST.get(outcode, {}).get(str(bedrooms), 575)

    def profit(occ: float) -> int:
        net_income = monthly_net_from_adr(nightly_rate, occ)
        return int(round(net_income - rent_pcm - bills))

    return {
        "night_rate": nightly_rate,
        "bills": bills,
        "profit_50": profit(0.5),
        "profit_70": profit(0.7),
        "profit_100": profit(1.0),
    }

# ------------------ Rightmove fetch ------------------ #
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
        "minBathrooms": 2,  # Mariana asked 2+ baths
        "propertyTypes": "detached,semi-detached,terraced",
    }
    url = "https://www.rightmove.co.uk/api/_search"
    try:
        r = requests.get(url, params=params, timeout=30)
        if r.status_code != 200:
            print(f"⚠️ API failed for {location_id}: {r.status_code}")
            return []
        return r.json().get("properties", [])
    except Exception as e:
        print(f"⚠️ Exception fetching {location_id}: {e}")
        return []

# ------------------ Filter & enrich ------------------ #
def filter_properties(properties: List[Dict], outcode: str) -> List[Dict]:
    results = []
    for prop in properties:
        try:
            beds = prop.get("bedrooms")
            rent = prop.get("price", {}).get("amount")
            subtype = (prop.get("propertySubType") or "House")
            subtype_up = subtype.upper()
            address = prop.get("displayAddress", "Unknown")
            summary = (prop.get("summary") or "").upper()
            title = (prop.get("propertyTitle") or "").upper()
            baths = prop.get("bathrooms", 0)

            if beds is None or rent is None: continue
            if not (MIN_BEDS <= beds <= MAX_BEDS): continue
            if rent > MAX_PRICE: continue
            if subtype_up in EXCLUDED_SUBTYPES: continue
            haystack = " ".join([address.upper(), summary, title])
            if any(w in haystack for w in EXCLUDED_KEYWORDS): continue

            p = calculate_profits(rent, beds, outcode)
            p70 = p["profit_70"]

            # Boolean + message for Make/Telegram
            meets_target = p70 >= TARGET_PROFIT_70
            if meets_target:
                ab_message = "🚀 Already ≥ target at 70% — no changes needed."
            else:
                need_adr  = adr_to_hit_target(TARGET_PROFIT_70, rent, p["bills"], 0.7)
                need_rent = rent_to_hit_target(TARGET_PROFIT_70, p["night_rate"], p["bills"], 0.7)
                ab_message = (
                    "💡 *A/B to hit target @ 70%:*\n"
                    f"• *A:* Raise nightly to *£{need_adr}*\n"
                    f"• *B:* Negotiate rent to *~£{need_rent}*/mo*"
                )

            rag = "🟢" if meets_target else ("🟡" if p70 >= TARGET_PROFIT_70 * 0.7 else "🔴")
            score10 = round(max(0, min(10, (p70 / TARGET_PROFIT_70) * 10)), 1)

            listing = {
                "id": prop.get("id"),
                "outcode": outcode,
                "address": address,
                "rent_pcm": rent,
                "bedrooms": beds,
                "bathrooms": baths,
                "propertySubType": subtype,
                "url": f"https://www.rightmove.co.uk{prop.get('propertyUrl')}",
                "nightly_rate": p["night_rate"],
                "profit_50": p["profit_50"],
                "profit_70": p70,
                "profit_100": p["profit_100"],
                "bills": p["bills"],
                "fees": int(BOOKING_FEE_PCT * 100),
                "rag": rag,
                "score10": score10,
                "target_profit_70": TARGET_PROFIT_70,
                "needed_nightly_rate": adr_to_hit_target(TARGET_PROFIT_70, rent, p["bills"], 0.7),
                "needed_rent": rent_to_hit_target(TARGET_PROFIT_70, p["night_rate"], p["bills"], 0.7),
                "meets_target": meets_target,      # ✅ boolean
                "ab_message": ab_message,          # ✅ prebuilt text block
            }
            results.append(listing)
        except Exception:
            continue
    return results

# ------------------ Scraper loop ------------------ #
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
    print("🚀 Scraper started!")
    seen_ids: Set[str] = set()
    while True:
        try:
            ts = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"\n⏰ New scrape at {ts}")
            new_listings = await scrape_once(seen_ids)

            if not new_listings:
                print("ℹ️ No new listings found this run.")
            for listing in new_listings:
                print(f"✅ Sending: {listing['address']} — £{listing['rent_pcm']} — {listing['bedrooms']} beds")
                try:
                    requests.post(WEBHOOK_URL, json=listing, timeout=10)
                except Exception as e:
                    print(f"⚠️ POST to webhook failed: {e}")

            sleep_sec = 3600 + random.randint(-300, 300)
            print(f"💤 Sleeping {sleep_sec}s…")
            await asyncio.sleep(sleep_sec)

        except Exception as e:
            print(f"🔥 Loop error: {e}")
            await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(main())
