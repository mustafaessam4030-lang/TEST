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
    say('We’re live. Sign in to begin.');
    if (cfg && cfg.launch_ms) $('whenLabel').textContent = 'Opened ' + describe(cfg.launch_ms, cfg.timezone);
    if (justNow) $('cdSpoken').textContent = 'ATLAS is now open. Continue to sign in.';
    wave();
  }
  function apply(c, sentAt, receivedAt){
    cfg = c;
    if (!greeted && c.state !== 'launched'){
      greeted = true;
      setTimeout(() => { wave(); say('Hello. I’m ATLAS. We’re preparing something powerful.'); }, calm ? 0 : 500);
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

  /* ── ATLAS: a scripted hello, a blink, a wave ── */
  const fig = $('atlas'), greet = $('greeting');
  let typing = 0;
  function say(text){
    clearInterval(typing);
    if (calm){ greet.textContent = text; return; }
    const chars = Array.from(text); let n = 0; greet.textContent = '';
    typing = setInterval(() => {
      greet.textContent = chars.slice(0, ++n).join('');
      if (n >= chars.length) clearInterval(typing);
    }, 38);
  }
  function wave(){
    if (calm) return;
    fig.classList.remove('wave'); void fig.offsetWidth; fig.classList.add('wave');
    fig.classList.add('happy'); setTimeout(() => fig.classList.remove('happy', 'wave'), 2900);
  }
  if (!calm){
    setInterval(() => {
      if (document.hidden) return;
      fig.classList.add('blink'); setTimeout(() => fig.classList.remove('blink'), 160);
    }, 5200);
  }

  /* ── soundtrack ── */
  const player = $('player'), play = $('play'), mute = $('mute'), vol = $('vol'), status = $('audioStatus');
  let audio = null, ready = false;
  function unavailable(text){
    if (audio){ audio.pause(); audio.removeAttribute('src'); try { audio.load(); } catch (e) {} }
    audio = null; ready = false;
    player.classList.add('is-off'); player.classList.remove('is-playing');
    play.disabled = true; mute.disabled = true; vol.disabled = true;
    play.setAttribute('aria-pressed', 'false'); play.setAttribute('aria-label', 'Soundtrack unavailable');
    status.textContent = text || 'Soundtrack unavailable';
  }
  function setupAudio(a){
    if (ready) return;
    if (a.title) $('trackTitle').textContent = a.title;
    if (a.artist) $('trackArtist').textContent = a.artist;
    if (!a.url){ unavailable('Soundtrack unavailable'); return; }
    audio = new Audio(); audio.preload = 'none'; audio.src = a.url;
    let v = 70;
    try { const saved = parseInt(localStorage.getItem('atlas-launch-volume'), 10); if (saved >= 0 && saved <= 100) v = saved; } catch (e) {}
    vol.value = v; audio.volume = v / 100;
    ready = true; player.classList.remove('is-off');
    play.disabled = false; mute.disabled = false; vol.disabled = false;
    play.setAttribute('aria-label', 'Play soundtrack');
    status.textContent = 'Press play';
    audio.addEventListener('playing', () => {
      player.classList.add('is-playing'); play.setAttribute('aria-pressed', 'true');
      play.setAttribute('aria-label', 'Pause soundtrack'); status.textContent = 'Playing';
    });
    audio.addEventListener('pause', () => {
      player.classList.remove('is-playing'); play.setAttribute('aria-pressed', 'false');
      play.setAttribute('aria-label', 'Play soundtrack'); if (ready) status.textContent = 'Paused';
    });
    audio.addEventListener('ended', () => { $('progress').style.width = '0'; });
    audio.addEventListener('timeupdate', () => {
      if (audio && audio.duration > 0 && isFinite(audio.duration))
        $('progress').style.width = (audio.currentTime / audio.duration * 100).toFixed(2) + '%';
    });
    audio.addEventListener('error', () => unavailable('Soundtrack unavailable'));
  }
  play.addEventListener('click', () => {
    if (!audio) return;
    if (!audio.paused){ audio.pause(); return; }
    status.textContent = 'Loading…';
    const p = audio.play();
    if (p && p.catch) p.catch((err) => {
      if (err && err.name === 'NotAllowedError'){ status.textContent = 'Press play'; return; }
      unavailable('Soundtrack unavailable');
    });
  });
  function setMuted(m){
    if (!audio) return;
    audio.muted = m; player.classList.toggle('is-muted', m);
    mute.setAttribute('aria-pressed', String(m)); mute.setAttribute('aria-label', m ? 'Unmute' : 'Mute');
  }
  mute.addEventListener('click', () => setMuted(!(audio && audio.muted)));
  vol.addEventListener('input', () => {
    if (!audio) return;
    const v = parseInt(vol.value, 10) || 0;
    audio.volume = v / 100;
    setMuted(v === 0);
    try { localStorage.setItem('atlas-launch-volume', String(v)); } catch (e) {}
  });
  // Leaving the page, or the tab going to the background, stops the music.
  const stop = () => { if (audio && !audio.paused) audio.pause(); };
  addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) stop();
    else if (cfg && cfg.state === 'countdown') sync();
  });
  $('enter').addEventListener('click', stop);
  $('adminLink').addEventListener('click', stop);

  // Stay in step with the server clock and any change the administrator makes.
  setInterval(() => { if (!document.hidden && cfg && cfg.state === 'countdown') sync(); }, 10 * 60 * 1000);
  sync();

  window.__atlasLaunch = {split, describe, state: () => cfg && cfg.state};
})();
