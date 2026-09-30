/**
 * 按需求截图：给单个词条页面拍一张能看清槽位的图。
 * 用法：node shoot.js <词条> [输出名] [高度] [baseUrl]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const Q = process.argv[2] || "频率";
const NAME = process.argv[3] || ("shot-" + Q + ".png");
const H = Number(process.argv[4] || 1100);
const BASE = process.argv[5] || "http://127.0.0.1:8765";
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
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), "dimshot-"));
  const port = 9361;
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
    await send("Page.enable");
    await send("Runtime.enable");
    await send("Emulation.setDeviceMetricsOverride",
      { width: 1500, height: H, deviceScaleFactor: 1, mobile: false });
    await send("Page.navigate",
      { url: BASE + "/#q=" + encodeURIComponent(Q) });
    await sleep(2200);
    const info = await ev(`(() => ({
      cards: document.querySelectorAll('.card').length,
      h: Math.round(document.querySelector('.card').getBoundingClientRect().height),
      slots: Array.from(document.querySelectorAll('.card')[0]
        ? document.querySelectorAll('.card')[0].querySelectorAll('section > h3') : [])
        .map(h => (h.childNodes[0]||{}).textContent || ''),
    }))()`);
    console.log(JSON.stringify(info));
    const r = await send("Page.captureScreenshot", { format: "png" });
    fs.writeFileSync(path.join(OUT, NAME), Buffer.from(r.data, "base64"));
    console.log("已存", path.join(OUT, NAME));
  } finally {
    try { ch.kill(); } catch (e) {}
  }
})();
