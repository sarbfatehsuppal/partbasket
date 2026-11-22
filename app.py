from datetime import datetime, timezone
from hmac import compare_digest
import os
from typing import Literal
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory
from pydantic import BaseModel, Field, StrictBool, StrictFloat, StrictInt, StrictStr, ValidationError

import history
from matching import Catalog
from planner import ROOT, micros, optimize, parse_bom, read_demo
from suppliers import SupplierError, configured_suppliers
from workspace import compare_orders, parse_quotes, sample_order

app = Flask(__name__, static_folder=str(ROOT / "web"), static_url_path="/assets")
app.config["MAX_CONTENT_LENGTH"] = 350_000
app.config["TRUSTED_HOSTS"] = [host.strip() for host in
                               os.environ.get("PARTBASKET_WEB_HOSTS", "127.0.0.1,localhost").split(",")]
catalog = Catalog()
suppliers = configured_suppliers()


class ProjectRequest(BaseModel):
    name: str
    boards: StrictInt | StrictStr = 1
    bom: str = Field(max_length=100_000)


class CompareRequest(BaseModel):
    projects: list[ProjectRequest] = Field(min_length=1, max_length=8)
    mode: Literal["sample", "quotes"] = "sample"
    quote_csv: str | None = Field(default=None, max_length=140_000)
    shipping: dict[str, StrictStr | StrictInt | StrictFloat] | None = None
    save: StrictBool = False


@app.before_request
def authorize():
    password = os.environ.get("PARTBASKET_WEB_PASSWORD")
    if password:
        credentials = request.authorization
        username = os.environ.get("PARTBASKET_WEB_USER", "partbasket")
        if not credentials or credentials.type != "basic" or not (
            compare_digest((credentials.username or "").encode(), username.encode()) &
            compare_digest((credentials.password or "").encode(), password.encode())
        ):
            return Response("PartBasket workspace sign-in required.", 401,
                            {"WWW-Authenticate": 'Basic realm="PartBasket", charset="UTF-8"'})
    if request.method in {"POST", "DELETE"} and request.headers.get("Origin"):
        try:
            allowed = urlsplit(request.headers["Origin"]).netloc.lower() == request.host.lower()
        except ValueError:
            allowed = False
        if not allowed:
            return {"error": "This action must be submitted from the workspace."}, 403


@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
    )
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(413)
def too_large(_):
    return {"error": "The request is too large. Use a smaller BOM or quote CSV."}, 413


@app.get("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/workspace/example")
def workspace_example():
    return sample_order()


@app.get("/api/workspace/quote-template")
def quote_template():
    return send_from_directory(ROOT / "data", "quote-template.csv", as_attachment=True)


@app.post("/api/workspace/quotes")
def inspect_quotes():
    payload = request.get_json(silent=True) or {}
    try:
        if not isinstance(payload, dict):
            raise ValueError("Supply a quote CSV.")
        offers, _, names, _ = parse_quotes(payload.get("quote_csv"))
        return {"suppliers": names, "offers": len(offers),
                "parts": len({offer.mpn.casefold() for offer in offers})}
    except ValueError as error:
        return {"error": str(error)}, 400


@app.post("/api/workspace/compare")
def compare():
    try:
        payload = CompareRequest.model_validate(request.get_json(silent=True))
    except ValidationError as error:
        details = error.errors(include_input=False, include_context=False, include_url=False)
        return {"error": "Check the order request fields.", "details": details}, 400

    try:
        result = compare_orders([project.model_dump() for project in payload.projects],
                                payload.mode, payload.quote_csv, payload.shipping)
        if payload.save:
            if result["unavailable"]:
                return {"error": "Resolve missing parts before saving this order."}, 400
            result["saved_id"] = history.save(result)
        return jsonify(result)
    except ValueError as error:
        return {"error": str(error)}, 400
    except RuntimeError as error:
        return {"error": str(error)}, 503


@app.get("/api/workspace/plans")
def saved_plans():
    return {"orders": history.recent()}


@app.get("/api/workspace/plans/<uuid:order_id>")
def saved_plan(order_id):
    identifier = str(order_id)
    result = history.get(identifier)
    return jsonify(result | {"saved_id": identifier}) if result else ({"error": "Order not found."}, 404)


@app.delete("/api/workspace/plans/<uuid:order_id>")
def remove_plan(order_id):
    return ("", 204) if history.delete(str(order_id)) else ({"error": "Order not found."}, 404)


@app.get("/api/demo")
def demo():
    return {"bom": (ROOT / "data/demo_bom.csv").read_text(encoding="utf-8"),
            "live_suppliers": [supplier.name for supplier in suppliers]}


@app.post("/api/suggest")
def suggest():
    payload = request.get_json(silent=True)
    query = payload.get("description") if isinstance(payload, dict) else None
    if not isinstance(query, str) or not 2 <= len(query.strip()) <= 200:
        return {"error": "Enter a part description of 2–200 characters."}, 400
    return {"candidates": catalog.suggest(query)}


@app.post("/api/plan")
def plan():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get("bom"), str):
        return {"error": "Supply a BOM as CSV text."}, 400
    try:
        parts = parse_bom(payload["bom"], payload.get("boards", 1))
        mode = payload.get("mode", "demo")
        if mode not in ("demo", "live"):
            raise ValueError("Choose demo or live mode.")
        names = ["Northstar", "CircuitHouse"] if mode == "demo" else [s.name for s in suppliers]
        if not names:
            raise ValueError("No supplier credentials are configured. Start with demo quotes.")
        costs = payload.get("shipping", {})
        if not isinstance(costs, dict) or any(name not in costs for name in names):
            raise ValueError("Enter a shipping estimate in CAD for each selected supplier.")
        shipping = {name: micros(costs[name], f"{name} shipping", allow_zero=True) for name in names}
        unavailable_suppliers = []
        if mode == "demo":
            offers = read_demo()
        else:
            if len(parts) > 12:
                raise ValueError("Live mode supports up to 12 unique part numbers per request.")
            offers = []
            for supplier in suppliers:
                # Require a complete lookup per supplier so a partial failure cannot appear as missing stock.
                try:
                    batch = [offer for part in parts for offer in supplier.find(part)]
                except (SupplierError, TypeError, KeyError, AttributeError, ValueError):
                    unavailable_suppliers.append(supplier.name)
                else:
                    offers.extend(batch)
            if len(unavailable_suppliers) == len(names):
                return {"error": "Live lookups failed for every configured supplier.",
                        "unavailable_suppliers": unavailable_suppliers}, 502
        result = optimize(parts, offers, shipping)
        return jsonify(result | {"mode": mode, "quoted_at": datetime.now(timezone.utc).isoformat(),
                                 "unavailable_suppliers": unavailable_suppliers,
                                 "compared_suppliers": [n for n in names if n not in unavailable_suppliers]})
    except ValueError as error:
        return {"error": str(error)}, 400
    except RuntimeError as error:
        return {"error": str(error)}, 503


if __name__ == "__main__":
    from waitress import serve

    host = os.environ.get("PARTBASKET_WEB_BIND", "127.0.0.1")
    if host not in ("127.0.0.1", "::1", "localhost") and not os.environ.get("PARTBASKET_WEB_PASSWORD"):
        raise SystemExit("Set PARTBASKET_WEB_PASSWORD before binding the workspace beyond loopback.")
    serve(app, host=host, port=int(os.environ.get("PARTBASKET_WEB_PORT", "5174")),
          threads=4, max_request_body_size=350_000, expose_tracebacks=False)
