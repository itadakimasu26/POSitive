import { spawn } from 'node:child_process';
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
const outputDirectory = join(projectRoot, 'static', 'pos', 'images', 'product');
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const baseUrl = (process.env.OXPOS_CAPTURE_BASE_URL || 'http://127.0.0.1:8765').replace(/\/$/, '');
const username = process.env.OXPOS_CAPTURE_USERNAME;
const password = process.env.OXPOS_CAPTURE_PASSWORD;
const screenshots = [
  { path: '/dashboard/', filename: 'dashboard.webp' },
  { path: '/sell/', filename: 'checkout.webp' },
  { path: '/products/', filename: 'inventory.webp' },
  { path: '/reports/', filename: 'reports.webp' },
];

if (!username || !password) throw new Error('OXPOS_CAPTURE_USERNAME and OXPOS_CAPTURE_PASSWORD are required.');
if (!existsSync(chromePath)) throw new Error(`Chrome was not found at ${chromePath}.`);

async function freePort() {
  const { createServer } = await import('node:http');
  const server = createServer();
  await new Promise((resolvePromise, rejectPromise) => {
    server.once('error', rejectPromise);
    server.listen(0, '127.0.0.1', resolvePromise);
  });
  const port = server.address().port;
  await new Promise(resolvePromise => server.close(resolvePromise));
  return port;
}

async function waitForDebugger(port) {
  let lastError;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return response.json();
    } catch (error) {
      lastError = error;
    }
    await new Promise(resolvePromise => setTimeout(resolvePromise, 100));
  }
  throw lastError || new Error('Chrome DevTools did not become ready.');
}

function connectDebugger(webSocketUrl) {
  const socket = new WebSocket(webSocketUrl);
  const pending = new Map();
  let sequence = 0;
  socket.addEventListener('message', event => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    const { resolve: resolvePromise, reject: rejectPromise } = pending.get(message.id);
    pending.delete(message.id);
    if (message.error) rejectPromise(new Error(message.error.message));
    else resolvePromise(message.result);
  });
  const ready = new Promise((resolvePromise, rejectPromise) => {
    socket.addEventListener('open', resolvePromise, { once: true });
    socket.addEventListener('error', rejectPromise, { once: true });
  });
  return {
    socket,
    ready,
    async send(method, params = {}) {
      await ready;
      sequence += 1;
      return new Promise((resolvePromise, rejectPromise) => {
        pending.set(sequence, { resolve: resolvePromise, reject: rejectPromise });
        socket.send(JSON.stringify({ id: sequence, method, params }));
      });
    },
  };
}

async function waitForExpression(client, expression, timeout = 15_000) {
  const deadline = Date.now() + timeout;
  let lastValue;
  while (Date.now() < deadline) {
    const evaluation = await client.send('Runtime.evaluate', { expression, returnByValue: true });
    lastValue = evaluation.result.value;
    if (lastValue) return lastValue;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 150));
  }
  throw new Error(`Timed out waiting for browser condition: ${expression}; last value: ${lastValue}`);
}

async function navigate(client, url) {
  await client.send('Page.navigate', { url });
  await waitForExpression(client, 'document.readyState === "complete"');
}

const debuggerPort = await freePort();
const chromeProfile = join(tmpdir(), `oxpos-screenshots-${process.pid}`);
const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    '--disable-gpu',
    `--remote-debugging-port=${debuggerPort}`,
    `--user-data-dir=${chromeProfile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--hide-scrollbars',
    '--window-size=1440,900',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] },
);

let chromeErrors = '';
chrome.stderr.on('data', chunk => { chromeErrors += chunk.toString(); });

try {
  await waitForDebugger(debuggerPort);
  const targetResponse = await fetch(`http://127.0.0.1:${debuggerPort}/json/new?${encodeURIComponent(`${baseUrl}/login/`)}`, { method: 'PUT' });
  if (!targetResponse.ok) throw new Error(`Chrome target creation failed with ${targetResponse.status}.`);
  const target = await targetResponse.json();
  const client = connectDebugger(target.webSocketDebuggerUrl);
  await client.ready;
  await client.send('Runtime.enable');
  await client.send('Page.enable');
  await client.send('Emulation.setDeviceMetricsOverride', {
    width: 1440,
    height: 900,
    deviceScaleFactor: 1,
    mobile: false,
  });

  await waitForExpression(client, 'Boolean(document.querySelector("input[name=username]"))');
  const loginExpression = `(() => {
    const username = document.querySelector('input[name=username]');
    const password = document.querySelector('input[name=password]');
    username.value = ${JSON.stringify(username)};
    password.value = ${JSON.stringify(password)};
    username.dispatchEvent(new Event('input', { bubbles: true }));
    password.dispatchEvent(new Event('input', { bubbles: true }));
    username.form.requestSubmit();
    return true;
  })()`;
  await client.send('Runtime.evaluate', { expression: loginExpression, returnByValue: true });
  await waitForExpression(client, 'location.pathname.startsWith("/dashboard") && Boolean(document.querySelector(".app-shell"))');

  mkdirSync(outputDirectory, { recursive: true });
  for (const screenshot of screenshots) {
    await navigate(client, `${baseUrl}${screenshot.path}`);
    await waitForExpression(client, 'Boolean(document.querySelector(".app-shell"))');
    await new Promise(resolvePromise => setTimeout(resolvePromise, 250));
    const capture = await client.send('Page.captureScreenshot', {
      format: 'webp',
      quality: 92,
      fromSurface: true,
      captureBeyondViewport: false,
    });
    const destination = join(outputDirectory, screenshot.filename);
    const candidate = `${destination}.capture`;
    writeFileSync(candidate, Buffer.from(capture.data, 'base64'), { flag: 'wx' });
    if (statSync(candidate).size < 20_000) throw new Error(`Screenshot is unexpectedly small: ${candidate}`);
    copyFileSync(candidate, destination);
    rmSync(candidate);
    console.log(`Captured ${destination} (${statSync(destination).size.toLocaleString()} bytes)`);
  }

  client.socket.close();
} catch (error) {
  throw new Error(`${error.message}${chromeErrors ? `\nChrome: ${chromeErrors.slice(-2000)}` : ''}`);
} finally {
  const chromeExited = chrome.exitCode !== null
    ? Promise.resolve()
    : new Promise(resolvePromise => chrome.once('exit', resolvePromise));
  chrome.kill();
  await Promise.race([chromeExited, new Promise(resolvePromise => setTimeout(resolvePromise, 5_000))]);
  for (let attempt = 0; attempt < 5 && existsSync(chromeProfile); attempt += 1) {
    try {
      rmSync(chromeProfile, { recursive: true, force: true, maxRetries: 3, retryDelay: 250 });
    } catch (error) {
      if (attempt === 4) console.warn(`Chrome profile cleanup deferred: ${error.message}`);
      else await new Promise(resolvePromise => setTimeout(resolvePromise, 500));
    }
  }
}
