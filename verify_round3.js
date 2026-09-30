/**
 * 验证本轮改进：
 *   bug: 折叠可收回 / 顿号不重复 / 「这也正是」也有折叠
 *   目录: 右侧固定居中、横条三态、无 ⚠ 标记
 *   首页: 按领域浏览，不搜索可达每个词条
 * 用法：node verify_round3.js [baseUrl] [outDir]
 */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");
const path = require("path");
const os = require("os");

const BASE = process.argv[2] || "http://127.0.0.1:8830";
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
  const ud = fs.mkdtempSync(path.join(os.tmpdir(), "dimr3-"));
  const port = 9347;
  const ch = spawn(BIN, ["--headless=new","--disable-gpu","--no-first-run",
    "--remote-debugging-port="+port,"--user-data-dir="+ud,
    "--window-size=1500,1000","about:blank"], { stdio: "ignore" });

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
    const doSearch = async q => {
      await ev(`(() => { const i=document.getElementById('q');
        i.value=${JSON.stringify(q)}; document.getElementById('go').click(); return 1; })()`);
      for (let i = 0; i < 50; i++) {
        if (await ev("document.querySelectorAll('.card,.sugpanel,.err').length>0")) break;
        await sleep(140);
      }
      await sleep(300);
    };

    await send("Page.enable");
    await send("Runtime.enable");
    await send("Log.enable");
    await send("Page.navigate", { url: BASE + "/" });
    await sleep(1800);

    // ================= 首页分类浏览 =================
    console.log("=== 首页分类浏览 ===");
    const home = await ev(`(() => {
      const h = document.getElementById('home');
      return {
        visible: h && !h.hidden,
        cards: document.querySelectorAll('.domcard').length,
        items: document.querySelectorAll('.domitem').length,
        head: (document.querySelector('.browsehead h2')||{}).textContent || '',
        cnt: (document.querySelector('.browsehead .cnt')||{}).textContent || '',
        domains: Array.from(document.querySelectorAll('.domcard .dh .zh'))
          .map(e => e.textContent),
        empty: document.querySelectorAll('.domempty').length,
        outEmpty: document.getElementById('out').innerHTML.trim().length === 0,
      };
    })()`);
    console.log("  首页可见:", home.visible, "| 领域卡片:", home.cards,
                "| 词条:", home.items, "|", home.cnt);
    console.log("  领域:", home.domains.join(" / "));
    note(home.visible, "首页没有显示分类浏览");
    note(home.cards >= 8, `领域卡片过少（${home.cards}）`);
    note(home.items >= 55, `词条数不足（${home.items}，应为全部 55 条）`);
    note(home.outEmpty, "首页显示时查询结果区应清空");
    await shot("r3-首页分类浏览.png");

    // 点第 3 个领域里的某个词条，确认可达
    const clicked = await ev(`(() => {
      const cards = document.querySelectorAll('.domcard');
      const items = cards[2].querySelectorAll('.domitem');
      if (!items.length) return { err: '第 3 个领域没有词条' };
      const label = items[0].dataset.jumpLabel;
      items[0].click();
      return { label, domain: cards[2].querySelector('.dh .zh').textContent };
    })()`);
    await sleep(1600);
    const afterClick = await ev(`(() => ({
      zh: (document.querySelector('.card .zh')||{}).textContent || '',
      homeHidden: document.getElementById('home').hidden,
    }))()`);
    console.log("  点", clicked.domain, "里的", clicked.label,
                "-> 卡片:", afterClick.zh, "| 首页已隐藏:", afterClick.homeHidden);
    note(afterClick.zh === clicked.label,
         `首页词条点击没进入对应页面（得到「${afterClick.zh}」）`);
    note(afterClick.homeHidden, "进入查询后首页应隐藏");

    // ================= 目录：右侧固定、三态、无 ⚠ =================
    console.log("\n=== 目录样式 ===");
    await doSearch("力矩");
    const toc = await ev(`(() => {
      const wrap = document.getElementById('tocwrap');
      const nav = document.getElementById('toc');
      if (!wrap || !nav) return { none: true };
      const cw = getComputedStyle(wrap), cs = getComputedStyle(nav);
      const links = Array.from(nav.querySelectorAll('a'));
      const a0 = links[0], cs0 = getComputedStyle(a0);
      const desc0 = a0.querySelector('.tocdesc');
      const r = nav.getBoundingClientRect();
      return {
        wrapPos: cw.position, wrapRight: cw.right,
        wrapAlign: cw.alignItems,
        navMaxW: cs0.maxWidth,
        navCenterY: Math.round(r.top + r.height / 2),
        viewportMid: Math.round(window.innerHeight / 2),
        navTop: Math.round(r.top), navH: Math.round(r.height),
        inlineCount: links.length,
        firstOpacity: cs0.opacity,
        tocZhFont: getComputedStyle(a0.querySelector('.toczh')).fontSize,
        hasDesc: !!desc0, descText: desc0 ? desc0.textContent : '',
        activeCount: links.filter(a => a.classList.contains('active')).length,
        warnMarks: nav.textContent.includes('⚠'),
        ixMarks: nav.querySelectorAll('.ix').length,
      };
    })()`);
    if (toc.none) {
      note(false, "力矩页面没有目录");
    } else {
      console.log("  wrap position:", toc.wrapPos, "| right:", toc.wrapRight,
                  "| align:", toc.wrapAlign);
      console.log("  目录中点 y:", toc.navCenterY, "| 视口中点:",
                  toc.viewportMid, "| 偏差:",
                  Math.abs(toc.navCenterY - toc.viewportMid), "px");
      console.log("  条目:", toc.inlineCount, "| active:", toc.activeCount,
                  "| 含 ⚠:", toc.warnMarks, "| 旧序号:", toc.ixMarks);
      console.log("  词条名字号:", toc.tocZhFont, "| 未停留 opacity:", toc.firstOpacity);
      console.log("  描述示例:", toc.descText);
      note(toc.wrapPos === "fixed", `目录未固定（${toc.wrapPos}）`);
      note(!toc.warnMarks, "目录里仍有 ⚠ 标记");
      note(toc.ixMarks === 0, "目录里仍有旧序号标记");
      note(toc.hasDesc, "目录条目没有描述元素");
      note(toc.activeCount === 1, `active 应为 1 个，实为 ${toc.activeCount}`);
      // 垂直居中：中点应与视口中点接近（允许 40px 偏差，因为有 padding）
      note(Math.abs(toc.navCenterY - toc.viewportMid) < 60,
           `目录未垂直居中：中点 ${toc.navCenterY} vs 视口中点 ${toc.viewportMid}`);
      // 尺寸不应过小
      note(parseFloat(toc.navMaxW) >= 300,
           `目录条目最大宽度过小（${toc.navMaxW}）`);
      note(parseFloat(toc.tocZhFont) >= 13.5,
           `词条名字号过小（${toc.tocZhFont}）`);
    }

    // 弹跳检测：切换 active 项（点目录不同条目触发滚动 → active 变化），
    // 观察目录自身 top 是否稳定。百分比位移的老实现会让它上下跳。
    const bounce = await ev(`(async () => {
      const nav = document.getElementById('toc');
      const links = Array.from(nav.querySelectorAll('a'));
      const tops = [];
      const measure = () => {
        const r = nav.getBoundingClientRect();
        return { top: Math.round(r.top), h: Math.round(r.height) };
      };
      // 记录初始
      tops.push(measure());
      // 依次激活不同条目（active 类切换会改变宽度与换行，进而改高度）
      for (let i = 1; i < Math.min(links.length, 5); i++) {
        links.forEach(a => a.classList.remove('active'));
        links[i].classList.add('active');
        await new Promise(r => setTimeout(r, 120));
        tops.push(measure());
      }
      const centers = tops.map(t => t.top + t.h / 2);
      const spread = Math.max(...centers) - Math.min(...centers);
      const heights = tops.map(t => t.h);
      return { tops, centers, spread, heightSpread: Math.max(...heights) - Math.min(...heights) };
    })()`);
    console.log("  中点漂移（越小越稳）:", bounce.spread, "px",
                "| 高度变化:", bounce.heightSpread, "px");
    console.log("  各次中点:", bounce.centers.join(", "));
    note(bounce.spread <= 2,
         `切换 active 时目录中点漂移 ${bounce.spread}px（应稳定，弹跳 bug 复现）`);

    // 悬停态：模拟 :hover 用 CDP 不行，改用检查 CSS 规则存在性 + 直接读取样式表
    const hoverRule = await ev(`(() => {
      for (const ss of document.styleSheets) {
        try {
          for (const r of ss.cssRules) {
            if (r.selectorText && r.selectorText.includes('#toc a:hover')) {
              return r.style.maxWidth + ' | ' + r.style.opacity;
            }
          }
        } catch (e) {}
      }
      return '';
    })()`);
    console.log("  悬停规则 maxWidth|opacity:", hoverRule);
    note(!!hoverRule, "缺少 #toc a:hover 规则");

    // 搜索区（含联想面板）未被目录挤占。
    // 第四轮把历史横条换成了搜索框内的联想面板，所以这里量的是 #searchbox：
    // 面板与输入框同宽同左边界，量它等价于量「搜索区的右边界」。
    const hist = await ev(`(() => {
      const nav = document.getElementById('toc');
      const row = document.getElementById('searchbox');
      if (!nav || !row) return { skip: true };
      const nr = nav.getBoundingClientRect();
      const hr = row.getBoundingClientRect();
      // 目录应在页面右侧，搜索框在顶栏内，二者不应重叠
      const overlap = !(nr.left >= hr.right || nr.right <= hr.left ||
                        nr.top >= hr.bottom || nr.bottom <= hr.top);
      const panel = document.getElementById('ac');
      return { tocLeft: Math.round(nr.left), searchRight: Math.round(hr.right),
               overlap,
               panelInside: !!document.querySelector('#searchbox #ac'),
               panelHidden: panel ? panel.hidden : null };
    })()`);
    console.log("  搜索区:", JSON.stringify(hist));
    if (!hist.skip) {
      note(!hist.overlap, "目录与搜索区重叠了");
      note(hist.panelInside, "联想面板不在搜索框内（历史应随搜索弹出）");
    }
    await shot("r3-目录样式.png", 1500);

    // ================= 折叠：可收回、顿号不重复、分支也有 =================
    console.log("\n=== 折叠行为 ===");
    // 必须在「力矩」页测同量纲异构：它下面有 9 个同量纲条目才会折叠。
    // 「力」页面只有 1 个（重力），本来就该没有按钮。
    await doSearch("力矩");

    const groups = await ev(`(() => {
      const homogBtn = document.querySelector('.homog [data-group-more]');
      const ptBtn = document.querySelector('.points [data-group-more]');
      return {
        homogBtns: document.querySelectorAll('.homog [data-group-more]').length,
        homogCards: document.querySelectorAll('.homog').length,
        ptBtns: document.querySelectorAll('.points [data-group-more]').length,
        homogBtnText: homogBtn ? homogBtn.textContent.trim() : '',
        ptBtnText: ptBtn ? ptBtn.textContent.trim() : '',
      };
    })()`);
    console.log("  同量纲异构: " + groups.homogCards + " 处，其中有折叠按钮的 "
                + groups.homogBtns + " 个（例：" + groups.homogBtnText + "）");
    console.log("  「这也正是」: 有折叠按钮 " + groups.ptBtns + " 个（例："
                + groups.ptBtnText + "）");
    note(groups.homogBtns > 0, "同量纲异构里没有折叠按钮");
    note(groups.ptBtns > 0, "「这也正是」里没有折叠按钮（用户要求加）");

    // 展开第一个同量纲异构的按钮
    await ev(`document.querySelector('.homog [data-group-more]').click(); 1`);
    await sleep(280);
    const expanded = await ev(`(() => {
      const btn = document.querySelector('.homog [data-group-more]');
      const wrap = btn.closest('.homog');
      return {
        btnText: btn.textContent.trim(),
        hiddenShown: !wrap.querySelector('.grouprest').hidden,
        ellipVisible: !wrap.querySelector('.groupellip').hidden,
        text: wrap.querySelector('.t').textContent.replace(/\\s+/g,' '),
      };
    })()`);
    console.log("  展开后按钮:", expanded.btnText,
                "| 隐藏盒已显:", expanded.hiddenShown,
                "| 省略号可见:", expanded.ellipVisible);
    console.log("  文本:", expanded.text.slice(0, 130));
    note(expanded.hiddenShown, "点击后没有展开");
    note(/收起/.test(expanded.btnText), `按钮未变成「收起」（${expanded.btnText}）`);
    note(expanded.ellipVisible, "展开后省略号消失了（它兼任项间连接符）");
    note(!/、\s*、/.test(expanded.text), "展开后出现连续顿号：\n      " + expanded.text);
    // 关键：按钮必须在文本最末，不能卡在中间
    const btnAtEnd = /收起\s*⤴\s*$/.test(expanded.text.trim());
    console.log("  按钮位于末尾:", btnAtEnd);
    note(btnAtEnd, "「收起」按钮没有排在文字最后（读起来是 abc 收起 efg）：\n      "
         + expanded.text);
    // 可见项与隐藏项之间要有省略号连接，不能直接粘在一起
    note(/…/.test(expanded.text), "展开后可见项与隐藏项之间缺连接符");

    // 收回
    await ev(`document.querySelector('.homog [data-group-more]').click(); 1`);
    await sleep(280);
    const collapsed = await ev(`(() => {
      const btn = document.querySelector('.homog [data-group-more]');
      const wrap = btn.closest('.homog');
      return {
        btnText: btn.textContent.trim(),
        hiddenAgain: wrap.querySelector('.grouprest').hidden,
        text: wrap.querySelector('.t').textContent.replace(/\\s+/g,' '),
        // 只取可见文本：克隆后删掉隐藏盒
        visibleText: (() => {
          const c = wrap.querySelector('.t').cloneNode(true);
          const h = c.querySelector('.grouprest');
          if (h) h.remove();
          return c.textContent.replace(/\\s+/g,' ');
        })(),
      };
    })()`);
    console.log("  收回后按钮:", collapsed.btnText,
                "| 隐藏盒已隐:", collapsed.hiddenAgain);
    console.log("  可见文本:", collapsed.visibleText.slice(0, 110));
    note(collapsed.hiddenAgain, "折叠无法收回（用户报的 bug）");
    note(/还有/.test(collapsed.btnText), `收回后按钮文案不对（${collapsed.btnText}）`);
    // 折叠态的**可见文本**应读作「A、B、C…还有 6 个 ⤵」
    // 注意不能直接用 textContent：它会把 hidden 元素的内容也算进来，
    // 而用户看不到隐藏项。这里克隆节点并移除隐藏盒后再取文本。
    note(/…\s*还有/.test(collapsed.visibleText),
         "折叠态可见文本不是「… 还有 N 个 ⤵」的连读形式：\n      "
         + collapsed.visibleText);

    // 「这也正是」也要能收放
    await ev(`document.querySelector('.points [data-group-more]').click(); 1`);
    await sleep(250);
    const ptExp = await ev(`(() => {
      const btn = document.querySelector('.points [data-group-more]');
      return { text: btn.textContent.trim(),
               shown: !btn.closest('.points').querySelector('.grouprest').hidden };
    })()`);
    await ev(`document.querySelector('.points [data-group-more]').click(); 1`);
    await sleep(250);
    const ptCol = await ev(`(() => {
      const btn = document.querySelector('.points [data-group-more]');
      return { text: btn.textContent.trim(),
               hidden: btn.closest('.points').querySelector('.grouprest').hidden };
    })()`);
    console.log("  「这也正是」展开:", ptExp.text, "| 收回:", ptCol.text);
    note(ptExp.shown, "「这也正是」无法展开");
    note(ptCol.hidden, "「这也正是」无法收回");
    await shot("r3-折叠.png", 1300);

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
    try { ch.kill(); } catch (e) {}
    await sleep(300);
    try { fs.rmSync(ud, { recursive: true, force: true }); } catch (e) {}
  }

  if (problems.length) {
    console.log("\n发现问题:");
    problems.forEach(p => console.log("  x " + p));
    process.exit(1);
  }
  console.log("\n本轮改进全部验证通过，截图在 " + OUT);
})().catch(e => { console.error("验证异常:", e.message); process.exit(2); });
