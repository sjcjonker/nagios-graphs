// Dependency-free browser-event smoke tests. Run with node tests/test_nagios_rrd_hover.js.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(path.join(__dirname, '../scripts/nagios-rrd-hover.js'), 'utf8');

function setup(href, pointerType = 'mouse', graphPath) {
  const listeners = {}, elements = [], timers = new Map();
  let timerId = 0;
  const anchor = {href, contains: target => target === anchor};
  const icon = {closest: () => anchor};
  const document = {
    createElement: tag => {
      const element = {tag, style: {}, children: [], setAttribute() {},
        appendChild(child) { this.children.push(child); },
        removeAttribute(name) { delete this[name]; }};
      elements.push(element);
      return element;
    },
    addEventListener: (name, handler) => { listeners[name] = handler; },
    body: {appendChild() {}}
  };
  vm.runInNewContext(source, {document, URL, Date, location: {
    origin: 'https://nagios.example', href: 'https://nagios.example/nagios4/cgi-bin/status.cgi'
  }, window: {NagiosGraphs: graphPath ? {path: graphPath} : undefined, innerWidth: 1200, innerHeight: 900, addEventListener() {}},
  setTimeout: handler => { timers.set(++timerId, handler); return timerId; },
  clearTimeout: id => timers.delete(id)});
  listeners.pointerover({target: icon, pointerType, clientX: 100, clientY: 100});
  return {tip: elements[0], image: elements[1], timers, listeners, icon, anchor,
    flush() { for (const handler of timers.values()) handler(); timers.clear(); }};
}

for (const query of [
  'view=power', 'view=ups', 'view=interface-uplink',
  'view=uplink-errors', 'view=uplink-discards',
  new URLSearchParams({host: 'router01', service: 'Uplink 1/2'}).toString(),
  new URLSearchParams({host: 'switch', service: 'Port 01 - Office & backup'}).toString()
]) {
  const input = new URL('https://nagios.example/nagios4/cgi-bin/graphs.cgi?' + query);
  const test = setup(input.href);
  assert.equal(test.tip.style.display, 'none');
  assert.equal(test.image.src, undefined); // No request before the hover delay.
  assert.equal(test.timers.size, 1);
  test.flush();
  assert.equal(test.tip.style.display, 'block');
  const preview = new URL(test.image.src);
  assert.equal(preview.searchParams.get('format'), 'png');
  assert.equal(preview.searchParams.get('period'), 'hour');
  assert.ok(preview.searchParams.has('preview'));
  for (const name of ['host', 'service']) {
    assert.equal(preview.searchParams.get(name), input.searchParams.get(name));
  }
  if (input.searchParams.has('view')) {
    assert.equal(preview.searchParams.get('view'), input.searchParams.get('view'));
  } else {
    assert.equal(preview.searchParams.has('view'), false);
  }
  test.listeners.pointerout({target: test.anchor, relatedTarget: null});
  assert.equal(test.tip.style.display, 'none');
  assert.equal(test.image.src, undefined);
}

for (const href of [
  'https://other.example/nagios4/cgi-bin/graphs.cgi?host=router01&service=Interface',
  '/other.cgi?host=router01&service=Interface',
  '/nagios4/cgi-bin/graphs.cgi?host=router01',
  '/nagios4/cgi-bin/graphs.cgi?service=Interface',
  '/nagios4/cgi-bin/graphs.cgi?host=&service=Interface',
  '/nagios4/cgi-bin/graphs.cgi?host=router01&service=%20',
  '/nagios4/cgi-bin/graphs.cgi?view=../bad&host=router01&service=Interface'
]) assert.equal(setup(href).timers.size, 0, href);

const touch = setup('/nagios4/cgi-bin/graphs.cgi?host=router01&service=Interface', 'touch');
assert.equal(touch.timers.size, 0);
const cancelled = setup('/nagios4/cgi-bin/graphs.cgi?host=router01&service=Interface');
cancelled.listeners.pointerout({target: cancelled.anchor, relatedTarget: null});
assert.equal(cancelled.timers.size, 0);
console.log('Hover previews passed: generic and host/service links, delayed PNG, encoding, cancellation and scope');


const generic = setup('/graphs.cgi?view=server-temperature', 'mouse', '/graphs.cgi');
assert.equal(generic.timers.size, 1);
generic.flush();
assert.equal(new URL(generic.image.src).pathname, '/graphs.cgi');
assert.equal(setup('/graphs.cgi?view=../bad', 'mouse', '/graphs.cgi').timers.size, 0);
assert.equal(setup('https://other.example/graphs.cgi?view=test', 'mouse', '/graphs.cgi').timers.size, 0);
