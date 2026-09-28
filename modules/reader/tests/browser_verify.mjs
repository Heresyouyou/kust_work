/**
 * browser_verify.mjs —— 用 CDP 驱动 Edge 无头，对 /reader 做真实渲染验收。
 *
 * 为什么不用 `msedge --screenshot`：它在本机沙箱里会静默脱离父进程（exit 0 但不产出文件）。
 * 改为先常驻启动 `--remote-debugging-port`，再用协议建页、导航、截图、取运行时状态。
 *
 * 用法：
 *   node browser_verify.mjs --port 9333 --base http://127.0.0.1:5566 \
 *        --file /uploads/x.pdf [--peer /translated/x.zh.dual.pdf] [--layout spread] \
 *        [--out dir] [--wait 12000] [--scale 1.2] [--scroll 0.35]
 */
import { writeFileSync, mkdirSync } from 'node:fs';
import { resolve } from 'node:path';

function argOf(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : dflt;
}

const PORT = argOf('port', '9333');
const BASE = argOf('base', 'http://127.0.0.1:5566');
const FILE = argOf('file', '');
const PEER = argOf('peer', '');
const LAYOUT = argOf('layout', 'spread');
const SCALE = argOf('scale', '');
const SCROLL = argOf('scroll', '');
const WAIT = parseInt(argOf('wait', '14000'), 10);
const OUT = resolve(argOf('out', 'D:/PostNews/.workbuddy/reader_shots'));
const TAG = argOf('tag', 'shot');

mkdirSync(OUT, { recursive: true });

const qs = new URLSearchParams();
if (FILE) qs.set('file', FILE);
if (PEER) qs.set('peer', PEER);
if (LAYOUT) qs.set('layout', LAYOUT);
if (PEER) qs.set('sync', '1');
const TARGET = `${BASE}/reader?${qs.toString()}`;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function newTarget(url) {
  // Chrome 111+ 要求 PUT
  const res = await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' });
  if (!res.ok) throw new Error('创建标签页失败: ' + res.status + ' ' + (await res.text()));
  return res.json();
}

class CDP {
  constructor(ws) { this.ws = ws; this.id = 0; this.pending = new Map(); this.events = []; this.handlers = []; }
  static async connect(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = (e) => rej(new Error('WS 连接失败')); });
    const c = new CDP(ws);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.id != null) {
        const p = c.pending.get(msg.id);
        if (p) { c.pending.delete(msg.id); msg.error ? p.rej(new Error(msg.error.message)) : p.res(msg.result); }
      } else {
        c.events.push(msg);
        for (const h of c.handlers) h(msg);
      }
    };
    return c;
  }
  send(method, params = {}) {
    const id = ++this.id;
    this.ws.send(JSON.stringify({ id, method, params }));
    return new Promise((res, rej) => this.pending.set(id, { res, rej }));
  }
  on(fn) { this.handlers.push(fn); }
  close() { try { this.ws.close(); } catch {} }
}

const main = async () => {
  const t = await newTarget('about:blank');
  const cdp = await CDP.connect(t.webSocketDebuggerUrl);

  const consoleMsgs = [];
  const failed = [];
  cdp.on((m) => {
    if (m.method === 'Runtime.consoleAPICalled') {
      consoleMsgs.push(`[${m.params.type}] ` + m.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '));
    }
    if (m.method === 'Runtime.exceptionThrown') {
      consoleMsgs.push('[exception] ' + (m.params.exceptionDetails?.exception?.description || m.params.exceptionDetails?.text));
    }
    if (m.method === 'Log.entryAdded') {
      const e = m.params.entry;
      consoleMsgs.push(`[log:${e.level}] ${e.text}`);
      if (e.level === 'error') failed.push(e.text + ' @ ' + (e.url || ''));
    }
    if (m.method === 'Network.loadingFailed') failed.push(`加载失败 ${m.params.errorText} (${m.params.type})`);
  });

  await cdp.send('Runtime.enable');
  await cdp.send('Log.enable');
  await cdp.send('Network.enable');
  await cdp.send('Page.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width: 1680, height: 1050, deviceScaleFactor: 1, mobile: false,
  });

  await cdp.send('Page.navigate', { url: TARGET });
  await sleep(WAIT);

  // 可选：改缩放 / 滚到指定比例，验证同步是否跟随
  if (SCALE) {
    await cdp.send('Runtime.evaluate', {
      expression: `(()=>{const r=window.__reader; if(r) r.setScale(${Number(SCALE)}); return !!r;})()`,
      returnByValue: true,
    });
    await sleep(3500);
  }
  if (SCROLL) {
    // 直接赋 scrollTop 浏览器确实会派 scroll，但 SyncGroup 的 listener 多在 rAF 里调度，
    // 我们多 sleep 一段，确保调度完成；同时显式派 scroll 兜底。
    await cdp.send('Runtime.evaluate', {
      expression: `(()=>{const r=window.__reader; if(!r) return false; const v=r.views[0]; const el=v._el; el.scrollTop=el.scrollHeight*${Number(SCROLL)}; el.dispatchEvent(new Event('scroll')); globalThis.__leftTop=el.scrollTop; return true;})()`,
      returnByValue: true,
    });
    await sleep(500);
    await cdp.send('Runtime.evaluate', {
      expression: `(()=>{window.__reader.views[0]._handleScroll(); return true;})()`,
      returnByValue: true,
    });
    await sleep(6000);
  }

  // 取运行时状态（DOM 选择器对齐真实结构：.kp-view / .kp-slot[data-page] / .kp-textLayer）
  const probe = `(()=>{
    const r = window.__reader;
    if (!r) return { ok:false, reason:'window.__reader 未挂载' };
    const st = (()=>{ try { return r.stats(); } catch(e){ return { err:String(e) }; } })();
    const views = [...document.querySelectorAll('.kp-view')];
    const slotInfo = (v) => [...v.querySelectorAll('.kp-slot')].map(s=>({
      page: Number(s.dataset.page),
      painted: !!s.querySelector('canvas'),
      h: Math.round(s.getBoundingClientRect().height),
      w: Math.round(s.getBoundingClientRect().width),
      textSpans: s.querySelectorAll('.kp-textLayer span').length,
      links: s.querySelectorAll('.kp-link').length,
    }));
    const frame = document.querySelector('.kp-paneBody')?.getBoundingClientRect();
    return {
      ok: true,
      stats: st,
      title: document.title,
      fatal: document.querySelector('.kp-fatalMsg')?.textContent || null,
      paneCount: document.querySelectorAll('.kp-pane').length,
      divider: !!document.querySelector('.kp-divider'),
      views: views.map(v=>({
        top: Math.round(v.scrollTop),
        max: Math.round(v.scrollHeight - v.clientHeight),
        cw: v.clientWidth, ch: v.clientHeight,
        canvases: v.querySelectorAll('canvas').length,
        live: [...v.querySelectorAll('.kp-slot')].filter(s=>s.querySelector('canvas')).length,
        slots: slotInfo(v),
      })),
      contentW: frame ? Math.round(frame.width) : null,
    };
  })()`;
  const evalRes = await cdp.send('Runtime.evaluate', { expression: probe, returnByValue: true, awaitPromise: true });
  const state = evalRes.result?.value ?? { ok: false, reason: JSON.stringify(evalRes) };

  const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
  const path = resolve(OUT, `${TAG}.png`);
  writeFileSync(path, Buffer.from(shot.data, 'base64'));

  const report = { url: TARGET, screenshot: path, state, console: consoleMsgs.slice(-40), failed: failed.slice(0, 20) };
  writeFileSync(resolve(OUT, `${TAG}.json`), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));

  await fetch(`http://127.0.0.1:${PORT}/json/close/${t.id}`).catch(() => {});
  cdp.close();
};

main().catch((e) => { console.error('验收失败:', e.message); process.exit(1); });
