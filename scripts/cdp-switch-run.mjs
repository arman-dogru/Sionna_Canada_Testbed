const port = process.argv[2] ?? '9222';
const targetRun = process.argv[3];
if (!targetRun) throw new Error('Target run id is required');

const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === 'page' && item.url.includes('127.0.0.1:8000'));
if (!page) throw new Error('No Ottawa visualization page found');

const socket = new WebSocket(page.webSocketDebuggerUrl);
let sequence = 0;
const pending = new Map();
const exceptions = [];
const request = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++sequence;
  pending.set(id, {resolve, reject});
  socket.send(JSON.stringify({id, method, params}));
});
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.method === 'Runtime.exceptionThrown') {
    exceptions.push(message.params.exceptionDetails.exception?.description ?? message.params.exceptionDetails.text);
  }
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
await request('Runtime.enable');
await new Promise(resolve => setTimeout(resolve, 10000));
const switched = await request('Runtime.evaluate', {
  expression: `(() => {
    const target = ${JSON.stringify(targetRun)};
    const select = [...document.querySelectorAll('select')].find(item =>
      [...item.options].some(option => option.value === target));
    if (!select) return {error: 'run selector not found'};
    const before = select.value;
    select.value = target;
    select.dispatchEvent(new Event('change', {bubbles: true}));
    return {before, requested: target};
  })()`,
  returnByValue: true,
});
await new Promise(resolve => setTimeout(resolve, 3500));
const state = await request('Runtime.evaluate', {
  expression: `(() => {
    const target = ${JSON.stringify(targetRun)};
    const select = [...document.querySelectorAll('select')].find(item =>
      [...item.options].some(option => option.value === target));
    return {
      selected: select?.value,
      cameraStatus: document.querySelector('.cesium-status span')?.textContent,
      coverageStatus: document.querySelector('.cesium-status strong')?.textContent,
    };
  })()`,
  returnByValue: true,
});
console.log(JSON.stringify({switched: switched.result.value, state: state.result.value, exceptions}, null, 2));
socket.close();
