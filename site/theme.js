/* 画面の色と明るさ。この端末に保存し、すべてのページで共通。読み込みの最初に適用してちらつきを防ぐ。 */
(function () {
  "use strict";
  var KEY = "uneri.theme", COLORS = { ai: 1, daidai: 1, ki: 1, midori: 1, ao: 1, "ai-blue": 1, murasaki: 1, sumi: 1 }, MODES = { auto: 1, light: 1, dark: 1 };
  var t = {};
  try { t = JSON.parse(localStorage.getItem(KEY)) || {}; } catch (e) { t = {}; }
  var root = document.documentElement;
  function norm(v) { return { color: COLORS[v.color] ? v.color : "ai", mode: MODES[v.mode] ? v.mode : "auto" }; }
  function apply() {
    var v = norm(t);
    if (v.color === "ai") root.removeAttribute("data-color"); else root.setAttribute("data-color", v.color);
    if (v.mode === "auto") root.removeAttribute("data-theme"); else root.setAttribute("data-theme", v.mode);
    // ブラウザの上部の色も合わせる
    var m = document.querySelector('meta[name="theme-color"]');
    if (m) m.setAttribute("content", (getComputedStyle(root).getPropertyValue("--sheet") || "").trim() || "#b8232c");
  }
  apply();
  document.addEventListener("DOMContentLoaded", apply);
  window.UneriTheme = {
    get: function () { return norm(t); },
    set: function (p) {
      t = norm(Object.assign({}, t, p));
      try { localStorage.setItem(KEY, JSON.stringify(t)); } catch (e) { /* 保存できなくても今の画面には反映する */ }
      apply();
    },
  };
})();
