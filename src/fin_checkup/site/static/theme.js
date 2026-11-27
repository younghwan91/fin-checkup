/* 라이트/다크 테마 전환 — OS 설정(prefers-color-scheme)을 기본으로 따르고, 누르면 브라우저에 기억한다. */
(function () {
  "use strict";
  var root = document.documentElement;

  function currentTheme() {
    var attr = root.getAttribute("data-theme");
    if (attr === "light" || attr === "dark") return attr;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches
      ? "dark"
      : "light";
  }

  function apply(theme) {
    root.setAttribute("data-theme", theme);
    var btn = document.getElementById("fc-theme-toggle");
    if (btn) {
      btn.setAttribute("aria-pressed", theme === "dark" ? "true" : "false");
      btn.setAttribute("title", theme === "dark" ? "라이트 테마로 전환" : "다크 테마로 전환");
    }
  }

  try {
    var saved = localStorage.getItem("fin-checkup:theme");
    if (saved === "light" || saved === "dark") {
      root.setAttribute("data-theme", saved);
    }
  } catch (_err) {
    /* 저장소 비활성화 환경은 OS 설정만 따른다. */
  }

  document.addEventListener("DOMContentLoaded", function () {
    var btn = document.getElementById("fc-theme-toggle");
    if (!btn) return;
    btn.setAttribute("aria-pressed", currentTheme() === "dark" ? "true" : "false");
    btn.addEventListener("click", function () {
      var next = currentTheme() === "dark" ? "light" : "dark";
      apply(next);
      try {
        localStorage.setItem("fin-checkup:theme", next);
      } catch (_err) {
        /* ignore */
      }
    });
  });
})();
