/**
 * 第四轮验证：用户报告的 7 条问题逐条验证。
 *   1 算法枚举重复（N·m / m·N）
 *   2 积分生成缺失（与求导成对）
 *   3 定义式缺失 → 空位 + 补录
 *   4 输入即匹配 + 多字匹配
 *   5 页面槽位统一
 *   6 历史记录移入搜索框
 *   7 前进 / 后退
 * 用法：node verify_round4.js [baseUrl] [outDir]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = process.argv[3] || path.join(__dirname, "ui-shots");
const BIN = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

function httpJson(u) {
  return new Promise((res, rej) => {
    http.get(u, r => { let b = ""; r.on("data", d => b += d);
      r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); })
      .on("error", rej);
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), "dimr4-"));
  const port = 9351;
  const ch = spawn(BIN, ["--headless=new","--disable-gpu","--no-first-run",
    "--remote-debugging-port="+port,"--user-data-dir="+ud,
    "--window-size=1500,1000","about:blank"], { stdio: "ignore" });

  const problems = [];
  const note = (ok, msg) => { if (!ok) problems.push(msg); };
  const JSERR = [];
  let cdp = null;
  try {
    let v = null;
    for (let i = 0; i < 60; i++) {
      try { v = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (e) { await sleep(300); }
    }
    if (!v) throw new Error("CDP 未就绪");
    const tg = await httpJson(`http://127.0.0.1:${port}/json/list`);
    const sock = new WebSocket(tg.find(t => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r, j) => {
      sock.addEventListener("open", r, { once: true });
      sock.addEventListener("error", j, { once: true });
    });
    let id = 0; const pend = new Map(); const events = [];
    sock.addEventListener("message", e => {
      const m = JSON.parse(e.data.toString());
      if (m.id && pend.has(m.id)) {
        const { res, rej } = pend.get(m.id); pend.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) events.push(m);
    });
    const send = (method, params) => new Promise((res, rej) => {
      const i = ++id; pend.set(i, { res, rej });
      sock.send(JSON.stringify({ id: i, method, params: params || {} }));
    });
    const ev = async x => {
      const r = await send("Runtime.evaluate",
        { expression: x, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) {
        const d = r.exceptionDetails;
        throw new Error("页面求值异常: " + String(
          (d.exception && (d.exception.description || d.exception.value)) || d.text
        ).split("\n")[0]);
      }
      return r.result.value;
    };
    const shot = async (name, clip) => {
      const h = clip || await ev(
        "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)");
      await send("Emulation.setDeviceMetricsOverride",
        { width: 1500, height: Math.min(Math.max(h, 600), 16000),
          deviceScaleFactor: 1, mobile: false });
      await sleep(220);
      const r = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      await send("Emulation.setDeviceMetricsOverride",
        { width: 1500, height: 1000, deviceScaleFactor: 1, mobile: false });
    };
    const waitFor = async (expr, tries) => {
      for (let i = 0; i < (tries || 50); i++) {
        if (await ev(expr)) return true;
        await sleep(140);
      }
      return false;
    };
    const doSearch = async q => {
      await ev(`(() => { const i=document.getElementById('q');
        i.value=${JSON.stringify(q)}; document.getElementById('go').click(); return 1; })()`);
      await waitFor("document.querySelectorAll('.card,.sugpanel,.err').length>0");
      await sleep(320);
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Log.enable");
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(1800);

    // ============ 5. 页面槽位统一 ============
    console.log("=== 5. 页面槽位统一 ===");
    const SLOTS = ["常见写法", "同量纲异构", "定义式", "等价写法",
                   "由物理式子生成", "相关物理式子"];
    const slotSurvey = async () => ev(`(() => {
      const out = [];
      document.querySelectorAll('.card').forEach(c => {
        const titles = Array.from(c.querySelectorAll('section > h3'))
          .map(h => (h.childNodes[0]||{}).textContent || '');
        out.push({
          zh: (c.querySelector('.zh')||{}).textContent || '',
          titles: titles,
          na: c.querySelectorAll('.slotna').length,
          miss: c.querySelectorAll('.slotmiss').length,
        });
      });
      return out;
    })()`);
    const slotWords = SLOTS;
    for (const q of ["力矩", "电场强度", "频率", "长度", "能量"]) {
      await doSearch(q);
      const cards = await slotSurvey();
      const first = cards[0];
      if (!first) { note(false, `槽位: 「${q}」没有渲染出卡片`); continue; }
      const missing = slotWords.filter(s => !first.titles.includes(s));
      console.log(`  ${q.padEnd(6)} 槽位 ${first.titles.length} 个 · 不适用 ${first.na}` +
                  ` · 暂缺 ${first.miss}` +
                  (missing.length ? ` · !! 缺少 ${missing.join("/")}` : ""));
      note(missing.length === 0,
           `槽位: 「${q}」缺少槽位 ${missing.join("/")}（实际 ${first.titles.join("|")}）`);
    }
    const zeroNa = await ev(`(() => {
      // 每个卡片都必须同时有 6 个槽位标题——数量恒定是这一轮的核心要求
      const want = ${JSON.stringify(SLOTS)};
      const bad = [];
      document.querySelectorAll('.card').forEach(c => {
        const t = Array.from(c.querySelectorAll('section > h3'))
          .map(h => (h.childNodes[0]||{}).textContent || '');
        want.forEach(w => { if (!t.includes(w)) bad.push(((c.querySelector('.zh')||{}).textContent||'?') + ':' + w); });
      });
      return bad;
    })()`);
    note(zeroNa.length === 0, `槽位: 有卡片缺槽位 ${zeroNa.slice(0,6).join(", ")}`);
    await shot("r4-统一槽位.png");

    // ============ 1. 算法枚举不重复 ============
    console.log("\n=== 1. 算法枚举重复 ===");
    await doSearch("能量");
    await ev(`(() => { const b=document.querySelector('.morebtn');
      if (b) b.click(); return 1; })()`);
    await waitFor("document.querySelector('section.searched .branch.search')");
    await sleep(300);
    const dupInfo = await ev(`(() => {
      // 归一化：把乘法因子排序、把 a/b 变成 a·b^-1，再比较
      const canon = s => {
        s = String(s).replace(/\\s+/g, '');
        // 拆分子分母
        let num = s, den = '';
        const i = s.indexOf('/');
        if (i >= 0) { num = s.slice(0, i); den = s.slice(i + 1).replace(/^[(]|[)]$/g, ''); }
        const atoms = [];
        const push = (t, sign) => {
          t.split(/[·*]/).filter(Boolean).forEach(f => {
            const m = f.match(/^(.+?)\\^(-?\\d+)$/);
            const b = m ? m[1] : f;
            const e = (m ? parseInt(m[2], 10) : 1) * sign;
            atoms.push([b, e]);
          });
        };
        push(num, 1); if (den) push(den, -1);
        atoms.sort((a, b) => a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
        return atoms.map(([b, e]) => b + '^' + e).join('·');
      };
      const eq = Array.from(document.querySelectorAll('.card .branch.equivalence .expr'))
        .map(e => canon(e.textContent));
      const gen = Array.from(document.querySelectorAll('.card .branch.derivative .expr, .card .branch.integral .expr'))
        .map(e => canon(e.textContent));
      const se = Array.from(document.querySelectorAll('.card section.searched .branch.search .expr'))
        .map(e => ({ raw: e.textContent, k: canon(e.textContent) }));
      const seen = {}; const selfdup = [];
      se.forEach(x => { if (seen[x.k]) selfdup.push(x.raw + ' ≈ ' + seen[x.k]); seen[x.k] = x.raw; });
      const shown = new Set([...eq, ...gen]);
      const clash = se.filter(x => shown.has(x.k)).map(x => x.raw);
      return { eq, gen, se: se.map(x => x.raw), selfdup, clash };
    })()`);
    console.log("  等价:", dupInfo.eq.join(" | "));
    console.log("  生成:", dupInfo.gen.join(" | "));
    console.log("  枚举:", dupInfo.se.join(" | "));
    note(dupInfo.selfdup.length === 0,
         `枚举内部重复: ${dupInfo.selfdup.join(" ; ")}`);
    note(dupInfo.clash.length === 0,
         `枚举与已有写法重复: ${dupInfo.clash.join(" ; ")}`);
    console.log("  枚举内部重复:", dupInfo.selfdup.length,
                "| 与已有写法重复:", dupInfo.clash.length);

    // ============ 2. 求导 / 积分成对 ============
    console.log("\n=== 2. 求导 / 积分成对 ===");
    await doSearch("能量");
    const genInfo = await ev(`(() => {
      const rows = Array.from(document.querySelectorAll('.branch.derivative, .branch.integral'));
      return rows.map(r => ({
        kind: r.classList.contains('integral') ? 'integral' : 'derivative',
        expr: (r.querySelector('.expr')||{}).textContent || '',
        inverse: !!r.querySelector('.badge.inverse'),
        formula: (r.querySelector('.via .formula')||{}).textContent || '',
      }));
    })()`);
    console.log("  能量页面生成分支:");
    genInfo.forEach(g => console.log(`    [${g.kind}] ${g.expr}` +
      (g.inverse ? " （反读）" : "") + "  ← " + g.formula));
    note(genInfo.some(g => g.kind === "integral"),
         "积分: 「能量」页面没有任何积分生成分支");
    note(genInfo.some(g => g.kind === "integral" && g.inverse),
         "积分: 反读出来的积分分支没有 badge.inverse 标记");
    // 对称性：全库统计
    const pairStat = await (async () => {
      const r = await httpJson(BASE + "/api/quantities-stats").catch(() => null);
      return r;
    })();
    // 用几个页面横向核对
    const stat = {};
    for (const q of ["能量", "速度", "电势", "电荷量", "角动量"]) {
      await doSearch(q);
      const g = await ev(`(() => {
        const d = document.querySelectorAll('.branch.derivative').length;
        const i = document.querySelectorAll('.branch.integral').length;
        return {d, i};
      })()`);
      stat[q] = g;
      console.log(`  ${q.padEnd(6)} 求导 ${g.d} · 积分 ${g.i}`);
    }
    const withIntegral = Object.values(stat).filter(v => v.i > 0).length;
    note(withIntegral >= 3, `积分: 抽查的 5 个页面里只有 ${withIntegral} 个有积分分支`);

    // ============ 3. 定义式三态 ============
    console.log("\n=== 3. 定义式三态（有 / 不适用 / 暂缺）===");
    await doSearch("力");
    const defHas = await ev(`(() => {
      const s = Array.from(document.querySelectorAll('section.defs'))[0];
      return { rows: document.querySelectorAll('.defrow').length,
               na: document.querySelectorAll('section.defs .slotna').length,
               miss: document.querySelectorAll('section.defs .slotmiss').length,
               txt: (s ? s.textContent : '').slice(0, 60) };
    })()`);
    console.log("  力  : 定义式行", defHas.rows, "| 不适用", defHas.na, "| 暂缺", defHas.miss);
    note(defHas.rows > 0, "定义式: 「力」页面没有定义式行");

    await doSearch("长度");
    const defNa = await ev(`(() => ({
      rows: document.querySelectorAll('.defrow').length,
      na: document.querySelectorAll('section.defs .slotna').length,
      state: (document.querySelector('section.defs .state')||{}).textContent || '',
      txt: (document.querySelector('section.defs .slotna')||{}).textContent || '',
    }))()`);
    console.log("  长度: 定义式行", defNa.rows, "| 不适用说明:", defNa.state,
                "|", defNa.txt.slice(0, 40));
    note(defNa.na > 0 && defNa.txt.includes("基本量"),
         "定义式: 「长度」没有把「SI 基本量」作为不适用理由显示出来");

    await doSearch("能量");
    const defUmbrella = await ev(`(() => ({
      na: document.querySelectorAll('section.defs .slotna').length,
      txt: (document.querySelector('section.defs .slotna')||{}).textContent || '',
    }))()`);
    console.log("  能量: 不适用", defUmbrella.na, "|", defUmbrella.txt.slice(0, 50));
    note(defUmbrella.txt.includes("总称"),
         "定义式: 「能量」没有把「总称量纲」作为不适用理由显示出来");

    let missBtn = await ev(`(() => {
      const b = document.querySelector('.slotmiss .submitbtn');
      return b ? {kind: b.dataset.subKind, qid: b.dataset.subQid, slot: b.dataset.subSlot} : null;
    })()`);
    // 当前数据里定义式已全覆盖，所以「暂缺」入口只在**未登记量纲**的页面上
    // 才会出现（韦伯 Wb 的量纲 M·L²·T⁻²·I⁻¹ 没有对应物理量条目）。
    // 用一个没有条目的量纲来验证这条通路，而不是假装数据缺了。
    if (!missBtn) {
      console.log("  （当前数据定义式全覆盖，改用未登记量纲验证「暂缺」通路）");
      await doSearch("Wb");
      const anonSlot = await ev(`(() => {
        const ms = Array.from(document.querySelectorAll('.slotmiss'));
        const na = Array.from(document.querySelectorAll('.slotna'));
        const card = document.querySelector('.card');
        return {
          zh: card ? (card.querySelector('.zh')||{}).textContent : '',
          miss: ms.length,
          na: na.length,
          missKinds: ms.map(m => {
            const b = m.querySelector('.submitbtn');
            return b ? b.dataset.subKind : '?';
          }),
          titles: Array.from(document.querySelectorAll('.card section > h3'))
            .map(h => (h.childNodes[0]||{}).textContent || ''),
        };
      })()`);
      console.log("  Wb 页（未登记量纲）:", JSON.stringify(anonSlot));
      note(anonSlot.miss > 0, "暂缺: 未登记量纲的页面上没有出现「暂缺 + 补录」入口");
      note(anonSlot.missKinds.every(k => k === "formula"),
           `暂缺: 未登记量纲的补录种类不该是按条目的（得到 ${anonSlot.missKinds}）`);
      missBtn = { kind: anonSlot.missKinds[0] || "formula", qid: "", slot: "" };
    }
    console.log("  暂缺补录按钮:", JSON.stringify(missBtn));
    note(!!missBtn, "定义式: 页面上找不到任何「暂缺 + 补录」入口");
    await shot("r4-定义式三态.png");

    // 补录弹窗按 kind 切换标题（在还有 .slotmiss 的页面上测）
    if (missBtn) {
      await ev(`(() => { const b=document.querySelector('.slotmiss .submitbtn');
        if (b) b.click(); return 1; })()`);
      await sleep(320);
      const modal = await ev(`(() => {
        const m = document.querySelector('.modal');
        if (!m) return null;
        return {
          title: (m.querySelector('h3')||{}).textContent || '',
          hasExpr: !!document.getElementById('fexpr'),
          hasQid: !!m.querySelector('.ro'),
        };
      })()`);
      console.log("  弹窗:", JSON.stringify(modal));
      note(modal && modal.title.length > 0, "补录: 弹窗没有打开或标题为空");
      await shot("r4-补录弹窗.png", 900);
      await ev(`(() => { const b=document.querySelector('.modal .ghost');
        if (b) b.click(); return 1; })()`);
      await sleep(200);
    }

    // ============ 4. 输入即匹配 ============
    console.log("\n=== 4. 输入即时匹配 ===");
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(1500);
    const typeAndWait = async (text) => {
      await ev(`(() => { const i=document.getElementById('q');
        i.focus(); i.value=${JSON.stringify(text)};
        i.dispatchEvent(new Event('input', {bubbles:true})); return 1; })()`);
      await sleep(520);
      return ev(`(() => {
        const p = document.getElementById('ac');
        return { hidden: p.hidden,
          rows: Array.from(p.querySelectorAll('.acrow')).map(r => ({
            zh: (r.querySelector('.an')||{}).textContent || '',
            match: (r.querySelector('.am')||{}).textContent || '' })) };
      })()`);
    };
    const typed = {};
    for (const t of ["dcqd", "dianchang", "电场", "kgm", "ohmxyz", "转动惯量", "zdgl"]) {
      const r = await typeAndWait(t);
      typed[t] = r;
      console.log(`  「${t}」→ ${r.hidden ? "(面板未显示)" : ""}` +
        r.rows.map(x => x.zh + "/" + x.match).join(" | "));
    }
    note(!typed["dcqd"].hidden && typed["dcqd"].rows.some(r => r.zh === "电场强度"),
         "联想: 拼音首字母 dcqd 没有匹配到电场强度");
    note(!typed["dianchang"].hidden && typed["dianchang"].rows.some(r => r.zh === "电场强度"),
         "联想: 拼音全拼 dianchang 没有匹配到电场强度");
    // 「输入比候选长」这个方向：kgm 应包含 kg（千克）或 质量
    note(typed["kgm"].rows.length > 0,
         "联想: 输入 kgm（比候选长）没有匹配到任何结果 —— 这正是用户报告的 abc→ab 方向");
    console.log("  kgm 命中:", typed["kgm"].rows.map(r => r.zh).join(" / ") || "（无）");
    note(typed["ohmxyz"].rows.some(r => r.zh === "欧"),
         "联想: ohmxyz（比候选长）没有匹配到「欧」");
    await shot("r4-联想面板.png", 900);

    // 键盘导航：用合成 KeyboardEvent 直接测处理函数。
    // 不用 Input.dispatchKeyEvent —— 无头窗口未必有真实焦点，
    // 那样测的是浏览器焦点系统，不是我们的键盘逻辑。
    await ev(`(() => { const i=document.getElementById('q'); i.focus();
      i.value='dcqd'; i.dispatchEvent(new Event('input',{bubbles:true})); return 1; })()`);
    await sleep(560);
    const keyNav = await ev(`(() => {
      const i = document.getElementById('q');
      const press = k => i.dispatchEvent(new KeyboardEvent('keydown',
        { key: k, bubbles: true, cancelable: true }));
      const read = () => {
        const on = document.querySelector('.acrow.on');
        return on ? (on.querySelector('.an')||{}).textContent : null;
      };
      const seq = [];
      press('ArrowDown'); seq.push(read());
      press('ArrowDown'); seq.push(read());
      press('ArrowUp');   seq.push(read());
      return { seq, rows: document.querySelectorAll('.acrow').length };
    })()`);
    console.log("  ↓/↓/↑ 高亮序列:", JSON.stringify(keyNav));
    note(keyNav.seq[0] === "电场强度",
         `键盘: ↓ 没有把第一项高亮（得到 ${keyNav.seq[0]}）`);
    note(keyNav.seq[1] !== keyNav.seq[0],
         "键盘: 第二次 ↓ 没有移动高亮");
    note(keyNav.seq[2] === keyNav.seq[0],
         "键盘: ↑ 没有回到上一项");

    // ============ 6. 历史移入搜索框 ============
    console.log("\n=== 6. 历史记录移入搜索框 ===");
    const histPlacement = await ev(`(() => ({
      strip: !!document.getElementById('histstrip'),
      row: !!document.getElementById('histrow'),
      chip: document.querySelectorAll('.hchip').length,
      panelInsideBox: !!document.querySelector('#searchbox #ac'),
    }))()`);
    console.log("  常驻历史条:", histPlacement.strip || histPlacement.row,
                "| 面板在搜索框内:", histPlacement.panelInsideBox);
    note(!histPlacement.strip && !histPlacement.row,
         "历史: 页面里还留着常驻的历史横条（histstrip/histrow）");
    note(histPlacement.panelInsideBox, "历史: 联想面板不在 #searchbox 里");

    await ev(`(() => { const i=document.getElementById('q');
      i.focus(); i.value=''; i.dispatchEvent(new Event('input',{bubbles:true}));
      return 1; })()`);
    for (const q of ["力矩", "能量", "电场强度"]) await doSearch(q);
    await ev(`(() => { const i=document.getElementById('q');
      i.focus(); i.value=''; i.dispatchEvent(new Event('input',{bubbles:true}));
      return 1; })()`);
    await sleep(320);
    const histPanel = await ev(`(() => {
      const p = document.getElementById('ac');
      return { hidden: p.hidden,
        head: (p.querySelector('.acgroup')||{}).textContent || '',
        rows: Array.from(p.querySelectorAll('.acrow .an')).map(e => e.textContent),
        hasClear: !!p.querySelector('[data-hclear]'),
        hasDel: p.querySelectorAll('[data-hdel]').length };
    })()`);
    console.log("  面板:", histPanel.head);
    console.log("  历史行:", histPanel.rows.join(" / "), "| 删除按钮", histPanel.hasDel,
                "| 清空", histPanel.hasClear);
    note(!histPanel.hidden && histPanel.rows.length >= 3,
         `历史: 聚焦空输入框时没有弹出历史（${histPanel.rows.length} 行）`);
    note(histPanel.rows[0] === "电场强度" || histPanel.rows[1] === "能量",
         `历史: 顺序不是时间倒序（${histPanel.rows.join("/")}）`);
    note(histPanel.hasClear, "历史: 面板里没有清空入口");
    await shot("r4-历史面板.png", 900);
    // 输入后历史应让位于候选
    const afterType = await typeAndWait("力");
    console.log("  输入「力」后:", afterType.rows.map(r => r.zh).join(" / "));
    note(afterType.rows.some(r => r.zh === "力"), "历史: 输入后没有切换到候选");

    // ============ 7. 前进 / 后退 ============
    console.log("\n=== 7. 前进 / 后退 ===");
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(1600);
    const len = () => ev("history.length");
    const l0 = await len();
    await doSearch("力矩");
    const l1 = await len();
    await doSearch("能量");
    const l2 = await len();
    await doSearch("电场强度");
    const l3 = await len();
    console.log(`  history.length: 初始 ${l0} → ${l1} → ${l2} → ${l3}`);
    note(l3 > l0, "导航: 查询没有产生新的历史条目（用了 replaceState）");

    await ev("history.back()");
    await sleep(1200);
    const back1 = await ev(`(() => ({
      q: document.getElementById('q').value,
      hash: location.hash,
      first: (document.querySelector('.card .zh')||{}).textContent || '',
      err: document.querySelectorAll('.err').length,
    }))()`);
    console.log("  后退一次 →", JSON.stringify(back1));
    note(back1.first === "能量" || back1.q === "能量",
         `导航: 后退一次没有回到「能量」（得到 q=${back1.q}, 卡片=${back1.first}）`);

    await ev("history.back()");
    await sleep(1200);
    const back2 = await ev(`(() => ({
      q: document.getElementById('q').value,
      first: (document.querySelector('.card .zh')||{}).textContent || '',
    }))()`);
    console.log("  再后退 →", JSON.stringify(back2));
    note(back2.q === "力矩" || back2.first === "力矩",
         `导航: 第二次后退没有回到「力矩」（得到 q=${back2.q}）`);

    await ev("history.forward()");
    await sleep(1200);
    const fwd = await ev(`(() => ({
      q: document.getElementById('q').value,
      first: (document.querySelector('.card .zh')||{}).textContent || '',
    }))()`);
    console.log("  前进 →", JSON.stringify(fwd));
    note(fwd.q === "能量" || fwd.first === "能量",
         `导航: 前进没有回到「能量」（得到 q=${fwd.q}）`);

    // 一路退到头：必须回到首页分类浏览（而不是空白或残留上一次结果）
    await ev("history.back()"); await sleep(900);   // → 力矩
    await ev("history.back()"); await sleep(1400);  // → 首页
    const homeBack = await ev(`(() => ({
      homeVisible: !document.getElementById('home').hidden,
      cards: document.querySelectorAll('.domcard').length,
      outEmpty: document.getElementById('out').innerHTML.trim().length === 0,
      hash: location.hash,
      inputVal: document.getElementById('q').value,
    }))()`);
    console.log("  退到首页 →", JSON.stringify(homeBack));
    note(homeBack.homeVisible && homeBack.cards > 0,
         `导航: 退到头没有回到首页浏览（${JSON.stringify(homeBack)}）`);
    note(homeBack.inputVal === "" && homeBack.hash === "",
         `导航: 回到首页后地址栏/输入框没有清空（${JSON.stringify(homeBack)}）`);
    await shot("r4-从结果退回首页.png", 1000);

    // ============ 收集 JS 异常 ============
    const errs = events.filter(e =>
      e.method === "Runtime.exceptionThrown" ||
      (e.method === "Log.entryAdded" && e.params.entry.level === "error"));
    errs.forEach(e => {
      const t = e.method === "Runtime.exceptionThrown"
        ? (e.params.exceptionDetails.exception || {}).description
        : e.params.entry.text;
      JSERR.push(String(t).split("\n")[0]);
    });
    note(JSERR.length === 0, `页面有 JS 错误：${JSERR.slice(0,3).join(" | ")}`);

  } catch (e) {
    problems.push("验证脚本异常：" + e.message);
    console.log("!! 脚本异常:", e.message);
  } finally {
    try { ch.kill(); } catch (e) {}
  }

  console.log("\n" + "=".repeat(60));
  if (JSERR.length) console.log("JS 错误:\n  " + JSERR.join("\n  "));
  if (problems.length) {
    console.log(`失败 ${problems.length} 项：`);
    problems.forEach(p => console.log("  ✗ " + p));
    process.exit(1);
  }
  console.log("全部通过。");
})();
