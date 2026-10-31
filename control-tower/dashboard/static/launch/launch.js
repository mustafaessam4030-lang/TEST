/* ATLAS launch page. The launch moment, zone and soundtrack come from the
   server (/api/launch, set by the administrator); the page only counts down
   and plays. Signing in, and everything behind it, is the server's. */
(function(){
  'use strict';
  const $ = (id) => document.getElementById(id);
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pad = (n) => String(n).padStart(2, '0');
  // The dashboard's own light/dark choice, if this browser made one there.
  try {
    const t = localStorage.getItem('ata-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) {}

  // Whole units left; never negative.
  function split(ms){
    const total = Math.max(0, Math.floor(ms / 1000));
    return {total, days: Math.floor(total / 86400), hours: Math.floor(total % 86400 / 3600),
            minutes: Math.floor(total % 3600 / 60), seconds: total % 60};
  }

  function describe(ms, zone){
    const when = new Date(ms);
    const fmt = (tz) => new Intl.DateTimeFormat('en-GB', {timeZone: tz, weekday: 'long', day: 'numeric',
      month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}).format(when);
    let offset = '';
    try {
      const part = new Intl.DateTimeFormat('en-US', {timeZone: zone, timeZoneName: 'longOffset'})
        .formatToParts(when).find((p) => p.type === 'timeZoneName');
      offset = part ? part.value.replace('GMT', 'UTC') : '';
      if (offset === 'UTC') offset = 'UTC+00:00';
      if (zone === 'UTC') return fmt('UTC').replace(' at ', ', ') + ' · UTC';
      const city = zone.split('/').pop().replace(/_/g, ' ') + ' time';
      return fmt(zone).replace(' at ', ', ') + ' · ' + city + (offset ? ' (' + offset + ')' : '');
    } catch (e) {
      return fmt('UTC').replace(' at ', ', ') + ' · UTC';
    }
  }

  /* ── countdown ── */
  let cfg = null, offset = 0, timer = 0, shown = {}, greeted = false;
  const cells = {days: $('cdD'), hours: $('cdH'), minutes: $('cdM'), seconds: $('cdS')};

  function render(left){
    for (const k of Object.keys(cells)){
      const text = k === 'days' ? String(left[k]).padStart(2, '0') : pad(left[k]);
      if (shown[k] !== text){
        cells[k].textContent = text; shown[k] = text;
        if (!calm && k !== 'days'){ cells[k].classList.remove('tick'); void cells[k].offsetWidth; cells[k].classList.add('tick'); }
      }
    }
  }
  function tick(){
    clearTimeout(timer);
    if (!cfg || cfg.state !== 'countdown') return;
    const left = cfg.launch_ms - (Date.now() + offset);
    if (left <= 0){ opened(true); return; }
    render(split(left));
    timer = setTimeout(tick, (left % 1000 || 1000) + 15);
  }
  function opened(justNow){
    clearTimeout(timer);
    if (cfg) cfg.state = 'launched';
    render(split(0));
    $('countdown').hidden = true;
    $('ready').hidden = false;
    $('adminLink').parentElement.hidden = true;
    $('kicker').textContent = 'Now open';
    stopLines('We’re live. Let’s get to work together. Sign in to begin.');
    if (cfg && cfg.launch_ms) $('whenLabel').textContent = 'Opened ' + describe(cfg.launch_ms, cfg.timezone);
    if (justNow) $('cdSpoken').textContent = 'ATLAS is now open. Continue to sign in.';
    wave();
  }
  function apply(c, sentAt, receivedAt){
    cfg = c;
    document.body.dataset.state = c.state;
    if (!greeted && c.state !== 'launched'){
      greeted = true;
      setTimeout(nextLine, calm ? 0 : 500);
    }
    greeted = true;
    offset = c.server_now_ms - (sentAt + receivedAt) / 2;
    if (c.login === '/login') $('enter').href = '/login';
    if (c.state === 'countdown'){
      $('countdown').hidden = false; $('ready').hidden = true;
      $('whenLabel').textContent = 'Opens ' + describe(c.launch_ms, c.timezone);
      tick();
    } else if (c.state === 'launched'){
      opened(false);
    } else {
      // No launch date in use: nothing to count down to, and nothing to hold back.
      if (c.problem) console.warn('Launch date not in use: ' + c.problem);
      $('countdown').hidden = true; $('ready').hidden = false;
      $('adminLink').parentElement.hidden = true;
      $('kicker').textContent = 'Welcome'; $('whenLabel').textContent = '';
    }
    setupAudio(c.audio || {});
  }
  function sync(){
    const sentAt = Date.now();
    return fetch('/api/launch', {credentials: 'same-origin', cache: 'no-store'})
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((c) => apply(c, sentAt, Date.now()))
      .catch(() => {
        if (cfg) return;          // a failed re-sync keeps the countdown it has
        $('countdown').hidden = true; $('ready').hidden = false;
        $('kicker').textContent = 'Welcome';
        $('whenLabel').textContent = 'The launch date could not be loaded.';
        setupAudio({});
      });
  }

  /* ── ATLAS: scripted lines, a blink, a glance, a wave ── */
  const fig = $('atlas'), greet = $('greeting');
  // What ATLAS says, on its own, over and over: who it is and what it does.
  // Scripted lines; nothing here listens or answers.
  const LINES = ['Hello. I’m ATLAS.', 'We’ll work together soon.', 'We have a lot to do together.',
    'I check ETAs and ATAs on the carriers’ sites, air and ocean.',
    'I update the Hub, then read it back to be sure.',
    'When a shipment needs a person, I say which one and why.',
    'I can check a PO and the documents behind it.',
    'Ask me about any shipment and I’ll answer from the records.',
    'I can turn a run into a PDF report for you.',
    'I never guess a date. If I’m not sure, I ask.'];
  // When someone clicks ATLAS.
  const POKES = ['Hi! You found me 👋', 'Not long now. Watch the countdown.',
    'Soon you can ask me anything about your shipments.', 'I’m getting ready for you.',
    'Click again, I have more to say.'];
  let poke = 0;
  let typing = 0, lineAt = 0, lineTimer = 0, loop = true;
  const cls = (c, on) => fig.classList.toggle(c, on);
  function say(text, then){
    clearInterval(typing);
    if (calm){ greet.textContent = text; if (then) then(); return; }
    const chars = Array.from(text); let n = 0; greet.textContent = '';
    cls('talk', true); cls('glance', true);
    typing = setInterval(() => {
      greet.textContent = chars.slice(0, ++n).join('');
      if (n >= chars.length){
        clearInterval(typing); cls('talk', false);
        setTimeout(() => cls('glance', false), 900);
        if (then) then();
      }
    }, 42);
  }
  function nextLine(){
    clearTimeout(lineTimer);
    if (!loop) return;
    const text = LINES[lineAt % LINES.length];
    if (lineAt % LINES.length === 0) wave(); else nod();
    lineAt += 1;
    say(text, () => { lineTimer = setTimeout(nextLine, calm ? 6000 : 3200); });
  }
  function wave(){
    if (calm) return;
    fig.classList.remove('wave'); void fig.offsetWidth; fig.classList.add('wave');
    fig.classList.add('happy'); setTimeout(() => fig.classList.remove('happy', 'wave'), 2900);
  }
  function nod(){
    if (calm) return;
    fig.classList.remove('nod'); void fig.offsetWidth; fig.classList.add('nod');
    setTimeout(() => fig.classList.remove('nod'), 800);
  }
  function stopLines(text){ loop = false; clearTimeout(lineTimer); say(text); }
  // A click on ATLAS: a wave and something new, then he carries on by himself.
  $('cover').addEventListener('click', () => {
    clearTimeout(lineTimer);
    wave();
    const text = poke % 2 === 0 ? POKES[(poke / 2) % POKES.length]
                                : LINES[3 + (lineAt++ % (LINES.length - 3))];
    poke += 1;
    say(text, () => { lineTimer = setTimeout(loop ? nextLine : () => {}, calm ? 6000 : 3600); });
  });
  if (!calm){
    setInterval(() => {
      if (document.hidden || fig.classList.contains('talk')) return;
      fig.classList.add('blink'); setTimeout(() => fig.classList.remove('blink'), 160);
    }, 4700);
  }

  /* ── soundtrack: heard, not seen ──
     It starts by itself where the browser allows (e.g. a company policy
     allow-listing this site), otherwise on the first click or key press
     anywhere except the way out to sign-in. Leaving the page stops it. */
  const status = $('audioStatus');
  let audio = null, ready = false, wasPlaying = false;
  function unavailable(){
    if (audio){ audio.pause(); audio.removeAttribute('src'); try { audio.load(); } catch (e) {} }
    audio = null; ready = false;
    document.body.dataset.music = 'unavailable';
    status.textContent = 'Soundtrack unavailable';
  }
  function start(){
    if (!audio || !audio.paused) return;
    const p = audio.play();
    if (p && p.catch) p.catch((err) => {
      // Not allowed yet, or paused while loading: wait for the next click.
      if (!(err && (err.name === 'NotAllowedError' || err.name === 'AbortError'))) unavailable();
    });
  }
  function setupAudio(a){
    if (ready) return;
    if (!a.url){ unavailable(); return; }
    audio = new Audio(); audio.preload = 'metadata'; audio.loop = true; audio.volume = 0.7; audio.src = a.url;
    ready = true;
    const mine = audio;      // a replaced element's late events change nothing
    audio.addEventListener('playing', () => { if (audio === mine){ document.body.dataset.music = 'playing'; status.textContent = 'Music playing'; } });
    audio.addEventListener('pause', () => { if (audio === mine) document.body.dataset.music = 'paused'; });
    audio.addEventListener('error', () => { if (audio === mine) unavailable(); });
    document.body.dataset.music = 'waiting';
    start();
  }
  const firstGesture = (e) => {
    if (e.target && e.target.closest && e.target.closest('a')) return;
    if (e.type === 'keydown' && (e.key === 'Tab' || e.key === 'Shift')) return;
    start();
  };
  document.addEventListener('pointerdown', firstGesture, true);
  document.addEventListener('keydown', firstGesture, true);
  const stop = () => { if (audio && !audio.paused){ wasPlaying = true; audio.pause(); } };
  addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else {
      if (wasPlaying){ wasPlaying = false; start(); }
      if (cfg && cfg.state === 'countdown') sync();
    }
  });
  $('enter').addEventListener('click', stop);
  $('adminLink').addEventListener('click', stop);

  // Stay in step with the server clock and any change the administrator makes.
  setInterval(() => { if (!document.hidden && cfg && cfg.state === 'countdown') sync(); }, 10 * 60 * 1000);
  sync();

  window.__atlasLaunch = {split, describe, state: () => cfg && cfg.state};
})();
