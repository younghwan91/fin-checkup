<picture><source media="(prefers-color-scheme: dark)" srcset="docs/images/banner-en-dark.png"><img src="docs/images/banner-en.png" width="100%" alt="fin-checkup — Korean listed companies' financial statements as a traffic light, with the formula and thresholds behind every color. Signals: Normal ● Caution ▲ Risk ■ Measured ▬ N/A ⊘ No data ◌"></picture>

# fin-checkup

**English** | [한국어](README.md)

[![CI](https://github.com/younghwan91/fin-checkup/actions/workflows/ci.yml/badge.svg)](https://github.com/younghwan91/fin-checkup/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/) [![LinkedIn](https://img.shields.io/badge/LinkedIn-younghwan--chae-0A66C2?logo=linkedin&logoColor=white)](https://www.linkedin.com/in/younghwan-chae/)

**A free tool that reads listed companies' financial statements as traffic lights.** Enter a company name to see 17 profitability, stability, and growth ratios as 🟢🟡🔴, each paired with its formula, thresholds, prior-year value, and industry peer comparison. No install, no sign-up, and no API key required.

### ▶ Open the site: [younghwan91.github.io/fin-checkup](https://younghwan91.github.io/fin-checkup/)

Example: **[Samsung Electronics (005930)](https://younghwan91.github.io/fin-checkup/c/005930/)** · [KB Financial Group (105560)](https://younghwan91.github.io/fin-checkup/c/105560/) · [First time? How to read](https://younghwan91.github.io/fin-checkup/learn/)

[![Site demo — search the initial consonants ㅅㅅㅈㅈ to find Samsung Electronics, scan all 17 signals at a glance, then open the operating-margin card's formula, thresholds, industry distribution and the metric explainer](docs/images/demo.gif)](https://younghwan91.github.io/fin-checkup/c/005930/)

> Measurements computed from filed figures. Not investment advice, and no stock recommendations.
>
> **Note:** The site is in Korean only—numbers, colors, and formulas read fine as-is, and browser translation handles explanations. US companies are CLI-only via SEC EDGAR (see [For developers](#for-developers)).

## Three ways to use it

| If you want to… | Do this |
|---|---|
| **Browse in your browser** | Search by company name or Korean initial consonants (`ㅅㅅㅈㅈ`) on the [site](https://younghwan91.github.io/fin-checkup/) |
| **Ask your AI** | Paste *"Read https://younghwan91.github.io/fin-checkup/llms.txt and follow it to diagnose Samsung Electronics' (005930) financials"* into ChatGPT or Claude → [Details](#ask-your-ai-agent) |
| **Work with the data (Developers)** | CLI · Static JSON · Badges → [For developers](#for-developers) |

## How to read the traffic lights

| Signal | Meaning |
|---|---|
| 🟢 Normal · 🟡 Caution · 🔴 Risk | Compared against thresholds. Every ratio displays its exact thresholds |
| ⚪ Measured | Wide sector variation, so no threshold is applied—shows the value and industry median only |
| ⊘ N/A | Ratio does not fit the sector (e.g., debt ratio for banks, where KB Financial's `1,211.73%` is normal because deposits are liabilities); shows what to check instead |
| ⚫ No data | Account not found in the filing. Never filled with `0` |

**For example** — Samsung Electronics FY2025 operating margin **13.07% 🟢**: threshold *7%+ normal* · prior year 10.88% · top 10% of 294 industry peers · industry median 2.00%.
There is intentionally no composite score—a score that hides its rationale invites misunderstanding.

## Ask your AI agent

Ask your agent *"diagnose Samsung Electronics' financials"* and it returns a table of ratios, formulas, and thresholds from this data—no API key or sign-up needed. If you ask *"is it a buy?"*, the agent declines to judge and shows the measured figures instead.

**1) No install — ChatGPT · Claude.ai · Perplexity**

```text
Read https://younghwan91.github.io/fin-checkup/llms.txt and follow it to diagnose Samsung Electronics' (005930) financials
```

**2) Claude Code** — run these two commands, then ask *"diagnose 005930"* in a new session.

```bash
claude plugin marketplace add younghwan91/fin-checkup
claude plugin install fin-checkup@fin-checkup
```

**3) Cursor · VS Code · Claude Desktop · Codex (MCP server)** — [![Add to Cursor](https://img.shields.io/badge/Add_to-Cursor-000000?logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=fin-checkup&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyItLWZyb20iLCJmaW4tY2hlY2t1cFttY3BdIEAgZ2l0K2h0dHBzOi8vZ2l0aHViLmNvbS95b3VuZ2h3YW45MS9maW4tY2hlY2t1cCIsImZpbi1jaGVja3VwLW1jcCJdfQ%3D%3D) [![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect?url=vscode%3Amcp%2Finstall%3F%7B%22name%22%3A%22fin-checkup%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22--from%22%2C%22fin-checkup%5Bmcp%5D%20%40%20git%2Bhttps%3A%2F%2Fgithub.com%2Fyounghwan91%2Ffin-checkup%22%2C%22fin-checkup-mcp%22%5D%7D)

All MCP clients run the server with the command below (for Claude Code MCP-only without the plugin, prefix with `claude mcp add fin-checkup --`):

```bash
uvx --from "fin-checkup[mcp] @ git+https://github.com/younghwan91/fin-checkup" fin-checkup-mcp
```

Requires [uv](https://docs.astral.sh/uv/getting-started/installation/) and git. Per-client setup is in [`docs/agents.md`](docs/agents.md) (Korean), and using the same diagnose/compare skills in Codex or Cursor is in [`docs/agents-skill.md`](docs/agents-skill.md) (Korean). If installation feels complicated, just tell your agent *"set up fin-checkup"* — it walks you through setup and, if you want, obtaining your own DART API key.

<details>
<summary>Sample agent output (Ecopro 086520, FY2025 excerpt)</summary>

```text
에코프로 (086520, KOSDAQ): 2025 사업연도 사업보고서(연결) 기준. 신호 🟢 2 · 🟡 6 · 🔴 5 · ⚪ 3 · ⊘ 1 · ⚫ 0

| 지표       | 값      | 신호 | 계산식 / 경계값                                  | 업종 중앙값 · 작년        |
| 부채비율   | 117.92% | 🟡   | 부채총계 ÷ 자기자본 × 100 / 정상 100%↓ · 주의 200%↓ | 61.58% · 작년 112.02%     |
| 이자보상배율 | 1.58배 | 🟡   | 영업이익 ÷ 이자비용 / 정상 3배↑ · 주의 1배↑        | 4.24배 · 작년 -2.50배     |
| 현금흐름/순이익 | —  | ⊘    | 적자 구간에서는 이 비율이 뜻을 잃어 계산하지 않음 — 영업활동현금흐름을 대신 보세요 |

본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며 …
출처: https://younghwan91.github.io/fin-checkup/c/086520/ · DART
```
</details>

## On the site

| Section | What you can do |
|---|---|
| [Company page](https://younghwan91.github.io/fin-checkup/c/005930/) | Check 17 ratio traffic lights, red flags (capital impairment, consecutive operating losses, etc.), and 5-year trends |
| [Ratios](https://younghwan91.github.io/fin-checkup/m/) · [First read](https://younghwan91.github.io/fin-checkup/learn/first-read/) | Plain-language explanations of terms like operating margin and debt ratio |
| [Industries](https://younghwan91.github.io/fin-checkup/s/) · [Market](https://younghwan91.github.io/fin-checkup/market/) | Distributions by sector or whole market, plus industry company lists sorted by name |
| [Compare](https://younghwan91.github.io/fin-checkup/compare/) | Compare 2–4 companies side by side in one table |
| [Watchlist](https://younghwan91.github.io/fin-checkup/watch/) | Track signal changes for saved companies (stored only in your browser, no account) |
| Risk-disclosure feeds | Subscribe to filings like rights offerings, CBs, audit opinions, and delistings in an RSS reader or Slack/Discord — [Whole market](https://younghwan91.github.io/fin-checkup/feeds/disclosures.xml) · Per company `feeds/c/<code>.xml` |

## FAQ

<details>
<summary>Does it tell me what to buy or sell?</summary>

No. It only shows **measurements** computed from filed figures using public formulas. It never recommends buying or selling, and it never publishes stock rankings. See [docs/REGULATORY.md](docs/REGULATORY.md) (Korean).
</details>

<details>
<summary>Where does the data come from and when does it update?</summary>

Annual reports from Korea's Financial Supervisory Service disclosure system (DART). It covers **2,642 companies**—KOSPI 829 · KOSDAQ 1,798 · KONEX 15—with FY2025 figures and trends since 2021. The site rebuilds daily at 07:00 KST; risk disclosures refresh daily, and financial statements update when new annual reports are filed. Browsers and AI agents read only this static site.
</details>

<details>
<summary>Who sets the thresholds (🟢/🟡/🔴)?</summary>

Rather than borrowing textbook rules of thumb, thresholds are calibrated to actual listed-company quantiles. The rationale is documented in comments in [`metrics/engine.py`](src/fin_checkup/metrics/engine.py), and every ratio shows its thresholds alongside the number. Industry comparisons use two-digit KSIC codes; if a sector has fewer than 5 peers, the company is compared against the whole market.
</details>

<details>
<summary>A number looks wrong.</summary>

Please open a ["data issue"](https://github.com/younghwan91/fin-checkup/issues/new/choose) with the company code, metric name, and a link to the DART filing. User-reported checks have already helped fix several edge cases in interest coverage, FCF, and revenue growth.
</details>

## For developers

**CLI** — Korean companies via DART, US companies via SEC EDGAR (**US companies are CLI-only**; CLI output is in Korean).

```bash
uv sync --all-extras
uv run python -m fin_checkup.cli setup          # Save your DART key securely — never logged (site & agents need no key)
uv run python -m fin_checkup.cli sync           # Company list (once)
uv run python -m fin_checkup.cli checkup 005930 # Korean company checkup
uv run python -m fin_checkup.cli us AAPL        # US company (needs SEC_USER_AGENT)
uv run python -m fin_checkup.cli --help         # watch · poll · serve · site build …
```

**Static JSON** — identical values to each company page, with no API key or rate limit. Schema details are in [`docs/api-static.md`](docs/api-static.md) (Korean), OpenAPI spec at [`openapi.json`](https://younghwan91.github.io/fin-checkup/openapi.json), and examples in [`examples/`](examples/README.md).

```bash
curl -s https://younghwan91.github.io/fin-checkup/api/c/005930.json | jq '.metrics[] | {label, value_text, signal: .signal.emoji}'
```

**Badge** — signal-count badge for blogs or READMEs. Always wrap it in a link to the company page where formulas, thresholds, and sources live ([`docs/embed.md`](docs/embed.md)). Card-news generation is documented in [`docs/card-news.md`](docs/card-news.md).

```markdown
[![fin-checkup 삼성전자 2025 재무 신호등](https://younghwan91.github.io/fin-checkup/badge/005930.svg)](https://younghwan91.github.io/fin-checkup/c/005930/)
```

**Contributing** — tests run without `.env` or any API key (`uv run pytest -q`). See [`CONTRIBUTING.md`](CONTRIBUTING.md) for editing explainers, glossary entries, and sector notes.

<details>
<summary>API keys and notification channels</summary>

| Key | Where | For |
|---|---|---|
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) — email only, instant | Fetching Korean companies via CLI |
| `SEC_USER_AGENT` | Write your own (`fin-checkup you@example.com`) | Fetching US companies via CLI |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) — free | Telegram bot (live delivery is unverified; RSS/Atom feeds are the active notification channel) |

Under Article 19 of the DART terms, API keys must not be shared with third parties. `.env` is never committed.
Operations & policy docs: [OPERATIONS](docs/OPERATIONS.md) · [REGULATORY](docs/REGULATORY.md) · [TERMS](docs/TERMS.md) · [PRIVACY](docs/PRIVACY.md)
</details>

## License

**MIT** — see [LICENSE](./LICENSE). You are free to use, modify, and distribute this software, including commercially.

The underlying data does not belong to fin-checkup: DART is public data that may be used commercially and redistributed, and SEC EDGAR is in the public domain. Because DART API keys are issued per individual, never include your own key in a fork or deployment. Additional domain conditions outside the MIT license are in [docs/REGULATORY.md](docs/REGULATORY.md) (Korean).

## Disclaimer

This service is a software tool and does not provide investment solicitation or advice. Displayed results are measurements computed from filed figures using public financial-ratio formulas. It is not investment advice, and the operator is not responsible for data accuracy or any gains or losses from its use.

## Author

**Younghwan Chae (채영환)** · [GitHub @younghwan91](https://github.com/younghwan91) · [LinkedIn](https://www.linkedin.com/in/younghwan-chae/)

View the full open-source quant stack on my [GitHub profile](https://github.com/younghwan91).

## Issues

- 🐛 Bug reports, questions, feature ideas: [GitHub Issues](https://github.com/younghwan91/fin-checkup/issues)
- 📈 Updates: [Follow @younghwan91](https://github.com/younghwan91)
