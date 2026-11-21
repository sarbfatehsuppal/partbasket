"""Read-only supplier quote adapters. Secrets are supplied through the server environment."""
import os
import threading
import time

from urllib.parse import quote
import requests

from planner import Offer, integer, key, micros


class SupplierError(Exception):
    pass


def fetch_json(method, url, *, missing_ok=False, **kwargs):
    try:
        response = requests.request(method, url, timeout=(3, 8), allow_redirects=False, **kwargs)
        if missing_ok and response.status_code == 404:
            return {}
        if not 200 <= response.status_code < 300:
            raise SupplierError(f"Request failed (HTTP {response.status_code}).")
        data = response.json()
        if not isinstance(data, dict):
            raise SupplierError("Unexpected supplier response.")
        return data
    except (requests.RequestException, ValueError):
        raise SupplierError("The supplier could not be reached or returned invalid data.") from None


def same_part(part, number, manufacturer):
    return key(part.mpn) == key(number) and (
        not part.manufacturer or key(part.manufacturer) == key(manufacturer)
    )


def stock_count(value):
    return integer(str(value).strip().replace(",", ""), "Stock", minimum=0)


def offer(part, supplier, sku, stock, minimum, multiple, tiers):
    if not sku or not tiers:
        return None
    try:
        available = stock_count(stock)
        if available == 0:
            return None
        return Offer(part.mpn, supplier, str(sku), available,
                     integer(minimum or 1, "Minimum"), integer(multiple or 1, "Pack size"), tiers)
    except ValueError:
        return None


class DigiKey:
    name = "DigiKey"

    def __init__(self, client_id, secret):
        self.id, self.secret = client_id, secret
        self.lock = threading.Lock()
        self.bearer, self.expires = "", 0

    def token(self):
        with self.lock:
            if time.monotonic() >= self.expires:
                data = fetch_json("POST", "https://api.digikey.com/v1/oauth2/token",
                                  data={"client_id": self.id, "client_secret": self.secret,
                                        "grant_type": "client_credentials"})
                token = data.get("access_token")
                if not isinstance(token, str) or not token:
                    raise SupplierError("DigiKey did not return an access token.")
                try:
                    lifetime = integer(data.get("expires_in"), "Token lifetime")
                except ValueError:
                    raise SupplierError("DigiKey returned an invalid access token lifetime.") from None
                self.bearer = token
                self.expires = time.monotonic() + max(1, lifetime - 30)
            return self.bearer

    def find(self, part):
        data = fetch_json("GET", f"https://api.digikey.com/products/v4/search/{quote(part.mpn, safe='')}/pricing",
                          missing_ok=True, params={"limit": 10, "excludeMarketplace": "true", "inStock": "true"},
                          headers={
                              "Authorization": f"Bearer {self.token()}",
                              "X-DIGIKEY-Client-Id": self.id,
                              "X-DIGIKEY-Locale-Site": "CA", "X-DIGIKEY-Locale-Language": "en",
                              "X-DIGIKEY-Locale-Currency": "CAD",
                              "X-DIGIKEY-Account-Id": os.environ.get("DIGIKEY_ACCOUNT_ID", "0"),
                          })
        if not data:
            return []
        locale = (data.get("SettingsUsed") or {}).get("SearchLocale") or {}
        if locale.get("Currency") != "CAD":
            raise SupplierError("DigiKey did not confirm CAD pricing.")
        products = data.get("ProductPricings") or []
        results = []
        for item in products:
            if not same_part(part, item.get("ManufacturerProductNumber"), (item.get("Manufacturer") or {}).get("Name")):
                continue
            for package in item.get("ProductVariations") or ():
                if package.get("MarketPlace") or package.get("DigiReelingFee"):
                    continue
                prices = {}
                for tier in package.get("StandardPricing") or ():
                    try:
                        prices[integer(tier.get("BreakQuantity"), "Break")] = micros(tier.get("UnitPrice"), "Unit price")
                    except ValueError:
                        continue
                package_type = key((package.get("PackageType") or {}).get("Name"))
                multiple = (item.get("StandardPackage") or package.get("MinimumOrderQuantity")) if "tape & reel" in package_type else 1
                if not multiple:
                    continue
                try:
                    available = stock_count(package.get("QuantityAvailableforPackageType"))
                    cap = package.get("MaxQuantityForDistribution")
                    if cap and stock_count(cap) > 0:
                        available = min(available, stock_count(cap))
                except ValueError:
                    continue
                result = offer(part, self.name, package.get("DigiKeyProductNumber"), available,
                               package.get("MinimumOrderQuantity"), multiple, prices)
                if result:
                    results.append(result)
        return results


class Mouser:
    name = "Mouser"

    def __init__(self, api_key):
        self.api_key = api_key

    def find(self, part):
        data = fetch_json("POST", "https://api.mouser.com/api/v1/search/partnumber",
                          params={"apiKey": self.api_key},
                          json={"SearchByPartRequest": {"mouserPartNumber": part.mpn, "partSearchOptions": "None"}})
        if data.get("Errors"):
            raise SupplierError("Mouser rejected the search request.")
        results = []
        for item in (data.get("SearchResults") or {}).get("Parts") or ():
            if not same_part(part, item.get("ManufacturerPartNumber"), item.get("Manufacturer")):
                continue
            prices = {}
            for tier in item.get("PriceBreaks") or ():
                if tier.get("Currency") != "CAD":
                    continue
                value = str(tier.get("Price", "")).strip().removeprefix("C$").removeprefix("$").replace(",", "")
                try:
                    prices[integer(tier.get("Quantity"), "Break")] = micros(value, "Unit price")
                except ValueError:
                    continue
            result = offer(part, self.name, item.get("MouserPartNumber"), item.get("AvailabilityInStock"),
                           item.get("Min"), item.get("Mult"), prices)
            if result:
                results.append(result)
        return results


def configured_suppliers():
    result = []
    client_id, secret = os.environ.get("DIGIKEY_CLIENT_ID"), os.environ.get("DIGIKEY_CLIENT_SECRET")
    if client_id and secret:
        result.append(DigiKey(client_id, secret))
    if os.environ.get("MOUSER_API_KEY"):
        result.append(Mouser(os.environ["MOUSER_API_KEY"]))
    return result
