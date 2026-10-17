// Exercise the actual browser logic without a server or external test packages.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const { Blob } = require('node:buffer');
const elements = new Map();
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.value = ''; this.disabled = false;
    this.textContent = ''; this.events = {}; this.classes = new Set();
    this.style = {};
    this.classList = {add: (...xs) => xs.forEach(x => this.classes.add(x)),
      remove: (...xs) => xs.forEach(x => this.classes.delete(x)),
      toggle: (x, force) => { const on = force ?? !this.classes.has(x); on ? this.classes.add(x) : this.classes.delete(x); }};
  }
  set id(value) { this._id = value; elements.set(value, this); }
  get id() { return this._id; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  addEventListener(name, fn) { this.events[name] = fn; }
  setAttribute() {}
  removeAttribute() {}
  pause() {}
  async play() {}
  focus() {}
  scrollIntoView() {}
  click() { return this.events.click?.(); }
}
const html = fs.readFileSync('static/index.html', 'utf8');
for (const [, tag, id] of html.matchAll(/<(\w+)[^>]*\bid="([^"]+)"/g)) { const el = new Element(tag); el.id = id; }
let serverState = {pages: [], lesson: null, answers: {}, language: 'English', document_language: 'English', filename: '', job: null, partial: false, configured: true};
let requests = [];
const doc = {body: new Element('body'), getElementById: id => elements.get(id),
  createElement: tag => new Element(tag), createTextNode: text => ({textContent: text}),
  querySelectorAll: () => [...elements.values()]};
const context = vm.createContext({document: doc, navigator: {}, console, Blob,
  window: {addEventListener() {}}, URL: {createObjectURL: () => 'blob:test', revokeObjectURL() {}},
  Uint8Array, Float32Array, ArrayBuffer, DataView, setTimeout, clearTimeout, setInterval, clearInterval,
  atob: value => Buffer.from(value, 'base64').toString('binary'),
  fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, json: async () => JSON.parse(JSON.stringify(serverState))};
  }});
vm.runInContext(fs.readFileSync('static/app.js', 'utf8'), context);
const run = source => vm.runInContext(source, context);
(async () => {
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(requests[0].url, '/api/session');
  assert.equal(run('busy'), false);
  assert.equal(elements.get('evaluate').disabled, true);
  run('chooseFile({name:"bad.txt",size:10})');
  assert.match(elements.get('notice').textContent, /PDF/);
  run('chooseFile({name:"good.pdf",size:2048})');
  assert.equal(elements.get('file-label').textContent, 'good.pdf');

  const questions = Array.from({length: 3}, (_, i) => ({question: `Question ${i + 1}?`, explanation: 'Understand this.', page: 1, quote: 'Actual source text.'}));
  serverState = {...serverState, pages: [{page:1,text:'Actual source text.'}], filename: 'Test.pdf', job: 'test-job',
    lesson: {title: 'Clear instructions', summary: 'A concise summary.', questions}};
  context.fixture = serverState;
  run('session=fixture; render()');
  assert.equal(elements.get('question-text').textContent, 'Question 1?');
  assert.equal(elements.get('source-quote').textContent, 'Actual source text.');
  assert.equal(elements.get('question-tabs').children.length, 3);
  assert.equal(elements.get('previous').disabled, true);
  elements.get('answer').value = 'My draft answer';
  run('navigate(1)');
  assert.equal(elements.get('question-text').textContent, 'Question 2?');
  run('navigate(0)');
  assert.equal(elements.get('answer').value, 'My draft answer');

  const assessment = {score:50, attempt:1, answer:'My assessed answer', correction:'Here is the correction.', results:[
    {status:'understood', criterion:'Fact one', evidence:'assessed', feedback:'Good'},
    {status:'missed', criterion:'Fact two', evidence:'', feedback:'Include this too'}]};
  serverState.answers = {'0':assessment,'1':assessment,'2':assessment};
  run('render()');
  assert.ok(!elements.get('report').classes.has('hidden'));
  assert.ok(elements.get('report').children.some(el => el.textContent === '50%'));
  assert.ok(elements.get('feedback').children.length > 3);
  assert.equal(elements.get('completion-note').textContent, '3 of 3 explored');
  assert.equal(elements.get('session-progress-bar').value, 3);
  assert.equal(elements.get('session-progress-label').textContent, '3 / 3');
  assert.match(elements.get('answer-count').textContent, /4,000/);
  const original = run('current'); run('busy=true; navigate(1)');
  assert.equal(run('current'), original, 'Navigation blocked during requests');
  run('busy=false');
  // User text is rendered as textContent, never inserted as HTML.
  serverState.lesson.title = '<img src=x onerror=alert(1)>';
  run('render()');
  assert.equal(elements.get('lesson-title').textContent, '<img src=x onerror=alert(1)>');
  const wav = run('wavBlob([new Float32Array(48000 * 31)],48000)');
  const bytes = Buffer.from(await wav.arrayBuffer());
  assert.equal(bytes.subarray(0,4).toString(), 'RIFF');
  assert.equal(bytes.readUInt32LE(24), 16000);
  assert.equal(bytes.readUInt32LE(40), 16000 * 30 * 2, 'WAV capped at thirty seconds');
  assert.equal(bytes.length, 44 + 16000 * 30 * 2);
  serverState._state = 'signed-session-snapshot';
  await run('api("session")');
  assert.equal(run('stateToken'), 'signed-session-snapshot');
  assert.ok(!Object.hasOwn(serverState, 'audio'));
  await run('api("poll")');
  assert.equal(JSON.parse(requests.at(-1).options.body)._state, 'signed-session-snapshot');
  run('maxPdfBytes=3000000; chooseFile({name:"large.pdf",size:3000001})');
  assert.match(elements.get('notice').textContent, /3 MB/);
  console.log('Frontend checks passed: startup, upload validation, question navigation, drafts, feedback, report, text escaping, WAV encoding and duration.');
})().catch(e => { console.error(e); process.exitCode = 1; });
