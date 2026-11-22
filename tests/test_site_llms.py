"""`/llms.txt` · `/llms-full.txt` — 웹 에이전트가 이 사이트를 찾아 바르게 쓰게 하는 안내.

llmstxt.org 형식(H1 · 인용 요약 · H2 절마다 링크 목록), 링크는 전부 빌드 안의 파일로 이어지고,
넘지 않는 선(측정값만·계산식·⚫/⊘·면책·추천 금지)을 적고, 금지 표현은 없다.
"""

from __future__ import annotations

import re
from datetime import date

import pytest
from hub_seed import seed_site_cache

from fin_checkup.disclaimer import DISCLAIMER
from fin_checkup.explain import banned
from fin_checkup.explain.metrics import explainers
from fin_checkup.metrics.signals import Signal
from fin_checkup.site.build import build_site
from fin_checkup.site.guard import check_file
from fin_checkup.site.links import check_links
from fin_checkup.storage import Cache

BASE = "https://example.github.io/fin-checkup/"
_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


async def _build(tmp_path, base_url: str = BASE, name: str = "out"):
    with Cache(tmp_path / f"{name}.duckdb") as cache:
        seed_site_cache(cache)
        target = tmp_path / name
        await build_site(cache, target, 2024, base_url=base_url, built_on=date(2026, 11, 21), images=False)
    return target


@pytest.fixture
async def out(tmp_path):
    return await _build(tmp_path)


def read(out, name: str) -> str:
    return (out / name).read_text(encoding="utf-8")


def _local(url: str, base: str) -> str | None:
    if base and url.startswith(base):
        return url[len(base) :]
    if re.match(r"^[a-z]+:", url):
        return None
    return url


def _resolves(out, rel: str) -> bool:
    rel = rel.split("#", 1)[0].split("?", 1)[0]
    target = out / rel
    if rel == "" or rel.endswith("/"):
        target = target / "index.html"
    return target.is_file()


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
async def test_llms_files_follow_the_llmstxt_shape(out, name):
    text = read(out, name)
    lines = text.splitlines()
    assert lines[0] == "# fin-checkup"
    first_body = next(line for line in lines[1:] if line.strip())
    assert first_body.startswith("> "), "H1 바로 뒤는 인용 요약"
    assert sum(1 for line in lines if line.startswith("## ")) >= 5
    assert "## Optional" in lines


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
async def test_llms_carry_the_red_lines_and_disclaimer(out, name):
    text = read(out, name)
    assert DISCLAIMER in text
    assert banned.find_outside_allowed(text) == []
    for must in ("⚫", "⊘", "formula", "threshold_text", "never tell the user to buy, sell or hold"):
        assert must in text, must
    assert check_file(name, text.encode("utf-8")) == []


@pytest.mark.parametrize("name", ["llms.txt", "llms-full.txt"])
@pytest.mark.parametrize("base_url", [BASE, "/"])
async def test_every_link_in_llms_resolves_to_a_built_file(tmp_path, name, base_url):
    out = await _build(tmp_path, base_url, name="b" if base_url == "/" else "a")
    links = _MD_LINK.findall(read(out, name))
    assert len(links) >= 10
    local = [rel for url in links if (rel := _local(url, base_url if base_url != "/" else "")) is not None]
    assert local, "사이트 안 링크가 하나도 없다"
    broken = [rel for rel in local if not _resolves(out, rel)]
    assert broken == []
    if base_url == BASE:
        assert all(u.startswith(("https://", "http://")) for u in links), "절대 base 면 절대주소"


async def test_llms_explains_the_two_or_three_get_recipe(out):
    text = read(out, "llms.txt")
    assert f"{BASE}api/index.json" in text
    assert f"{BASE}search-index.json" in text
    assert f"{BASE}api/c/005930.json" in text
    assert f"{BASE}c/005930/" in text
    assert "chosung" in text and "compare/?c=" in text


async def test_llms_full_inlines_every_metric_and_the_signal_legend(out):
    text = read(out, "llms-full.txt")
    for e in explainers():
        assert f"`{e.key}`" in text, e.key
        assert e.formula in text, e.key
        assert e.threshold_text() in text, e.key
        assert e.one_liner in text, e.key
        for caveat in e.sector_caveats:
            assert caveat.instead in text, e.key
    for signal in Signal:
        assert f"{signal.emoji} {signal.label}" in text
        assert f"`{signal.value}`" in text
    assert len(text.encode("utf-8")) > 3 * len(read(out, "llms.txt").encode("utf-8"))


async def test_llms_states_the_build_facts(out):
    text = read(out, "llms.txt")
    assert "fiscal year 2024" in text
    assert "3 companies" in text
    assert "2026-11-21" in text


async def test_robots_and_footer_point_agents_to_llms(out):
    robots = read(out, "robots.txt")
    assert f"# {BASE}llms.txt" in robots.replace("AI agents: ", "")
    page = read(out, "c/005930/index.html")
    assert 'href="../../llms.txt"' in page
    assert check_links(out, BASE).broken == []


def test_guard_checks_agent_text_files_for_disclaimer_and_banned_phrases():
    ok = f"# fin-checkup\n\n{DISCLAIMER}\n".encode()
    assert check_file("llms.txt", ok) == []
    rules = {v.rule for v in check_file("llms.txt", b"# fin-checkup\n")}
    assert "disclaimer" in rules
    rules = {v.rule for v in check_file("llms-full.txt", ok + "이 종목을 추천해요.\n".encode())}
    assert "banned" in rules
    # 다른 .txt(robots.txt)는 면책을 요구하지 않는다.
    assert check_file("robots.txt", b"User-agent: *\n") == []


def test_link_checker_reads_markdown_links_in_agent_text(tmp_path):
    (tmp_path / "index.html").write_text("<html><body></body></html>", encoding="utf-8")
    (tmp_path / "llms.txt").write_text(
        f"# x\n\n- [ok]({BASE})\n- [gone]({BASE}nope.json)\n- [ext](https://example.org/)\n", encoding="utf-8"
    )
    report = check_links(tmp_path, BASE)
    assert [(b.source, b.url) for b in report.broken] == [("llms.txt", f"{BASE}nope.json")]
    assert "https://example.org/" in report.external
