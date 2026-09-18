import { writeFile } from 'node:fs/promises';

const port = process.argv[2] ?? '9222';
const targetText = process.argv[3] ?? '127.0.0.1:8000';
const outputPath = process.argv[4];
if (!outputPath) throw new Error('Output path is required');

const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === 'page' && item.url.includes(targetText));
if (!page) throw new Error(`No page matching ${targetText}`);

const socket = new WebSocket(page.webSocketDebuggerUrl);
let sequence = 0;
const pending = new Map();
const request = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, {resolve, reject});
  socket.send(JSON.stringify({id, method, params}));
});
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  const waiter = pending.get(message.id);
  if (!waiter) return;
  pending.delete(message.id);
  if (message.error) waiter.reject(new Error(message.error.message));
  else waiter.resolve(message.result);
});
await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, {once: true});
  socket.addEventListener('error', reject, {once: true});
});
await request('Page.enable');
await request('Emulation.setDeviceMetricsOverride', {
  width: 1600,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});
await new Promise(resolve => setTimeout(resolve, 15000));
const capture = await request('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
await writeFile(outputPath, Buffer.from(capture.data, 'base64'));
console.log(JSON.stringify({url: page.url, outputPath}));
socket.close();
