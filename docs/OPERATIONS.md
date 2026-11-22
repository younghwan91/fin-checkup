# 운영 가이드

서비스를 실제로 띄우고 유지할 때 알아야 하는 것들. 설계 결정의 *이유*를 함께 적었다 —
왜 그런지 모르면 나중에 잘못된 방향으로 되돌리게 된다.

---

## 1. 프로세스 구조 — 왜 서버 하나가 전부를 소유하는가

```
┌─────────────────────────────────────────┐
│  fin-checkup serve  (단일 프로세스)      │
│                                         │
│   FastAPI  ──┐                          │
│              ├──▶ Cache (DuckDB)  ◀── 단일 writer
│   AlertScheduler (백그라운드 task)       │
└─────────────────────────────────────────┘
```

**DuckDB는 한 번에 한 프로세스만 쓸 수 있다.** 워커를 따로 띄우면 반드시 락에서
부딪힌다(실제로 개발 중 겪었고, 그래서 `CacheLocked`로 명확히 알리도록 고쳤다).

그래서 `serve`가 API와 알림 워커를 한 프로세스에서 돌린다. 규모가 커져 둘을 분리해야
할 때가 오면 **그때가 Postgres로 옮길 시점**이다. `storage/db.py` 한 파일만 갈아끼우면
되도록 나머지 코드는 저장소를 모른다.

```bash
uv run python -m fin_checkup.cli serve --port 8000
```

**읽기 전용으로 우회할 수는 없다.** `Cache(path, read_only=True)`는 *쓰는 프로세스가
아예 없을 때* 여러 곳이 함께 읽는 용도다. writer가 락을 잡고 있으면 read-only 연결도
거부된다(직접 확인함). 그래서 Streamlit은 락이 잡혀 있으면 우회하지 않고 무엇을 해야
하는지 알린다.

서버와 화면을 함께 쓰려면 Streamlit 대신 `/docs`의 API를 이용한다.

**한 프로세스 안에서는 스레드가 나눠 써도 된다.** FastAPI 는 동기 엔드포인트를 스레드풀에서
돌리고 워커는 이벤트 루프에서 같은 `Cache` 를 쓴다. DuckDB 연결 하나를 그대로 나눠 쓰면
`fetchone()` 이 다른 스레드의 결과를 집어오는데(실측 요청 네 건 중 한 건), `Cache` 가
스레드마다 `cursor()` 를 따로 주고 메서드를 락으로 직렬화해 막는다. `cache.conn` 을 직접
만질 일이 있으면 그 속성이 이미 스레드별 연결이라는 것만 기억하면 된다.

## 2. DART 호출 예산

약관 제10조에 일별 호출 허용량이 있다. 우리가 스스로 지켜야 한다.

```bash
uv run python -m fin_checkup.cli budget
```

**호출을 아끼는 세 가지 장치:**

1. **캐시** — 재무데이터는 분기에 한 번 갱신된다. 같은 조합을 두 번 받지 않는다.
2. **빈 응답 기억** — 조회했는데 데이터가 없던 조합은 `fetch_misses`에 남겨 30일간
   다시 묻지 않는다. 사업보고서 시즌(3/20~4/30)에는 3일 — 3월 말에 없던 회사가 4월 내내 ⚫ 로 남지 않게
   (`fiscal.miss_ttl_days`, 설정 `MISS_TTL_DAYS`·`MISS_TTL_SEASON_DAYS`).
3. **선별적 재시도** — 인증키 오류처럼 다시 해도 같은 실패는 재시도하지 않는다.
   재시도가 예산을 태우는 가장 흔한 경로다.

전 상장사 재무는 증분으로 받는다 — 상장사 중 재무가 없는 (회사, 연도)만, 정한 호출 수 안에서
(`src/fin_checkup/collect.py`). 예산·`020`·`--max-minutes` 에 닿으면 받은 데까지 남기고 0 으로 끝나고,
다음 실행이 이어받는다. 평소엔 GitHub Actions 가 매일 돌린다(§7 "자동 수집").

```bash
uv run python -m fin_checkup.cli collect --budget 5000                    # 기본 연도: 새 보고서가 들어올 해 + 그 전 해
uv run python -m fin_checkup.cli collect --budget 12000 --years 2021 2022 # 지난 연도 채우기
```

사이트의 "최근 위험 공시" 재료는 따로 받는다. 공시유형 5개 × 페이지 수만큼 호출하고(7일치 수십 건 — 지분공시가 가장 많다),
시작 전에 최대치(5 × `--max-pages`)가 오늘 남은 허용량 안인지 보고 넘으면 시작하지 않는다.
`020` 을 받으면 지난 파일을 그대로 두고 멈춘다 — 빈 목록으로 덮으면 "위험 공시 없음" 으로 읽힌다.

```bash
uv run python -m fin_checkup.cli disclosures fetch --days 7 --out site-data/disclosures.json
```

## 3. 알림 워커가 공시를 놓치지 않는 방법

스케줄러는 "최근 N일"이 아니라 **"마지막으로 성공한 시점부터"**를 조회한다.

- 성공했을 때만 `meta.alerts.last_poll`을 갱신한다
- 실패하면 시각을 그대로 둬서 다음 실행이 그 구간을 다시 본다
- 겹쳐서 조회해도 `notified` 테이블이 중복 발송을 막는다
- 날짜는 전부 한국 시각(`clock.kst_today`)이다. UTC 서버에서 `date.today()`를 쓰면
  KST 00~09시 사이 당일 공시가 조회 범위에서 빠진다 — 장 개시 전 공시가 딱 그 시간대다
- `poll --dry-run`과 토큰 없는 콘솔 출력은 `notified`에 기록하지 않는다. 미리보기가
  기록을 남기면 나중에 토큰을 넣어도 그 공시는 영영 안 나간다
- `serve`는 `TELEGRAM_BOT_TOKEN`이 없으면 워커를 띄우지 않는다. 서버 안에서 콘솔로 찍는
  알림은 아무에게도 가지 않는다

프로세스가 며칠 죽어 있었어도 복구 후 그 구간을 메운다(최대 30일).

**의도적 예외:** 처음 켤 때는 하루치만 본다. 한 달치 공시를 한꺼번에 보내면 그건
알림이 아니라 스팸이다.

## 4. 업종별 판정

일반 기업 기준을 모든 업종에 들이대면 거짓말이 된다. 실제로 KB금융이 🔴 4개로
나왔었다 — 부채비율 1,167%, 자기자본비율 7.89%, 이자보상배율 0.46배. 셋 다 은행에서는
정상인데도.

지금은 업종코드(KSIC)로 갈라 적용할 수 없는 지표를 `⊘`로 표시하고 **무엇으로 봐야
하는지**를 함께 적는다(부채비율 → BIS 자기자본비율).

| 업종 | KSIC | 처리 |
|---|---|---|
| 은행·증권·금융지주 | 641(은행·저축은행) · 6612(증권·선물 중개) · 661 중 이름이 '증권'으로 끝나는 곳 · 64992 중 금융지주 | 금융업 처리 전부 + 영업이익률·순이익률·매출액 증가율 미적용 |
| 금융업 | 그 밖의 64~66 (보험·카드·캐피탈·신탁·투자) | 유동비율·이자보상배율·회전율·부채비율·자기자본비율 미적용, ROA는 별도 밴드, 좀비(이자보상배율 연속) 판정 안 함 |
| 부동산업 | 68 | 재고자산회전율 미적용 |
| 그 외 | — | 일반 기준 |

### 매출이 없는 금융회사 — 은행·증권·금융지주의 이익률·매출 증가율은 ⊘

은행·은행지주의 손익계산서에는 매출 줄이 없다(이자수익·수수료수익에서 비용을 빼는 순서로 적는다). 그래서
이 셋이 ⚫ "데이터 없음" 으로 나갔는데, 없는 게 아니라 개념이 없는 것이다. 증권사·증권지주는 '영업수익'
줄이 있지만 금융상품 평가·처분이익과 외환거래이익을 같은 종류의 손실과 따로 **총액**으로 담는다 —
미래에셋증권 2024 영업수익 22.2조 중 63%, 2023~2025 증권사 45 회사-연도에서 표준 태그로만 세도 중앙값 49%.
이 값으로 나눈 영업이익률(2~6%)은 수익성이 아니라 거래 규모를 따라 움직인다. 대신 볼 것: ROE·ROA, 사업보고서의
순이자마진(NIM)·영업이익경비율(CIR).

보험사(65)는 그대로 둔다. 매출로 잡는 보험수익(IFRS 17)은 보험 서비스의 대가라 총액 문제가 없다(같은 이익 ÷
보험수익 중앙값 12%, 30 회사-연도). 카드·캐피탈의 영업수익도 가맹점 수수료·대출 이자라 그대로 둔다.

| 2023~2025 상장 34곳 · 100 회사-연도 | 바꾸기 전 | 바꾼 뒤 |
|---|---|---|
| 영업이익률 | ⚫ 47 · 🟡 41 · 🟢 8 · 🔴 4 | ⊘ 100 |
| 순이익률 | ⚫ 47 · 🟡 45 · 🟢 4 · 🔴 4 | ⊘ 100 |
| 매출액 증가율 | ⚫ 47 · 🟢 22 · 🔴 20 · 🟡 11 | ⊘ 100 |

⊘ 값은 대조군(`metric_values`)에도 넣지 않는다(`service._comparable`) — 같은 묶음 64·66 의 카드사·PG사나 시장
전체 분포에 은행 부채비율·증권사 이익률이 섞이지 않게. 사이트 분포(`site.groups`)는 처음부터 ⊘ 를 뺐고, 이제 회사
페이지 대조군도 같다. `metric_values` 는 `cli backfill` 이 회사-연도마다 통째로 다시 쓰므로 마이그레이션은 없다.

### 다른 업종은 왜 그냥 두는가

13개 업종군을 실측했다(`scripts/check_sectors.py`, 2024년 911사). 금융업은 🔴 7.7%로
가장 낮아 처리가 잘 듣는 걸 확인했다. 반면 아래는 시장 평균보다 🔴가 몰렸는데,
**일부러 손대지 않았다.**

| 업종군 | 몰린 지표 | 판단 |
|---|---|---|
| 운수·창고 | 부채비율 42%(시장 12%) | 항공기·선박 리스가 IFRS16으로 부채에 잡힌다. **다만 실제 위험이기도 하다** — 코로나 때 항공사 부채비율은 진짜 문제였다 |
| 조선 | 영업활동현금흐름 70%(28%) | 헤비테일 대금 구조상 건조 중엔 현금이 나간다. 그래도 현금 유출은 유출이다 |
| 전문·과학기술 | 수익성 전반 57~69% | 특정 지표가 아니라 **수익성 전반**에 몰렸다. 연구개발·바이오 기업이 많아 실제로 적자가 많다는 뜻이다 |

금융업과 결정적으로 다른 점은 **"구조적 특성"과 "실제 위험"이 섞여 있다**는 것이다.
은행의 유동비율은 계산 자체가 무의미하지만, 항공사의 높은 부채비율은 뜻이 있는 숫자다.
여기서 ⊘로 빼면 **진짜 위험을 숨기게 된다.**

그래서 정답은 절대 기준을 끄는 게 아니라 **업종 중앙값을 함께 보여주는 것**이고,
그건 이미 `metrics/peers.py`가 한다(같은 업종 5개사 이상 캐시에 있을 때).

**새 업종을 ⊘로 뺄 때의 기준**: 그 지표가 그 업종에서 *계산은 되지만 뜻이 없는가?*
단지 값이 나쁜 쪽으로 치우친 것이라면 그건 사실 전달이지 오판정이 아니다.

### 이자보상배율의 분모 — 대체값은 판정하지 않는다

손익계산서에 이자비용을 따로 적는 상장사는 2024년 2,759곳 중 117곳뿐이다. 나머지는
금융원가 한 줄인데 외환·파생 손실이 섞인다(삼성전자 금융비용 12.99조 vs 이자 지급 0.68조 →
2.52배 🟡 로 찍혔다). 그래서 분모는 이 순서로 찾고 출처를 `Financials.interest_expense_source`
에 남긴다(`dart/normalize.py`).

| 출처 | 2024 | 처리 |
|---|---|---|
| 손익계산서 이자비용 | 117 | 그대로 판정 |
| 현금흐름표 이자의 지급(영업·투자·재무 합) | 2,412 | 판정 + "이자 지급액 기준" 비고. 0 이면 다음으로 |
| 금융원가·금융비용 | 214 | 값만 보여 주고 ⚪ — 판정·대조군·전년 비교·좀비 연속에서 뺀다 |

이 규칙을 넣으면서 옛 `metric_values` 의 이자보상배율을 지운다(캐시를 쓰기 모드로 열 때 한 번,
`migrations.metric_values_interest_source`). **`cli backfill` 을 다시 돌려야** 대조군이 채워진다.

미국 기업(`sec/normalize.py`)도 같은 순서다. 2024 택소노미부터 `InterestExpense` 대신
`InterestExpenseNonoperating` 을 쓰는 회사가 늘어서(frames CY2023 158곳 → CY2024 456곳) 총액 태그에 넣었다.

| 출처 (SEC 태그) | CY2024 frames | 처리 |
|---|---|---|
| `InterestExpense` → `InterestExpenseNonoperating` → `InterestExpenseDebt` | 1,869 | 그대로 판정 |
| `InterestPaidNet` (이자 지급액, 자본화 이자 제외) | 1,841 | 판정 + "이자 지급액 기준" 비고. 0 이면 다음으로 |
| `InterestIncomeExpenseNet` 음수 (순이자비용) | 41 | 값만 보여 주고 ⚪. 0·양수(순이자수익)는 쓰지 않는다 |

순이자비용은 이자수익만큼 분모가 작아 배율이 **높게만** 틀린다 — 총액과 둘 다 낸 135곳(CY2023)에서
순액이 총액의 80% 미만인 곳이 24%(p10 13%)였다. 이걸로 🟢 를 매기면 이자 부담을 감춘다.
SEC 값은 대조군(`metric_values`)에 들어가지 않아 마이그레이션은 없다.

### 매출채권회전율의 분모 — 순수 매출채권을 먼저

합계 태그 `ifrs-full_TradeAndOtherCurrentReceivables` 를 먼저 잡던 때는 매출채권 줄이 따로 있어도
합계가 분모였고, 그 태그를 미수금 묶음('기타수취채권'·'기타유동채권')에 붙인 회사는 미수금으로
나눴다(2024 상장사 219곳 값이 바뀌었다 — 바뀐 곳의 새 값/옛 값 중앙값 0.34). 이제 순서는
(`dart/normalize.py::_trade_receivables`, 출처는 `Financials.trade_receivables_source`):

| 출처 | 2024 | 처리 |
|---|---|---|
| 매출채권 단독 (`dart_ShortTermTradeReceivable`·`ifrs-full_CurrentTradeReceivables`·이름 '매출채권') | 1,116 | 그대로 |
| 매출채권및기타채권 합계 (이름에 '매출채권'이 든 줄만) | 1,298 | 그대로 + "합계 기준" 비고 |
| 미수금·기타금융자산 줄뿐 | — | ⚫ (매출채권이 아니다) |

합계만 있을 때 ⚪ 대체값으로 빼지 않은 근거: 둘 다 공시한 99곳에서 매출채권 ÷ 합계가 중앙값 0.957
(p25 0.85, p10 0.47)이라, 회사 사이 회전율 차이(2024 사분위 4.6~9.4회)보다 훨씬 작다. 상장사 절반을
대조군에서 빼면 분포가 더 틀린다. 해마다 출처가 바뀌는 회사는 67곳(2.9%)이라 전년 비교도 그대로 둔다.
옛 `metric_values` 의 매출채권회전율은 다시 열 때 지운다(`migrations.metric_values_receivable_source`)
— **`cli backfill`** 로 다시 채운다. SEC 는 `AccountsReceivableNetCurrent` = 단독,
`ReceivablesNetCurrent` = 합계로 같은 비고를 단다.

### 잉여현금흐름의 설비투자 — 유형자산 취득 합계, 없으면 종류별 합

공식은 '영업활동현금흐름 − 유형자산 취득' 이다. 출처는 `Financials.capex_source`
(`dart/normalize.py::_capex`):

| 출처 | 2024 | 처리 |
|---|---|---|
| 유형자산 취득 합계 줄 (`…PurchaseOfPropertyPlantAndEquipment…` 태그 · '유형자산의 취득') | 2,228 | 그대로 |
| 합계 줄 없이 종류별(토지·건물·구축물·기계장치·차량·건설중인자산·기타유형자산·비품…)만 | 224 | 종류별 줄을 더한다 + 비고 |
| 유·무형자산 취득 한 줄 ('유ㆍ무형자산의 취득') | 3 | 그대로 판정 + 비고 |

- **종류별 합**: 옛 규칙은 이 중 '기타유형자산의 취득' 한 줄만 설비투자로 썼다(62곳 값이 커졌다 — 국도화학
  13.9억 → 937억, FCF 🟢 → 🔴). 나머지 158곳은 FCF 가 ⚫ 였다.
- **유·무형 합계를 판정하는 근거**: 두 줄을 따로 적은 1,816곳에 무형자산 취득을 더해 보면 FCF 부호가 바뀌는
  곳은 36곳(2.0%), 무형자산 비중 중앙값 5.7%. 방향도 한쪽(FCF 가 낮게)이라 ⚪ 로 빼지 않고 비고만 단다.
  옛 목록의 '유형자산및무형자산의취득' 이름은 2023·2024 전 회사에서 0곳이었다 — 실제로는 합계 태그를 단
  '유ㆍ무형자산의 취득'·'유무형자산의 취득' 이라 이름으로 가른다.
- 합계 줄과 종류별 줄(대개 건설중인자산)을 함께 적은 29곳은 합계만 쓴다. 종류별 줄이 내역인지 별도 항목인지
  공시만으로 가를 수 없다(3곳은 합이 합계와 같았다) — 남은 한계.
- 사용권자산 취득 한 줄은 리스 계약이라 빼지만 '유형자산 및 사용권자산의 취득' 합계 줄은 설비투자다.
- SEC: `PaymentsToAcquireProductiveAssets` 는 무형자산이 섞인 태그라 유·무형 합계 비고를 단다.
- 옛 `metric_values` 의 FCF 는 다시 열 때 지운다(`migrations.metric_values_capex_source`) — **`cli backfill`**.

## 5. 계정과 키

- 비밀번호를 받지 않는다 — 저장·재설정·유출 대응을 떠안지 않기 위해서
- API 키는 **해시만 저장**하고 발급 순간에 한 번만 보여준다
- 이메일 소유를 검증하지 않으므로 **같은 이메일로 두 번 만들 수 없다**(409). 그걸 허용하면
  누구나 남의 이메일로 그 사람의 `user_id`에 묶인 키를 받는다. 추가 키(기기별)는 기존 키로
  인증한 `POST /me/keys`에서 받는다
- 이메일은 JSON 본문으로만 받는다. 쿼리스트링은 접근 로그에 남는다
- 키를 잃은 사용자는 API 로는 복구할 수 없다. 운영자가 발급한다:
  `uv run python -m fin_checkup.cli account issue-key user@example.com`
- `user_id`는 이메일의 SHA-256 앞 32자 — 로그·URL에 개인정보가 남지 않는다
- 키 폐기는 `revoked_at`을 채운다. 삭제하지 않아 감사 추적이 남는다

## 6. 장애 시 확인 순서

```bash
curl localhost:8000/health          # corp_code 건수, 호출량, 마지막 폴링 시각
uv run python -m fin_checkup.cli budget
```

| 증상 | 확인 |
|---|---|
| `CacheLocked` | 다른 프로세스(Streamlit·워커·CLI)가 DB를 잡고 있다 |
| 검진 결과가 ⚫ 투성이 | 계정 정규화 실패 — `scripts/analyze_universe.py`로 누락률 확인 |
| 알림이 안 온다 | `/health`의 `alerts_worker`가 `stopped`면 워커가 죽은 것(로그에 예외가 있다). `off`면 토큰이 없어 안 띄운 것. `running`인데 `alerts_last_poll`이 오래됐으면 계속 실패 중 |
| 봇이 답을 안 한다 | 로그에 `getUpdates 실패 (연속 N회)`가 쌓이면 토큰 오류(401)거나 봇을 두 곳에서 띄운 것(409). 실패가 이어지면 최대 60초까지 쉬며 재시도한다 |
| DART `[020]` | 일별 허용량 초과. 재시도하지 않고 즉시 멈춘다(`RateLimitExceeded`). 내일까지 캐시로 버틴다 |
| 건전한 기업이 🔴 | 업종 판정을 의심할 것. 4절 참고 |

## 7. 정적 사이트 (GitHub Pages)

`https://younghwan91.github.io/fin-checkup/` 은 캐시만 읽어 만든 정적 파일이다. 사이트 빌드는 DART 를 부르지 않는다.
데이터는 GitHub Actions 가 매일 증분 수집하고, 공개해도 되는 테이블만 릴리스로 올린다.

```
.github/workflows/collect.yml   # 매일 04:00 KST: 최근 data-* → cli collect (DART) → 새 data-YYYYMMDD[-N] 릴리스 → pages.yml 디스패치
  → .github/workflows/pages.yml # 릴리스 → 캐시 재구성 → (최근 위험 공시) → site build → site check → site links → 배포
```

스냅샷 받기·올리기는 두 워크플로와 로컬이 같은 스크립트를 쓴다: `scripts/fetch_snapshot.sh`(최근 `data-*` → 새 캐시),
`scripts/publish_snapshot.sh`(캐시 → 릴리스).

### 자동 수집 (collect.yml)

- **언제**: 매일 19:00 UTC(04:00 KST, Pages 07:00 KST 빌드 전) · 수동 실행. 둘이 겹치지 않는다(`concurrency: collect`).
- **무엇을**: 가장 최근 `data-*` 를 들여온 캐시에서 `cli collect --budget 12000 --max-minutes 270`. 기본 연도는
  `fiscal.collect_years` — 3/20 부터 직전 연도와 그 전 해(지각 제출). 새 상장사는 기업개황부터 받아 상장 여부를 본다.
  이미 있는 재무는 다시 받지 않는다 — 정정공시로 숫자가 바뀐 회사는 자동으로 갱신되지 않는다.
- **예산**: 일별 20,000건을 로컬·Pages 공시 수집(최대 150건)과 나눠 쓴다. 호출량 기록(`dart_call_log`)은 스냅샷에
  없어서 Actions 는 그날 로컬에서 쓴 호출을 모른다 — 12,000건이 그 몫이다. 평소엔 수백 건, 시즌(4월)에 상장사 ~2,800곳 ×
  CFS·OFS 로 최대 ~6,000건/일. 러너에서 건당 ~0.7초(2026-11-22 첫 실행: 300건 3분 20초)라 12,000건이면 2시간 20분 남짓 —
  `--max-minutes 270`·작업 제한 330분 안이다. 쓴 호출 수는 실행 요약(Summary 의 "증분 수집" 표)과 로그 끝 `collect-summary {...}` 에 있다.
- **바뀐 게 있으면** (새 재무·기업개황·빈 응답 기록) backfill → 새 `data-*` 릴리스 → 최근 7개만 남기고 정리(방금 것은 안 지운다).
  빈 응답만 늘어도 올린다 — 안 올리면 다음 날 같은 회사를 또 묻는다. **새 재무·개황이 있으면** `gh workflow run pages.yml`.
  GITHUB_TOKEN 으로 만든 릴리스는 다른 워크플로의 `release` 트리거를 깨우지 않아서(GitHub 의 재귀 방지) 직접 디스패치한다.
  아무것도 없으면 notice 만 남기고 릴리스를 만들지 않는다.
- **키**: secret `DART_API_KEY` 는 "증분 수집"·"새 스냅샷 릴리스"(export 의 키 섞임 검사용, DART 호출 없음) 단계의 env 에만.

수동 실행 (Actions → Collect → Run workflow, 또는):

```bash
gh workflow run collect.yml                                # 기본 (예산 12,000)
gh workflow run collect.yml -f budget=300                  # 시험 삼아 적게
gh workflow run collect.yml -f years="2021 2022" -f budget=15000   # 지난 연도 채우기 (그날 로컬 사용이 없을 때)
gh run watch "$(gh run list --workflow collect.yml --limit 1 --json databaseId -q '.[0].databaseId')"
```

지난 연도 하나를 처음부터 채우면 상장사 ~2,800곳 × 1~2건이라 하루 예산을 넘을 수 있다. 같은 입력으로 다음 날 다시
돌리면 멈춘 데서 이어받는다(이미 받은 재무·빈 응답은 건너뛴다).

멈춘 이유(요약의 `stopped`)별 대응:

| stopped | 뜻 | 할 일 |
|---|---|---|
| (없음) | 빈 곳을 다 봤다 | 없음 |
| `budget` | 예산에 닿았다 | 없음 — 다음 날 이어받는다. 급하면 예산을 올려 수동 실행 |
| `quota` | DART `020` — 오늘 허용량이 끝났다(로컬·다른 사용과 합쳐서) | 받은 것은 올렸다. 그날은 다시 돌리지 않는다(재시도해도 안 풀린다). KST 자정 뒤 이어받는다 |
| `time` | `--max-minutes 270` 에 닿았다 | 없음 — 이어받는다 |
| `errors` | 인증키 오류(010·011·012·101) 또는 연속 5회 실패 | 받은 것은 올리고 작업은 실패로 표시된다. 키(secret)·DART 장애 공지를 확인 |

**로컬에서 수집·업로드할 때**: 공개 데이터의 원본은 이제 가장 최근 `data-*` 릴리스다. 로컬 캐시(`data/`)로
`publish_snapshot.sh` 를 하면 Actions 가 받은 것이 빠진 스냅샷이 최신이 된다. 먼저 받아서 시작한다:
`FIN_CHECKUP_DB_PATH=/tmp/latest.duckdb scripts/fetch_snapshot.sh /tmp/latest` → 그 캐시로 `cli collect` →
`FIN_CHECKUP_DB_PATH=/tmp/latest.duckdb scripts/publish_snapshot.sh`. 로컬 수집 중에는 같은 날 Actions 예산과 합쳐 20,000건을 넘지 않게.

### 스냅샷 올리기 (손으로)

평소엔 collect.yml 이 올린다. 손으로 올릴 땐 캐시를 쓰는 프로세스(수집·`serve`·Streamlit)를 모두 내린 뒤
`scripts/publish_snapshot.sh` — 같은 날 태그가 있으면 `data-YYYYMMDD-2`, `-3` … 으로 붙는다(날짜를 정하려면
`scripts/publish_snapshot.sh 20261122`, 이미 있으면 멈춘다). 스냅샷에는
`snapshot.py` 의 허용목록 테이블만 들어가고, 계정·키·관심종목·알림 기록 같은 개인정보·운영 테이블은 아예 빠진다.
인증키처럼 생긴 값이 섞여 있으면 export 가 멈춘다.

### Pages 워크플로가 도는 때와 하는 일

- 언제: `data-*` 릴리스가 publish 될 때(손으로 올린 것만 — collect.yml 이 올린 것은 디스패치로) · collect.yml 의 디스패치 ·
  매일 07:00 KST · 수동 실행(사업연도 지정 가능). 코드 릴리스(`data-` 가 아닌 태그)는 무시한다.
- 가장 최근 `data-*` 릴리스를 받는다. **없으면 경고만 남기고 배포하지 않는다** — 지난 사이트가 그대로 남는다.
- `snapshot import` 로 새 캐시를 만들고 `site build --base-url $SITE_URL` 로 빌드한다.
- **`site check site-out`** 이 면책·계산식·CSP·외부 스크립트·인증키 패턴을 산출물에서 확인한다(규칙은 `site/guard.py`).
  하나라도 걸리면 배포하지 않는다.
- **`site links site-out --base-url $SITE_URL`** 이 내부 링크(상대경로·사이트 주소 아래 절대주소·`#앵커`)가 실제 파일로
  이어지는지 본다(`site/links.py`). 깨진 내부 링크가 있으면 배포하지 않는다. 외부 링크는 목록만 남긴다.
- 배포는 한 번에 하나(`concurrency: pages`, 진행 중인 배포를 끊지 않는다).
- 배포 환경 `github-pages` 는 **브랜치 `main` 과 태그 `data-*`** 에서만 배포를 받는다(저장소 Settings → Environments).
  릴리스로 시작된 실행은 태그 위에서 돌기 때문에, 태그 규칙이 빠지면 build 는 녹색인데 deploy 만 거부된다
  (2026-11-21 첫 배포가 그렇게 실패했다). 확인: `gh api repos/younghwan91/fin-checkup/environments/github-pages/deployment-branch-policies`.
- 스냅샷의 지표 값은 배포마다 `cli backfill --years 5` 로 지금 코드 기준으로 다시 계산한다 — 지표 계산을 고친 PR 은
  스냅샷을 다시 올리지 않아도 다음 배포(매일 07:00 KST)에 반영된다. 회사 페이지가 2,000곳보다 적으면
  `site check --min-companies` 가 배포를 막는다.

### secret `DART_API_KEY`

사이트에서 DART 를 부르는 건 "최근 위험 공시" 한 단계뿐이다.

| secret | 동작 |
|---|---|
| 없음 | 그 단계를 건너뛰고(notice) 공시 없이 배포한다 |
| 있음 | `disclosures fetch --days 7 --max-pages 30` — 최대 150회 호출. collect.yml·로컬 수집과 일별 허용량을 나눠 쓴다 |
| 있는데 실패 (장애·`020`) | `continue-on-error` — 이번 빌드엔 공시가 빠지고 배포는 계속된다 |

키는 그 단계의 `env` 로만 넘긴다. 빌드 로그·산출물에 찍히지 않게 하는 것이 DART 약관 제19조의 요구이고,
`site check` 의 `secret` 규칙이 40자리 16진수·`crtfc_key` 를 산출물 전체에서 찾는다.

### 로컬에서 다시 빌드하기

```bash
uv run python -m fin_checkup.cli site build --out site-out --limit 50   # 앞 50곳만 (확인용), 사업연도는 --year
uv run python -m fin_checkup.cli site check site-out                     # 배포 전과 같은 가드
uv run python -m fin_checkup.cli site links site-out                     # 깨진 내부 링크
uv run python -m http.server -d site-out 8080                            # http://localhost:8080/
```

- 기본 캐시는 현재 디렉터리의 `data/fin_checkup.duckdb` 다. 다른 캐시는 `FIN_CHECKUP_DB_PATH=<경로>` 로.
- 배포본과 같은 데이터로 보려면 최근 릴리스를 받아 캐시를 새로 만든다(캐시 파일은 없는 경로여야 한다):
  `FIN_CHECKUP_DB_PATH=/tmp/snap.duckdb scripts/fetch_snapshot.sh /tmp/snap [data-YYYYMMDD]` →
  `FIN_CHECKUP_DB_PATH=/tmp/snap.duckdb uv run python -m fin_checkup.cli site build --snapshot /tmp/snap/files ...`
- 출력 폴더는 빌드마다 통째로 비운다. 비어 있지 않은 폴더는 빌드 표식(`.fin-checkup-site`)이 있을 때만 비우고,
  없으면 멈춘다 — 엉뚱한 폴더를 지우지 않게.
- canonical·sitemap 같은 절대주소는 `--base-url https://…` 를 줄 때만 나온다. 페이지 사이 링크는 상대경로라 무관하다.
- 빠르게: `--jobs N` 은 건강검진 계산을 프로세스 N 개로 나눈다(DB 를 읽기 전용으로 여는 `site build` 에서만).
  `--incremental <폴더>` 는 코드·데이터가 같은 건강검진과 공유 카드 PNG 를 다시 만들지 않는다 — 열쇠는 패키지
  소스 전체 해시 + 데이터 테이블 내용 해시(+ PNG 는 카드 내용)라 하나라도 바뀌면 다시 만든다
  (`site/incremental.py`). 2026-11-21 실측(2,542곳·16코어): 기본 87초 → `--jobs 8` 55초 → 같은 캐시로 다시 9초.
  `pages.yml` 은 `--jobs $(nproc) --incremental _build-cache` 에 actions/cache 로 폴더를 이어 쓴다.

### 검색 엔진에 사이트 알리기 (sitemap)

`site build --base-url <사이트 주소>` 가 `sitemap.xml`(색인할 HTML 전부, lastmod = 빌드 날짜 KST)과
`robots.txt` 를 만든다. `--base-url` 이 절대주소가 아니면 robots.txt 에 `Sitemap:` 줄이 빠지고 canonical 도 없다.

**robots.txt 는 도메인 루트에서만 읽힌다.** Pages 프로젝트 사이트(`https://younghwan91.github.io/fin-checkup/`)의
`/fin-checkup/robots.txt` 는 크롤러가 보지 않으니, sitemap 주소를 아래 두 곳에 직접 제출한다.

| 어디 | 순서 |
|---|---|
| Google Search Console | 속성 추가 → **URL 접두어** `https://younghwan91.github.io/fin-checkup/` → 소유 확인(HTML 파일) → Sitemaps 에 `sitemap.xml` 제출 |
| 네이버 서치어드바이저 (searchadvisor.naver.com) | 웹마스터 도구 → 사이트 등록(같은 주소) → 소유 확인(HTML 파일) → 요청 → 사이트맵 제출에 `…/fin-checkup/sitemap.xml` |

소유 확인 HTML 파일(`google….html`, `naver….html`)은 비밀이 아니다(내용이 파일 이름 그대로 공개된다). 빌드가
출력 폴더를 매번 비우므로, `pages.yml` 의 사이트 빌드 단계 끝에서 `site-out/` 루트에 그 파일을 쓰는 한 줄을 더한다.
하위 경로 등록이 거부되면 사용자 지정 도메인을 붙여야 한다 — 그때 `SITE_URL` 도 함께 바꾼다.
