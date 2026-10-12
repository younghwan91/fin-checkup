# fin-checkup — 작업 규약

DART·SEC EDGAR 공시로 ① 관심종목 위험 공시를 텔레그램으로 알리고 ② 재무제표를 신호등(🟢🟡🔴⚪⊘⚫)으로
보여주는 도구. **MIT PUBLIC 저장소.** CLI·Streamlit·FastAPI 가 모두 `service.py` 를 쓴다(UI 를 모른다).

## 명령

```bash
uv sync --all-extras                       # 웹·API·테스트까지 (CI 와 같음: --frozen)
uv run pytest -q                           # ~25초, .env 없이 돈다 (conftest Settings 픽스처가 키 주입)
uv run ruff check src tests scripts        # CI 가 막는 린트
uv run python -m fin_checkup.cli --help    # sync·checkup·us·watch·poll·budget·bot·serve·account ...
uv run streamlit run src/fin_checkup/app.py
```

`ruff format` 은 **강제하지 않는다** — 39개 파일이 포맷과 어긋나 있다(의도적, 버그 수정 diff 를 읽기 좋게).
수정 커밋에서 파일 전체를 재포맷하지 않는다. 포맷을 강제하자고 하면 단독 커밋 하나로 한다.

## 선 — 넘지 않는다 (`docs/REGULATORY.md`)

- **도구만 제공한다. 종목 추천·투자 판단·"그러니 파세요" 를 쓰지 않는다** — 유사투자자문업이다. 알림은
  "어떤 공시가 떴다는 사실 + DART 원문 링크" 까지. 신호등은 측정 결과이고 계산식·경계값을 항상 함께 노출한다
  (판단 근거를 감춘 종합 점수는 넣지 않는다). 화면·알림·API 에는 면책 문구(`DISCLAIMER`, `CheckupOut.disclaimer`).
- **데이터가 없으면 없다고 한다** — 못 찾은 계정은 0 이 아니라 ⚫. 업종에 안 맞는 지표는 ⊘ + 대신 볼 것
  (`docs/OPERATIONS.md` §4 의 ⊘ 기준: 계산은 되지만 뜻이 없을 때만 — 값이 나쁘게 치우친 건 ⊘ 사유가 아니다).
- 2026-08-16 수익화를 접고 공개했다. **티어·구독·결제·PG·사업자등록을 다시 제안하지 않는다.** 옛 계획서
  (PLAN.md·LAUNCH.md)를 되살리지 않는다 — 사적 판단이 들어 있어 일부러 뺐고, 옛 히스토리는 비공개 `fin-checkup-archive` 에 봉인돼 있다.

## 환경 함정

- 설정은 pydantic-settings 가 **현재 디렉터리의 `.env`** 를 읽는다(`config.py`). 캐시 DB 도 상대경로
  `data/fin_checkup.duckdb`. 실데이터로 돌릴 땐 `~/git/fin-checkup` 에서 — 워크트리에는 `.env`·`data/` 가 없어
  키 없음·빈 캐시로 돈다.
- **이 레포의 `.env` 는 셸로 source 하면 안 된다** — `SEC_USER_AGENT` 값에 따옴표 없는 공백이 있어
  `set -a; . ./.env` 가 그 줄을 명령으로 실행하고 값을 비운다(2026-10-12 확인). 키 유무는
  `uv run python -c "from fin_checkup.config import settings as s; print(s.has_api_key, s.has_telegram, s.has_sec_user_agent)"` 로 본다.
- **텔레그램 발송은 실제 API 로 검증된 적이 없다** — `TELEGRAM_BOT_TOKEN` 이 비어 있다(2026-10-12 확인).
  "알림이 동작한다" 고 말하지 않는다. 사용자는 텔레그램을 쓰지 않는다 — 이메일은 도메인이 필요하고
  카카오 알림톡은 사업자등록 전제라 불가. 채널 결정은 사용자에게.
- **DuckDB 는 단일 writer.** `serve` 가 API 와 알림 워커를 한 프로세스에서 돌린다 — 워커·CLI·Streamlit 을
  따로 띄우면 `CacheLocked`. read-only 연결도 writer 가 있으면 거부된다.
- DART 일별 호출 한도: `cli budget` 으로 확인. status `020` 은 재시도하지 않는다(`RateLimitExceeded`).
  인증키는 오류 메시지·로그에 찍히지 않게 한다(dc8ed56) — DART 약관 제19조. 전 상장사 수집은
  `scripts/collect_universe.py --limit 1000 [--skip N]` 로 나눠서.
- 날짜는 전부 KST(`clock.kst_today`). `date.today()` 를 쓰면 UTC 서버에서 KST 00~09시 공시가 빠진다(76d1b7d).
- `poll --dry-run`·토큰 없는 콘솔 출력은 `notified` 에 기록하지 않는다 — 미리보기가 기록하면 그 공시는 영영 안 나간다.

## 일부러 남긴 것 — "고치지" 말고 먼저 묻는다 (2026-10-05 업그레이드 판단)

- `Cache` 는 프로세스 전역 RLock + 스레드별 커서로 모든 공개 메서드를 직렬화한다. API 지연 문제가 나오면 여기부터.
- `/accounts` 는 이메일 소유를 검증하지 않는다 — 같은 이메일 재가입은 409, 복구는 운영자 `cli account issue-key`.
- DART `020` 이 분당 한도에도 쓰인다면 워커는 30분 뒤 재시도한다(문서상 020 = 일별 한도로 취급).

## 커밋·공개

- 커밋 신원은 레포 git 설정(`48156556+younghwan91@users.noreply.github.com`) 그대로. `git -c user.email=...`
  금지 — `commit-identity.yml` 이 거부한다.
- 커밋 메시지는 한국어 Conventional Commits(`fix(alerts): ...`), 본문엔 실패 시나리오(왜 문제였나). 수정 묶음당 커밋 하나.
- **공개 레포의 히스토리 재작성은 아무것도 지우지 않는다** — force push 뒤에도 GitHub API 로 옛 SHA 가 열린다
  (2026-08-16 실측). 민감한 것을 올렸으면 레포 교체(rename → 새 레포)뿐. 공개 전 점검은 워킹트리가 아니라 히스토리
  기준. 되돌릴 수 없는 행동 전엔 백업을 홈 디렉터리에.

## 문서

- `docs/OPERATIONS.md` — 프로세스 구조·호출 예산·알림 워커·업종별 판정·계정과 키·장애 대응 (운영 코드 손대기 전에)
- `docs/REGULATORY.md` — 지켜야 하는 선 (문구·알림·화면을 바꿀 때)
- `docs/TERMS.md` · `docs/PRIVACY.md` — 약관·방침 초안
- 지표 경계값의 근거는 `src/fin_checkup/metrics/engine.py` 주석 (실제 시장 분위수로 맞췄다 — 통용 기준으로 바꾸지 않는다)
