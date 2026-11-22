"""PartBasket's read-only ChatGPT tools. Each call operates on its own inputs."""
import argparse
import asyncio
import csv
from datetime import datetime, timezone
from io import StringIO
import os
from pathlib import Path
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from matching import Catalog
from planner import Part, MICROS
from suppliers import SupplierError, configured_suppliers
from workspace import compare_orders, label, sample_order

WIDGET = "ui://partbasket/order-comparison-v1.html"
READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
hosts = os.environ.get("PARTBASKET_MCP_HOSTS", "127.0.0.1:*,localhost:*").split(",")
origins = os.environ.get("PARTBASKET_MCP_ORIGINS", "http://127.0.0.1:*,http://localhost:*").split(",")
server = FastMCP(
    "PartBasket",
    instructions=(
        "Compare electronic component orders. Use sample mode only when asked for a demo. "
        "Use supplied CAD quotes or the separately enabled supplier lookup tool for real work. "
        "Do not invent prices, stock, shipping, or part numbers. Ask for missing quantities, "
        "shipping estimates or ambiguous manufacturer part numbers. Report shortfalls explicitly. "
        "All comparisons are estimates; preserve the quoted source, limits and CAD currency."
    ),
    stateless_http=True,
    json_response=True,
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[value.strip() for value in hosts if value.strip()],
        allowed_origins=[value.strip() for value in origins if value.strip()],
    ),
)
catalog = Catalog()


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    boards: int = Field(ge=1, le=1000)
    bom: str = Field(min_length=1, max_length=35_000, description="CSV text with Manufacturer Part Number and Quantity per board. Use confirmed part numbers.")


class PriceSummary(BaseModel):
    parts: str
    shipping: str
    total: str


class Purchase(BaseModel):
    mpn: str
    supplier: str
    sku: str
    needed: int
    ordered: int
    unit_price: str
    cost: str
    quote_reference: str
    projects: list[dict[str, str | int]]


class ProjectShare(BaseModel):
    name: str
    boards: int
    parts: str | None = None
    shipping: str | None = None
    total: str | None = None


class Shortfall(BaseModel):
    mpn: str
    needed: int
    projects: list[str]


class Comparison(BaseModel):
    mode: Literal["sample", "quotes"]
    quote_source: str
    currency: str
    calculated_at: str
    compared_suppliers: list[str]
    distinct_parts: int
    total_units: int
    plans: dict[str, PriceSummary | None]
    optimized_lines: list[Purchase]
    unavailable: list[Shortfall]
    projects: list[ProjectShare]
    savings: str | None
    allocation_method: str | None = None
    limitations: list[str]


@server.tool(
    title="Compare a shared electronics order",
    annotations=READ_ONLY,
    meta={"ui": {"resourceUri": WIDGET}},
)
async def compare_boms(
    projects: Annotated[list[ProjectInput], Field(min_length=1, max_length=8)],
    mode: Literal["sample", "quotes"],
    quote_csv: Annotated[str | None, Field(max_length=100_000)] = None,
    shipping: dict[str, str] | None = None,
) -> Comparison:
    """Compare a complete PCB order and allocate costs per project. In quotes mode, provide quote CSV
    columns mpn,supplier,sku,stock,minimum,multiple,threshold,unit_cad (manufacturer/reference optional)
    and an estimated flat shipping amount in CAD for every supplier. Sample mode is synthetic.
    A row is filled from one supplier. Use only confirmed manufacturer part numbers."""
    if shipping is not None and len(shipping) > 8:
        raise ToolError("Provide shipping estimates for at most eight suppliers.")
    try:
        result = await asyncio.to_thread(compare_orders, [project.model_dump() for project in projects],
                                         mode, quote_csv, shipping)
    except (ValueError, RuntimeError) as error:
        raise ToolError(str(error)) from None
    optimized = result["plans"].get("optimized")
    return Comparison(
        **{key: value for key, value in result.items() if key in Comparison.model_fields and key != "plans"},
        plans={name: PriceSummary.model_validate(plan) if plan else None for name, plan in result["plans"].items()},
        optimized_lines=[Purchase.model_validate(row) for row in optimized["rows"]] if optimized else [],
    )


class PartCandidate(BaseModel):
    mpn: str
    manufacturer: str
    description: str


class PartMatches(BaseModel):
    catalog_scope: str
    candidates: list[PartCandidate]


@server.tool(title="Suggest exact part numbers from a description", annotations=READ_ONLY)
def suggest_part_numbers(description: Annotated[str, Field(min_length=2, max_length=200)]) -> PartMatches:
    """Rank candidates from the small bundled sample catalog. This does not look up live inventory,
    confirm equivalence, or silently replace a line. Ask the user to confirm a match before ordering."""
    return PartMatches(catalog_scope="Bundled 10-part sample catalog",
                       candidates=[PartCandidate.model_validate(row) for row in catalog.suggest(description)])


class DemoOrder(BaseModel):
    projects: list[ProjectInput]
    shipping: dict[str, str]
    quote_source: str


@server.tool(title="Load a sample electronics order", annotations=READ_ONLY)
def get_sample_order() -> DemoOrder:
    """Return two sample electronics BOMs with synthetic quotes and shipping. Use only for demos."""
    return DemoOrder.model_validate({**sample_order(), "quote_source": "Synthetic demo quotes"})


class ExactPart(BaseModel):
    mpn: str = Field(min_length=1, max_length=100)
    manufacturer: str = Field(default="", max_length=100)


class SupplierQuotes(BaseModel):
    received_at: str
    compared_suppliers: list[str]
    failed_suppliers: list[str]
    unquoted_part_numbers: list[str]
    quote_csv: str
    currency: str = "CAD"


# Off by default. Enable only after confirming the intended downstream display
# is authorized by each configured distributor.
if os.environ.get("PARTBASKET_ENABLE_MCP_SUPPLIER_LOOKUP") == "1" and (providers := configured_suppliers()):
    @server.tool(
        title="Look up live quotes for exact part numbers",
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True),
    )
    async def lookup_part_quotes(parts: Annotated[list[ExactPart], Field(min_length=1, max_length=6)]) -> SupplierQuotes:
        """Look up current CAD prices and stock from the operator's approved distributor connections.
        Use verified manufacturer part numbers only. Carry the source and time into the final result.
        This is a quote lookup. It does not send orders or estimate shipping."""
        try:
            requested = [Part(label(part.mpn, "Part number"), 1, part.manufacturer.strip()) for part in parts]
            if len({part.mpn.casefold() for part in requested}) != len(requested):
                raise ValueError("Provide each part number only once.")
        except ValueError as error:
            raise ToolError(str(error)) from None
        offers, failed = [], []
        for provider in providers:
            try:
                batch = await asyncio.to_thread(collect_offers, provider, requested)
            except (SupplierError, KeyError, TypeError, AttributeError, ValueError):
                failed.append(provider.name)
            else:
                offers.extend(batch)
        if len(failed) == len(providers):
            raise ToolError("Every configured supplier lookup failed. Use user-provided quotes or retry later.")
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        target = StringIO()
        writer = csv.writer(target)
        writer.writerow(["mpn", "manufacturer", "supplier", "sku", "stock", "minimum", "multiple",
                         "threshold", "unit_cad", "reference"])
        manufacturers = {part.mpn.casefold(): part.manufacturer for part in requested}
        for quote in offers:
            for threshold, unit in sorted(quote.tiers.items()):
                writer.writerow([quote.mpn, manufacturers[quote.mpn.casefold()], quote.supplier, quote.sku, quote.stock,
                                 quote.minimum, quote.multiple, threshold, f"{unit / MICROS:.6f}",
                                 f"{quote.supplier} quote retrieved {when}"])
        return SupplierQuotes(received_at=when, failed_suppliers=failed,
                              compared_suppliers=[p.name for p in providers if p.name not in failed],
                              unquoted_part_numbers=[part.mpn for part in requested if not any(
                                  item.mpn.casefold() == part.mpn.casefold() for item in offers)],
                              quote_csv=target.getvalue())


def collect_offers(provider, parts):
    return [item for part in parts for item in provider.find(part)]


@server.resource(WIDGET, title="PartBasket order comparison", mime_type="text/html;profile=mcp-app")
def order_widget() -> str:
    return (Path(__file__).resolve().parent / "widget.html").read_text(encoding="utf-8")


class RequestLimit:
    def __init__(self, app, limit=400_000):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST":
            return await self.app(scope, receive, send)
        payload = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if len(payload) + len(chunk) > self.limit:
                await send({"type": "http.response.start", "status": 413,
                            "headers": [(b"content-type", b"application/json")]})
                await send({"type": "http.response.body",
                            "body": b'{"error":"The order is too large for this plugin."}'})
                return
            payload.extend(chunk)
            if not message.get("more_body", False):
                break

        async def replay():
            if len(payload) or not replay.sent:
                data = bytes(payload)
                payload.clear()
                replay.sent = True
                return {"type": "http.request", "body": data, "more_body": False}
            return await receive()

        replay.sent = False
        return await self.app(scope, replay, send)


http_app = RequestLimit(server.streamable_http_app())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PartBasket read-only MCP server")
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="streamable-http")
    args = parser.parse_args()
    if args.transport == "stdio":
        server.run(transport="stdio")
    else:
        import uvicorn
        uvicorn.run(http_app, host=os.environ.get("PARTBASKET_MCP_BIND", "127.0.0.1"),
                    port=int(os.environ.get("PARTBASKET_MCP_PORT", "8787")),
                    limit_concurrency=24, timeout_keep_alive=5, access_log=False, proxy_headers=False)
