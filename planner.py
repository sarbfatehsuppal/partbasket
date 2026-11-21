import csv
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import StringIO
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parent
MICROS = 1_000_000


@dataclass(frozen=True)
class Part:
    mpn: str
    quantity: int
    manufacturer: str = ""


@dataclass
class Offer:
    mpn: str
    supplier: str
    sku: str
    stock: int
    minimum: int
    multiple: int
    tiers: dict[int, int] = field(default_factory=dict)


def integer(value, label, minimum=1, maximum=1_000_000):
    if not re.fullmatch(r"[0-9]{1,9}", str(value).strip()):
        raise ValueError(f"{label} must be a whole number.")
    number = int(value)
    if not minimum <= number <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return number


def micros(value, label, *, allow_zero=False, limit=1000):
    try:
        raw = Decimal(str(value))
        if not raw.is_finite() or raw < 0 or raw > limit:
            raise ValueError
        amount = int((raw * MICROS).to_integral_value(rounding=ROUND_HALF_UP))
        if amount == 0 and not allow_zero:
            raise ValueError
        return amount
    except (ValueError, InvalidOperation, OverflowError):
        raise ValueError(f"{label} must be a valid CAD amount up to {limit}.") from None


def key(value):
    return str(value or "").strip().casefold()


def money(value):
    return format((Decimal(value) / MICROS).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


def parse_bom(text, boards):
    boards = integer(boards, "Board count", maximum=1000)
    try:
        rows = csv.DictReader(StringIO(text.lstrip("\ufeff")), strict=True)
        headers = {re.sub(r"[^a-z0-9]", "", h.lower()): h for h in (rows.fieldnames or ())}
        mpn = next((headers[h] for h in ("mpn", "manufacturerpartnumber", "manufacturerpartno") if h in headers), None)
        qty = next((headers[h] for h in ("quantity", "qty", "quantityperboard") if h in headers), None)
        if not mpn or not qty:
            raise ValueError("The CSV needs Manufacturer Part Number and Quantity columns.")
        parts = {}
        for index, row in enumerate(rows, 2):
            if index > 201:
                raise ValueError("Use a BOM with at most 200 rows.")
            if not any(v and str(v).strip() for v in row.values()):
                continue
            number = (row.get(mpn) or "").strip()
            if not number or len(number) > 100 or any(ord(ch) < 32 for ch in number):
                raise ValueError(f"Row {index} needs an exact manufacturer part number.")
            manufacturer = (row.get(headers.get("manufacturer", "")) or "").strip()
            count = integer(row.get(qty), f"Quantity on row {index}", maximum=1000) * boards
            existing = parts.get(key(number))
            if existing and existing.manufacturer and manufacturer and key(existing.manufacturer) != key(manufacturer):
                raise ValueError(f"Part number {number} has conflicting manufacturers.")
            total = count + (existing.quantity if existing else 0)
            if total > 1_000_000:
                raise ValueError(f"Quantity for {number} exceeds the supported limit.")
            parts[key(number)] = Part(number, total, manufacturer or (existing.manufacturer if existing else ""))
        if not parts:
            raise ValueError("The BOM has no parts.")
        return list(parts.values())
    except csv.Error:
        raise ValueError("The BOM contains malformed CSV.") from None


def read_demo():
    with (ROOT / "data/demo_quotes.csv").open(encoding="utf-8", newline="") as source:
        quotes = {}
        for row in csv.DictReader(source):
            identity = row["supplier"], row["sku"]
            if identity not in quotes:
                quotes[identity] = Offer(row["mpn"], *identity, int(row["stock"]), int(row["minimum"]),
                                         int(row["multiple"]))
            quotes[identity].tiers[int(row["threshold"])] = micros(row["unit_cad"], "Price")
    return list(quotes.values())


def can_fill(part, offer):
    quantity = max(part.quantity, offer.minimum, min(offer.tiers))
    return ((quantity + offer.multiple - 1) // offer.multiple) * offer.multiple <= offer.stock


def optimize(parts, offers, shipping):
    solver = ROOT / "build" / ("optimizer.exe" if os.name == "nt" else "optimizer")
    if not solver.is_file():
        raise RuntimeError("Run python build_optimizer.py to build the C++ optimizer.")
    suppliers = tuple(shipping)
    supplier_ids = {supplier: idx for idx, supplier in enumerate(suppliers)}
    grouped = {key(part.mpn): [] for part in parts}
    for offer in offers:
        if key(offer.mpn) in grouped and offer.supplier in supplier_ids and offer.tiers:
            grouped[key(offer.mpn)].append(offer)
    unavailable = [part.mpn for part in parts if not any(can_fill(part, o) for o in grouped[key(part.mpn)])]
    if unavailable:
        return {"unavailable": unavailable, "plans": {}, "savings": None}
    lines = [f"{len(suppliers)} {len(parts)}", " ".join(str(shipping[supplier]) for supplier in suppliers)]
    indexed = []
    for part in parts:
        quotes = grouped[key(part.mpn)]
        lines.append(f"{part.quantity} {len(quotes)}")
        for quote in quotes:
            lines.append(f"{supplier_ids[quote.supplier]} {len(indexed)} {quote.stock} "
                         f"{quote.minimum} {quote.multiple} {len(quote.tiers)}")
            lines += [f"{threshold} {price}" for threshold, price in sorted(quote.tiers.items())]
            indexed.append(quote)
    try:
        run = subprocess.run([str(solver)], input="\n".join(lines), capture_output=True,
                             text=True, check=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("The native optimizer could not complete the request.") from None
    result = iter(run.stdout.splitlines())
    plans, totals = {}, {}
    for name in ("optimized", "per_part", "single_store"):
        header = next(result).split()
        if header[0] != name:
            raise RuntimeError("Unexpected response from the optimizer.")
        if header[1] == "0":
            plans[name] = None
            continue
        parts_cost, shipping_cost, count = map(int, header[2:])
        if count != len(parts):
            raise RuntimeError("The optimizer returned an incomplete plan.")
        rows = []
        for part in parts:
            index, units, cost = map(int, next(result).split())
            quote = indexed[index]
            rows.append({
                "mpn": part.mpn, "supplier": quote.supplier, "sku": quote.sku, "needed": part.quantity,
                "ordered": units, "unit_price": f"{Decimal(cost) / (units * MICROS):.6f}".rstrip("0").rstrip("."),
                "cost": money(cost),
                "cost_micros": cost,
            })
        totals[name] = parts_cost + shipping_cost
        plans[name] = {"parts": money(parts_cost), "shipping": money(shipping_cost),
                       "total": money(totals[name]), "rows": rows}
    return {"unavailable": [], "plans": plans, "savings": money(totals["per_part"] - totals["optimized"])}
