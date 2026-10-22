/* ATLAS launch page. The launch moment, zone and soundtrack come from the
   server (/api/launch, set by the administrator); the page only counts down
   and plays. Signing in, and everything behind it, is the server's. */
(function(){
  'use strict';
  const $ = (id) => document.getElementById(id);
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const pad = (n) => String(n).padStart(2, '0');

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
  const LINES = ['Hello. I’m ATLAS.', 'We’ll work together soon.', 'We have a lot to do together.',
                 'Shipments, arrivals, every ETA and ATA.', 'We’re preparing something powerful.'];
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
  if (!calm){
    setInterval(() => {
      if (document.hidden || fig.classList.contains('talk')) return;
      fig.classList.add('blink'); setTimeout(() => fig.classList.remove('blink'), 160);
    }, 4700);
  }

  /* ── soundtrack: no player, just music ──
     It starts by itself where the browser allows (e.g. a company policy
     allow-listing this site), otherwise on the first click or key press
     anywhere. The corner switch stops and restarts it. */
  const sound = $('sound'), snd = $('snd'), status = $('audioStatus');
  let audio = null, ready = false, stoppedByUser = false, wasPlaying = false;
  function show(playing){
    sound.classList.toggle('is-playing', playing);
    snd.setAttribute('aria-pressed', String(playing));
    snd.setAttribute('aria-label', playing ? 'Stop music' : 'Play music');
    status.textContent = playing ? 'Music playing' : (stoppedByUser ? 'Music stopped' : 'Music ready');
  }
  function unavailable(){
    if (audio){ audio.pause(); audio.removeAttribute('src'); try { audio.load(); } catch (e) {} }
    audio = null; ready = false;
    sound.hidden = true;
    status.textContent = 'Soundtrack unavailable';
  }
  function start(){
    if (!audio || stoppedByUser || !audio.paused) return;
    const p = audio.play();
    if (p && p.catch) p.catch((err) => {
      // Not allowed yet: wait for the first click or key press.
      // Paused while still loading: nothing wrong either.
      if (!(err && (err.name === 'NotAllowedError' || err.name === 'AbortError'))) unavailable();
    });
  }
  function setupAudio(a){
    if (ready) return;
    if (!a.url){ unavailable(); return; }
    audio = new Audio(); audio.preload = 'metadata'; audio.loop = true; audio.volume = 0.7; audio.src = a.url;
    ready = true; sound.hidden = false;
    snd.title = [a.title, a.artist].filter(Boolean).join(' · ');
    const mine = audio;      // a replaced element's late events change nothing
    audio.addEventListener('playing', () => { if (audio === mine) show(true); });
    audio.addEventListener('pause', () => { if (audio === mine) show(false); });
    audio.addEventListener('error', () => { if (audio === mine) unavailable(); });
    show(false);
    start();
  }
  // The first click or key press starts it; not the switch itself (it has
  // its own job), not the way out to sign-in, not Tab moving the focus.
  const firstGesture = (e) => {
    if (e.target && e.target.closest && e.target.closest('#snd, a')) return;
    if (e.type === 'keydown' && (e.key === 'Tab' || e.key === 'Shift')) return;
    start();
  };
  document.addEventListener('pointerdown', firstGesture, true);
  document.addEventListener('keydown', firstGesture, true);
  snd.addEventListener('click', () => {
    if (!audio) return;
    if (!audio.paused){ stoppedByUser = true; sound.classList.add('was-stopped'); audio.pause(); return; }
    stoppedByUser = false; start();
  });
  // Leaving the page stops the music; coming back to the tab resumes it.
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
