const port = process.argv[2] ?? '9222';
const targetText = process.argv[3] ?? '127.0.0.1:8000';
const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === 'page' && item.url.includes(targetText));
if (!page) throw new Error(`No page matching ${targetText}`);

const socket = new WebSocket(page.webSocketDebuggerUrl);
let sequence = 0;
const pending = new Map();
const queryRequests = [];
const request = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, {resolve, reject});
  socket.send(JSON.stringify({id, method, params}));
});

socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  const waiter = pending.get(message.id);
  if (waiter) {
    pending.delete(message.id);
    if (message.error) waiter.reject(new Error(message.error.message));
    else waiter.resolve(message.result);
  }
  if (message.method === 'Network.requestWillBeSent' && message.params.request.url.includes('/v1/query?')) {
    queryRequests.push(message.params.request.url);
  }
});

await new Promise((resolve, reject) => {
  socket.addEventListener('open', resolve, {once: true});
  socket.addEventListener('error', reject, {once: true});
});
await request('Page.enable');
await request('Runtime.enable');
await request('Network.enable');
await request('Emulation.setDeviceMetricsOverride', {
  width: 1600,
  height: 1000,
  deviceScaleFactor: 1,
  mobile: false,
});
await request('Page.reload', {ignoreCache: true});
await new Promise(resolve => setTimeout(resolve, 12000));

const pointResult = await request('Runtime.evaluate', {
  expression: `(() => {
    const shell = document.querySelector('.map-shell')?.getBoundingClientRect();
    if (!shell) return null;
    return {x: shell.left + shell.width * 0.57, y: shell.top + shell.height * 0.58};
  })()`,
  returnByValue: true,
});
const point = pointResult.result.value;
if (!point) throw new Error('Planning Map shell was not found');
await request('Input.dispatchMouseEvent', {type: 'mouseMoved', x: point.x, y: point.y});
await request('Input.dispatchMouseEvent', {type: 'mousePressed', x: point.x, y: point.y, button: 'left', clickCount: 1});
await request('Input.dispatchMouseEvent', {type: 'mouseReleased', x: point.x, y: point.y, button: 'left', clickCount: 1});
await new Promise(resolve => setTimeout(resolve, 5000));

const stateResult = await request('Runtime.evaluate', {
  expression: `JSON.stringify({
    text: document.body.innerText,
    latitude: document.querySelector('input')?.value,
  })`,
  returnByValue: true,
});
const state = JSON.parse(stateResult.result.value);
console.log(JSON.stringify({
  clicked: point,
  queryRequestCount: queryRequests.length,
  lastQueryRequest: queryRequests.at(-1) ?? null,
  resultsVisible: /Service predicted|Below threshold/.test(state.text),
  errorVisible: /Query failed:/.test(state.text),
}, null, 2));
socket.close();
