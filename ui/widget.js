/* ===========================================================================
   DeepSeek Peak / Off-Peak widget — renderer.

   The card shows one thing: the countdown to the next price change. Everything
   else (timezone, switches) lives in the sheet behind the gear.

   All schedule facts arrive from schedule.py (the single source of truth) as a
   payload; this file only draws them and counts the seconds down locally
   between resyncs.  Without the Python host the page still renders from
   ui/state.preview.json, so the design can be reviewed in any browser.
   =========================================================================== */

'use strict';

const TICK_MS = 250;
const RESYNC_EVERY_MS = 5 * 60 * 1000;
const PREVIEW_STATE = 'state.preview.json';
// WebView2 can take several seconds to inject the host API on a cold start, so
// the page waits rather than immediately falling back to the preview payload.
const BRIDGE_WAIT_ATTEMPTS = 30;
const BRIDGE_WAIT_MS = 200;
const BRIDGE_RETRY_MS = 3000;

const API_METHODS = [
  'get_state',
  'set_tz',
  'set_pref',
  'hide_window',
  'quit_app',
  'boot_report',
];

const el = (id) => document.getElementById(id);
const pad = (value) => String(value).padStart(2, '0');
const esc = (value) =>
  String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  })[char]);

let state = null;
let anchor = null; // { ms, remaining }
let lastMode = null;
let previewMode = false;
let frozen = false;
let pending = false;

/* ------------------------------------------------------------- bridge --- */

const hasBridge = () => Boolean(window.pywebview && window.pywebview.api);

async function call(name, ...args) {
  if (!hasBridge()) return null;
  try {
    return await window.pywebview.api[name](...args);
  } catch (error) {
    console.error('[widget] bridge call failed:', name, error);
    return null;
  }
}

/** Every interaction and boot stage goes to widget.log (the widget has no console). */
function report(stage, detail = '') {
  call('boot_report', stage, String(detail));
}

async function waitForBridge(attempts = BRIDGE_WAIT_ATTEMPTS, delayMs = BRIDGE_WAIT_MS) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    if (hasBridge()) return true;
    await new Promise((resolve) => setTimeout(resolve, delayMs));
  }
  return false;
}

/* -------------------------------------------------------- formatting --- */
/* Mirrors schedule.fmt_countdown.  A pure formatter only: the payload is
   authoritative and every boundary crossing triggers a resync, so the two can
   never disagree by more than the current second. */

function fmtCountdown(seconds) {
  const total = Math.max(0, Math.floor(seconds));
  const days = Math.floor(total / 86400);
  const rest = total % 86400;
  const hours = Math.floor(rest / 3600);
  const minutes = Math.floor((rest % 3600) / 60);
  const secs = rest % 60;
  return days
    ? `${days}d ${hours}:${pad(minutes)}:${pad(secs)}`
    : `${hours}:${pad(minutes)}:${pad(secs)}`;
}

/* ------------------------------------------------------------ render --- */

function render(next) {
  state = next;
  frozen = false;

  const root = el('widget');
  root.dataset.mode = next.mode;
  // One material, two densities: "clear" over a dark backdrop, "dense" over a
  // bright one (see styles.css).  With the glass switched off there is no native
  // blur to be transparent with, so the card paints itself solid instead.
  const on = next.glass_enabled !== false;
  root.dataset.glass = on ? (next.glass === 'dense' ? 'dense' : 'clear') : 'off';
  if (lastMode && lastMode !== next.mode) {
    report('price flipped to', next.mode);
  }
  lastMode = next.mode;

  el('badge-label').textContent = next.mode_label;
  el('badge').title = [
    rulesTitle(next.rules),
    holidayNote(next.holiday),
    next.next.line,
  ]
    .filter(Boolean)
    .join('\n');

  const digits = el('countdown');
  digits.textContent = next.countdown.text;
  digits.dataset.long = String(Boolean(next.countdown.long));
  el('countdown-suffix').textContent = next.countdown.suffix;

  // When the next change is not today, the day is what makes a long off-peak
  // stretch read as correct rather than broken, and it takes the offset's place.
  const day = next.next.day || '';
  const tail = day ? '' : ` ${esc(next.next.abbr)}`;
  el('next-line').innerHTML =
    `${esc(next.next.phrase)} ${esc(day)}<b>${esc(next.next.clock)}</b>${tail}`;

  renderChips(next.tz);
  if (next.prefs) renderToggles(next.prefs);

  anchor = { ms: Date.now(), remaining: next.countdown.seconds };
  tick();
  reportGeometry();
}

function rulesTitle(rules) {
  return (
    `Peak ${rules.peak_utc.join(' & ')} UTC, ${rules.days}. ` +
    'Weekends and Chinese public holidays are off-peak all day.'
  );
}

/** Why a window can run for days: a public holiday plus the weekend it touches. */
function holidayNote(holiday) {
  if (!holiday) return '';
  if (holiday.today) {
    return `${holiday.today.name} today — Chinese public holidays are off-peak all day.`;
  }
  const next = (holiday.upcoming || [])[0];
  if (!next || next.in_days > 10) return '';
  const when = next.in_days === 1 ? 'tomorrow' : `in ${next.in_days} days`;
  return `${next.name} ${when} — Chinese public holidays are off-peak all day.`;
}

/**
 * Geometry trace.  A widget that renders its controls outside the window looks
 * "broken" from the outside with nothing in the log, so say what actually
 * fitted: viewport, card box and content height.
 */
function reportGeometry() {
  const card = document.querySelector('.card').getBoundingClientRect();
  const sheet = el('sheet');
  report(
    'layout',
    `viewport=${window.innerWidth}x${window.innerHeight}` +
      ` card=${Math.round(card.width)}x${Math.round(card.height)}` +
      ` content=${document.documentElement.scrollHeight}` +
      ` sheet=${sheet.hasAttribute('hidden') ? 'closed' : 'open'}` +
      ` dpr=${window.devicePixelRatio}`,
  );
  if (document.documentElement.scrollHeight > window.innerHeight + 1) {
    report('warning', 'content is taller than the window — something is cut off');
  }
  if (card.height > window.innerHeight + 1) {
    report('warning', 'card is taller than the viewport');
  }
}

function renderChips(tz) {
  const host = el('tz-chips');
  if (host.dataset.built !== '1') {
    host.innerHTML = '';
    tz.choices.forEach((choice) => {
      const chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip';
      chip.dataset.tz = choice.id;
      chip.textContent = choice.short;
      chip.title = `${choice.label} — ${choice.id}`;
      chip.addEventListener('click', () => chooseTimezone(choice.id));
      host.append(chip);
    });
    host.dataset.built = '1';
  }
  host.querySelectorAll('.chip').forEach((chip) => {
    chip.setAttribute('aria-pressed', String(chip.dataset.tz === tz.selected));
  });
}

function renderToggles(prefs) {
  el('sw-on-top').setAttribute('aria-checked', String(Boolean(prefs.on_top)));
  el('sw-autostart').setAttribute('aria-checked', String(Boolean(prefs.autostart)));
  el('sw-notify').setAttribute('aria-checked', String(Boolean(prefs.notify)));
  el('sw-glass').setAttribute('aria-checked', String(Boolean(prefs.glass)));
}

/* ------------------------------------------------------------- ticking --- */

function tick() {
  if (!anchor || frozen) return;
  const elapsed = (Date.now() - anchor.ms) / 1000;
  const remaining = anchor.remaining - elapsed;

  const digits = el('countdown');
  digits.textContent = fmtCountdown(remaining);
  digits.dataset.long = String(remaining >= 86400);

  if (remaining <= 0) {
    if (previewMode) {
      frozen = true;
      digits.textContent = fmtCountdown(0);
    } else {
      resync();
    }
  }
}

async function resync() {
  if (pending) return;
  pending = true;
  const fresh = await call('resync');
  pending = false;
  if (fresh) render(fresh);
}

/* ------------------------------------------------------------ actions --- */

async function chooseTimezone(id) {
  report('chip click', id);
  const fresh = await call('set_tz', id);
  if (fresh) render(fresh);
  else report('host unavailable', `set_tz ${id}`);
}

function bindSwitch(id, key) {
  el(id).addEventListener('click', async () => {
    const wanted = el(id).getAttribute('aria-checked') !== 'true';
    report('switch click', `${key}=${wanted}`);
    const fresh = await call('set_pref', key, wanted);
    if (fresh) render(fresh);
    else report('host unavailable', `set_pref ${key}`);
  });
}

function setSheet(open) {
  el('sheet').toggleAttribute('hidden', !open);
  el('btn-settings').setAttribute('aria-expanded', String(open));
  report('settings panel', open ? 'opened' : 'closed');
  reportGeometry();
}

/* ---------------------------------------------------------- gestures --- */
/* The window is frameless, so neither pywebview's drag nor the OS resize loop
   can move it: the host performs both gestures itself.  All this side does is
   say where the drag started. */

const INTERACTIVE = 'button, input, select, textarea, a, .chip, .switch, .grip';

document.querySelector('.card').addEventListener('mousedown', (event) => {
  if (event.button !== 0 || event.target.closest(INTERACTIVE)) return;
  report('gesture', 'move');
  call('begin_move');
});

el('grip').addEventListener('mousedown', (event) => {
  if (event.button !== 0) return;
  event.preventDefault();
  event.stopPropagation();
  report('gesture', 'resize');
  call('begin_resize');
});

el('btn-settings').addEventListener('click', () => {
  setSheet(el('sheet').hasAttribute('hidden'));
});
el('sheet-close').addEventListener('click', () => setSheet(false));
el('btn-hide').addEventListener('click', () => {
  report('action', 'hide to tray');
  call('hide_window');
});
el('btn-quit').addEventListener('click', () => {
  report('action', 'quit');
  call('quit_app');
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') setSheet(false);
});

bindSwitch('sw-on-top', 'on_top');
bindSwitch('sw-autostart', 'autostart');
bindSwitch('sw-notify', 'notify');
bindSwitch('sw-glass', 'glass');

/* --------------------------------------------------------------- boot --- */

async function bootFromBridge() {
  const fresh = await call('get_state');
  if (!fresh) return false;
  previewMode = false;
  render(fresh);
  report('live state loaded');
  return true;
}

async function bootFromPreview() {
  previewMode = true;
  try {
    const response = await fetch(PREVIEW_STATE, { cache: 'no-store' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    render(data);
    report('preview fallback', data.generated_at);
  } catch (error) {
    console.warn('[widget] no preview state:', error);
    el('next-line').textContent = 'run run.cmd to start the widget';
    report('preview unavailable', error.message);
  }
}

(async function main() {
  const hosted = new URLSearchParams(window.location.search).has('host');
  report(
    'script loaded',
    `hosted=${hosted} bridge=${hasBridge()} ` +
      API_METHODS.map((name) => `${name}=${typeof (window.pywebview && window.pywebview.api ? window.pywebview.api[name] : undefined)}`).join(' '),
  );

  if (hosted) {
    // Inside the widget: prefer live data, and never leave a stale countdown on
    // screen while WebView2 is still injecting its API.
    const bridge = await waitForBridge();
    report('bridge wait finished', String(bridge));
    if (!bridge || !(await bootFromBridge())) await bootFromPreview();
  } else {
    await bootFromPreview();
  }

  window.setInterval(tick, TICK_MS);
  window.setInterval(() => {
    if (!previewMode) resync();
  }, RESYNC_EVERY_MS);

  // Self-healing: if the host turns up late, swap the preview for live data.
  window.setInterval(() => {
    if (previewMode && hasBridge()) bootFromBridge();
  }, BRIDGE_RETRY_MS);

  window.addEventListener('pywebviewready', async () => {
    report('pywebviewready event');
    if (!previewMode) return;
    if (await bootFromBridge()) reportGeometry();
  });

  window.addEventListener('error', (event) => report('js error', event.message));
})();

/* The host re-anchors the countdown once a second.  Chromium throttles — and
   eventually freezes — the timers of a window that is occluded by other windows,
   which stopped the countdown dead while the card sat behind the user's apps.  A
   forced script call still runs (the UI test relies on that), so this is the one
   path that works even then. */
window.__widgetTick = (seconds) => {
  if (!Number.isFinite(seconds)) return;
  previewMode = false;
  frozen = false;
  anchor = { ms: Date.now(), remaining: seconds };
  tick();
};

/* Called by app.py when the price flips, so the card updates the moment the
   host notices instead of waiting for the next local resync. */
window.__widgetPush = (next) => {
  previewMode = false;
  frozen = false;
  render(next);
};

/* app.py samples the desktop behind the card and reports how thick the glass
   needs to be; the harness drives the same hook to measure both. */
window.__setGlass = (mode) => {
  document.getElementById('widget').dataset.glass = mode === 'dense' ? 'dense' : 'clear';
};
