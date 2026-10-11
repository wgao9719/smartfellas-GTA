/* Run the actual dashboard script with deferred HTTP responses and a small DOM. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');

class Element {
  constructor() {
    this.children = [];
    this.dataset = {};
    this.events = {};
    this.value = '';
    this.hidden = false;
    this._text = '';
  }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  append(...children) { this.children.push(...children); }
  setAttribute() {}
  addEventListener(name, callback) { this.events[name] = callback; }
  emit(name) { return this.events[name]?.({preventDefault() {}}); }
  showModal() {}
  close() {}
  focus() {}
  scrollIntoView() {}
}

function dashboard() {
  const nodes = new Map();
  const get = id => {
    if (!nodes.has(id)) nodes.set(id, new Element());
    return nodes.get(id);
  };
  const requests = [];
  const window = new Element();
  const context = vm.createContext({
    document: {getElementById: get, createElement: () => new Element()},
    window,
    fetch(url, options) {
      return new Promise(resolve => requests.push({
        url, method: options.method,
        respond(data, ok = true) { resolve({ok, json: async () => data}); },
      }));
    },
  });
  const html = fs.readFileSync(path.join(__dirname, '../service/lls/dashboard.html'), 'utf8');
  const source = html.match(/<script nonce="__CSP_NONCE__">([\s\S]*?)<\/script>/)[1]
    .replace('__ADMIN_BOOTSTRAP__', '{token:"fixture",csrf:"fixture"}')
    .replace(/\n  run\(refresh\);\s*$/, '\n');
  vm.runInContext(source, context);
  const run = code => vm.runInContext(code, context);
  run("state.profile='pilot';state.actor={id:'npc'};el('diagnostic-session').value='session';");
  get('export-panel').hidden = true;
  return {get, requests, run, window};
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const diagnostic = text => ({
  closed: false, protagonist_id: 'franklin', game_datetime: '2025-01-01', provider: 'fake',
  actors: [], world: {}, commands: [{payload: {text}}], trace: [], social: {text},
});
const memory = text => ({
  memory_id: 'memory', text, provenance: 'heard', protagonist_id: 'franklin',
  game_datetime: '2025-01-01', branch_id: 'branch', source_event_ids: ['source'],
  importance: 0.5, confidence: 1,
});
function contents(node) {
  return [node.textContent, node.value, ...node.children.map(contents)].join(' ');
}

for (const boundary of ['close', 'pagehide', 'profile_change', 'mutation']) {
  test(`an export response cannot restore text after ${boundary}`, async () => {
    const page = dashboard();
    const exporting = page.get('export-profile').emit('click');
    const oldExport = page.requests[0];
    if (boundary === 'close') page.get('close-export').emit('click');
    if (boundary === 'pagehide') page.window.emit('pagehide');
    if (boundary === 'profile_change') await page.run("state.profile='';loadProfile()");
    if (boundary === 'mutation') {
      const mutation = page.run("api('/profiles/pilot/actors/npc/reset','POST',{})");
      page.requests[1].respond({status: 'reset'});
      await mutation;
    }
    oldExport.respond({private_text: 'ERASED_PRIVATE_MARKER'});
    await exporting;
    assert.equal(page.get('export-content').value, '');
    assert.equal(page.get('export-panel').hidden, true);
  });
}

test('only the newest export response can populate the export panel', async () => {
  const page = dashboard();
  const first = page.get('export-profile').emit('click');
  const second = page.get('export-profile').emit('click');
  page.requests[1].respond({text: 'current'});
  await second;
  page.requests[0].respond({text: 'ERASED_PRIVATE_MARKER'});
  await first;
  assert.match(page.get('export-content').value, /current/);
  assert.doesNotMatch(page.get('export-content').value, /ERASED_PRIVATE_MARKER/);
  assert.equal(page.get('export-panel').hidden, false);
});

test('a character export cannot restore the previous character after selection changes', async () => {
  const page = dashboard();
  const exporting = page.get('export-actor').emit('click');
  assert.ok(page.requests[0].url.endsWith('/profiles/pilot/actors/npc/export'));
  page.run('loadMemories=async()=>{}');
  await page.run("selectActor({id:'other',actor_kind:'curated_persistent'})");
  page.requests[0].respond({private_text: 'ERASED_PRIVATE_MARKER'});
  await exporting;
  assert.equal(page.get('export-content').value, '');
  assert.equal(page.get('export-panel').hidden, true);
});

test('the selected character export is displayed and cleared by mutations', async () => {
  const page = dashboard();
  const exporting = page.get('export-actor').emit('click');
  page.requests[0].respond({actor_id: 'npc', memories: [{text: 'private character memory'}]});
  await exporting;
  assert.match(page.get('export-content').value, /private character memory/);
  assert.equal(page.get('export-panel').hidden, false);
  const mutation = page.run("api('/profiles/pilot/actors/npc/reset','POST',{})");
  page.requests[1].respond({status: 'reset'});
  await mutation;
  assert.equal(page.get('export-content').value, '');
  assert.equal(page.get('export-panel').hidden, true);
});

test('actor reset clears rendered text and rejects old diagnostics and memory reads', async () => {
  const page = dashboard();
  const oldMemories = page.run('loadMemories()');
  const oldDiagnostics = page.run('loadDiagnostics()');
  const staleMemoryRequest = page.requests[0];
  const staleDiagnosticRequest = page.requests[1];
  for (const id of ['memory-list', 'diagnostic-commands', 'diagnostic-perception'])
    page.get(id).textContent = 'ERASED_PRIVATE_MARKER';
  const reset = page.get('reset-actor').emit('click');
  page.run('finishConfirmation(true)');
  await flush();
  const mutation = page.requests.find(request => request.method === 'POST');
  assert.ok(mutation.url.endsWith('/actors/npc/reset'));
  mutation.respond({status: 'reset'});
  await flush();
  for (const id of ['memory-list', 'diagnostic-commands', 'diagnostic-perception'])
    assert.doesNotMatch(contents(page.get(id)), /ERASED_PRIVATE_MARKER/);
  const reads = page.requests.filter(request => request.method === 'GET');
  assert.equal(reads.length, 4);
  reads[2].respond({memories: []});
  reads[3].respond(diagnostic('current'));
  await reset;
  staleMemoryRequest.respond({memories: [memory('ERASED_PRIVATE_MARKER')]});
  staleDiagnosticRequest.respond(diagnostic('ERASED_PRIVATE_MARKER'));
  await Promise.all([oldMemories, oldDiagnostics]);
  assert.match(page.get('diagnostic-commands').textContent, /current/);
  for (const id of ['memory-list', 'diagnostic-commands', 'diagnostic-perception'])
    assert.doesNotMatch(contents(page.get(id)), /ERASED_PRIVATE_MARKER/);
});

test('an older memory page cannot replace a newer response for the same actor', async () => {
  const page = dashboard();
  const first = page.run('loadMemories()');
  const second = page.run('state.offset=20;loadMemories()');
  page.requests[1].respond({memories: [memory('current memory')]});
  await second;
  page.requests[0].respond({memories: [memory('ERASED_PRIVATE_MARKER')]});
  await first;
  assert.match(contents(page.get('memory-list')), /current memory/);
  assert.doesNotMatch(contents(page.get('memory-list')), /ERASED_PRIVATE_MARKER/);
});

test('an old overview response cannot restart stale reads after a mutation', async () => {
  const page = dashboard();
  page.run('loadProvider=async()=>{}');
  const refreshing = page.run('refresh()');
  await flush();
  const mutation = page.run("api('/profiles/pilot/actors/npc/reset','POST',{})");
  page.requests[1].respond({status: 'reset'});
  await mutation;
  page.requests[0].respond({provider: 'fake', sessions: [], profiles: []});
  await refreshing;
  assert.equal(page.requests.length, 2);
});
