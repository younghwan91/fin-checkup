/* fin-checkup 나란히 보기 — /compare/?c=005930,000660 (2~4곳).
   회사마다 정적 JSON(api/c/<코드>.json)을 받아 표의 회사 칸을 채운다. 신호·값·경계값·중앙값 문장은
   JSON 에 있는 것을 그대로 옮긴다 — 여기서 판정하거나 형식을 다시 만들지 않는다.
   순서: 회사는 고른 순서, 지표는 페이지에 고정된 순서. 값으로 줄 세우거나 나은 쪽을 칠하지 않는다.
   지난 비교 목록만 이 기기의 브라우저 저장소("fin-checkup:compare")에 둔다 — 회사 페이지의 "비교에 추가"
   (?add=<코드>)가 그 끝에 붙인다. 외부 요청·추적 없음. 글자는 전부 textContent 로 넣는다. */
(function () {
  "use strict";

  var MAX = 4;
  var CODE = /^[0-9A-Z]{6}$/;

  // "005930, 000660" → ["005930", "000660"]. 고른 순서 그대로, 중복·형식 밖은 버리고 MAX 곳까지.
  function parseCodes(raw) {
    var out = [];
    String(raw == null ? "" : raw).split(",").forEach(function (part) {
      var code = part.trim().toUpperCase();
      if (CODE.test(code) && out.indexOf(code) < 0 && out.length < MAX) out.push(code);
    });
    return out;
  }

  // 끝에 붙인다. 이미 있거나 MAX 곳이 찼으면 그대로.
  function addCode(codes, code) {
    if (codes.indexOf(code) >= 0 || codes.length >= MAX) return codes.slice();
    return codes.concat([code]);
  }

  function queryFor(codes) {
    return codes.length ? "?c=" + codes.join(",") : "";
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { parseCodes: parseCodes, addCode: addCode, queryFor: queryFor, MAX: MAX };
    return;
  }

  var table = document.getElementById("fc-compare");
  if (!table) return;
  var root = document.documentElement.getAttribute("data-root") || "./";
  var heads = table.querySelector(".fc-compare-heads");
  var subheads = table.querySelector(".fc-compare-subheads");
  var status = document.querySelector(".fc-compare-status");
  var addForm = document.querySelector("form.fc-compare-add");

  var codes = [];
  var docs = {};
  var notes = [];
  var token = 0;

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }

  function stored() {
    try {
      return parseCodes(window.localStorage.getItem("fin-checkup:compare"));
    } catch (e) {
      return [];
    }
  }

  function remember() {
    try {
      if (codes.length) window.localStorage.setItem("fin-checkup:compare", codes.join(","));
      else window.localStorage.removeItem("fin-checkup:compare");
    } catch (e) {
      /* 저장소를 못 쓰면 주소(?c=)만으로 동작한다 */
    }
  }

  function fetchDoc(code) {
    if (docs[code]) return Promise.resolve(docs[code]);
    return fetch(root + "api/c/" + code + ".json")
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (doc) {
        if (doc) docs[code] = doc;
        return doc;
      });
  }

  function say() {
    var head;
    if (!codes.length) head = "회사를 골라 더하세요. 2곳 이상이면 나란히 볼 수 있습니다.";
    else if (codes.length === 1) head = "한 곳을 더 고르면 나란히 볼 수 있습니다.";
    else head = codes.length + "곳을 고른 순서대로 놓았습니다.";
    status.textContent = [head].concat(notes).join(" ");
  }

  function clearColumns(row) {
    while (row.children.length > (row === subheads ? 0 : 1)) row.removeChild(row.lastChild);
  }

  function companyHead(doc) {
    var c = doc.company;
    var th = el("th", "fc-compare-company");
    th.scope = "col";
    th.colSpan = 2;
    var link = el("a", "", c.name);
    link.href = root + c.page;
    th.appendChild(link);
    var meta = [c.stock_code, c.market, c.sector_label].filter(Boolean).join(" · ");
    th.appendChild(el("small", "", meta));
    th.appendChild(el("small", "", doc.fiscal_year + " " + doc.report.label));
    var remove = el("button", "fc-button fc-compare-remove", "빼기");
    remove.type = "button";
    remove.setAttribute("data-code", c.stock_code);
    remove.setAttribute("aria-label", c.name + " 빼기");
    th.appendChild(remove);
    return th;
  }

  function valueCell(metric, rowFormula) {
    if (!metric) {
      var none = el("td", "fc-compare-value muted", "—");
      none.appendChild(el("small", "", "이 회사 자료에 없는 지표"));
      return none;
    }
    var td = el("td", "fc-compare-value sig-" + metric.signal.key);
    var sig = el("span", "fc-sig");
    // 신호 모양은 CSS(.fc-mark) — 이모지 글꼴에 기대지 않는다 (_signal.html 과 같은 모양).
    sig.className = "fc-sig sig-" + metric.signal.key;
    sig.appendChild(el("span", "fc-mark mark-" + metric.signal.key)).setAttribute("aria-hidden", "true");
    sig.appendChild(document.createTextNode(metric.signal.label));
    td.appendChild(sig);
    td.appendChild(el("b", "fc-compare-number" + (metric.value == null ? " muted" : ""), metric.value_text));
    td.appendChild(el("small", "", "경계값 " + metric.threshold_text));
    if (metric.formula && metric.formula !== rowFormula) {
      td.appendChild(el("small", "", "계산식 " + metric.formula));
    }
    if (metric.note) {
      var lead = metric.signal.key === "not_applicable" ? "대신 볼 것 " : "";
      td.appendChild(el("small", "fc-compare-note", lead + metric.note));
    }
    return td;
  }

  function peerCell(metric) {
    var peer = metric && metric.peer;
    if (!peer) {
      var none = el("td", "fc-compare-peer muted", "—");
      none.appendChild(el("small", "", "대조군 없음"));
      return none;
    }
    var td = el("td", "fc-compare-peer", peer.median_text);
    td.appendChild(el("small", "", peer.scope_label + " " + peer.sample_size + "곳 중앙값"));
    return td;
  }

  function paint() {
    var shown = codes.map(function (code) { return docs[code]; });
    clearColumns(heads);
    clearColumns(subheads);
    shown.forEach(function (doc) {
      heads.appendChild(companyHead(doc));
      subheads.appendChild(el("th", "", "신호 · 값 · 경계값")).scope = "col";
      subheads.appendChild(el("th", "", "대조군 중앙값")).scope = "col";
    });
    var cats = table.querySelectorAll(".fc-compare-cat th");
    for (var i = 0; i < cats.length; i++) cats[i].colSpan = 1 + 2 * shown.length;
    var rows = table.querySelectorAll("tr.fc-compare-row");
    for (var j = 0; j < rows.length; j++) {
      var row = rows[j];
      var key = row.getAttribute("data-key");
      var fEl = row.querySelector(".fc-compare-formula");
      var formula = fEl ? (fEl.getAttribute("data-formula") || fEl.textContent) : "";
      clearColumns(row);
      shown.forEach(function (doc) {
        var metric = null;
        for (var k = 0; k < doc.metrics.length; k++) {
          if (doc.metrics[k].key === key) metric = doc.metrics[k];
        }
        row.appendChild(valueCell(metric, formula));
        row.appendChild(peerCell(metric));
      });
    }
    say();
  }

  // 원하는 목록으로 바꾼다. 받지 못한 회사는 알리고 뺀다. 주소·저장소를 맞춘다.
  function show(wanted) {
    var mine = ++token;
    return Promise.all(wanted.map(fetchDoc)).then(function (got) {
      if (mine !== token) return;
      var missing = wanted.filter(function (_c, i) { return !got[i]; });
      if (missing.length) notes.push(missing.join(", ") + " 회사 자료를 찾지 못해 뺐습니다.");
      codes = wanted.filter(function (_c, i) { return got[i]; });
      remember();
      window.history.replaceState(null, "", window.location.pathname + queryFor(codes));
      paint();
    });
  }

  function add(code) {
    notes = [];
    if (codes.indexOf(code) < 0 && codes.length >= MAX) {
      notes.push("최대 " + MAX + "곳까지 놓을 수 있습니다. 한 곳을 빼고 더하세요.");
      say();
      return;
    }
    show(addCode(codes, code));
  }

  table.addEventListener("click", function (e) {
    var button = e.target.closest("button.fc-compare-remove");
    if (!button) return;
    notes = [];
    var code = button.getAttribute("data-code");
    show(codes.filter(function (c) { return c !== code; }));
  });

  if (addForm) {
    addForm.addEventListener("fc-pick", function (e) {
      var code = parseCodes(e.detail && e.detail.code)[0];
      if (code) add(code);
    });
  }

  var params = new URLSearchParams(window.location.search);
  var wanted = parseCodes(params.get("c"));
  var adding = parseCodes(params.get("add"))[0];
  if (adding) {
    var base = wanted.length ? wanted : stored();
    if (base.indexOf(adding) < 0 && base.length >= MAX) {
      notes.push("최대 " + MAX + "곳까지 놓을 수 있어 " + adding + " 을(를) 더하지 못했습니다. 한 곳을 빼고 다시 더하세요.");
    }
    wanted = addCode(base, adding);
  } else if (!params.has("c")) {
    // 메뉴로 들어오면(주소에 ?c= 없음) 지난번 고른 회사를 그대로 — 빈 목록으로 저장소를 지우지 않는다.
    wanted = stored();
  }
  show(wanted);
})();
