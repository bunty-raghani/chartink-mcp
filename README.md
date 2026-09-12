# Chartink MCP Server

> **Original work by [Parthiv Shah](https://github.com/shahparthiv)** — forked from [**shahparthiv/chartink-mcp**](https://github.com/shahparthiv/chartink-mcp).
> The original 5-tool server and `chartink-query` skill are the original author's work. Extended 21-tool coverage, reliability fixes, and MCP SDK v2 / OpenCode V2 migration here are modifications on top.

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io) server that lets Claude — or any MCP-compatible AI agent — run **[Chartink](https://chartink.com) stock screeners and backtests** for the Indian (NSE/BSE) market directly from a chat.

You describe a strategy in plain English, the agent writes the Chartink **scan clause**, and this server runs it against Chartink and returns the matching stocks.

> Bundled with a **`chartink-query` skill** (see [`skills/`](skills/)) that teaches the agent the full Chartink scan-clause syntax — every indicator, function, and gotcha — so it writes correct queries on the first try.

---

## Features

| Tool | What it does |
|------|--------------|
| `set_cookies` | Authenticate (saved to disk, survives restarts). |
| `server_status` | Health check: site, cookies age, premium. No scan consumed. |
| `list_indicators` | Valid clause vocabulary cheat-sheet (no login). |
| `validate_scan` | Static + live 1-row validation for a clause. |
| `search_screeners` | Search curated catalog (~111 scans, no login). |
| `search_all_screeners` | Full-text search over all public scans, paginated (no login). |
| `browse_catalog` | Browse `top_loved` + curated groups (no login). |
| `get_screener_details` | Full metadata for one scan without running it (no login). |
| `list_user_scans` | Public scans by one user, optional clauses (no login). |
| `list_segments` | Runnable universes for the `segment` param (no login). |
| `get_fundamentals` | Snapshot + quarterly/yearly/balance/cash-flow tables (no login). |
| `run_screener` | Run a clause (`segment`, `sort_by`, `min_price`, `csv`; validated before run). |
| `run_multiple_screeners` | Run several clauses in one call. |
| `compare_screeners` | Run 2+ clauses, overlap + uniques join. |
| `run_backtest` | Current backtest matches (latest-candle snapshot + as-of date). |
| `backtest_summary` | Backtest time-series summary per sector + trend. |
| `run_saved_screener` | Fetch public scan by slug/URL and run it (metadata + stocks). |
| `scan_history` | Local log of past runs (recall + re-run). |
| `list_my_scans` | Your dashboard scans (login). |
| `list_alerts` | Your alerts (login + premium). |
| `list_watchlists` | Your watchlists; names feed `segment` (login). |

---

## Requirements

- **Python 3.11** (3.10+ minimum; developed and tested on 3.11.11) — required by the `mcp` package; your system's default `python3` may be older, see [Installation](#installation)
- **`mcp>=2,<3` (currently 2.2.0)** — this server uses the MCP Python SDK v2 lowlevel API (`Server` with `on_list_tools` / `on_call_tool`, `ListToolsResult` / `CallToolResult`). v1 (`@app.list_tools()`, `mcp<2`) is no longer supported.
- A **Chartink account** (free works for most scans; some features need premium)
- An MCP-compatible client: **Claude Desktop**, **Claude Code**, **OpenCode V2**, or any agent that speaks MCP over stdio

---

## Installation

> ⚠️ **Check your Python version first.** The `mcp` v2 package requires **Python 3.10+** (use 3.11). On many machines the default `python3` is older (e.g. macOS ships 3.9), and install will fail. Verify:
> ```bash
> python3 --version        # must be 3.10 or newer, 3.11 recommended
> ```
> If it's older, use one of the options below to get a suitable interpreter.

```bash
# 1. Clone
git clone https://github.com/<your-username>/chartink-mcp.git
cd chartink-mcp
```

### Option A — using `uv` (recommended; handles the Python version for you)

[`uv`](https://docs.astral.sh/uv/) auto-downloads a suitable Python, so you don't need a system 3.10+:

```bash
uv venv venv --python 3.11        # creates ./venv with Python 3.11
uv pip install --python venv/bin/python -r requirements.txt
```

### Option B — plain `venv` + `pip`

```bash
# Use a 3.10+ interpreter. If `python3` is too old, call a specific one,
# e.g. python3.11 / python3.12 (install via Homebrew: `brew install python@3.11`).
python3.11 -m venv venv           # or: python3 -m venv venv  (only if 3.10+)
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies (`requirements.txt`): `mcp>=2,<3`, `requests`, `beautifulsoup4`.

**Verify the install:**
```bash
venv/bin/python --version                       # should print 3.11.x
venv/bin/python -c "import importlib.metadata as m; print(m.version('mcp'))"  # should print 2.x
venv/bin/python -c "import server; print('OK')"  # deps load, server importable
venv/bin/python -c "import asyncio, server; print(len(asyncio.run(server._get_tools())), 'tools')"  # should print 21 tools
```

If you previously installed with `mcp<2`, recreate the venv cleanly — do not mix v1/v2 installs (a mixed `mcp 1.x` + `mcp-types 2.x` + `httpx2` tree will break startup):

```bash
rm -rf venv
uv venv venv --python 3.11
uv pip install --python venv/bin/python -r requirements.txt
```

Note the **absolute path** to the venv's Python and to `server.py` — you'll need them for the client config:

```bash
echo "$(pwd)/venv/bin/python"   # → command
echo "$(pwd)/server.py"         # → args[0]
```

---

## Configuration

The server runs over **stdio**. Point your MCP client at the venv Python + `server.py`. Ready-to-edit templates live in [`config-examples/`](config-examples/).

> **OpenCode V2 note:** servers live under `mcp.servers` (not `mcp.<name>` directly). Do not use an `enabled` field — V2 uses `disabled: true` to turn a server off, otherwise omit it. A legacy `mcp.<name>` + `enabled: true` file may still load via compat, but migrate to the shape below.

### OpenCode V2 (`opencode.json`)

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "chartink": {
        "type": "local",
        "command": [
          "/absolute/path/to/chartink-mcp/venv/bin/python",
          "/absolute/path/to/chartink-mcp/server.py"
        ]
      }
    }
  }
}
```

Verify with `opencode mcp list` — `chartink` should show `connected`. If it shows `failed: MCP error -32000: Connection closed`, the process exited before handshake: check the venv path, `py_compile`, and `mcp` version (see [Troubleshooting](#troubleshooting)).

### Claude Desktop

Edit the config file:
- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "chartink": {
      "command": "/absolute/path/to/chartink-mcp/venv/bin/python",
      "args": ["/absolute/path/to/chartink-mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop. You should see the `chartink` tools appear.

### Claude Code

Create (or edit) `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "chartink": {
      "command": "/absolute/path/to/chartink-mcp/venv/bin/python",
      "args": ["/absolute/path/to/chartink-mcp/server.py"]
    }
  }
}
```

Or add it via the CLI:

```bash
claude mcp add chartink -- /absolute/path/to/chartink-mcp/venv/bin/python /absolute/path/to/chartink-mcp/server.py
```

### Any other MCP client

Use the generic stdio form — run the command below and speak MCP to its stdin/stdout:

```
/absolute/path/to/chartink-mcp/venv/bin/python /absolute/path/to/chartink-mcp/server.py
```

---

## Authentication (one-time per session)

Chartink's scan endpoints need a valid session, so you pass it your browser cookies once.

1. Log in to **[chartink.com](https://chartink.com)** in Chrome.
2. Open **DevTools → Network** tab.
3. Click any request to `chartink.com`, go to **Headers → Request Headers**, and copy the **entire `Cookie:` value** (it contains `XSRF-TOKEN=...; ci_session=...; ...`).
4. In your AI chat, tell the agent to set the cookies, e.g.:

   > "Set my Chartink cookies: `XSRF-TOKEN=...; ci_session=...; ...`"

   (This calls `set_cookies`.) You'll get back **"XSRF-TOKEN found ✓"** if it worked.

Cookies live for the duration of the server process. Re-run `set_cookies` if requests start failing (session expired).

Session persistence (no credentials needed at startup):

- Cookies are saved to `~/.chartink-mcp/session.json` (`0600`, best-effort) and restored on restart if fresh (2h TTL matching `ci_session`).
- Run history is appended to `~/.chartink-mcp/history.jsonl` (used by `scan_history`).
- `server_status` reports `cookies_present`, `cookie_age_seconds`, and `cookies_loaded_from_disk` without running a scan. Missing cookies only limit `run_*` tools — `list_*`, `search_*`, `get_fundamentals`, and `validate_scan --dry_run=false` work logged-out.

> ⚠️ **Never commit your cookies.** They are credentials. The `.gitignore` already excludes common secret/cache files, but treat the cookie string like a password.

---

## Usage examples

Once configured and authenticated, just talk to the agent:

> "Find NSE stocks within 5% of their 52-week high with rising volume."

> "Run this scan clause and show me the matches:
> `( {cash} ( latest rsi( 14 ) > 60 and latest close > latest sma( latest close , 50 ) ) )`"

> "Run the saved screener `short-term-breakouts`."

> "Search for RSI breakout scans, show me the options."

> "Who created `short-term-breakouts`? Show details before running."

> "List public scans by user `@admin`."

> "Compare two strategies: an RSI breakout vs a MACD crossover."

Recommended flow for other users' scans: `search_screeners` (or `list_user_scans`) → `get_screener_details` to confirm the exact slug/URL/author → `run_saved_screener` to execute. Slugs/URLs are unique — never rely on names alone, since many scans share the same name.

The agent writes the scan clause (using the bundled skill), the server runs it, and you get the stock list back.

### Scan-clause shape (quick primer)

```
( {cash} ( <condition> [and|or <condition>] ... ) )
```

`{cash}` = all NSE cash stocks. Conditions use Chartink's English-like grammar, e.g.
`latest close > latest sma( latest close , 50 )`. See the bundled **skill** for the full syntax.

---

## The `chartink-query` skill

The [`skills/chartink-query/`](skills/chartink-query/) folder is a portable **Claude skill** that documents the entire Chartink scan-clause language — price/volume attributes, timeframes & candle offsets, every indicator (SMA, EMA, RSI, MACD, ADX, Bollinger, Stochastic, Supertrend, Donchian, VWAP, OBV, pivots…), functions (`max`/`min`/`count`/`abs`), crossover construction, segments, and the supported fundamental fields — plus a library of ready-to-use example queries.

With the skill installed, the agent writes **valid scan clauses on the first try** and can produce both the MCP/API form and the copy-paste form for chartink.com.

### Install the skill

**Claude Code** — copy it into either location:

```bash
# user-level (available in every project)
cp -r skills/chartink-query ~/.claude/skills/

# or project-level
mkdir -p .claude/skills && cp -r skills/chartink-query .claude/skills/
```

**Claude Desktop / other agents** — point your skills directory at `skills/chartink-query/`, or paste the contents of `SKILL.md` into your system prompt / project knowledge.

Then just ask: *"write a chartink query for stocks breaking a 20-day high with a volume surge"* and the skill handles the syntax.

---

## v2 notes (MCP SDK 2.x + OpenCode V2)

- Server implements MCP Python SDK **v2 lowlevel `Server`** (`on_list_tools` / `on_call_tool`, `ListToolsResult` / `CallToolResult`, `input_schema`). Requires `mcp>=2,<3`.
- OpenCode config uses **`mcp.servers.chartink`** without `enabled`. Use `opencode mcp list` to confirm `connected`.
- No env vars or credentials are required at startup. Auth is runtime-only via `set_cookies`; state lives in `~/.chartink-mcp/`.

---

## Project structure

```
chartink-mcp/
├── server.py                     # the MCP server
├── requirements.txt
├── README.md
├── LICENSE
└── skills/
    └── chartink-query/           # portable Claude skill for Chartink syntax
        ├── SKILL.md
        ├── reference.md          # full keyword/indicator/function catalog
        └── examples.md           # ready-to-use query patterns
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `pip install` fails on `mcp` / `SyntaxError` during install | Your Python is < 3.10. Build the venv with 3.11 (`uv venv venv --python 3.11`) or use the `uv` option in [Installation](#installation). |
| `MCP error -32000: Connection closed` on startup | The stdio process exited before handshake. Check: (1) absolute `venv/bin/python` + `server.py` paths exist, (2) `venv/bin/python -m py_compile server.py` passes, (3) `mcp` is v2 (`venv/bin/python -c "import importlib.metadata as m; print(m.version('mcp'))"` → `2.x`). A mixed `mcp 1.x` + `mcp-types 2.x` tree means a partial upgrade — recreate the venv cleanly. |
| `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` | v1 code running against `mcp 2.x` (`FastMCP` was renamed to `MCPServer`). This repo is already on v2; if you see this in another server, pin that server to `mcp<2` or migrate it to `from mcp.server.mcpserver import MCPServer`. |
| `AttributeError: 'Server' object has no attribute 'list_tools'` / `'Tool' object has no attribute 'inputSchema'` | v1 lowlevel API. v2 uses `Server(..., on_list_tools=..., on_call_tool=...)`, returns `ListToolsResult` / `CallToolResult`, and snake_case attribute access (`tool.input_schema`). |
| Tools don't appear in the client | Check the absolute paths in the config; for OpenCode V2 confirm `mcp.servers.chartink` shape and run `opencode mcp list`. Restart the client (Claude Desktop must be fully quit & reopened). |
| `XSRF-TOKEN not found` / `are you logged in?` | Re-run `set_cookies` with a fresh cookie string from a logged-in browser. |
| Requests suddenly fail | Session expired — set cookies again. Check `server_status` (`cookie_age_seconds`, `cookies_loaded_from_disk`). |
| `run_saved_screener` says "Could not extract scan_clause" | That scan is **private** (its clause isn't in the public page). Ask the owner for the clause and use `run_screener` instead. |
| `run_backtest` returns empty | Chartink's full target/stop backtest is a website/premium feature; the endpoint may not return rich data via API. Use the website's Backtest tab for performance stats. |

> **Never commit secrets.** Cookie strings, `session.json`, and any `Authorization` headers are credentials. Keep them out of git and out of shared config files (use `{env:VAR}` references where the client supports it).

---

## Disclaimer

This project is for **educational and research purposes only**. It is **not investment advice**, and the authors are not SEBI-registered advisers. Screener output and any backtests are **not** recommendations to buy or sell. Always do your own due diligence and manage your own risk. Use in accordance with Chartink's terms of service.

---

## Contributing

Issues and PRs welcome. Ideas: caching, richer backtest parsing, more example skills, tests.

## License

[MIT](LICENSE) — original work Copyright (c) 2026 Parthiv Shah.

Licensing requirements for this MIT-licensed fork: you may use, copy, modify, merge, publish, distribute, sublicense, and sell this software, provided you retain the original copyright notice and permission notice (`LICENSE`) in all copies or substantial portions. No copyleft or disclosure obligation for your own modifications beyond that; the software is provided "AS IS" without warranty. See `LICENSE` for the full text.
