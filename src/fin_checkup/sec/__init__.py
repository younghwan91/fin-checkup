"""SEC EDGAR 연동 (계획서 Phase 2 — 미장 확장).

미 연방 저작물이라 저작권 자체가 없고(17 U.S.C. §105), SEC가 재배포를 명시적으로
허용한다. DART와 함께 이 프로젝트에서 가장 안전한 두 데이터원이다.

여기서 하는 일은 US-GAAP 계정을 DART와 같은 `Financials` 모델로 옮기는 것뿐이다.
그러면 지표 엔진·신호등·위험 신호·급변 감지가 코드 한 줄 안 고치고 미장에 그대로 돈다.
"""

from fin_checkup.sec.client import SecClient, SecError

__all__ = ["SecClient", "SecError"]
