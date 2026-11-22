"""Jinja2 환경 — 자동 이스케이프, 정의 안 된 변수는 오류.

회사명은 DB 에서 온다. 이스케이프를 끄면 이름 하나로 페이지에 스크립트가 들어간다.
템플릿엔 판정 로직을 두지 않는다 — 문장·숫자는 `view.py` 가 `presentation`·`format` 으로
미리 만들어 넘기고, 템플릿은 배치만 한다.

템플릿에서 쓸 수 있는 전역:
- `url(path)` — 현재 페이지 기준 상대 링크 (`url('c/005930/')`, `url('')` 은 홈)
- `DISCLAIMER` — 면책 문구 (`fin_checkup.disclaimer`)
- `site` · `root` · `page_path` — `Site.render` 가 넣는다

필터:
- `marks` — 문장 속 신호 이모지(🟢 정상 …)를 CSS 모양(`_signal.html` 과 같은 .fc-mark)으로.
  화면에서는 이모지 글꼴에 기대지 않는다(⚪ 가 흑백 "○" 로 나와 '비어 있음' 처럼 보였다).
  글자만 있는 곳(피드·JSON)은 그대로.
"""

from __future__ import annotations

import re

from jinja2 import Environment, PackageLoader, StrictUndefined, pass_context, select_autoescape
from jinja2.runtime import Context
from markupsafe import Markup, escape

from fin_checkup.brand import inline_mark_svg
from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.metrics.signals import Signal

_MARK = '<span class="fc-mark mark-{key}" aria-hidden="true"></span>'
_GLYPHS = re.compile("|".join(re.escape(s.emoji) for s in Signal))
_BY_GLYPH = {s.emoji: s for s in Signal}


@pass_context
def _url(context: Context, path: str = "") -> str:
    return context["root"] + path.lstrip("/")


def signal_marks(text: str) -> Markup:
    """이스케이프한 뒤 신호 이모지를 모양 `<span class="fc-mark mark-…">` 로 바꾼다.

    "🟢 정상" 처럼 바로 뒤에 신호 이름이 있으면 모양만, "🔴로 표시" 처럼 없으면 화면 읽기 프로그램용
    이름을 숨겨 붙인다 — 모양은 aria-hidden 이라 이름이 없으면 듣는 사람에게 뜻이 사라진다.
    """
    escaped = str(escape(text))

    def swap(m: re.Match[str]) -> str:
        signal = _BY_GLYPH[m.group(0)]
        mark = _MARK.format(key=signal.value)
        rest = escaped[m.end() :]
        if rest.startswith(" " + signal.label):
            return mark
        return mark + f'<span class="fc-visually-hidden">{signal.label}</span>'

    out = _GLYPHS.sub(swap, escaped)
    # "🟢 정상" 의 공백은 모양이 대신하므로 지운다.
    out = re.sub(r'(aria-hidden="true"></span>) ', r"\1", out)
    return Markup(out)


def make_env() -> Environment:
    env = Environment(
        loader=PackageLoader("fin_checkup.site", "templates"),
        autoescape=select_autoescape(enabled_extensions=("html", "xml", "svg"), default=True),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    env.globals.update(url=_url, DISCLAIMER=DISCLAIMER, LOGO=Markup(inline_mark_svg()))
    env.filters["marks"] = signal_marks
    return env
