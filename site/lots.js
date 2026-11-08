/* ロット管理: 購入計画と保有銘柄の管理。保存先は Firebase(本人だけが読み書きできる)。
   架空データのデモで Firebase が未設定のときだけ、確認用にこの端末内へ保存する。 */
(function () {
  "use strict";
  const SDK = "https://www.gstatic.com/firebasejs/11.10.0/";
  const CODE_RE = /^[0-9][0-9A-Z]{3}$/;
  const CODE = { test: c => CODE_RE.test(c) || (!!api && !!api.data() && !!api.data().demo && /^[A-Z][0-9]{3}$/.test(c)) };  // 架空データのコードは英字始まり
  const UNIT = 100;
  const state = { status: "idle", email: "", lots: [], budget: 0, error: "", armed: null, focus: null, loaded: false };
  let api = null, backend = null, pendingPlan = false, lastHeld = "", watchSynced = false;

  const $ = id => document.getElementById(id);
  const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const int = n => Math.round(n).toLocaleString("ja-JP");
  const money = n => (n < 0 ? "−" : "") + int(Math.abs(n));
  const smoney = n => (n > 0 ? "+" : n < 0 ? "−" : "") + int(Math.abs(n));
  const spct = v => v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
  const norm = v => String(v ?? "").normalize("NFKC");
  const plain = v => norm(v).replace(/[^\d.]/g, "");
  const toNum = v => { const p = plain(v); if (p === "") return null; const n = Number(p); return Number.isFinite(n) ? n : null; };
  const fmt = v => { if (v == null || v === "") return ""; const [i, ...r] = plain(v).split("."); const h = i.replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+$)/g, ","); return r.length ? `${h}.${r.join("")}` : h; };
  const stock = code => api && api.data() ? api.data().stocks.find(s => s.c === code) : null;
  const nameOf = code => { const s = stock(code); return s ? s.n : code; };
  const amount = l => l.target > 0 && l.shares > 0 ? l.target * l.shares : 0;
  const qty = l => amount(l) > 0 ? +l.shares : 0;
  const sorted = () => [...state.lots].sort((a, b) => (a.order || 0) - (b.order || 0));

  function toast(msg) {
    let t = $("lotToast");
    if (!t) { t = document.createElement("div"); t.id = "lotToast"; t.setAttribute("role", "status"); document.body.appendChild(t); }
    t.textContent = msg; t.hidden = false;
    clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 3200);
  }
  const failed = () => toast("保存できませんでした。通信を確認して、もう一度お試しください。");

  /* ---------- 保存先 ---------- */
  async function firebaseBackend(cfg) {
    const [appM, authM, fs] = await Promise.all([
      import(SDK + "firebase-app.js"), import(SDK + "firebase-auth.js"), import(SDK + "firebase-firestore.js")]);
    const app = appM.initializeApp(cfg), auth = authM.getAuth(app), db = fs.getFirestore(app);
    let stops = [], uid = null;
    authM.onAuthStateChanged(auth, user => {
      stops.forEach(f => f()); stops = [];
      uid = user ? user.uid : null;
      if (!user) return onUser(null);
      onUser(user.email || "");
      const base = `users/${uid}`;
      stops = [
        fs.onSnapshot(fs.collection(db, base, "lots"), s => onLots(s.docs.map(d => ({ ...d.data(), id: d.id }))), onDataError),
        fs.onSnapshot(fs.doc(db, base, "settings", "plan"), d => onBudget(d.exists() ? d.data().budget || 0 : 0), onDataError),
        fs.onSnapshot(fs.doc(db, base, "settings", "watch"), d => onWatch(d.exists() ? d.data().codes || [] : []), onDataError),
      ];
    });
    const ref = id => fs.doc(db, `users/${uid}/lots`, id);
    return {
      signIn: (e, p) => authM.signInWithEmailAndPassword(auth, e, p),
      signOut: () => authM.signOut(auth),
      reset: e => authM.sendPasswordResetEmail(auth, e),
      newId: () => fs.doc(fs.collection(db, `users/${uid}/lots`)).id,
      add: (id, o) => fs.setDoc(ref(id), o),
      addMany: async list => { const b = fs.writeBatch(db); list.forEach(o => b.set(fs.doc(fs.collection(db, `users/${uid}/lots`)), o)); await b.commit(); },
      update: (id, p) => fs.updateDoc(ref(id), p),
      remove: id => fs.deleteDoc(ref(id)),
      setBudget: v => fs.setDoc(fs.doc(db, `users/${uid}/settings/plan`), { budget: v }),
      setWatch: codes => fs.setDoc(fs.doc(db, `users/${uid}/settings/watch`), { codes }),
    };
  }
  function demoBackend() {
    const KEY = "uneri.demoLots";
    let d = { lots: [], budget: 0, watch: [] };
    try { d = JSON.parse(localStorage.getItem(KEY)) || d; } catch (e) { /* 空で始める */ }
    const push = () => { try { localStorage.setItem(KEY, JSON.stringify(d)); } catch (e) { /* デモなので無視 */ } setTimeout(() => { onLots(d.lots.map(x => ({ ...x }))); onBudget(d.budget); onWatch(d.watch || []); }, 0); };
    let n = Date.now();
    setTimeout(() => { onUser("デモ(この端末だけに保存)"); push(); }, 0);
    return {
      signIn: async () => {}, signOut: async () => {}, reset: async () => {},
      newId: () => "d" + (n++),
      add: async (id, o) => { d.lots.push({ ...o, id }); push(); },
      addMany: async list => { list.forEach(o => d.lots.push({ ...o, id: "d" + (n++) })); push(); },
      update: async (id, p) => { Object.assign(d.lots.find(l => l.id === id), p); push(); },
      remove: async id => { d.lots = d.lots.filter(l => l.id !== id); push(); },
      setBudget: async v => { d.budget = v; push(); },
      setWatch: async codes => { d.watch = codes; push(); },
    };
  }

  /* ---------- 状態の変化 ---------- */
  function onUser(email) {
    state.email = email || ""; state.error = "";
    state.status = email === null ? "signedout" : "ready";
    if (email === null) { state.lots = []; state.budget = 0; state.loaded = false; watchSynced = false; }
    refresh();
  }
  function onLots(lots) { state.lots = lots; state.loaded = true; refresh(); }
  function onBudget(v) { state.budget = v; refresh(); }
  // ★(ウォッチ): ログイン直後だけ端末の★とまとめ、以後はFirebaseの内容に合わせる
  function onWatch(remote) {
    if (!watchSynced) {
      watchSynced = true;
      const merged = [...new Set([...api.getWatch(), ...remote])];
      api.setWatch(merged);
      if (merged.length !== new Set(remote).size) backend.setWatch(merged).catch(failed);
      return;
    }
    api.setWatch(remote);
  }
  function onDataError(e) {
    state.status = "error";
    state.error = e && e.code === "permission-denied"
      ? "データを読めません。Firebaseのセキュリティルールを確認してください。" : "データを読み込めません。通信を確認して、開き直してください。";
    refresh();
  }
  function refresh() {
    render();
    const held = JSON.stringify(sorted().map(l => [l.code, !!l.done]));
    const box = $("dLots");
    if (box && api.currentCode()) box.outerHTML = detailHtml(stock(api.currentCode()) || { c: api.currentCode() });
    if (held !== lastHeld) { lastHeld = held; api.renderList(); }
  }

  /* ---------- 画面 ---------- */
  function totals() {
    const lots = state.lots, b = state.budget, sum = (a, f) => a.reduce((n, x) => n + f(x), 0), bought = lots.filter(l => l.done);
    const doneAmt = sum(bought, amount), planAmt = sum(lots, amount);
    return { doneN: sum(bought, qty), doneAmt, left: b - doneAmt, planN: sum(lots, qty), planAmt, leftPlan: b - planAmt };
  }
  function byCode() {
    const map = new Map();
    sorted().forEach(l => {
      const o = map.get(l.code) || { code: l.code, name: l.name, s: 0, a: 0, ps: 0, pa: 0 }, n = qty(l), amt = amount(l);
      o.ps += n; o.pa += amt; if (l.done) { o.s += n; o.a += amt; }
      map.set(l.code, o);
    });
    return [...map.values()];
  }
  const ceil2 = v => Math.ceil(v * 100 - 1e-9) / 100;
  const two = v => v.toLocaleString("ja-JP", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

  function totalsHtml() {
    const t = totals(), b = state.budget, pct = v => b ? Math.min(100, v / b * 100) : 0;
    const box = (a, n, amt, left) => `<div class="tblwrap"><table class="lottotal"><thead><tr><th>${a}株数</th><th>${a}購入金額</th><th>${a}残金額</th></tr></thead>
      <tbody><tr><td>${int(n)}</td><td>${int(amt)}</td><td class="${left < 0 ? "lotneg" : ""}">${money(left)}</td></tr></tbody></table></div>`;
    return `<p class="ckh">購入済</p>${box("", t.doneN, t.doneAmt, t.left)}
      <p class="ckh">予定を含む</p>${box("", t.planN, t.planAmt, t.leftPlan)}
      <div class="lotmeter" role="img" aria-label="投資金額に対して購入済${pct(t.doneAmt).toFixed(1)}%、予定を含めて${pct(t.planAmt).toFixed(1)}%"><i class="a" style="width:${pct(t.doneAmt)}%"></i><i class="b" style="width:${Math.max(0, pct(t.planAmt) - pct(t.doneAmt))}%"></i></div>`;
  }
  function planHtml() {
    const rows = sorted().map(l => {
      const s = stock(l.code), price = s ? s.p : null, amt = amount(l);
      const pl = l.done && price && amt ? (price - l.target) * l.shares : null;
      return `<div class="lotrow">
        <div class="lothead">
          <input class="lotcode" id="c-${esc(l.id)}" data-id="${esc(l.id)}" data-f="code" maxlength="4" autocomplete="off" value="${esc(l.code)}" aria-label="証券コード">
          ${s ? `<button class="lotname" type="button" data-lot-open="${esc(l.code)}">${esc(s.n)}</button>`
              : `<input class="lotnamein" id="n-${esc(l.id)}" data-id="${esc(l.id)}" data-f="name" maxlength="40" value="${esc(l.name)}" aria-label="銘柄名">`}
          <span class="lotprice">${price ? "株価 " + esc(fmt(price)) : "株価 —"}</span>
        </div>
        <div class="lotfields">
          <label>購入金額(1株)<input class="lotin" id="a-${esc(l.id)}" data-id="${esc(l.id)}" data-f="target" type="text" inputmode="decimal" autocomplete="off" value="${esc(fmt(l.target))}"></label>
          <label>株数<input class="lotin" id="s-${esc(l.id)}" data-id="${esc(l.id)}" data-f="shares" type="text" inputmode="numeric" autocomplete="off" value="${esc(fmt(l.shares))}"></label>
          <label class="lotdone">購入済<input type="checkbox" data-lot-done="${esc(l.id)}" ${l.done ? "checked" : ""}></label>
        </div>
        <div class="lotfoot">
          <span>${amt ? "計 " + int(amt) + "円" : ""}${pl != null ? ` · 含み損益 <b class="${pl >= 0 ? "up" : "dn"}">${smoney(pl)}円</b>` : ""}</span>
          <span><a href="https://finance.yahoo.co.jp/quote/${encodeURIComponent(l.code)}.T/chart" target="_blank" rel="noopener">株価を見る</a>
          <button class="lotdel" type="button" data-lot-del="${esc(l.id)}">${state.armed === l.id ? "もう一度押すと削除" : "削除"}</button></span>
        </div></div>`;
    }).join("");
    return rows || `<p class="note" style="margin:0">${state.loaded ? "下にコードを入れるか、銘柄の詳細画面の「購入計画に追加」から追加できます。" : "読み込み中です…"}</p>`;
  }
  function analysisHtml() {
    const held = byCode().filter(o => o.s > 0);
    if (!held.length) return `<p class="note" style="margin:0">購入済にした銘柄の損益と分析がここに出ます。</p>`;
    let cost = 0, value = 0;
    const cards = held.map(o => {
      const s = stock(o.code), avg = o.a / o.s, now = s ? s.p : null, pl = now ? (now - avg) * o.s : null;
      cost += o.a; value += now ? now * o.s : o.a;
      let extra = "";
      if (s) {
        const op = s.earn && s.earn.rows ? s.earn.rows.find(r => r[0] === "営業利益") : null;
        const lv = api.distortion(s);
        const ph = s.ph === "up" ? "反発中" : s.ph === "down" ? "下落中" : "–";
        extra = `<div class="cstats">
          <span>${s.x10 ? "上場来" : "10年"}位置 <b>${(s.pos * 100).toFixed(0)}%</b></span><span>局面 <b>${ph} ${spct(s.leg)}</b></span>
          <span>谷 <b>${s.sw}回</b></span><span>信用倍率 <b>${s.mr != null ? s.mr.toFixed(2) + "倍" : "–"}</b></span>
          <span>営業益 <b>${op && op[2] != null ? spct(op[2]) : "–"}</b></span><span>歪み <b>${esc(lv)}</b></span></div>`;
      }
      return `<button class="lotcard" type="button" ${s ? `data-lot-open="${esc(o.code)}"` : "disabled"}>
        <div><span class="code">${esc(o.code)}</span><b>${esc(s ? s.n : o.name)}</b></div>
        <div class="cstats"><span>${int(o.s)}株</span><span>取得単価 <b>${two(ceil2(avg))}</b></span><span>現在 <b>${now ? fmt(now) : "—"}</b></span>
          <span>含み損益 <b class="${pl == null ? "" : pl >= 0 ? "up" : "dn"}">${pl == null ? "—" : smoney(pl) + "円(" + spct(now / avg - 1) + ")"}</b></span></div>${extra}</button>`;
    }).join("");
    const total = value - cost;
    return `<p class="lotsum">合計の含み損益 <b class="${total >= 0 ? "up" : "dn"}">${smoney(total)}円</b>${cost ? `(${spct(total / cost)})` : ""}<span class="note"> 評価額 ${int(value)}円</span></p>${cards}`;
  }
  function summaryHtml() {
    const sum = byCode();
    if (!sum.length) return `<p class="note" style="margin:0">購入計画を追加すると表示されます。</p>`;
    return `<div class="tblwrap"><table><thead><tr><th>コード</th><th>銘柄</th><th class="r">株数(予)</th><th class="r">金額(予)</th><th class="r">単価(予)</th></tr></thead>
      <tbody>${sum.map(o => `<tr><td>${esc(o.code)}</td><td>${esc(stock(o.code) ? stock(o.code).n : o.name)}</td><td class="r num">${int(o.ps)}</td><td class="r num">${int(o.pa)}</td><td class="r num">${o.ps ? two(o.pa / o.ps) : ""}</td></tr>`).join("")}</tbody></table></div>`;
  }
  function render() {
    const host = $("lotsView");
    if (!host || host.hidden) return;
    if (state.status === "none") {
      host.innerHTML = `<div class="stack" style="padding-top:14px"><section class="sec"><h3>ロット管理の設定が必要です</h3><p class="note" style="margin:0">保存先のFirebaseがまだ設定されていません。README.md の「ロット管理の設定」の手順で設定すると使えるようになります。</p></section></div>`;
      return;
    }
    if (state.status === "idle" || state.status === "loading") {
      host.innerHTML = `<div class="empty">読み込み中です…</div>`; return;
    }
    if (state.status === "error") {
      host.innerHTML = `<div class="stack" style="padding-top:14px"><section class="sec"><h3>読み込めません</h3><p class="note" style="margin:0">${esc(state.error)}</p></section></div>`; return;
    }
    if (state.status === "signedout") {
      if ($("lotLogin")) return;
      host.innerHTML = `<div class="lock"><form id="lotLogin"><h2>ロット管理にログイン</h2>
        <p class="note" style="margin:0">Firebaseに登録したメールアドレスとパスワードです。アプリを開くときのパスワードとは別です。</p>
        <input type="email" id="lotEmail" autocomplete="username" autocapitalize="off" required aria-label="メールアドレス" placeholder="メールアドレス">
        <input type="password" id="lotPw" autocomplete="current-password" required aria-label="パスワード" placeholder="パスワード">
        <p class="err" id="lotErr"></p>
        <button class="btn primary" type="submit">ログイン</button>
        <button class="btn" type="button" id="lotReset">パスワードを忘れたとき</button></form></div>`;
      return;
    }
    if (!$("lotPlan")) {
      host.innerHTML = `<div class="stack" style="padding-top:14px">
        ${api.data() && api.data().demo && !window.UNERI_FIREBASE ? `<p class="demo" style="margin:0">デモ表示です。入力はこの端末だけに保存されます。</p>` : ""}
        <section class="sec"><h3>投資金額</h3>
          <input class="lotbudget" id="lotBudget" type="text" inputmode="numeric" autocomplete="off" aria-label="投資金額">
          <div id="lotTotals"></div></section>
        <section class="sec"><h3>購入計画 <span>株価は毎日の更新時の終値</span></h3>
          <div id="lotPlan" class="lotlist"></div>
          <label class="lotadd">銘柄を追加<input class="lotcode" id="lotNew" maxlength="4" autocomplete="off" placeholder="コード"></label>
          <p class="note" style="margin:0">購入金額(1株あたり)と株数を入れると、チェックなしは予定、チェックありは購入済として集計します。株数は100株単位です。</p></section>
        <section class="sec"><h3>保有銘柄の分析 <span>タップで詳細</span></h3><div id="lotAnalysis" class="lotlist"></div></section>
        <section class="sec"><h3>銘柄別(予定を含む)</h3><div id="lotSummary"></div></section>
        <section class="sec"><h3>データ</h3>
          <div class="lotbtns"><button class="btn" type="button" id="lotCsv">CSVで保存</button>
          <label class="btn lotfile">CSVを読み込む<input type="file" id="lotImport" accept=".csv,text/csv"></label>
          <button class="btn" type="button" id="lotOut">ログアウト</button></div>
          <p class="note" style="margin:0" id="lotWho"></p></section>
      </div>`;
    }
    if (document.activeElement !== $("lotBudget")) $("lotBudget").value = state.budget ? int(state.budget) : "";
    $("lotTotals").innerHTML = totalsHtml();
    $("lotAnalysis").innerHTML = analysisHtml();
    $("lotSummary").innerHTML = summaryHtml();
    $("lotWho").textContent = "ログイン中: " + state.email;
    renderPlan();
  }
  // 入力中に行を作り直すと入力が消えるので、計画の欄だけ入力が終わるまで待つ
  function renderPlan() {
    const plan = $("lotPlan");
    if (!plan) return;
    const a = document.activeElement;
    if (a && plan.contains(a) && a.tagName === "INPUT" && a.type !== "checkbox") { pendingPlan = true; return; }
    pendingPlan = false;
    plan.innerHTML = planHtml();
    if (state.focus && $(state.focus)) { $(state.focus).focus(); state.focus = null; }
  }
  function detailHtml(s) {
    if (!["ready"].includes(state.status) && state.status !== "signedout") return `<div id="dLots" hidden></div>`;
    if (state.status === "signedout") return `<section class="sec" id="dLots"><h3>ロット管理</h3><p class="note" style="margin:0">「ロット」タブでログインすると、この銘柄を購入計画に追加できます。</p></section>`;
    const mine = state.lots.filter(l => l.code === s.c), done = mine.filter(l => l.done), plan = mine.filter(l => !l.done);
    const dn = done.reduce((n, l) => n + qty(l), 0), da = done.reduce((n, l) => n + amount(l), 0), pn = plan.reduce((n, l) => n + qty(l), 0);
    const pl = dn && s.p ? s.p * dn - da : null;
    return `<section class="sec" id="dLots"><h3>ロット管理</h3>
      ${mine.length ? `<div class="cstats">${dn ? `<span>保有 <b>${int(dn)}株</b></span><span>取得単価 <b>${two(ceil2(da / dn))}</b></span>${pl != null ? `<span>含み損益 <b class="${pl >= 0 ? "up" : "dn"}">${smoney(pl)}円(${spct(pl / da)})</b></span>` : ""}` : ""}${plan.length ? `<span>予定 <b>${plan.length}件${pn ? ` ${int(pn)}株` : ""}</b></span>` : ""}</div>` : ""}
      <button class="btn primary" type="button" data-lot-add="${esc(s.c)}">購入計画に追加</button></section>`;
  }

  /* ---------- 操作 ---------- */
  const errMsg = e => {
    const c = e && e.code || "";
    if (/invalid-credential|wrong-password|user-not-found|invalid-email/.test(c)) return "メールアドレスかパスワードが違います。";
    if (/too-many-requests/.test(c)) return "試行回数が多すぎます。しばらく待ってからお試しください。";
    if (/network/.test(c)) return "通信できません。接続を確認してください。";
    if (/api-key|configuration-not-found|operation-not-allowed/.test(c)) return "Firebaseの設定を確認してください(" + c + ")。";
    return "ログインできませんでした(" + (c || "原因不明") + ")。";
  };
  function addLot(code, focus) {
    const id = backend.newId();
    if (focus) state.focus = "a-" + id;
    return backend.add(id, { code, name: nameOf(code), target: null, shares: null, done: false, order: Date.now() });
  }
  document.addEventListener("submit", async ev => {
    if (ev.target.id !== "lotLogin") return;
    ev.preventDefault();
    const err = $("lotErr");
    err.textContent = "確認しています…";
    try { await backend.signIn($("lotEmail").value.trim(), $("lotPw").value); if ($("lotErr")) $("lotErr").textContent = ""; }
    catch (e) { err.textContent = errMsg(e); }
  });
  document.addEventListener("change", ev => {
    const el = ev.target, d = el.dataset || {};
    if (!backend) return;
    if (el.id === "lotBudget") {
      const v = toNum(el.value) || 0;
      el.value = v ? int(v) : "";
      backend.setBudget(v).catch(failed);
    } else if (el.id === "lotNew") {
      const code = norm(el.value).trim().toUpperCase();
      if (!code) return;
      if (!CODE.test(code)) return toast("証券コードは4桁で入力してください。例 7203、130A");
      el.value = "";
      addLot(code, true).catch(failed);
    } else if (d.lotDone) {
      const box = id => toNum(($(id) || {}).value) > 0;
      if (el.checked && !box("a-" + d.lotDone)) { el.checked = false; toast("購入済にするには、先に購入金額(1株あたり)を入力してください"); return $("a-" + d.lotDone).focus(); }
      if (el.checked && !box("s-" + d.lotDone)) { el.checked = false; toast("購入済にするには、先に株数を入力してください"); return $("s-" + d.lotDone).focus(); }
      backend.update(d.lotDone, { done: el.checked }).catch(failed);
    } else if (d.f === "code") {
      const code = norm(el.value).trim().toUpperCase();
      const l = state.lots.find(x => x.id === d.id);
      if (!CODE.test(code)) { toast("証券コードは4桁で入力してください。例 7203、130A"); el.value = l ? l.code : ""; return; }
      backend.update(d.id, { code, name: nameOf(code) }).catch(failed);
    } else if (d.f === "name") {
      const l = state.lots.find(x => x.id === d.id);
      backend.update(d.id, { name: el.value.trim() || (l ? l.code : "") }).catch(failed);
    } else if (d.f === "target" || d.f === "shares") {
      const l = state.lots.find(x => x.id === d.id);
      let v = toNum(el.value);
      if (d.f === "shares" && v !== null) {
        const rounded = Math.floor(v / UNIT) * UNIT;
        if (rounded < UNIT) { toast("株数は100株単位で入力してください"); el.value = fmt(l && l.shares); return; }
        if (rounded !== v) toast(`100株単位に合わせて${int(rounded)}株にしました`);
        v = rounded;
      }
      el.value = fmt(v);
      backend.update(d.id, { [d.f]: v }).catch(failed);
    } else if (el.id === "lotImport") {
      const f = el.files && el.files[0];
      el.value = "";
      if (f) f.text().then(importCsv).catch(() => toast("ファイルを読めませんでした。"));
    }
  });
  document.addEventListener("focusout", ev => {
    if (pendingPlan && ev.target.closest && ev.target.closest("#lotPlan")) setTimeout(renderPlan, 120);
  });
  document.addEventListener("click", ev => {
    const t = ev.target;
    const open = t.closest && t.closest("[data-lot-open]");
    if (open) return api.openDetail(open.dataset.lotOpen);
    const add = t.closest && t.closest("[data-lot-add]");
    if (add && backend) {
      addLot(add.dataset.lotAdd, false).then(() => toast("購入計画に追加しました。「ロット」タブで金額と株数を入れてください。")).catch(failed);
      return;
    }
    const del = t.closest && t.closest("[data-lot-del]");
    if (del && backend) {
      const id = del.dataset.lotDel;
      if (state.armed !== id) { state.armed = id; renderPlan(); return; }
      state.armed = null;
      backend.remove(id).catch(failed);
      return;
    }
    if (t.id === "lotOut") return backend.signOut().catch(failed);
    if (t.id === "lotCsv") return exportCsv();
    if (t.id === "lotReset") {
      const e = $("lotEmail").value.trim();
      if (!e) return toast("先にメールアドレスを入れてください");
      backend.reset(e).then(() => toast("再設定のメールを送りました。")).catch(() => toast("送れませんでした。アドレスを確認してください。"));
    }
  });

  /* ---------- CSV(今のロット管理と同じ形式) ---------- */
  const cell = v => { const s = String(v ?? ""); return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
  function toCsv() {
    const rows = sorted().map(l => {
      const s = stock(l.code);
      return [l.code, s ? s.n : l.name, s ? s.p : "", l.shares ?? "", amount(l) || "", l.target ?? "", l.done ? "購入済" : ""].map(cell).join(",");
    });
    return "﻿" + ["コード,銘柄,株価,株数,購入総額,1株あたり購入金額,購入済", ...rows].join("\r\n");
  }
  async function exportCsv() {
    const blob = new Blob([toCsv()], { type: "text/csv" }), name = "lot-kanri.csv";
    try {
      const file = new File([blob], name, { type: "text/csv" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file] }); return; }
    } catch (e) { if (e && e.name === "AbortError") return; }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 5000);
  }
  function parseCsv(text) {
    const rows = []; let row = [], cur = "", q = false;
    const s = text.replace(/^﻿/, "");
    for (let i = 0; i < s.length; i++) {
      const c = s[i];
      if (q) { if (c === '"' && s[i + 1] === '"') { cur += '"'; i++; } else if (c === '"') q = false; else cur += c; }
      else if (c === '"') q = true;
      else if (c === ",") { row.push(cur); cur = ""; }
      else if (c === "\n" || c === "\r") { if (c === "\r" && s[i + 1] === "\n") i++; row.push(cur); cur = ""; if (row.some(x => x.trim())) rows.push(row); row = []; }
      else cur += c;
    }
    row.push(cur); if (row.some(x => x.trim())) rows.push(row);
    return rows;
  }
  function importCsv(text) {
    const rows = parseCsv(text);
    const h = (rows.shift() || []).map(x => x.trim()), ix = n => h.indexOf(n);
    if (ix("コード") < 0) return toast("CSVの形式が違います(「コード」の列がありません)。");
    let bad = 0; const base = Date.now();
    const list = [];
    rows.forEach((r, i) => {
      const code = norm(r[ix("コード")]).trim().toUpperCase();
      if (!CODE.test(code)) { bad++; return; }
      const v = n => ix(n) >= 0 ? toNum(r[ix(n)]) : null;
      list.push({ code, name: stock(code) ? stock(code).n : (ix("銘柄") >= 0 && r[ix("銘柄")] || code).trim(), target: v("1株あたり購入金額"), shares: v("株数"),
        done: ix("購入済") >= 0 && (r[ix("購入済")] || "").trim() === "購入済", order: base + i });
    });
    if (!list.length) return toast("読み込める行がありませんでした。");
    if (list.length > 400) return toast("一度に読み込めるのは400行までです。");
    if (!window.confirm(`${list.length}行を購入計画に追加します。今ある行はそのまま残ります。よろしいですか?`)) return;
    backend.addMany(list).then(() => toast(`${list.length}行を読み込みました${bad ? `(読めなかった行 ${bad})` : ""}。`)).catch(failed);
  }

  /* ---------- 公開する関数 ---------- */
  window.Lots = {
    init(a) {
      api = a;
      const cfg = window.UNERI_FIREBASE;
      if (cfg && cfg.apiKey) {
        state.status = "loading";
        firebaseBackend(cfg).then(b => { backend = b; }).catch(e => {
          state.status = "error"; state.error = "ロット管理の部品を読み込めませんでした。通信を確認して開き直してください。"; console.error(e); render();
        });
      } else if (a.data() && a.data().demo) {
        backend = demoBackend();
      } else {
        state.status = "none";
      }
    },
    render,
    detailHtml,
    saveWatch(codes) { if (backend && watchSynced) backend.setWatch(codes).catch(failed); },
    badge(code) {
      if (state.status !== "ready") return "";
      const mine = state.lots.filter(l => l.code === code);
      if (!mine.length) return "";
      return mine.some(l => l.done) ? `<span class="lotbadge held">保有</span>` : `<span class="lotbadge plan">予定</span>`;
    },
    // テスト用
    _state: state,
    _onWatch: codes => onWatch(codes),
  };
})();
