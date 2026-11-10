// Run with node tests/test_nagios_rrd_drag.js; no browser libraries required.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync(require('node:path').join(__dirname, '../scripts/nagios-rrd-web.cgi'), 'utf8');
const script = source.match(/SELECT_SCRIPT = """([\s\S]*?)"""/)[1];
function setup(width = 860) {
    const listeners = {};
    const element = name => ({style: {}, hidden: true,
        addEventListener: (type, fn) => { listeners[name + ':' + type] = fn; }});
    const plot = element('plot'), band = element('band'), image = element('image');
    plot.dataset = {view: 'power', period: 'day', bounds: JSON.stringify({graph_left: 70,
        graph_top: 35, graph_width: 860, graph_height: 280, graph_start: 1700000000,
        graph_end: 1700086400, image_width: 1010, image_height: 420})};
    plot.getBoundingClientRect = () => ({left: 100, width});
    let captured = false, destination = null;
    plot.setPointerCapture = () => { captured = true; };
    plot.hasPointerCapture = () => captured;
    plot.releasePointerCapture = () => { captured = false; listeners['plot:lostpointercapture'](); };
    image.complete = true; image.naturalWidth = 1010;
    const elements = {'plot-select': plot, 'plot-band': band, 'plot-image': image, 'zoom-hint': element('hint')};
    const document = {...element('document'), getElementById: id => elements[id] || null};
    vm.runInNewContext(script, {document, window: element('window'), URL, URLSearchParams,
        location: {href: 'https://example.test/graphs.cgi?view=power', assign: url => { destination = url; }}});
    const emit = (type, fraction, extra = {}) => listeners['plot:' + type]({clientX: 100 + width * fraction,
        pointerType: 'mouse', pointerId: 1, isPrimary: true, button: 0, preventDefault() {}, ...extra});
    return {emit, listeners, plot, band, result: () => destination};
}
for (const width of [860, 430]) {
    for (const [from, to] of [[0.25, 0.75], [0.75, 0.25]]) {
        const test = setup(width);
        assert.equal(test.plot.hidden, false);
        assert.equal(test.plot.style.left, (100 * 70 / 1010) + '%');
        test.emit('pointerdown', from); test.emit('pointermove', to);
        assert.equal(test.band.style.width, '50%');
        test.emit('pointerup', to);
        assert.equal(test.result().searchParams.get('start'), '1700021600');
        assert.equal(test.result().searchParams.get('end'), '1700064800');
    }
}
for (const kind of ['touch', 'pen']) {
    const test = setup(); test.emit('pointerdown', 0.1, {pointerType: kind});
    test.emit('pointerup', 0.9, {pointerType: kind}); assert.equal(test.result(), null);
}
for (const end of [0.25, 0.251]) {
    const test = setup(); test.emit('pointerdown', 0.25); test.emit('pointerup', end);
    assert.equal(test.result(), null);
}
for (const cancel of ['plot:pointercancel', 'window:resize', 'window:blur', 'document:keydown']) {
    const test = setup(); test.emit('pointerdown', 0.25);
    test.listeners[cancel]({key: 'Escape'}); test.emit('pointerup', 0.75);
    assert.equal(test.result(), null); assert.equal(test.band.hidden, true);
}
const outside = setup(); outside.emit('pointerdown', 0.25); outside.emit('pointerup', 2);
assert.equal(outside.result().searchParams.get('end'), '1700086400');
console.log('Drag interactions passed: scaled/reverse drag, clamping, small selections, touch and cancellation');

