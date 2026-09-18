const port = process.argv[2] ?? '9222';
const targetText = process.argv[3] ?? '127.0.0.1:8000';
const pages = await fetch(`http://127.0.0.1:${port}/json`).then(response => response.json());
const page = pages.find(item => item.type === 'page' && item.url.includes(targetText));
if (!page) throw new Error(`No diagnostic page matching ${targetText}`);

const socket = new WebSocket(page.webSocketDebuggerUrl);
let sequence = 0;
const messages = [];
const pending = new Map();
const send = (method, params = {}) => socket.send(JSON.stringify({id: ++sequence, method, params}));
const request = (method, params = {}) => new Promise(resolve => {
  const id = ++sequence;
  pending.set(id, resolve);
  socket.send(JSON.stringify({id, method, params}));
});

socket.addEventListener('open', () => {
  send('Runtime.enable');
  send('Log.enable');
  send('Page.enable');
  send('Network.enable');
  send('Page.reload', {ignoreCache: true});
});
socket.addEventListener('message', event => {
  const message = JSON.parse(event.data);
  if (message.id && pending.has(message.id)) {
    pending.get(message.id)(message.result);
    pending.delete(message.id);
  }
  if (message.method === 'Runtime.exceptionThrown') {
    const detail = message.params.exceptionDetails;
    messages.push({
      type: 'exception',
      text: detail.text,
      description: detail.exception?.description,
      exception: detail.exception,
      url: detail.url,
      line: detail.lineNumber,
      column: detail.columnNumber,
      stack: detail.stackTrace,
    });
  }
  if (message.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(message.params.type)) {
    messages.push({
      type: `console.${message.params.type}`,
      text: message.params.args.map(item => item.value ?? item.description).join(' '),
    });
  }
  if (message.method === 'Log.entryAdded' && ['error', 'warning'].includes(message.params.entry.level)) {
    messages.push({type: `log.${message.params.entry.level}`, text: message.params.entry.text, url: message.params.entry.url});
  }
  if (message.method === 'Network.responseReceived' && message.params.response.status >= 400) {
    messages.push({type: 'http', status: message.params.response.status, url: message.params.response.url});
  }
});

await new Promise(resolve => setTimeout(resolve, 12000));
const evaluation = await request('Runtime.evaluate', {
  expression: `JSON.stringify({text: document.body.innerText, root: document.getElementById('root')?.innerHTML.slice(0, 1000), canvases: [...document.querySelectorAll('canvas')].map(c => ({width:c.width,height:c.height,clientWidth:c.clientWidth,clientHeight:c.clientHeight}))})`,
  returnByValue: true,
});
console.log(JSON.stringify({url: page.url, messages, pageState: JSON.parse(evaluation.result.value)}, null, 2));
socket.close();
