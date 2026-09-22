const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/script.js', 'utf8');
assert(!source.includes('Math.random'));

function setup(useUUID) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      textContent: '', value: '', hidden: false, children: [],
      classList: {toggle() {}}, setAttribute() {}, removeAttribute() {},
      pause() {}, load() {}, focus() {}, append() {},
      replaceChildren(...children) { this.children = children; }
    });
    return elements.get(id);
  }
  let serial = 0;
  const crypto = {getRandomValues(bytes) { bytes.fill(++serial); return bytes; }};
  if (useUUID) crypto.randomUUID = () => `secure-uuid-${++serial}`;
  const calls = [];
  const sandbox = {window: {crypto, addEventListener() {}}, Uint8Array,
    document: {getElementById: element, createElement: element}, console,
    URL: {revokeObjectURL() {}}, fetch: async (...args) => { calls.push(args); return {ok: true}; }};
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return {sandbox, element, calls, run: code => vm.runInContext(code, sandbox)};
}

(async () => {
  for (const useUUID of [true, false]) {
    const {sandbox, element, calls, run} = setup(useUUID);
    const original = run('sessionId');
    if (!useUUID) assert.match(original, /^[a-f0-9]{32}$/);
    run("question = {field: 'age'}; skipped = ['age']; blob = {}; objectURL = 'blob:fixture';");
    element('typed-message').value = 'private profile';
    sandbox.fetch = async (...args) => { calls.push(args); return {ok: false}; };
    await element('delete-session').onclick();
    assert.equal(run('sessionId'), original);
    assert.equal(element('typed-message').value, 'private profile');
    sandbox.fetch = async (...args) => { calls.push(args); return {ok: true}; };
    await element('delete-session').onclick();
    assert.equal(calls[1][0], `/session/${original}`);
    assert.equal(calls[1][1].method, 'DELETE');
    assert.notEqual(run('sessionId'), original);
    assert.equal(run('question'), null);
    assert.equal(run('skipped.length'), 0);
    assert.equal(run('blob'), null);
    assert.equal(element('typed-message').value, '');
    assert.equal(element('reply').hidden, true);
    assert.equal(element('results').hidden, true);
    assert.equal(element('aiAudio').hidden, true);
    assert.equal(run('busy'), false);
    run('busy = true');
    await element('delete-session').onclick();
    assert.equal(calls.length, 2);
    run('busy = false; recording = true');
    await element('delete-session').onclick();
    assert.equal(calls.length, 2);
  }
  console.log('Secure IDs, delete success/failure, UI reset and busy guards passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
