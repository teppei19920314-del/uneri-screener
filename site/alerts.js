/* お知らせ: プッシュ通知の設定と、届いたお知らせの一覧 */
(function () {
  "use strict";
  let api = null, subscribed = null, busy = false, newKey = "";
  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const cfg = () => window.UNERI_PUSH || null;
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* 保存できなくても続ける */ } },
  };
  const supported = () => "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const isIOS = () => /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const standalone = () => window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  const b64ToBytes = s => { const b = atob((s + "=".repeat((4 - s.length % 4) % 4)).replace(/-/g, "+").replace(/_/g, "/")); return Uint8Array.from(b, c => c.charCodeAt(0)); };
  async function subId(endpoint) {
    const h = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(endpoint));
    return [...new Uint8Array(h)].map(b => b.toString(16).padStart(2, "0")).join("").slice(0, 24);
  }
  function toast(m) { if (window.Lots && Lots.toast) Lots.toast(m); }
  const reg = () => navigator.serviceWorker.ready;

  async function checkSub() {
    if (!supported() || !cfg()) { subscribed = false; return; }
    try { subscribed = !!(await (await reg()).pushManager.getSubscription()); } catch (e) { subscribed = false; }
  }

  /* ---------- 通知の設定 ---------- */
  function settingsHtml() {
    const c = cfg();
    if (!c || !c.publicKey) {
      return `<p class="note" style="margin:0">通知の準備がまだです。下のボタンで通知用の鍵を作り、GitHub の Secrets に <b>VAPID_PRIVATE_KEY</b> という名前で保存してから、「Run workflow」で更新してください。</p>
        <button class="btn" type="button" id="pushKeygen">通知用の鍵を作る</button>
        ${newKey ? `<div class="keybox"><p class="note" style="margin:0">この文字列を、そのまま Secrets の値に貼り付けてください。<b>他の人に見せないでください。</b>この画面を閉じると消えます(作り直せば別の鍵になります)。</p>
          <textarea id="pushKey" readonly rows="2">${esc(newKey)}</textarea>
          <button class="btn" type="button" id="pushKeyCopy">コピーする</button></div>` : ""}`;
    }
    const ready = window.Lots && Lots.isReady();
    if (!ready) return `<p class="note" style="margin:0">「ロット」タブでログインすると、通知をオンにできます(通知はログインした本人の端末に届きます)。</p>`;
    if (!supported() || (isIOS() && !standalone())) {
      return `<p class="note" style="margin:0">${isIOS() ? "iPhoneでは、Safariの共有ボタン →「ホーム画面に追加」で追加したアプリから開くと、通知をオンにできます(iOS 16.4以降)。" : "このブラウザは通知に対応していません。"}</p>`;
    }
    const warn = c.ready ? "" : `<p class="note warnline" style="margin:0">送る側の設定がまだです。GitHub の Secrets に NOTIFY_EMAIL と NOTIFY_PASSWORD(ロット管理のログイン情報)を登録するまで、通知は届きません。</p>`;
    if (Notification.permission === "denied") {
      return warn + `<p class="note" style="margin:0">通知が許可されていません。iPhoneの「設定」→「通知」→ このアプリ で許可してください。</p>`;
    }
    if (subscribed === null) return `<p class="note" style="margin:0">確認しています…</p>`;
    return warn + (subscribed
      ? `<p class="pushon">この端末で通知はオンです</p>
         <div class="lotbtns"><button class="btn" type="button" id="pushTest">テスト通知</button><button class="btn" type="button" id="pushOff">オフにする</button></div>`
      : `<button class="btn primary" type="button" id="pushOn" ${busy ? "disabled" : ""}>この端末で通知をオンにする</button>`);
  }

  async function turnOn() {
    busy = true; render();
    try {
      const perm = await Notification.requestPermission();
      if (perm !== "granted") { toast("通知が許可されませんでした"); return; }
      const r = await reg();
      const sub = (await r.pushManager.getSubscription()) ||
        await r.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToBytes(cfg().publicKey) });
      const j = sub.toJSON();
      await Lots.savePush(await subId(j.endpoint), { endpoint: j.endpoint, keys: j.keys, device: navigator.userAgent.slice(0, 120), created: new Date().toISOString() });
      subscribed = true;
      toast("通知をオンにしました。次の更新から届きます");
    } catch (e) {
      console.error(e);
      toast("通知をオンにできませんでした(" + (e && e.name || "エラー") + ")");
    } finally { busy = false; render(); }
  }
  async function turnOff() {
    try {
      const sub = await (await reg()).pushManager.getSubscription();
      if (sub) { await Lots.removePush(await subId(sub.endpoint)); await sub.unsubscribe(); }
      subscribed = false; toast("この端末の通知をオフにしました");
    } catch (e) { toast("オフにできませんでした"); }
    render();
  }
  async function testNotice() {
    try {
      const r = await reg();
      await r.showNotification("うねり底(テスト)", { body: "通知はこのように届きます。タップするとお知らせが開きます。", tag: "uneri-test", data: { url: new URL("index.html#alerts", location.href).href }, icon: "icon-180.png" });
    } catch (e) { toast("テスト通知を出せませんでした"); }
  }
  async function keygen() {
    const k = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, ["sign"]);
    newKey = (await crypto.subtle.exportKey("jwk", k.privateKey)).d;
    render();
  }

  /* ---------- お知らせの一覧 ---------- */
  function listHtml() {
    if (!(window.Lots && Lots.isReady())) return `<p class="note" style="margin:0">ログインすると、届いたお知らせが日付ごとに表示されます。</p>`;
    const days = Lots.alerts();
    if (!days.length) return `<p class="note" style="margin:0">まだお知らせはありません。毎日の更新のあと、★銘柄や購入予定が条件に当てはまると、ここに追加されて通知が届きます。</p>`;
    const data = api.data(), find = c => data && data.stocks.find(s => s.c === c);
    return days.map(d => `<div class="alertday"><p class="ckh">${esc(d.date)}</p>${(d.items || []).map(i => {
      const s = find(i.code);
      return `<button class="lotcard" type="button" ${s ? `data-alert-open="${esc(i.code)}"` : "disabled"}>
        <div><span class="lotbadge ${i.kind === "target" ? "plan" : "held"}">${i.kind === "target" ? "購入予定" : "★"}</span> <span class="code">${esc(i.code)}</span><b>${esc(i.name)}</b></div>
        <div class="note" style="color:var(--ink)">${esc(i.msg)}</div>
        ${s ? `<div class="cstats"><span>いまの株価 <b>${Number(s.p).toLocaleString("ja-JP")}円</b></span><span>${s.x10 ? "上場来" : "10年"}位置 <b>${(s.pos * 100).toFixed(0)}%</b></span></div>` : ""}</button>`;
    }).join("")}</div>`).join("");
  }

  function render() {
    const host = $("alertsView");
    if (!host || host.closest("[hidden]")) return;
    host.innerHTML = `<div class="stack" style="padding-top:12px">
      <section class="sec"><h3>通知 <span>毎日の更新のあとに届きます</span></h3>${settingsHtml()}</section>
      <section class="sec"><h3>お知らせ <span>タップで詳細</span></h3><div class="lotlist">${listHtml()}</div></section>
      <section class="sec"><h3>どんなときに届くか</h3>
        <ul class="checks">
          <li><span class="ok">★</span><span>★を付けた銘柄が、過去の谷の±15%(底値帯)に入ったとき</span></li>
          <li><span class="ok">★</span><span>★を付けた銘柄の10年レンジの位置が15%以下になったとき</span></li>
          <li><span class="ok">★</span><span>★を付けた銘柄が、10年位置30%以下で13週線を上回ったとき(反転サイン)</span></li>
          <li><span class="ok">予</span><span>購入予定(ロットの購入済でない行)の株価が、入力した購入金額(1株)以下になったとき</span></li>
        </ul>
        <p class="note" style="margin:0">同じ状態が続いている間は、くり返し通知しません。当てはまるものがない日は届きません。株価は毎日の更新時の終値です。</p></section>
    </div>`;
    const latest = (window.Lots && Lots.isReady() && Lots.alerts()[0]) ? Lots.alerts()[0].date : "";
    if (latest) store.set("uneri.alertsSeen", latest);
    dot();
  }
  function dot() {
    const b = $("bellDot");
    if (!b) return;
    const days = window.Lots && Lots.isReady() ? Lots.alerts() : [];
    b.hidden = !(days.length && days[0].date > store.get("uneri.alertsSeen", ""));
  }

  document.addEventListener("click", e => {
    const t = e.target;
    if (t.id === "pushOn") turnOn();
    else if (t.id === "pushOff") turnOff();
    else if (t.id === "pushTest") testNotice();
    else if (t.id === "pushKeygen") keygen().catch(() => toast("鍵を作れませんでした"));
    else if (t.id === "pushKeyCopy") navigator.clipboard.writeText(newKey).then(() => toast("コピーしました"), () => { $("pushKey").select(); });
    else { const o = t.closest && t.closest("[data-alert-open]"); if (o) api.openDetail(o.dataset.alertOpen); }
  });

  window.Alerts = {
    init(a) {
      api = a;
      if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("sw.js").then(() => checkSub().then(render)).catch(() => { subscribed = false; render(); });
        navigator.serviceWorker.addEventListener("message", ev => { if (ev.data && ev.data.open) { location.hash = "#alerts"; setTimeout(() => { const h = $("alertsView"); if (h) h.scrollIntoView(); }, 300); } });
      } else subscribed = false;
    },
    render, changed() { render(); dot(); },
    _setKeyForTest(k) { newKey = k; },
  };
})();
