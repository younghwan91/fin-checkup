"""알림 문구 작성.

여기가 규제 안전선이 실제로 지켜지는 자리다. 문구는 네 가지만 담는다.
회사 이름 / 어떤 공시가 떴는지 / 그 공시가 무엇인지에 대한 사실 설명 / 원문 링크.

"그래서 어떻게 하라"는 말은 넣지 않는다. 넣는 순간 투자자문이 된다.
"""

from __future__ import annotations

from fin_checkup.alerts.classify import RiskDisclosure

FOOTER = "ℹ️ 사실 전달이며 투자권유가 아닙니다. 판단은 원문을 직접 확인하세요."


def _escape(text: str) -> str:
    """텔레그램 HTML 파스 모드에서 깨지지 않게."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _tidy(text: str) -> str:
    """DART 공시명에 들어 있는 정렬용 공백을 정리한다.

    '주권매매거래정지              (지정자문인 선임계약 해지)' 처럼 온다.
    """
    return _escape(" ".join(text.split()))


def format_alert(risk: RiskDisclosure) -> str:
    """위험 공시 한 건을 텔레그램 HTML 메시지로."""
    d = risk.disclosure
    name = _escape(d.corp_name)
    ticker = f" ({d.stock_code})" if d.stock_code else ""
    correction = " [정정공시]" if risk.is_correction else ""

    return (
        f"{risk.severity.emoji} <b>{name}{ticker}</b>\n"
        f"<b>{_escape(risk.kind.label)}</b>{correction}\n\n"
        f"📄 {_tidy(d.report_nm)}\n"
        f"🗓 {d.rcept_date_display} · 제출인 {_escape(d.flr_nm or d.corp_name)}\n\n"
        f"{_escape(risk.why)}\n\n"
        f"🔗 <a href=\"{d.url}\">DART 원문 보기</a>\n\n"
        f"<i>{FOOTER}</i>"
    )


def format_channel_alert(risk: RiskDisclosure, bot_username: str = "") -> str:
    """채널(브로드캐스트)용.

    관심종목 알림과 두 가지가 다르다. 채널 구독자는 이 종목을 갖고 있는지 알 수
    없으므로 종목명을 앞세우고, 자기 종목 알림으로 넘어가는 길을 함께 안내한다.
    """
    d = risk.disclosure
    name = _escape(d.corp_name)
    ticker = f" <code>{d.stock_code}</code>" if d.stock_code else ""

    lines = [
        f"{risk.severity.emoji} <b>{name}</b>{ticker}",
        f"{_escape(risk.kind.label)}{' [정정공시]' if risk.is_correction else ''}",
        "",
        f"📄 {_tidy(d.report_nm)}",
        f"🗓 {d.rcept_date_display}",
        "",
        _escape(risk.why),
        "",
        f'🔗 <a href="{d.url}">DART 원문 보기</a>',
    ]
    if bot_username:
        lines += [
            "",
            f"💬 보유 종목의 유상증자·CB 발행까지 받아보려면 "
            f'<a href="https://t.me/{bot_username}">@{bot_username}</a>에서 '
            f"관심종목을 등록하세요.",
        ]
    lines += ["", f"<i>{FOOTER}</i>"]
    return "\n".join(lines)


def format_digest(risks: list[RiskDisclosure], limit: int = 10) -> str:
    """여러 건을 한 메시지로 묶는다. 알림 폭탄을 막기 위한 것."""
    if not risks:
        return ""
    head = f"📋 <b>관심종목 공시 {len(risks)}건</b>\n"
    lines = []
    for risk in risks[:limit]:
        d = risk.disclosure
        lines.append(
            f"\n{risk.severity.emoji} <b>{_escape(d.corp_name)}</b> — "
            f"{_escape(risk.kind.label)}\n"
            f"   {_tidy(d.report_nm)}\n"
            f"   <a href=\"{d.url}\">원문</a> · {d.rcept_date_display}"
        )
    if len(risks) > limit:
        lines.append(f"\n\n…외 {len(risks) - limit}건")
    return head + "".join(lines) + f"\n\n<i>{FOOTER}</i>"
