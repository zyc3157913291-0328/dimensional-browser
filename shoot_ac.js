/**
 * 抓一张「联想面板 + 看历史入口」的图，用于交付说明。
 * 用法：node shoot_ac.js [baseUrl]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8765";
const OUT = path.join(__dirname, "ui-shots");
const BIN = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const sleep = ms => new Promise(r => setTimeout(r, ms));
const httpJson = u => new Promise((res, rej) => {
  http.get(u, r => { let b = ""; r.on("data", d => b += d);
    r.on("end", () => { try { res(JSON.parse(b)); } catch (e) { rej(e); } }); })
    .on("error", rej);
});

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), "dimac-"));
  const port = 9367;
  const ch = spawn(BIN, ["--headless=new", "--disable-gpu", "--no-first-run",
    "--remote-debugging-port=" + port, "--user-data-dir=" + ud,
    "--window-size=1500,1000", "about:blank"], { stdio: "ignore" });
  try {
    let v = null;
    for (let i = 0; i < 60; i++) {
      try { v = await httpJson(`http://127.0.0.1:${port}/json/version`); break; }
      catch (e) { await sleep(300); }
    }
    const tg = await httpJson(`http://127.0.0.1:${port}/json/list`);
    const sock = new WebSocket(tg.find(t => t.type === "page").webSocketDebuggerUrl);
    await new Promise((r, j) => {
      sock.addEventListener("open", r, { once: true });
      sock.addEventListener("error", j, { once: true });
    });
    let id = 0; const pend = new Map();
    sock.addEventListener("message", e => {
      const m = JSON.parse(e.data.toString());
      if (m.id && pend.has(m.id)) {
        const { res, rej } = pend.get(m.id); pend.delete(m.id);
        m.error ? rej(new Error(JSON.stringify(m.error))) : res(m.result);
      }
    });
    const send = (method, params) => new Promise((res, rej) => {
      const i = ++id; pend.set(i, { res, rej });
      sock.send(JSON.stringify({ id: i, method, params: params || {} }));
    });
    const ev = async x => {
      const r = await send("Runtime.evaluate",
        { expression: x, returnByValue: true, awaitPromise: true });
      if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
      return r.result.value;
    };
    const shot = async name => {
      const r = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(path.join(OUT, name), Buffer.from(r.data, "base64"));
      console.log("已存", name);
    };
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: 940, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(2000);

    // 先搜几次，攒出历史
    for (const q of ["力矩", "能量", "电场强度", "转动惯量"]) {
      await ev(`(() => { const i=document.getElementById('q');
        i.value=${JSON.stringify(q)}; document.getElementById('go').click(); return 1; })()`);
      await sleep(700);
    }
    // 候选面板（含「看历史」入口）
    await ev(`(() => { const i=document.getElementById('q');
      i.focus(); i.value='dcqd';
      i.dispatchEvent(new Event('input',{bubbles:true})); return 1; })()`);
    await sleep(700);
    await shot("r4-联想与历史入口.png");

    // 反向匹配：输入比候选长
    await ev(`(() => { const i=document.getElementById('q');
      i.value='ohmxyz'; i.dispatchEvent(new Event('input',{bubbles:true})); return 1; })()`);
    await sleep(700);
    await shot("r4-反向匹配.png");

    // 历史面板
    await ev(`(() => { const i=document.getElementById('q');
      i.value=''; i.dispatchEvent(new Event('input',{bubbles:true})); return 1; })()`);
    await sleep(500);
    await shot("r4-历史在搜索框.png");
  } finally {
    try { ch.kill(); } catch (e) {}
  }
})();
