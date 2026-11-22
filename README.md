# PartBasket

PartBasket combines PCB bills of materials, finds the lowest estimated order total across the included offers, and splits parts and shipping costs by project. The web workspace and the ChatGPT plugin tools call the same C++17 optimizer.

## Try the workspace

Use Python 3.10 or newer and a C++17 compiler. From this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe build_optimizer.py
.\.venv\Scripts\python.exe app.py
```

On Linux or macOS, use `.venv/bin/python`. If needed, set `CXX` to your GCC or Clang executable. The build script also detects GCC installed under the default Windows MSYS2 UCRT64 path.

Open [the local workspace](http://127.0.0.1:5174/). The example compares synthetic supplier quotes for a signal-conditioning board and a timer module. Use your own part numbers and supplier quotes for any electronics project.

For a real comparison, select **Start blank**, add one or more projects, upload each BOM and a supplier quote CSV, and enter a shipping estimate for each supplier. Use **Save order** to record a comparison in SQLite. Export either the supplier order list or the project cost split as CSV. Unsaved inputs go to the local server for the request. The server stores results only when you save an order.

### CSV formats

- **BOM:** UTF-8 CSV with `Manufacturer Part Number` and `Quantity`. `Manufacturer` is optional but helps when several manufacturers use the same number. Quantity means parts per board.
- **Quotes:** Use the [quote template](data/quote-template.csv). Each row contains `mpn,supplier,sku,stock,minimum,multiple,threshold,unit_cad`. Add optional `manufacturer` and `reference` columns for attribution. Repeat the SKU on one row per price break, with matching stock and pack rules. Use CAD per unit and enter shipping separately.
- The [demo parts list](data/demo_bom.csv) shows a ready-to-import CSV.

### How it calculates totals

For the given offers, the solver compares supplier subsets and chooses the least expensive eligible quantity for each BOM line. It adds shipping once for each supplier actually used. It can buy extras when a pack size requires it or a higher price break lowers the line total. Parts costs are split by the units each project requests. Shipping is split by each project's parts cost within that supplier's order. Rounded cents reconcile to the order total.

Each part number uses one supplier. Shipping is a flat user estimate per supplier, and taxes and duties are excluded. A missing or short-stock part blocks a complete plan. The catalog matcher ranks 10 sample parts using TF-IDF. Users confirm part numbers before putting them on a BOM.

### HTTP API

- GET /api/workspace/example returns a sample request with two project BOMs and shipping estimates.
- POST /api/workspace/quotes validates supplier quote CSV in the quote_csv field and returns supplier, offer, and part counts.
- POST /api/workspace/compare accepts a JSON body with projects, mode, and shipping. In quotes mode, include quote_csv. Each project supplies name, boards (the number of units), and bom (CSV text). The response includes order totals, supplier line items, and project cost splits.

Both POST endpoints accept JSON. The compare endpoint validates requests with Pydantic and returns field-level errors for invalid input. It saves a comparison only when the request includes the JSON boolean true in the save field.

## ChatGPT plugin

Start the remote tool transport in a second terminal:

```powershell
.\.venv\Scripts\python.exe mcp_server.py
```

The local endpoint is `http://127.0.0.1:8787/mcp`. The tools are read-only and stateless. They compare supplied CSV data, show the saved sample fixture, and rank sample part descriptions. The comparison tool returns structured values and an optional inline order card. The plugin never reads the web workspace's saved orders.

**Connect privately to ChatGPT on Windows**

1. In [OpenAI Platform tunnel settings](https://platform.openai.com/settings/organization/tunnels), create a Secure MCP Tunnel for the ChatGPT workspace where you will use PartBasket. Create a runtime API key with tunnel access in [Platform API key settings](https://platform.openai.com/settings/organization/api-keys). See the [Secure MCP Tunnel guide](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) for role and workspace requirements.
2. Keep the MCP server running. In another PowerShell window in the PartBasket folder, run `.\Connect-PartBasket.ps1`. Enter the tunnel ID and runtime key in the local prompts. The launcher passes the key to the tunnel process without writing it to disk. If the client is missing, extract the [official Windows x64 release](https://github.com/openai/tunnel-client/releases/tag/v0.0.21) to `build\openai-tunnel-v0.0.21-win-amd64` and verify the archive against the release's SHA-256 digest.
3. Go to [ChatGPT Plugins](https://chatgpt.com/plugins), choose **Add custom MCP server → Tunnel**, select the running tunnel, and name the connection **PartBasket**. Keep the tunnel running while you use it. Try asking “Use PartBasket to compare the sample electronics order and split the cost by project.”

To use a CSV in ChatGPT, make its rows available to the conversation or paste them as text. The MCP service accepts CSV text. ChatGPT processes what you share in the conversation, including the CSV data it passes to the tool.

For local Codex clients, use stdio and the absolute paths to the project Python and script:

```text
codex mcp add partbasket -- /absolute/path/to/python /absolute/path/to/mcp_server.py --transport stdio
```

**Package for review:** After hosting the endpoint and posting the production privacy notice, run:

```powershell
.\.venv\Scripts\python.exe package_plugin.py --endpoint https://mcp.example.com/mcp --privacy-url https://example.com/privacy
```

The ZIP contains the plugin metadata, connection descriptor and assistant instructions. It contains no runtime data or credentials. Publishing requires [OpenAI plugin submission](https://developers.openai.com/plugins/deploy/submission) and a verified owner.

### Local and hosted setup

- Both services listen on loopback by default. The Dockerfile builds the native optimizer, and Docker Compose binds both services to loopback. Set a unique `PARTBASKET_WEB_PASSWORD` to run the separate web container. The web workspace stores explicitly saved orders in its SQLite volume. It is one shared workspace, so use a separate instance per team if you host for multiple teams.
- For a hosted MCP server, put HTTPS in front of the server, limit traffic at the proxy, and set `PARTBASKET_MCP_HOSTS` to the trusted host seen by the application (include explicit port variants if your proxy forwards them). If your client sends an Origin, allow only its exact verified origin via `PARTBASKET_MCP_ORIGINS`. Avoid recording tool request bodies.
- Keep the workspace private or behind HTTPS and authentication. Its loopback settings are for running on a trusted machine. Set `PARTBASKET_WEB_HOSTS` when a proxy serves it under another hostname.
- Live quote lookup is an optional MCP tool. It is off unless `PARTBASKET_ENABLE_MCP_SUPPLIER_LOOKUP=1` and approved distributor credentials are supplied on the server. Enable only for distribution surfaces the providers authorize. See the [DigiKey API agreement](https://developer.digikey.com/api-user-agreement), [DigiKey OAuth docs](https://developer.digikey.com/tutorials-and-resources/oauth-20-2-legged-flow) and [Mouser Search API](https://my.mouser.com/en/api-search/). Customer-provided CSV quotes require no distributor keys.
- The workspace's comparison requests are never sent to ChatGPT. In a connected ChatGPT session, ChatGPT sends only the arguments for the tools it calls. The plugin parses those CSV arguments as text and does not persist them. OpenAI and your hosting provider may separately process data under their own policies.

## Checks

```powershell
.\.venv\Scripts\python.exe -m unittest -v
.\.venv\Scripts\python.exe evaluate_matching.py
```

The checks cover supplier payload fixtures, native optimization against exhaustive search, cent reconciliation, missing stock, saved orders, and MCP result parity. The matching benchmark uses ten fixture phrases.

GitHub Actions builds the C++17 optimizer and runs the tests in Docker on pull requests, pushes to main, and version tags. A passing v-prefixed version tag also publishes a versioned Docker image to GitHub Container Registry using the workflow's GitHub token.
