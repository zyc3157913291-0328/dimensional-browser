/**
 * 三项新功能的真实浏览器验证：搜索历史、公式补录、符号表。
 *
 * 用本机 Chrome 走 CDP（与 verify_ui.js 同一套方法）。
 * 用法：node verify_features.js [baseUrl] [outDir]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = process.argv[3] || path.join(__dirname, "ui-shots");

const CANDIDATES = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

function findBrowser() {
  for (const p of CANDIDATES) if (fs.existsSync(p)) return p;
  throw new Error("找不到 Chrome 或 Edge");
}

function httpJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, res => {
      let b = "";
      res.on("data", d => (b += d));
      res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; }
  static async attach(wsUrl) {
    const sock = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      sock.addEventListener("open", res, { once: true });
      sock.addEventListener("error", rej, { once: true });
    });
    const c = new CDP(sock);
    sock.addEventListener("message", ev => {
      const m = JSON.parse(ev.data.toString());
      if (m.id && c.pending.has(m.id)) {
        const { res, rej } = c.pending.get(m.id);
        c.pending.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      } else if (m.method) c.events.push(m);
    });
    return c;
  }
  send(method, params) {
    const id = ++this.id;
    return new Promise((res, rej) => {
      this.pending.set(id, { res, rej });
      this.ws.send(JSON.stringify({ id, method, params: params || {} }));
    });
  }
  close() { try { this.ws.close(); } catch (e) {} }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const bin = findBrowser();
  console.log("使用浏览器:", bin);
  fs.mkdirSync(OUT, { recursive: true });

  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), "dimfeat-"));
  const port = 9335;
  const child = spawn(bin, [
    "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--remote-debugging-port=" + port, "--user-data-dir=" + userDir,
    "--window-size=1240,1000", "about:blank",
  ], { stdio: "ignore" });

  const problems = [];
  const note = (ok, msg) => { if (!ok) problems.push(msg); };
  let cdp = null;
  try {
    let version = null;
    for (let i = 0; i < 60; i++) {
      try { version = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (e) { await sleep(300); }
    }
    if (!version) throw new Error("CDP 端口未就绪");

    const targets = await httpJson(`http://127.0.0.1:${port}/json/list`);
    cdp = await CDP.attach(targets.find(t => t.type === "page").webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride",
      { width: 1240, height: 1000, deviceScaleFactor: 1, mobile: false });

    const evaluate = async expr => {
      const r = await cdp.send("Runtime.evaluate",
        { expression: expr, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) {
        const d = r.exceptionDetails;
        const desc = (d.exception && (d.exception.description || d.exception.value)) || d.text;
        throw new Error("页面求值异常: " + String(desc).split("\n")[0] +
                        "  ← 表达式: " + String(expr).replace(/\s+/g, " ").slice(0, 120));
      }
      return r.result.value;
    };
    const shot = async name => {
      const h = await evaluate(
        "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)");
      await cdp.send("Emulation.setDeviceMetricsOverride",
        { width: 1240, height: Math.min(Math.max(h, 600), 14000),
          deviceScaleFactor: 1, mobile: false });
      await sleep(200);
      const r = await cdp.send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      await cdp.send("Emulation.setDeviceMetricsOverride",
        { width: 1240, height: 1000, deviceScaleFactor: 1, mobile: false });
    };
    const doSearch = async q => {
      await evaluate(`(() => {
        const i = document.getElementById('q');
        i.value = ${JSON.stringify(q)};
        document.getElementById('go').click();
        return true;
      })()`);
      for (let i = 0; i < 50; i++) {
        const done = await evaluate(
          "document.querySelectorAll('.card').length > 0 || document.querySelectorAll('.err').length > 0");
        if (done) break;
        await sleep(150);
      }
      await sleep(260);
    };

    // 从干净状态开始（清掉可能残留的历史）
    await cdp.send("Page.navigate", { url: BASE + "/" });
    await sleep(1500);
    await evaluate("localStorage.clear(); true");
    await cdp.send("Page.navigate", { url: BASE + "/" });
    await sleep(1300);

    // ==================== 1. 搜索历史 ====================
    console.log("\n=== 1. 搜索历史 ===");
    // 历史现在只存在于搜索框的联想面板里：聚焦 + 清空输入框 → 弹出历史
    const openHistoryAndCount = async () => {
      await evaluate(`(() => {
        const i = document.getElementById('q');
        i.focus(); i.value = '';
        i.dispatchEvent(new Event('input', { bubbles: true }));
        return 1;
      })()`);
      await sleep(400);
      return evaluate("document.querySelectorAll('#ac .acrow').length");
    };
    const seq = ["牛", "电场强度", "力矩", "动量", "压强"];
    for (const q of seq) { await doSearch(q); }

    const hist = await evaluate(`(() => {
      // 第四轮把历史从常驻横条移进了搜索框的联想面板：
      // 聚焦 + 清空输入框 → 面板里就是历史。
      const i = document.getElementById('q');
      i.focus(); i.value = '';
      i.dispatchEvent(new Event('input', { bubbles: true }));
      return 1;
    })()`).then(() => new Promise(r => setTimeout(r, 400)))
      .then(() => evaluate(`(() => {
        const panel = document.getElementById('ac');
        const rows = Array.from(panel.querySelectorAll('.acrow'));
        return {
          open: !panel.hidden,
          labels: rows.map(r => (r.querySelector('.an') || {}).textContent || ''),
          del: panel.querySelectorAll('[data-hdel]').length,
          clear: !!panel.querySelector('[data-hclear]'),
          raw: localStorage.getItem('dimbrowser.history.v1') || '',
        };
      })()`));
    console.log("  历史条数:", hist.labels.length, "| 顺序:", hist.labels.join(" → "));
    console.log("  面板展开:", hist.open, "| 单条删除按钮:", hist.del,
                "| 清空入口:", hist.clear);

    note(hist.open, "历史面板没有弹出（聚焦空输入框时应显示历史）");
    note(hist.labels.length === 5, `历史应有 5 条，实为 ${hist.labels.length}`);
    note(hist.labels[0] === "压强", `最近一次「压强」应在最前，实为 ${hist.labels[0]}`);
    note(hist.labels[4] === "牛", `最早一次「牛」应在最后，实为 ${hist.labels[4]}`);
    note(hist.del === 5, `每条历史都应有删除按钮，实为 ${hist.del}`);
    note(hist.clear, "面板里缺少清空入口");
    // 页面下方不该再有常驻历史条
    const noStrip = await evaluate(
      "!document.getElementById('histrow') && !document.getElementById('histstrip')");
    note(noStrip, "页面里还留着常驻的历史横条");

    // 需求：保留数量不低于 10 个
    await evaluate(`(() => {
      const KEY='dimbrowser.history.v1';
      const list=[]; for (let i=1;i<=60;i++) list.push({q:'测试'+i, ts: Date.now()+i});
      localStorage.setItem(KEY, JSON.stringify(list));
      return true;
    })()`);
    await cdp.send("Page.navigate", { url: BASE + "/" });
    await sleep(1300);
    // 打开联想面板读取历史行数（历史现在只在搜索框里出现）
    const capped = await openHistoryAndCount();
    console.log("  塞 60 条后实际保留:", capped, "条");
    note(capped >= 10, `保留上限过低：只留了 ${capped} 条（要求不低于 10）`);
    note(capped <= 60, `保留上限异常：${capped} 条`);

    // 去重 + 提到最前
    await evaluate("localStorage.clear(); true");
    await cdp.send("Page.navigate", { url: BASE + "/" });
    await sleep(1200);
    await doSearch("牛");
    await doSearch("焦");
    await doSearch("牛");
    const dedup = await evaluate(`(() => {
      const i = document.getElementById('q');
      i.focus(); i.value = '';
      i.dispatchEvent(new Event('input', { bubbles: true }));
      return 1;
    })()`).then(() => sleep(400)).then(() => evaluate(`(() => {
      const rows = Array.from(document.querySelectorAll('#ac .acrow'));
      return { n: rows.length,
               labels: rows.map(r => (r.querySelector('.an')||{}).textContent || '') };
    })()`));
    console.log("  重复查询后:", dedup.n, "条", dedup.labels.join(" → "));
    note(dedup.n === 2, `去重失败：应有 2 条，实为 ${dedup.n}`);
    note(dedup.labels[0] === "牛", "重复查询应提到最前");

    // 点击历史行能重查
    await evaluate(`(() => {
      const rows = Array.from(document.querySelectorAll('#ac .acrow'));
      const c = rows.find(x => ((x.querySelector('.an')||{}).textContent||'').indexOf('焦') === 0);
      if (c) c.click();
      return true;
    })()`);
    await sleep(1200);
    const afterClick = await evaluate("document.getElementById('q').value");
    console.log("  点击历史后搜索框:", afterClick);
    note(afterClick === "焦", `点击历史未重查，搜索框为 ${afterClick}`);

    // ==================== 2. 符号表 ====================
    console.log("\n=== 2. 公式符号表 ===");
    await doSearch("牛");
    const glo = await evaluate(`(() => {
      const t = document.querySelector('.glostoggle');
      const n = document.querySelectorAll('.glotable').length;
      const cols = document.querySelectorAll('.glorow').length;
      const hiddenBefore = Array.from(document.querySelectorAll('.glotable'))
        .every(t => t.hidden);
      return { hasToggle: !!t, tables: n, rows: cols, hiddenBefore,
               label: t ? t.textContent.trim() : '' };
    })()`);
    console.log("  折叠开关:", glo.hasToggle, "| 表格数:", glo.tables,
                "| 行数:", glo.rows, "| 默认隐藏:", glo.hiddenBefore);
    console.log("  开关文案:", glo.label);
    note(glo.hasToggle, "没有符号表折叠开关");
    note(glo.tables > 0, "没有符号表容器");
    note(glo.hiddenBefore, "符号表默认应为折叠状态");

    // 展开第一个
    await evaluate("document.querySelector('.glostoggle').click(); true");
    await sleep(300);
    const opened = await evaluate(`(() => {
      const box = document.querySelector('.glotable');
      const rows = Array.from(box.querySelectorAll('.glorow')).map(r => ({
        s: (r.querySelector('.gs')||{}).textContent || '',
        z: (r.querySelector('.gz')||{}).textContent || '',
        u: (r.querySelector('.gu')||{}).textContent || '',
        m: (r.querySelector('.gm')||{}).textContent || '',
      }));
      return { hidden: box.hidden, rows, jumps: box.querySelectorAll('.jumplink').length };
    })()`);
    console.log("  展开后 hidden:", opened.hidden, "| 符号行数:", opened.rows.length,
                "| 可跳转:", opened.jumps);
    opened.rows.slice(0, 3).forEach(r =>
      console.log(`     ${r.s} = ${r.z} [${r.u}] ${r.m.slice(0, 26)}…`));
    note(!opened.hidden, "点击后符号表未展开");
    note(opened.rows.length >= 2, "符号表行数过少");
    note(opened.rows.every(r => r.z && r.m), "有符号行缺名称或物理意义");
    note(opened.rows.every(r => r.u), "有符号行缺单位");
    await shot("feat-符号表.png");

    // 跳转
    const jumpTarget = await evaluate(`(() => {
      const a = document.querySelector('.jumplink[data-jump]');
      if (!a) return '';
      const t = a.dataset.jump;
      a.click();
      return t;
    })()`);
    await sleep(1300);
    const afterJump = await evaluate(`(() => ({
      hash: location.hash,
      firstCard: (document.querySelector('.card .zh')||{}).textContent || '',
    }))()`);
    console.log("  点击跳转 ->", jumpTarget, "| hash:", afterJump.hash,
                "| 首卡:", afterJump.firstCard);
    note(jumpTarget, "符号表里没有可跳转的符号");
    note(afterJump.hash.indexOf("q=") >= 0, "跳转后 URL hash 未更新");

    // ==================== 3. 公式补录 ====================
    console.log("\n=== 3. 公式补录 ===");
    await doSearch("牛");
    // 展开更多，拿到补录按钮
    const hasMore = await evaluate("document.querySelectorAll('.morebtn').length > 0");
    note(hasMore, "没有「展开更多」按钮");
    await evaluate("document.querySelector('.morebtn').click(); true");
    for (let i = 0; i < 60; i++) {
      if (await evaluate("document.querySelectorAll('.submitbtn').length > 0")) break;
      await sleep(150);
    }
    const subBtns = await evaluate("document.querySelectorAll('.submitbtn').length");
    console.log("  算法枚举分支旁的补录按钮:", subBtns);
    note(subBtns > 0, "算法枚举分支旁没有补录入口");

    // 打开弹窗
    const ctx = await evaluate(`(() => {
      const b = document.querySelector('.submitbtn');
      const expr = b.dataset.subExpr, dim = b.dataset.subDim;
      b.click();
      return { expr, dim };
    })()`);
    await sleep(400);
    const modalOpen = await evaluate(`(() => {
      const m = document.querySelector('.modal');
      return { open: !!m, title: m ? m.querySelector('h3').textContent : '',
               fields: m ? m.querySelectorAll('.field').length : 0 };
    })()`);
    console.log("  弹窗:", modalOpen.open, "|", modalOpen.title,
                "| 字段数:", modalOpen.fields, "| 目标:", ctx.expr, ctx.dim);
    note(modalOpen.open, "补录弹窗没有打开");
    note(modalOpen.fields >= 4, `弹窗字段过少：${modalOpen.fields}`);
    await shot("feat-补录弹窗.png");

    // 空说明提交应被拒
    await evaluate("document.getElementById('fsubmit').click(); true");
    await sleep(700);
    const reject = await evaluate(
      "((document.querySelector('.modal .err')||{}).textContent||'').trim()");
    console.log("  空说明提交:", reject ? ("被拒 → " + reject) : "未被拒（问题）");
    note(reject, "空说明的提交应被拒绝");

    // 正常提交
    await evaluate(`(() => {
      document.getElementById('fnote').value = '验证脚本提交：梁的弯曲刚度情境';
      document.getElementById('fsrc').value = 'verify_features.js 自动提交';
      document.getElementById('fzh').value = '验证用组合';
      return true;
    })()`);
    await evaluate("document.getElementById('fsubmit').click(); true");
    await sleep(1300);
    const okText = await evaluate(
      "((document.querySelector('.modal .ok')||{}).textContent||'').trim()");
    const errText = await evaluate(
      "((document.querySelector('.modal .err')||{}).textContent||'').trim()");
    console.log("  正常提交:", okText ? ("成功 → " + okText.slice(0, 34))
                                     : ("失败 → " + errText));
    note(okText, "正常提交没有成功: " + errText);

    // 待核实角标
    await sleep(900);
    const closeRes = await evaluate("typeof closeModal === 'function' ? (closeModal(), 'closed') : 'no-fn'");
    console.log("  关闭弹窗:", closeRes);
    await sleep(200);
    const refreshRes = await evaluate(
      "typeof refreshPendingBadge === 'function' ? (refreshPendingBadge(), 'refreshed') : 'no-fn'");
    console.log("  刷新角标:", refreshRes);
    await sleep(800);
    const badge = await evaluate(`(() => {
      const b = document.getElementById('pendingbadge');
      return { exists: !!b, text: b ? b.textContent.trim() : '', title: b ? b.title : '' };
    })()`);
    console.log("  待核实角标:", badge.exists, "|", badge.text, "|", badge.title);
    note(badge.exists, "角标元素不存在");
    note(/补录 [1-9]/.test(badge.text), `角标未更新：${badge.text}`);
    await shot("feat-补录角标.png");

    // 清单弹窗里应能看到刚提交的那条
    const openRes = await evaluate(
      "typeof openSubmissionList === 'function' ? (openSubmissionList(), 'opened') : 'no-fn'");
    console.log("  打开清单:", openRes);
    await sleep(1000);
    const list = await evaluate(`(() => {
      const items = Array.from(document.querySelectorAll('.subitem'));
      return { n: items.length, first: items.length ? items[0].textContent.slice(0, 70) : '' };
    })()`);
    console.log("  清单条数:", list.n, "| 首条:", list.first.replace(/\s+/g, " "));
    note(list.n > 0, "补录清单为空");
    note(/待核实/.test(list.first), "新提交应显示为「待核实」");
    await shot("feat-补录清单.png");
    await evaluate("closeModal(); true");
    await sleep(300);
    // ==================== 页面 JS 错误 ====================
    // 只统计「真实异常」：页面抛出的未捕获异常。
    // 网络 400 不算——本脚本会故意提交空表单来验证后端拒绝逻辑，
    // 那次 POST 注定返回 400，把它当错误是误报。
    const jsErrors = cdp.events.filter(e =>
      e.method === "Runtime.exceptionThrown" ||
      (e.method === "Log.entryAdded" &&
       e.params.entry.level === "error" &&
       e.params.entry.source !== "network"));
    if (jsErrors.length) {
      problems.push("页面有 " + jsErrors.length + " 条 JS 异常");
      jsErrors.slice(0, 4).forEach(e =>
        console.log("   " + JSON.stringify(e.params).slice(0, 240)));
    } else {
      console.log("\n页面无 JS 异常（故意触发的 400 校验拒绝不计入）");
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
  console.log("\n三项新功能验证通过，截图已存到 " + OUT);
})().catch(e => { console.error("验证脚本异常:", e.message); process.exit(2); });
