<picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-en-dark.png"><img src="docs/images/banner-en.png" width="100%" alt="fin-checkup — Korean listed companies' financial statements as a traffic light, with the formula and thresholds behind every color. Signals: Normal ● Caution ▲ Risk ■ Measured ▬ N/A ⊘ No data ◌"></picture>

# fin-checkup

**English** | [한국어](README.md)

**Financial statements as traffic lights — with the formula behind every color.** fin-checkup reads Korean corporate filings (DART) and shows 17 financial ratios for 2,642 listed companies, each with its formula, thresholds, last year's value and where the company sits among its industry peers. No install, no sign-up, no API key — and you can simply ask your AI agent "diagnose Samsung Electronics' financials".

## ▶ Open the site: [younghwan91.github.io/fin-checkup](https://younghwan91.github.io/fin-checkup/)

Example: **[Samsung Electronics (005930)](https://younghwan91.github.io/fin-checkup/c/005930/)** · [KB Financial Group (105560)](https://younghwan91.github.io/fin-checkup/c/105560/)

> **The site is in Korean only.** Numbers, colors and formulas read fine without Korean; a browser's translate feature handles the explanations. US companies are not on the site — they are available through the command-line tool (SEC EDGAR), see below.

[![Site demo — search the initial consonants ㅅㅅㅈㅈ to find Samsung Electronics, scan all 17 signals at a glance, then open the operating-margin card's formula, thresholds, industry distribution and the metric explainer](docs/images/demo.gif)](https://younghwan91.github.io/fin-checkup/c/005930/)

> Measurements computed from filed figures. Not investment advice, and no stock recommendations.

[![CI](https://github.com/younghwan91/fin-checkup/actions/workflows/ci.yml/badge.svg)](https://github.com/younghwan91/fin-checkup/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![LinkedIn](https://img.shields.io/badge/LinkedIn-younghwan--chae-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/younghwan-chae/)

## Three things

- **2,642 Korean listed companies, FY2025 annual reports** — KOSPI 829 · KOSDAQ 1,798 · KONEX 15. Source: DART, Korea's Financial Supervisory Service disclosure system. Values from 2021 on give year-over-year change and 5-year trends.
- **Three layers of context per number.** "Operating margin 13.07%" comes with last year's 10.88%, "top 10% of 294 companies in the same industry" and the industry median of 2.00%. Industries are KSIC two-digit groups; by operating margin, 98.7% of companies (2,607) are compared with at least 5 industry peers, the rest with the whole market. Thresholds are calibrated to actual market quantiles, not borrowed rules of thumb (rationale in comments in [`metrics/engine.py`](src/fin_checkup/metrics/engine.py)).
- **Missing data stays missing.** An account that could not be found is ⚫, never 0. A ratio that doesn't fit the sector is ⊘ with what to look at instead — a bank's debt ratio of 1,211.73% ([KB Financial](https://younghwan91.github.io/fin-checkup/c/105560/)) is normal because deposits are liabilities, so it is not judged. There is no composite score.

Lights: 🟢 normal · 🟡 caution · 🔴 risk · ⚪ measured only (no absolute threshold) · ⊘ not applicable · ⚫ no data.

## Ask your AI agent — "diagnose Samsung Electronics' financials"

Agents answer from this data with **no API key and no sign-up** — they only read the static site.

**No install (ChatGPT · Claude.ai · Perplexity on the web)** — paste:

```text
Read https://younghwan91.github.io/fin-checkup/llms.txt and follow it to diagnose Samsung Electronics' (005930) financials
```

**Claude Code** — two lines add the "diagnose" / "compare" skills and tools:

```bash
claude plugin marketplace add younghwan91/fin-checkup
claude plugin install fin-checkup@fin-checkup
```

**MCP server (Cursor · VS Code · Claude Desktop · Codex …)** — [![Add to Cursor](https://img.shields.io/badge/Add_to-Cursor-000000?logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=fin-checkup&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJmaW4tY2hlY2t1cFttY3BdIEAgZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS95b3VuZ2h3YW45MS9maW4tY2hlY2t1cCIsImZpbi1jaGVja3VwLW1jcCJdfQ%3D%3D) [![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%7B%22name%22%3A%22fin-checkup%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22fin-checkup%5Bmcp%5D%20%40%20git%2Bhttps%3A%2F%2Fgithub.com%2Fyounghwan91%2Ffin-checkup%22%2C%22fin-checkup-mcp%22%5D%7D)

```bash
claude mcp add fin-checkup -- uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp
```

Needs [uv](https://docs.astral.sh/uv/getting-started/installation/) and git. Per-client setup and the tool list: [`docs/agents.md`](docs/agents.md) (Korean). Answers come in Korean by default with formulas, thresholds and the disclaimer; ask "is it a buy?" and the agent declines and shows the facts instead. If installing feels hard, just tell your agent *"set up fin-checkup"* — it walks you through installation and, if you want, getting your own DART API key.

> Data flow: DART → collection and a public snapshot → a static site rebuilt daily at 07:00 KST → browsers and agents read only that site. Financial statements refresh each filing season; risk disclosures daily.

## On the site

| | |
|---|---|
| [Company page](https://younghwan91.github.io/fin-checkup/c/005930/) | 17 ratios, red flags (capital impairment, consecutive operating losses, …), 5-year trends |
| [Ratios](https://younghwan91.github.io/fin-checkup/m/) · [Industries](https://younghwan91.github.io/fin-checkup/s/) · [Market](https://younghwan91.github.io/fin-checkup/market/) | What each ratio means and its distribution; companies per industry, sorted by name |
| [Compare](https://younghwan91.github.io/fin-checkup/compare/) | Several companies side by side |
| [Watchlist](https://younghwan91.github.io/fin-checkup/watch/) | Signal changes for companies you saved. Stored only in your browser; no account |

### Risk-disclosure feeds

Filings such as rights offerings, convertible bonds, audit opinions and delisting are published as **Atom feeds** — add the URL to any RSS reader or the RSS app in Slack/Discord. Each entry is the fact that a filing appeared plus a link to the original on DART. The site and feeds are rebuilt daily at 07:00 KST.

- Whole market (critical filings only): `https://younghwan91.github.io/fin-checkup/feeds/disclosures.xml`
- Per company (signal changes + that company's risk filings): `https://younghwan91.github.io/fin-checkup/feeds/c/005930.xml`

> ⚠️ A Telegram bot (`cli bot`) is included, but sending real messages has not been verified (no bot token issued; mocked tests only). Feeds are the notification path that works today.

## For developers

**CLI** — Korean companies via DART, US companies via SEC EDGAR. **US companies are CLI-only.** CLI output is in Korean.

```bash
uv sync --all-extras                                 # `uv sync` is enough for the CLI
cp .env.example .env                                 # DART_API_KEY: free and instant at opendart.fss.or.kr
uv run python -m fin_checkup.cli sync                # company list (once)
uv run python -m fin_checkup.cli checkup 005930      # Korean company
uv run python -m fin_checkup.cli us AAPL             # US company (needs SEC_USER_AGENT)
uv run python -m fin_checkup.cli --help              # watch · poll · serve (FastAPI) · site build …
```

**Static JSON** — the same values as each company page, as static files: no key, no rate limit. Fields are documented in [`docs/api-static.md`](docs/api-static.md) (Korean); a worked example is in [`examples/`](examples/README.md).

```bash
curl -s https://younghwan91.github.io/fin-checkup/api/c/005930.json | jq '.metrics[] | {key, value, signal: .signal.key}'
```

**Badge** — signal counts for a blog or README. Always wrap it in a link to the company page, where the formulas, thresholds and source are ([`docs/embed.md`](docs/embed.md)).

```markdown
[![fin-checkup 삼성전자 2025 재무 신호등](https://younghwan91.github.io/fin-checkup/badge/005930.svg)](https://younghwan91.github.io/fin-checkup/c/005930/)
```

**Contributing** — tests run without `.env` or any API key (`uv run pytest -q`). See [`CONTRIBUTING.md`](CONTRIBUTING.md) (English summary at the bottom).

| Key | Where | For |
|---|---|---|
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) — email only, instant | Korean companies |
| `SEC_USER_AGENT` | Write your own (`fin-checkup you@example.com`) | US companies |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) — free | Telegram bot (unverified) |

> ⚠️ Under Article 19 of the DART terms, an API key must not be handed to third parties. `.env` is never committed.

## License

**MIT** — see [LICENSE](./LICENSE). The data isn't ours to begin with: DART is public data that may be used commercially and redistributed, and SEC EDGAR is in the public domain. Don't ship your own DART key with a fork; conditions that apply apart from the license are in [docs/REGULATORY.md](./docs/REGULATORY.md) (Korean).

## Disclaimer

This is a software tool and does not provide investment solicitation or advice. What it shows are objective measurements computed from filed figures with public financial-ratio formulas. The operator is not responsible for data accuracy or for any gains or losses from its use.

## Author

**Younghwan Chae (채영환)** · [GitHub @younghwan91](https://github.com/younghwan91) · [LinkedIn](https://www.linkedin.com/in/younghwan-chae/)

## Issues

- 🐛 Bugs, questions, feature ideas: [GitHub Issues](https://github.com/younghwan91/fin-checkup/issues)
