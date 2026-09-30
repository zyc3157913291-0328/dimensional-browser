/**
 * 验证本轮修复的 4 个 bug + 4 条建议。
 * 用法：node verify_fixes.js [baseUrl] [outDir]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8820";
const OUT = process.argv[3] || path.join(__dirname, "ui-shots");
const BIN = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";

function httpJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => { let b = ""; r.on("data", d => b += d);
      r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); })
      .on("error", rej);
  });
}
const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), "dimfix-"));
  const port = 9341;
  const child = spawn(BIN, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--remote-debugging-port=" + port, "--user-data-dir=" + userDir,
    "--window-size=1400,1000", "about:blank"], { stdio: "ignore" });

  const problems = [];
  const note = (ok, msg) => { if (!ok) problems.push(msg); };
  let cdp = null;
  try {
    let v = null;
    for (let i = 0; i < 60; i++) {
      try { v = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (e) { await sleep(300); }
    }
    if (!v) throw new Error("CDP 未就绪");
    const targets = await httpJson(`http://127.0.0.1:${port}/json/list`);
    const sock = new WebSocket(targets.find(t => t.type === "page").webSocketDebuggerUrl);
    await new Promise((res, rej) => {
      sock.addEventListener("open", res, { once: true });
      sock.addEventListener("error", rej, { once: true });
    });
    let id = 0; const pending = new Map(); const events = [];
    sock.addEventListener("message", ev => {
      const m = JSON.parse(ev.data.toString());
      if (m.id && pending.has(m.id)) {
        const { res, rej } = pending.get(m.id); pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) events.push(m);
    });
    const send = (method, params) => new Promise((res, rej) => {
      const i = ++id; pending.set(i, { res, rej });
      sock.send(JSON.stringify({ id: i, method, params: params || {} }));
    });
    const ev = async expr => {
      const r = await send("Runtime.evaluate",
        { expression: expr, returnByValue: true, awaitPromise: true });
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
        { width: 1400, height: Math.min(Math.max(h, 600), 16000),
          deviceScaleFactor: 1, mobile: false });
      await sleep(220);
      const r = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      await send("Emulation.setDeviceMetricsOverride",
        { width: 1400, height: 1000, deviceScaleFactor: 1, mobile: false });
    };
    const doSearch = async q => {
      await ev(`(() => { const i=document.getElementById('q');
        i.value=${JSON.stringify(q)}; document.getElementById('go').click(); return 1; })()`);
      for (let i = 0; i < 50; i++) {
        const done = await ev(
          "document.querySelectorAll('.card,.sugpanel,.err').length > 0");
        if (done) break;
        await sleep(140);
      }
      await sleep(280);
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Log.enable");
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(1600);
    await ev("localStorage.clear(); true");

    // ================= bug1：符号表在多卡片下不串台 =================
    console.log("=== bug1: 符号表不串台 ===");
    await doSearch("牛");
    const cards = await ev("document.querySelectorAll('.card').length");
    // 找到第二个卡片（重力）的第一个「展开符号说明」按钮并点击
    const res1 = await ev(`(() => {
      const cardList = document.querySelectorAll('.card');
      if (cardList.length < 2) return { err: '第二张卡片不存在，共 ' + cardList.length };
      const second = cardList[1];
      const btn = second.querySelector('.glostoggle');
      if (!btn) return { err: '第二张卡片没有符号表按钮' };
      btn.click();
      const box = btn.parentElement.querySelector('.glotable');
      // 检查是否**只有**这张卡片的表格被展开
      const opened = Array.from(document.querySelectorAll('.glotable'))
        .filter(t => !t.hidden).length;
      const firstCardOpen = Array.from(cardList[0].querySelectorAll('.glotable'))
        .filter(t => !t.hidden).length;
      const myOpen = Array.from(second.querySelectorAll('.glotable'))
        .filter(t => !t.hidden).length;
      return {
        cardZh: cardList[1].querySelector('.zh').textContent,
        opened, firstCardOpen, myOpen,
        rowsInBox: box ? box.querySelectorAll('.glorow').length : 0,
      };
    })()`);
    console.log("  第二张卡片:", res1.cardZh, "| 全页展开数:", res1.opened,
                "| 第一张卡展开数:", res1.firstCardOpen, "| 本卡展开数:", res1.myOpen,
                "| 行数:", res1.rowsInBox);
    note(!res1.err, "bug1: " + (res1.err || ""));
    note(res1.opened === 1, `bug1: 应只展开 1 个表格，实为 ${res1.opened}`);
    note(res1.firstCardOpen === 0, "bug1: 第一张卡片的表格被误展开了");
    note(res1.myOpen === 1, "bug1: 本卡片的表格没展开");

    // ================= bug2：检索 I =================
    console.log("\n=== bug2: 检索 I ===");
    await doSearch("I");
    const sug = await ev(`(() => {
      const p = document.querySelector('.sugpanel');
      if (!p) return { none: true };
      return {
        head: p.querySelector('.head').textContent.trim(),
        rows: Array.from(p.querySelectorAll('.sugrow')).map(r => ({
          zh: (r.querySelector('.sz')||{}).textContent || '',
          sym: (r.querySelector('.ss')||{}).textContent || '',
          dim: (r.querySelector('.sd .dim')||{}).textContent || '',
        })),
      };
    })()`);
    if (sug.none) {
      console.log("  没有候选面板");
      note(false, "bug2: 检索 I 没有候选面板");
    } else {
      console.log("  " + sug.head);
      sug.rows.forEach(r => console.log(`     ${r.zh} [${r.sym}] ${r.dim}`));
      note(sug.rows.length >= 2, `bug2: 候选过少（${sug.rows.length}）`);
      const names = sug.rows.map(r => r.zh).join("");
      note(/冲量/.test(names), "bug2: 候选里没有冲量");
      note(/电流/.test(names), "bug2: 候选里没有电流");
    }
    await shot("fix-检索I候选.png", 900);

    // ================= bug3：势能/动能页面 =================
    console.log("\n=== bug3: 势能/动能页面 ===");
    for (const q of ["动能", "势能", "重力势能", "弹性势能", "电势能", "化学势能"]) {
      await doSearch(q);
      const r = await ev(`(() => {
        const c = document.querySelector('.card');
        return { zh: c ? (c.querySelector('.zh')||{}).textContent : '',
                 dim: c ? (c.querySelector('.dimbox')||{}).textContent : '',
                 defs: document.querySelectorAll('section.defs .defrow').length };
      })()`);
      console.log(`   ${q} -> ${r.zh || "（无卡片）"} ${r.dim} 定义式 ${r.defs} 条`);
      note(r.zh === q, `bug3: ${q} 页面首卡是「${r.zh}」`);
    }
    await shot("fix-势能页面.png", 1500);

    // ================= bug4：层数选择器说明 =================
    console.log("\n=== bug4: 层数选择器 ===");
    const depthInfo = await ev(`(() => {
      const s = document.getElementById('depth');
      const d = document.getElementById('depthhint');
      const l = document.getElementById('depthlabel');
      return { label: l ? l.textContent : '', hint: d ? d.textContent : '',
               title: s ? s.title : '',
               opts: Array.from(s.options).map(o => o.textContent) };
    })()`);
    console.log("  标签:", depthInfo.label);
    console.log("  选项:", depthInfo.opts.join(" / "));
    console.log("  旁边说明:", depthInfo.hint);
    console.log("  悬停说明:", depthInfo.title);
    note(depthInfo.opts.includes("不展开"), "bug4: 缺少「不展开」选项");
    note(depthInfo.hint.length > 6, "bug4: 旁边没有层数说明");
    note(/层数|展开层/.test(depthInfo.title), "bug4: 悬停说明不清楚");

    // ================= 建议1：定义式单列 =================
    console.log("\n=== 建议1: 定义式单列 ===");
    await doSearch("力");
    const defs = await ev(`(() => {
      const sec = document.querySelector('section.defs');
      if (!sec) return { none: true };
      return {
        count: sec.querySelectorAll('.defrow').length,
        eqs: Array.from(sec.querySelectorAll('.defeq')).map(e => e.textContent.trim()),
        notes: Array.from(sec.querySelectorAll('.defnote')).map(e => e.textContent.trim()),
        hasGlossary: sec.querySelectorAll('.glostoggle').length,
        // 下方公式列表也必须仍然存在（重复保留）
        formulaRows: document.querySelectorAll('.frow').length,
      };
    })()`);
    if (defs.none) {
      note(false, "建议1: 没有定义式区块");
    } else {
      console.log("  定义式:", defs.eqs.join(" | "));
      console.log("  说明:", (defs.notes[0] || "").slice(0, 40));
      console.log("  带符号表:", defs.hasGlossary, "| 下方公式数:", defs.formulaRows);
      note(defs.count >= 1, "建议1: 定义式条数为 0");
      note(defs.formulaRows > 0, "建议1: 下方公式列表被删掉了（应重复保留）");
    }
    await shot("fix-定义式.png", 1400);

    // ================= 建议2：候选列表（同上 bug2，已验）=================
    console.log("\n=== 建议2: 候选列表 === 已随 bug2 验证");
    // 再验一个中文歧义输入
    await doSearch("势");
    const sug2 = await ev(`(() => {
      const p = document.querySelector('.sugpanel');
      return p ? Array.from(p.querySelectorAll('.sugrow .sz')).map(x => x.textContent) : [];
    })()`);
    console.log("  输「势」的候选:", sug2.join("、"));
    note(sug2.length >= 4, `建议2: 「势」的候选过少（${sug2.length}）`);

    // ================= 建议3：历史显示点击文本 =================
    // 第四轮把历史从页面下方的常驻横条移进了搜索框的联想面板
    // （用户要求「只有当前正在搜索时才会弹出历史记录」），
    // 所以这里改成：聚焦空输入框 → 面板里就是历史。
    console.log("\n=== 建议3: 历史标签 ===");
    await ev("localStorage.clear(); true");
    await doSearch("牛");
    // 点同量纲异构里的「重力」跳转
    const jumped = await ev(`(() => {
      const link = document.querySelector('.homog .jumplink[data-jump]');
      if (!link) return '';
      const label = link.dataset.jumpLabel;
      link.click();
      return label;
    })()`);
    await sleep(1400);
    // 聚焦 + 清空输入框 → 弹出历史
    const hist = await ev(`(() => {
      const i = document.getElementById('q');
      i.focus(); i.value = '';
      i.dispatchEvent(new Event('input', {bubbles: true}));
      return 1;
    })()`).then(() => sleep(320)).then(() => ev(`(() => {
      const panel = document.getElementById('ac');
      const rows = Array.from(panel.querySelectorAll('.acrow'));
      return {
        open: !panel.hidden,
        labels: rows.map(r => (r.querySelector('.an')||{}).textContent || ''),
        raw: JSON.parse(localStorage.getItem('dimbrowser.history.v1')||'[]')
               .map(x => x.label + ' ← ' + x.q),
      };
    })()`));
    console.log("  点击了:", jumped, "| 历史面板:",
                hist.open ? hist.labels.join(" → ") : "（未弹出）");
    console.log("  存储:", hist.raw.join(" | "));
    note(hist.open, "建议3: 聚焦空输入框时历史面板没有弹出");
    note(hist.labels[0] === jumped,
          `建议3: 历史首项应为点击文本「${jumped}」，实为「${hist.labels[0]}」`);
    note(!/^[a-z_]+$/.test(hist.labels[0] || ""),
          `建议3: 历史显示了英文 id「${hist.labels[0]}」`);

    // ================= 建议4：侧边跳转目录 =================
    console.log("\n=== 建议4: 侧边跳转目录 ===");
    await doSearch("力矩");
    const toc = await ev(`(() => {
      const nav = document.getElementById('toc');
      if (!nav) return { none: true };
      const links = Array.from(nav.querySelectorAll('a'));
      return {
        count: links.length,
        labels: links.map(a => a.textContent.trim()),
        cardCount: document.querySelectorAll('.card').length,
        // 顺序应与页面卡片顺序一致
        order: links.map(a => {
          const el = document.getElementById('ent-' + a.dataset.toc);
          return el ? el.querySelector('.zh').textContent : '?';
        }),
        cardOrder: Array.from(document.querySelectorAll('.card .zh'))
          .map(e => e.textContent),
        firstActive: links.length ? links[0].classList.contains('active') : false,
      };
    })()`);
    if (toc.none) {
      note(false, "建议4: 没有侧边目录");
    } else {
      console.log("  目录条目:", toc.labels.join(" → "));
      console.log("  页面卡片:", toc.cardOrder.join(" → "));
      console.log("  目录顺序与卡片顺序一致:", JSON.stringify(toc.order) === JSON.stringify(toc.cardOrder));
      note(toc.count === toc.cardCount, `建议4: 目录数 ${toc.count} 与卡片数 ${toc.cardCount} 不符`);
      note(JSON.stringify(toc.order) === JSON.stringify(toc.cardOrder),
           "建议4: 目录顺序与页面顺序不一致");
    }
    await shot("fix-侧边目录.png", 1700);

    // 点目录第二项测试跳转
    const tocJump = await ev(`(() => {
      const links = document.querySelectorAll('#toc a');
      if (links.length < 2) return { skip: true };
      const want = document.getElementById('ent-' + links[1].dataset.toc)
        .querySelector('.zh').textContent;
      links[1].click();
      return { want };
    })()`);
    await sleep(900);
    const scrolled = await ev("window.scrollY > 100");
    console.log("  点目录第 2 项:", tocJump.want, "| 已滚动:", scrolled);
    if (!tocJump.skip) note(scrolled, "建议4: 点目录没有滚动");

    // ================= JS 异常 =================
    const jsErrors = events.filter(e =>
      e.method === "Runtime.exceptionThrown" ||
      (e.method === "Log.entryAdded" && e.params.entry.level === "error" &&
       e.params.entry.source !== "network"));
    if (jsErrors.length) {
      problems.push("页面有 " + jsErrors.length + " 条 JS 异常");
      jsErrors.slice(0, 4).forEach(e =>
        console.log("   " + JSON.stringify(e.params).slice(0, 240)));
    } else {
      console.log("\n页面无 JS 异常");
    }
  } finally {
    if (cdp) cdp.close();
    try { child.kill(); } catch (e) {}
    await sleep(300);
    try { fs.rmSync(userDir, { recursive: true, force: true }); } catch (e) {}
  }

  if (problems.length) {
    console.log("\n发现问题:");
    problems.forEach(p => console.log("  x " + p));
    process.exit(1);
  }
  console.log("\n4 个 bug + 4 条建议全部验证通过，截图在 " + OUT);
})().catch(e => { console.error("验证异常:", e.message); process.exit(2); });
