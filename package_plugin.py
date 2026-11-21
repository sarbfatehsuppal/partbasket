"""Create a portable plugin ZIP pointed at a deployment you control."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZIP_DEFLATED, ZipFile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--endpoint", required=True, help="Stable HTTPS MCP endpoint ending in /mcp")
parser.add_argument("--privacy-url", required=True, help="Public HTTPS page with your privacy notice")
parser.add_argument("--output", type=Path, default=Path("dist/partbasket-plugin.zip"))
args = parser.parse_args()


def https_url(value, label, suffix=None):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname or
            parsed.username or parsed.password or parsed.query or parsed.fragment or
            (suffix and not parsed.path.endswith(suffix))):
        parser.error(f"{label} must be a public HTTPS URL without credentials or a query string.")
    return value


endpoint = https_url(args.endpoint, "MCP endpoint", "/mcp")
privacy = https_url(args.privacy_url, "Privacy notice")
manifest = {
    "$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json",
    "name": "partbasket",
    "version": "1.0.0",
    "description": "Compare electronic parts orders and split costs between projects.",
    "extensions": {"com.openai": {"interface": {
        "displayName": "PartBasket",
        "shortDescription": "Compare electronic parts orders",
        "longDescription": "Combine BOMs, compare supplier quotes in CAD, and divide the cost between projects.",
        "privacyPolicyURL": privacy,
        "brandColor": "#126B50",
    }}},
}
mcp = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
       "mcpServers": {"partbasket": {"type": "streamable-http", "url": endpoint,
                                    "extensions": {"com.openai": {"auth": {"type": "none"}}}}}}
args.output.parent.mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parent
with ZipFile(args.output, "w", compression=ZIP_DEFLATED) as package:
    package.writestr("plugin.json", json.dumps(manifest, indent=2) + "\n")
    package.writestr("mcp.json", json.dumps(mcp, indent=2) + "\n")
    package.write(source / "plugin/skills/parts-order/SKILL.md", "skills/parts-order/SKILL.md")
print(f"Created {args.output}")
