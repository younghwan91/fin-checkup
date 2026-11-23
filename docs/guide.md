# fin-checkup 상세 안내

처음이라면 [README](../README.md) 부터.

<!-- toc -->
- [자주 묻는 것](#자주-묻는-것)
- [개발자라면](#개발자라면)
- [데이터 라이선스 및 면책](#데이터-라이선스-및-면책)
<!-- /toc -->

## 자주 묻는 것

<details>
<summary>사라·팔라고 알려 주나요?</summary>

아닙니다. 공시 숫자를 공개된 공식으로 계산한 **측정값**만 보여 줍니다. 무엇을 사고팔지는 말하지 않고, 지표 순위표도 만들지 않습니다. 지키는 선은 [docs/REGULATORY.md](REGULATORY.md) 에 있습니다.
</details>

<details>
<summary>데이터는 어디서 오고 언제 바뀌나요?</summary>

금융감독원 전자공시(DART) 사업보고서입니다. 코스피 829 · 코스닥 1,798 · 코넥스 15 — 2,642곳의 2025년 값과 2021년부터의 추이가 있습니다. 사이트는 매일 07:00(KST)에 다시 만들어지며, 위험 공시는 매일, 재무제표는 새 사업보고서가 나오면 반영됩니다. 브라우저와 AI 에이전트는 이 정적 사이트만 읽습니다.
</details>

<details>
<summary>기준값(🟢/🟡/🔴 경계)은 누가 정했나요?</summary>

교과서 기준을 그대로 옮겨 오지 않고 실제 상장사 분포(분위수)에 맞췄습니다. 근거는 [`metrics/engine.py`](../src/fin_checkup/metrics/engine.py) 주석에 있고, 모든 지표 옆에 기준이 적혀 있습니다. 같은 업종 비교는 업종코드(KSIC 앞 두 자리) 기준이며, 업종 표본이 5곳 미만이면 전체 상장사와 견줍니다.
</details>

<details>
<summary>숫자가 이상해요.</summary>

회사 코드·지표·DART 원문 링크와 함께 [이슈 "데이터가 이상해요"](https://github.com/younghwan91/fin-checkup/issues/new/choose) 로 알려 주세요. 실제로 이자보상배율·FCF·매출 증가율 같은 계산 오류를 사용자 관점 점검으로 여럿 고쳤습니다.
</details>

## 개발자라면

**CLI** — 국내는 DART, 미국 상장사는 SEC EDGAR 를 씁니다(미국 기업은 CLI 에서만 지원합니다).

```bash
uv sync --all-extras
uv run python -m fin_checkup.cli setup          # DART 키 넣기 — 화면·로그에 남기지 않음 (사이트·에이전트는 키 불필요)
uv run python -m fin_checkup.cli sync           # 종목 목록 (최초 1회)
uv run python -m fin_checkup.cli checkup 005930 # 국내 건강검진
uv run python -m fin_checkup.cli us AAPL        # 미국 (SEC_USER_AGENT 필요)
uv run python -m fin_checkup.cli --help         # watch · poll · serve · site build …
```

**정적 JSON** — 회사 페이지와 같은 값이며 키·호출 제한이 없습니다. 필드는 [`docs/api-static.md`](api-static.md), 명세는 [`openapi.json`](https://younghwan91.github.io/fin-checkup/openapi.json), 예제는 [`examples/`](../examples/README.md) 에서 볼 수 있습니다.

```bash
curl -s https://younghwan91.github.io/fin-checkup/api/c/005930.json | jq '.metrics[] | {label, value_text, signal: .signal.emoji}'
```

**배지** — 블로그·README 용 신호 개수 배지입니다. 근거가 있는 회사 페이지로 꼭 감싸 주세요([`docs/embed.md`](embed.md)). 카드뉴스 생성은 [`docs/card-news.md`](card-news.md) 에 있습니다.

```markdown
[![fin-checkup 삼성전자 2025 재무 신호등](https://younghwan91.github.io/fin-checkup/badge/005930.svg)](https://younghwan91.github.io/fin-checkup/c/005930/)
```

**기여** — 테스트는 `.env` 없이 돕니다(`uv run pytest -q`). 해설·용어집·업종 설명을 고치는 법은 [`CONTRIBUTING.md`](../CONTRIBUTING.md) 에 있습니다.

<details>
<summary>키와 알림 채널</summary>

| 키 | 어디서 | 무엇에 |
|---|---|---|
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) — 이메일만, 즉시 | CLI 로 국내 기업 직접 수집 |
| `SEC_USER_AGENT` | 직접 작성 (`fin-checkup you@example.com`) | 미국 기업 수집 |
| `TELEGRAM_BOT_TOKEN` | [@BotFather](https://t.me/BotFather) — 무료 | 텔레그램 봇 (실제 발송은 아직 검증되지 않았으며, 현재 동작하는 알림은 RSS/Atom 피드입니다) |

DART 약관 제19조에 따라 인증키를 다른 사람에게 넘기면 안 됩니다. `.env` 는 커밋되지 않습니다.
운영 문서: [OPERATIONS](OPERATIONS.md) · [REGULATORY](REGULATORY.md) · [TERMS](TERMS.md) · [PRIVACY](PRIVACY.md)
</details>

## 데이터 라이선스 및 면책

조회하는 데이터는 fin-checkup 의 소유가 아닙니다. DART(OpenDART) 약관에는 영리 이용·재배포를 금지하는 조항이 없고 공공데이터법을 따릅니다(명시적 허용 문구는 없습니다). SEC EDGAR 는 퍼블릭 도메인입니다. 다만 DART 인증키는 개인에게 발급되므로 포크하거나 배포할 때 본인 키를 포함하지 마세요. 라이선스 외에 지켜야 할 조건은 [docs/REGULATORY.md](REGULATORY.md) 에 정리되어 있습니다.

본 서비스는 소프트웨어 도구이며 투자권유·투자자문을 제공하지 않습니다. 본 결과는 공시 수치를 공개된 재무비율 공식으로 계산한 측정값입니다. 투자권유·자문이 아니며, 데이터 정확성과 이용에 따른 손익에 책임지지 않습니다.
