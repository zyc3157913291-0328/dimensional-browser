/**
 * 用真实浏览器（本机 Chrome / Edge）验证界面渲染并截图。
 *
 * 为什么不用 Playwright 自带的浏览器：本机沙箱不允许 Playwright 的
 * 下载器 spawn 子进程（EPERM），而系统里已有 Chrome 与 Edge。
 * 直接走 CDP（Chrome DevTools Protocol）反而更轻，截图能力相同。
 *
 * 用法：node verify_ui.js [baseUrl] [outDir]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8791";
const OUT = process.argv[3] || path.join(__dirname, "ui-shots");

const CANDIDATES = [
  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  "C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
];

const QUERIES = ["牛", "电场强度", "力矩", "转动惯量", "动量"];

function findBrowser() {
  for (const p of CANDIDATES) if (fs.existsSync(p)) return p;
  throw new Error("找不到 Chrome 或 Edge");
}

/* ---------- 极简 CDP 客户端 ---------- */
function httpJson(url) {
  return new Promise((resolve, reject) => {
    http.get(url, res => {
      let b = "";
      res.on("data", d => (b += d));
      res.on("end", () => {
        try { resolve(JSON.parse(b)); } catch (e) { reject(e); }
      });
    }).on("error", reject);
  });
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; }
  static async attach(wsUrl) {
    // Node 24 自带全局 WebSocket，无需第三方 ws 包
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
      } else if (m.method) {
        c.events.push(m);
      }
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

  const userDir = fs.mkdtempSync(path.join(os.tmpdir(), "dimbrowser-"));
  const port = 9333;
  const child = spawn(bin, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    "--no-default-browser-check",
    "--remote-debugging-port=" + port,
    "--user-data-dir=" + userDir,
    "--window-size=1220,1000",
    "about:blank",
  ], { stdio: "ignore", detached: false });

  const problems = [];
  let cdp = null;
  try {
    // 等 CDP 端口就绪
    let version = null;
    for (let i = 0; i < 60; i++) {
      try { version = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (e) { await sleep(300); }
    }
    if (!version) throw new Error("CDP 端口未就绪");

    const targets = await httpJson(`http://127.0.0.1:${port}/json/list`);
    const page = targets.find(t => t.type === "page");
    cdp = await CDP.attach(page.webSocketDebuggerUrl);

    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Log.enable");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1220, height: 1000, deviceScaleFactor: 1, mobile: false,
    });

    const evaluate = async expr => {
      const r = await cdp.send("Runtime.evaluate", {
        expression: expr, returnByValue: true, awaitPromise: true,
      });
      if (r.exceptionDetails) {
        throw new Error("页面求值异常: " + JSON.stringify(r.exceptionDetails.text));
      }
      return r.result.value;
    };

    await cdp.send("Page.navigate", { url: BASE + "/" });
    await sleep(1400);

    const title = await evaluate("document.title");
    console.log("页面标题:", title);

    const chips = await evaluate("document.querySelectorAll('.chip').length");
    console.log("示例入口数量:", chips);
    if (!chips) problems.push("示例入口没有渲染");

    const shot = async name => {
      // fullPage：量取文档高度后设设备尺寸，再截图
      const h = await evaluate(
        "Math.max(document.body.scrollHeight, document.documentElement.scrollHeight)");
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: 1220, height: Math.min(Math.max(h, 600), 12000),
        deviceScaleFactor: 1, mobile: false,
      });
      await sleep(180);
      const r = await cdp.send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      await cdp.send("Emulation.setDeviceMetricsOverride", {
        width: 1220, height: 1000, deviceScaleFactor: 1, mobile: false,
      });
    };

    const runQuery = async q => {
      await evaluate(`(() => {
        const i = document.getElementById('q');
        i.value = ${JSON.stringify(q)};
        document.getElementById('go').click();
        return true;
      })()`);
      for (let i = 0; i < 40; i++) {
        const done = await evaluate(
          "document.querySelectorAll('.card').length > 0 || document.querySelectorAll('.err').length > 0");
        if (done) break;
        await sleep(150);
      }
      await sleep(280);

      const info = await evaluate(`(() => {
        const card = document.querySelector('.card');
        const txt = s => { const e = document.querySelector(s); return e ? e.textContent.trim() : ''; };
        const n = s => document.querySelectorAll(s).length;
        return {
          cards: n('.card'),
          zh: txt('.card .zh'),
          dimbox: txt('.card .dimbox'),
          dimzh: txt('.card .dimzh'),
          eq: n('.branch.equivalence'),
          deriv: n('.branch.derivative'),
          integ: n('.branch.integral'),
          matrix: n('.branch.matrix'),
          formulas: n('.frow'),
          homog: n('.homog'),
          legend: n('.legend'),
          err: txt('.err'),
          hasSup: !!document.querySelector('.dimbox sup') ||
                  /[\\u2070-\\u209f\\u00b2\\u00b3]/.test(txt('.card .dimbox')),
          exprSample: txt('.branch .expr') || txt('.card .forms'),
        };
      })()`);

      console.log(
        `${q.padEnd(6)} | 卡片${info.cards} 首卡「${info.zh}」 ` +
        `量纲=${info.dimbox} (${info.dimzh}) | ` +
        `等价${info.eq} 求导${info.deriv} 积分${info.integ} | ` +
        `公式${info.formulas} 警示${info.homog} 图例${info.legend}`
      );
      if (!info.cards) problems.push(`${q}: 没有渲染出卡片`);
      if (!info.dimbox) problems.push(`${q}: 量纲没有渲染`);
      if (!info.legend) problems.push(`${q}: 图例没有渲染`);
      if (!info.exprSample) problems.push(`${q}: 分支表达式没有渲染`);
      return info;
    };

    const results = {};
    for (const q of QUERIES) {
      results[q] = await runQuery(q);
      await shot("ui-" + q + ".png");
    }

    // 关键教学断言
    const efield = results["电场强度"];
    if (efield && efield.deriv < 1) {
      problems.push("电场强度: 缺少求导分支（E = -dU/dx 应出现）");
    }
    const torque = results["力矩"];
    if (torque && !torque.homog) {
      problems.push("力矩: 缺少同量纲异构警示（应与能量对照）");
    }

    // 错误路径
    await evaluate(`(() => {
      const i = document.getElementById('q');
      i.value = '弯沉xyz';
      document.getElementById('go').click();
      return true;
    })()`);
    await sleep(900);
    const errText = await evaluate(
      "(document.querySelector('.err')||{}).textContent || ''");
    console.log("错误提示:", errText.trim() ? "已渲染" : "缺失");
    if (!errText.trim()) problems.push("错误提示没有渲染");
    await shot("ui-error.png");

    // ---- 「展开更多」：按需跑有界搜索 ----
    await evaluate(`(() => {
      const i = document.getElementById('q');
      i.value = '牛';
      document.getElementById('go').click();
      return true;
    })()`);
    await sleep(1200);

    const beforeBtn = await evaluate("document.querySelectorAll('.morebtn').length");
    console.log("展开按钮数量:", beforeBtn);
    if (!beforeBtn) problems.push("「展开更多」按钮没有渲染");

    // 点第一个按钮
    await evaluate("document.querySelector('.morebtn').click()");
    for (let i = 0; i < 60; i++) {
      const done = await evaluate(
        "document.querySelectorAll('section.searched').length > 0");
      if (done) break;
      await sleep(150);
    }
    await sleep(300);

    const after = await evaluate(`(() => {
      const sec = document.querySelector('section.searched');
      const n = s => document.querySelectorAll(s).length;
      return {
        sections: n('section.searched'),
        branches: n('section.searched .branch.search'),
        warning: (document.querySelector('section.searched .searchwarn')||{}).textContent||'',
        marks: Array.from(document.querySelectorAll('section.searched .branch.search .expr'))
                 .slice(0,4).map(e => e.textContent.trim()),
        buttonsLeft: n('.morebtn'),
      };
    })()`);
    console.log("算法枚举区块:", after.sections,
                "| 分支:", after.branches,
                "| 剩余按钮:", after.buttonsLeft, "(点击前", beforeBtn + ")");
    console.log("  示例:", after.marks.join(" / "));
    if (!after.sections) problems.push("点击后没有出现算法枚举区块");
    if (!after.branches) problems.push("算法枚举区块里没有分支");
    // 注意：页面上可能有多个按钮（同量纲的多张卡片各一个），
    // 所以这里断言「数量减少」，而不是「全部消失」
    if (after.buttonsLeft >= beforeBtn) {
      problems.push("点击后按钮数量没有减少（应为 " + (beforeBtn - 1) +
                    "，实为 " + after.buttonsLeft + "）");
    }
    if (!/算法枚举/.test(after.warning)) {
      problems.push("算法枚举区块缺少「算法枚举」警示文字");
    }
    if (!/尚未找到|未对应/.test(after.warning)) {
      problems.push("算法枚举区块缺少「无物理来源」的说明");
    }
    await shot("ui-展开更多.png");

    // 页面级 JS 错误
    const jsErrors = cdp.events.filter(e =>
      e.method === "Runtime.exceptionThrown" ||
      (e.method === "Log.entryAdded" && e.params.entry.level === "error"));
    if (jsErrors.length) {
      problems.push("页面有 " + jsErrors.length + " 条 JS 错误/异常");
      jsErrors.slice(0, 3).forEach(e =>
        console.log("   " + JSON.stringify(e.params).slice(0, 200)));
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
  console.log("\n界面渲染验证通过，截图已存到 " + OUT);
})().catch(e => {
  console.error("验证脚本异常:", e.message);
  process.exit(2);
});
