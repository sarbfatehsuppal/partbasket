"""Compute a combined order and divide each supplier's costs across its projects."""
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
from io import StringIO

from planner import Offer, Part, integer, key, micros, money, optimize, parse_bom, read_demo

DEMO_SHIPPING = {"Northstar": "8.00", "CircuitHouse": "10.00"}
REQUIRED_QUOTE_COLUMNS = {"mpn", "supplier", "sku", "stock", "minimum", "multiple", "threshold", "unit_cad"}
LIMITS = ["Shipping is a flat CAD estimate per supplier.", "Each BOM line is filled by one supplier.",
          "Tax and duties are excluded. Confirm estimates at checkout."]


def label(value, field, maximum=100):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= maximum:
        raise ValueError(f"{field} must be between 1 and {maximum} characters.")
    value = value.strip()
    if any(ord(char) < 32 for char in value):
        raise ValueError(f"{field} contains an invalid control character.")
    return value


def parse_quotes(text):
    if not isinstance(text, str) or len(text) > 140_000:
        raise ValueError("Quote CSV must be text no longer than 140,000 characters.")
    try:
        reader = csv.DictReader(StringIO(text.lstrip("\ufeff")), strict=True)
        headers = [key(h) for h in reader.fieldnames or []]
        if not REQUIRED_QUOTE_COLUMNS.issubset(headers) or len(set(headers)) != len(headers):
            raise ValueError("Quote CSV needs mpn, supplier, sku, stock, minimum, multiple, threshold, unit_cad.")
        reader.fieldnames = headers
        offers, refs, supplier_names, sku_parts, part_manufacturers = {}, {}, {}, {}, {}
        for line, row in enumerate(reader, 2):
            if line > 2401:
                raise ValueError("Use at most 2,400 quote rows.")
            if None in row:
                raise ValueError(f"Quote row {line} has extra columns.")
            mpn = label(row["mpn"], f"Quote row {line} part number")
            supplier_entered = label(row["supplier"], f"Quote row {line} supplier", 60)
            supplier = supplier_names.setdefault(key(supplier_entered), supplier_entered)
            sku = label(row["sku"], f"Quote row {line} SKU")
            manufacturer = (row.get("manufacturer") or "").strip()
            reference = (row.get("reference") or "").strip()
            if manufacturer:
                label(manufacturer, f"Quote row {line} manufacturer")
            if reference:
                label(reference, f"Quote row {line} reference", 160)
            identity = key(mpn), supplier, key(sku)
            previous = sku_parts.setdefault((supplier, key(sku)), key(mpn))
            if previous != key(mpn):
                raise ValueError(f"Supplier SKU {sku} is assigned to more than one part number.")
            if manufacturer:
                part_manufacturers.setdefault(key(mpn), set()).add(key(manufacturer))
            counts = (integer(row["stock"], f"Quote row {line} stock", minimum=0),
                      integer(row["minimum"], f"Quote row {line} minimum"),
                      integer(row["multiple"], f"Quote row {line} multiple"))
            threshold = integer(row["threshold"], f"Quote row {line} threshold")
            unit_price = micros(row["unit_cad"], f"Quote row {line} unit price")
            if identity not in offers:
                offers[identity] = Offer(mpn, supplier, sku, *counts)
                refs[identity] = {"manufacturer": manufacturer, "reference": reference}
            offer = offers[identity]
            if counts != (offer.stock, offer.minimum, offer.multiple) or refs[identity] != {
                "manufacturer": manufacturer, "reference": reference
            }:
                raise ValueError(f"Quote row {line} conflicts with another tier for SKU {sku}.")
            if threshold in offer.tiers and offer.tiers[threshold] != unit_price:
                raise ValueError(f"Quote row {line} repeats a price break with a different price.")
            offer.tiers[threshold] = unit_price
            if len(offer.tiers) > 100:
                raise ValueError(f"SKU {sku} has more than 100 price breaks.")
        if not offers or len(supplier_names) > 8:
            raise ValueError("Supply quotes from one to eight suppliers.")
        return list(offers.values()), refs, list(supplier_names.values()), part_manufacturers
    except csv.Error:
        raise ValueError("The quote file contains malformed CSV.") from None


def merge_projects(projects):
    if not isinstance(projects, list) or not 1 <= len(projects) <= 8:
        raise ValueError("Add one to eight projects.")
    combined, quantities, names, per_project = {}, defaultdict(dict), [], []
    for project in projects:
        if not isinstance(project, dict):
            raise ValueError("Each project needs a name, board count and BOM CSV.")
        name = label(project.get("name"), "Project name", 60)
        if key(name) in {key(value) for value in names}:
            raise ValueError("Give each project a unique name.")
        names.append(name)
        if not isinstance(project.get("bom"), str) or len(project["bom"]) > 100_000:
            raise ValueError(f"{name} needs a BOM CSV under 100,000 characters.")
        try:
            parts = parse_bom(project["bom"], project.get("boards", 1))
        except ValueError as error:
            raise ValueError(f"{name}: {error}") from None
        index = len(names) - 1
        for part in parts:
            number = key(part.mpn)
            current = combined.get(number)
            if current and current.manufacturer and part.manufacturer and key(current.manufacturer) != key(part.manufacturer):
                raise ValueError(f"Select one manufacturer for {part.mpn}; projects use conflicting manufacturers.")
            total = part.quantity + (current.quantity if current else 0)
            if total > 1_000_000:
                raise ValueError(f"Combined quantity for {part.mpn} exceeds 1,000,000.")
            combined[number] = Part(part.mpn, total, part.manufacturer or (current.manufacturer if current else ""))
            quantities[number][index] = part.quantity
        per_project.append({"name": name, "boards": int(project.get("boards", 1)), "line_count": len(parts)})
    if len(combined) > 200:
        raise ValueError("The combined order supports up to 200 distinct part numbers.")
    return combined, quantities, per_project


def split_units(amount, weights):
    """Allocate integer amounts by largest remainder, with stable project-order ties."""
    total = sum(weights.values())
    if amount < 0 or total <= 0:
        raise ValueError("Cannot allocate this amount.")
    shares = {}
    remainders = []
    for index, weight in sorted(weights.items()):
        base, remainder = divmod(amount * weight, total)
        shares[index] = base
        remainders.append((remainder, index))
    missing = amount - sum(shares.values())
    for _, index in sorted(remainders, key=lambda item: (-item[0], item[1]))[:missing]:
        shares[index] += 1
    return shares


def as_cents(value):
    return int(Decimal(value) * 100)


def format_cents(value):
    return money(value * 10_000)


def allocate(plan, quantities, projects, shipping, refs):
    part_weights = defaultdict(dict)
    supplier_cost = defaultdict(int)
    rows_by_supplier = defaultdict(list)
    for row in plan["rows"]:
        identity = key(row["mpn"]), row["supplier"], key(row["sku"])
        row["quote_reference"] = refs.get(identity, {}).get("reference", "")
        rows_by_supplier[row["supplier"]].append(row)
        supplier_cost[row["supplier"]] += row["cost_micros"]
        project_qty = quantities[key(row["mpn"])]
        row["projects"] = [{"name": projects[index]["name"], "needed": count}
                           for index, count in project_qty.items()]
        distributed = split_units(row["cost_micros"], project_qty)
        for index, amount in distributed.items():
            part_weights[row["supplier"]][index] = part_weights[row["supplier"]].get(index, 0) + amount

    supplier_names = sorted(rows_by_supplier)
    # Reconcile every displayed cent with the order total, even with sub-cent unit prices.
    subtotal_cents = split_units(as_cents(plan["parts"]),
                                 {i: supplier_cost[s] for i, s in enumerate(supplier_names)})
    totals = [dict(project, parts=0, shipping=0) for project in projects]
    supplier_groups = []
    for i, supplier in enumerate(supplier_names):
        weights = part_weights[supplier]
        parts = split_units(subtotal_cents[i], weights)
        ship = as_cents(money(shipping[supplier]))
        shares = split_units(ship, weights)
        rows = rows_by_supplier[supplier]
        display_costs = split_units(subtotal_cents[i], {i: row["cost_micros"] for i, row in enumerate(rows)})
        for line, row in enumerate(rows):
            row["cost"] = format_cents(display_costs[line])
        for index in weights:
            totals[index]["parts"] += parts[index]
            totals[index]["shipping"] += shares[index]
        supplier_groups.append({
            "name": supplier, "parts": format_cents(subtotal_cents[i]), "shipping": format_cents(ship),
            "total": format_cents(subtotal_cents[i] + ship), "rows": rows,
        })
    projects_out = [{**project, "parts": format_cents(project["parts"]),
                     "shipping": format_cents(project["shipping"]),
                     "total": format_cents(project["parts"] + project["shipping"])} for project in totals]
    return projects_out, supplier_groups


def compare_orders(projects, mode, quote_csv, shipping):
    parts, quantities, project_info = merge_projects(projects)
    if mode == "sample":
        offers, refs, names = read_demo(), {}, list(DEMO_SHIPPING)
        status = "Synthetic demo quotes"
    elif mode == "quotes":
        offers, refs, names, manufacturers = parse_quotes(quote_csv)
        status = "User-provided CAD quotes"
        for number, part in parts.items():
            known = manufacturers.get(number, set())
            if len(known) > 1 and not part.manufacturer:
                raise ValueError(f"Quotes match multiple manufacturers for {part.mpn}. Specify one in the BOM.")
        selected = []
        for offer in offers:
            number = key(offer.mpn)
            if number not in parts:
                continue
            expected = key(parts[number].manufacturer)
            quoted = key(refs[number, offer.supplier, key(offer.sku)]["manufacturer"])
            if expected and quoted and expected != quoted:
                continue
            if expected and not quoted and len(manufacturers.get(number, ())) > 1:
                continue
            selected.append(offer)
        offers = selected
    else:
        raise ValueError("Choose sample or quotes mode.")

    if shipping is None and mode == "sample":
        shipping = DEMO_SHIPPING
    if not isinstance(shipping, dict) or {key(s) for s in shipping} != {key(s) for s in names} or len(shipping) != len(names):
        raise ValueError("Enter a shipping estimate for each listed supplier.")
    normalized = {key(name): value for name, value in shipping.items()}
    costs = {}
    for name in names:
        amount = micros(normalized[key(name)], f"{name} shipping", allow_zero=True)
        if amount % 10_000:
            raise ValueError(f"{name} shipping must be stated to the nearest cent.")
        costs[name] = amount

    offer_count = defaultdict(int)
    for offer in offers:
        if key(offer.mpn) in parts:
            offer_count[key(offer.mpn)] += 1
            if offer_count[key(offer.mpn)] > 200:
                raise ValueError(f"Provide at most 200 offers for {offer.mpn}.")
    result = optimize(list(parts.values()), offers, costs)
    result.update(currency="CAD", quote_source=status, mode=mode,
                  calculated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  compared_suppliers=names, distinct_parts=len(parts),
                  total_units=sum(part.quantity for part in parts.values()),
                  limitations=list(LIMITS), projects=project_info, suppliers=[])
    if result["unavailable"]:
        result["unavailable"] = [{"mpn": part, "needed": parts[key(part)].quantity,
                                  "projects": [project_info[i]["name"] for i in quantities[key(part)]]}
                                 for part in result["unavailable"]]
    else:
        optimized = result["plans"]["optimized"]
        result["projects"], result["suppliers"] = allocate(optimized, quantities, project_info, costs, refs)
        result["allocation_method"] = "Line costs by units requested; shipping by parts cost within each supplier."
    for plan in result["plans"].values():
        if plan:
            for row in plan["rows"]:
                row.pop("cost_micros", None)
    return result
