import { spawn } from 'node:child_process';
import { createServer } from 'node:http';
import { createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDirectory = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(scriptDirectory, '..');
const videoPath = join(projectRoot, 'static', 'pos', 'media', 'oxpos-60-second-story-v3.webm');
const posterPath = join(projectRoot, 'static', 'pos', 'images', 'product', 'oxpos-demo-story-poster-v3.png');
const chromePath = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const duration = 60_000;

if (!existsSync(chromePath)) throw new Error(`Chrome was not found at ${chromePath}.`);
if (existsSync(videoPath)) throw new Error(`Refusing to overwrite ${videoPath}. Remove it explicitly before recording again.`);

const mimeTypes = {
  '.css': 'text/css; charset=utf-8',
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.png': 'image/png',
  '.wav': 'audio/wav',
  '.webm': 'video/webm',
  '.webp': 'image/webp',
};

let captureResolve;
let captureReject;
const captureComplete = new Promise((resolvePromise, rejectPromise) => {
  captureResolve = resolvePromise;
  captureReject = rejectPromise;
});

const server = createServer((request, response) => {
  const requestUrl = new URL(request.url, 'http://127.0.0.1');
  if (request.method === 'POST' && requestUrl.pathname === '/__capture') {
    mkdirSync(dirname(videoPath), { recursive: true });
    const output = createWriteStream(videoPath, { flags: 'wx' });
    request.pipe(output);
    request.on('error', captureReject);
    output.on('error', error => {
      response.writeHead(error.code === 'EEXIST' ? 409 : 500).end();
      captureReject(error);
    });
    output.on('finish', () => {
      const size = statSync(videoPath).size;
      if (size < 100_000) {
        const error = new Error(`Recorded video is unexpectedly small: ${size} bytes.`);
        response.writeHead(500).end();
        captureReject(error);
        return;
      }
      response.writeHead(204).end();
      captureResolve(size);
    });
    return;
  }

  if (request.method !== 'GET') {
    response.writeHead(405).end();
    return;
  }
  const relativePath = decodeURIComponent(requestUrl.pathname).replace(/^\/+/, '');
  const filePath = normalize(resolve(projectRoot, relativePath));
  if (!filePath.startsWith(`${projectRoot}\\`) || !existsSync(filePath)) {
    response.writeHead(404).end();
    return;
  }
  response.writeHead(200, {
    'Content-Type': mimeTypes[extname(filePath).toLowerCase()] || 'application/octet-stream',
    'Cache-Control': 'no-store',
  });
  response.end(readFileSync(filePath));
});

function listen(serverInstance) {
  return new Promise((resolvePromise, rejectPromise) => {
    serverInstance.once('error', rejectPromise);
    serverInstance.listen(0, '127.0.0.1', () => resolvePromise(serverInstance.address().port));
  });
}

async function freePort() {
  const probe = createServer();
  const port = await listen(probe);
  await new Promise(resolvePromise => probe.close(resolvePromise));
  return port;
}

async function waitForDebugger(port) {
  let lastError;
  for (let attempt = 0; attempt < 120; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}/json/version`);
      if (response.ok) return;
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

const serverPort = await listen(server);
const debuggerPort = await freePort();
const chromeProfile = join(tmpdir(), `oxpos-video-${process.pid}`);
const pageUrl = `http://127.0.0.1:${serverPort}/scripts/demo_recorder.html?record=1&duration=${duration}`;
const chrome = spawn(
  chromePath,
  [
    '--headless=new',
    '--disable-gpu',
    '--autoplay-policy=no-user-gesture-required',
    `--remote-debugging-port=${debuggerPort}`,
    `--user-data-dir=${chromeProfile}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--hide-scrollbars',
    '--window-size=1280,720',
    'about:blank',
  ],
  { stdio: ['ignore', 'ignore', 'pipe'] },
);

let chromeErrors = '';
chrome.stderr.on('data', chunk => { chromeErrors += chunk.toString(); });

try {
  await waitForDebugger(debuggerPort);
  const targetResponse = await fetch(`http://127.0.0.1:${debuggerPort}/json/new?${encodeURIComponent(pageUrl)}`, { method: 'PUT' });
  if (!targetResponse.ok) throw new Error(`Chrome target creation failed with ${targetResponse.status}.`);
  const target = await targetResponse.json();
  const debuggerClient = connectDebugger(target.webSocketDebuggerUrl);
  await debuggerClient.ready;
  await debuggerClient.send('Runtime.enable');
  await debuggerClient.send('Page.enable');
  await debuggerClient.send('Emulation.setDeviceMetricsOverride', {
    width: 1280,
    height: 720,
    deviceScaleFactor: 1,
    mobile: false,
  });

  const deadline = Date.now() + 90_000;
  let state = 'loading';
  while (Date.now() < deadline) {
    const evaluation = await debuggerClient.send('Runtime.evaluate', {
      expression: '({state: window.recordingState, bytes: window.recordingBytes || 0, narrationDuration: window.narrationDuration || 0})',
      returnByValue: true,
    });
    const status = evaluation.result.value || {};
    state = status.state || 'loading';
    if (String(state).startsWith('error:')) throw new Error(state);
    if (state === 'complete') break;
    await new Promise(resolvePromise => setTimeout(resolvePromise, 500));
  }
  if (state !== 'complete') throw new Error(`Recording timed out in state: ${state}`);

  const recordedSize = await captureComplete;
  const screenshot = await debuggerClient.send('Page.captureScreenshot', {
    format: 'png',
    fromSurface: true,
    captureBeyondViewport: false,
  });
  writeFileSync(posterPath, Buffer.from(screenshot.data, 'base64'), { flag: 'wx' });
  const posterSize = statSync(posterPath).size;
  debuggerClient.socket.close();
  console.log(`Recorded ${videoPath} (${recordedSize.toLocaleString()} bytes)`);
  console.log(`Captured ${posterPath} (${posterSize.toLocaleString()} bytes)`);
} catch (error) {
  if (existsSync(videoPath) && statSync(videoPath).size < 100_000) rmSync(videoPath);
  throw new Error(`${error.message}${chromeErrors ? `\nChrome: ${chromeErrors.slice(-2000)}` : ''}`);
} finally {
  const chromeExited = chrome.exitCode !== null
    ? Promise.resolve()
    : new Promise(resolvePromise => chrome.once('exit', resolvePromise));
  chrome.kill();
  await Promise.race([chromeExited, new Promise(resolvePromise => setTimeout(resolvePromise, 5_000))]);
  await new Promise(resolvePromise => server.close(resolvePromise));
  for (let attempt = 0; attempt < 5 && existsSync(chromeProfile); attempt += 1) {
    try {
      rmSync(chromeProfile, { recursive: true, force: true, maxRetries: 3, retryDelay: 250 });
    } catch (error) {
      if (attempt === 4) console.warn(`Chrome profile cleanup deferred: ${error.message}`);
      else await new Promise(resolvePromise => setTimeout(resolvePromise, 500));
    }
  }
}
