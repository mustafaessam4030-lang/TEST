/* Sign-in and set-password. Talks only to this origin. */
(function(){
  'use strict';
  const $ = (id) => document.getElementById(id);
  try {
    const t = localStorage.getItem('ata-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) {}

  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const atlas = (what) => document.dispatchEvent(new CustomEvent('atlas', {detail: what}));
  const params = new URLSearchParams(location.search);
  const note = (text, ok) => {
    const n = $('note'); n.textContent = text; n.hidden = !text;
    n.classList.toggle('ok', !!ok);
  };
  if (params.get('expired')) note('Your session expired. Sign in again.');
  if (params.get('sso_error')) note('Microsoft sign-in did not complete. Try again.');
  if (params.get('no_access')) note('Your account does not have access to ATA. Ask your administrator.');
  if (params.get('signed_out')) note('You are signed out.', true);
  const next = (() => {
    const n = params.get('next') || '/';
    const safe = n.startsWith('/') && !n.startsWith('//') ? n : '/';
    // The page part after "#" (e.g. /hub#missions?id=…) never reaches the server, so the browser
    // keeps it on this sign-in page: carry it on, so a link lands where it pointed.
    const h = location.hash;
    return safe.includes('#') || !h || !/^#[\w\-?=&%.,/]{1,200}$/.test(h) ? safe : safe + h;
  })();

  fetch('/api/auth/config').then((r) => r.json()).then((c) => {
    if (c.sso){
      $('ssoBox').hidden = false;
      $('sso').href = '/auth/sso/start?next=' + encodeURIComponent(next);
      $('sso').textContent = c.sso_label || 'Sign in with Microsoft';
    }
    if (!c.local){
      $('signin').hidden = true;
      $('ssoBox').querySelector('.or').hidden = true;
    }
  }).catch(() => {});

  // A set-password link carries its one-time token after '#', which the
  // browser never sends to any server or puts in a Referer.
  const token = (location.hash.match(/token=([A-Za-z0-9_-]+)/) || [])[1];
  if (location.pathname === '/set-password'){
    $('signin').hidden = true; $('ssoBox').hidden = true; $('setpw').hidden = false;
    if (!token) note('This link is not complete. Ask your administrator for a new one.');
    $('pw1').focus();
  } else {
    $('email').focus();
  }

  const post = (url, body) => fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  }).then((r) => r.json().catch(() => ({})).then((d) => ({status: r.status, d})));

  $('signin').addEventListener('submit', (e) => {
    e.preventDefault();
    const email = $('email').value.trim(), password = $('password').value;
    if (!email || !password){ note('Enter your work email and password.'); return; }
    $('go').disabled = true; $('go').textContent = 'Signing in…'; note('');
    post('/api/auth/login', {email, password}).then(({status, d}) => {
      if (d.ok){
        atlas('welcome');
        setTimeout(() => location.replace(next), calm ? 0 : 650);
        return;
      }
      atlas('oops');
      note(d.message || (status === 429 ? 'Too many attempts. Wait and try again.'
                                         : 'Sign-in failed.'));
      $('password').value = ''; $('password').focus();
    }).catch(() => note('The Control Tower could not be reached. Check your connection.'))
      .finally(() => { $('go').disabled = false; $('go').textContent = 'Sign in'; });
  });

  $('setpw').addEventListener('submit', (e) => {
    e.preventDefault();
    const a = $('pw1').value, b = $('pw2').value;
    if (a !== b){ note('The two passwords are different.'); return; }
    $('save').disabled = true; note('');
    post('/api/auth/set-password', {token, password: a}).then(({d}) => {
      if (d.ok){
        history.replaceState(null, '', '/login');
        $('setpw').hidden = true; $('signin').hidden = false;
        $('email').value = d.email || ''; $('password').focus();
        note('Password set. Sign in with it now.', true);
        return;
      }
      note(d.message || 'The password could not be set.');
    }).catch(() => note('The Control Tower could not be reached.'))
      .finally(() => { $('save').disabled = false; });
  });
})();

/* ATLAS on the stage: types a greeting, blinks, watches the email as it is
   typed, looks away from the password, shakes at a refused sign-in and hops
   when it works. Decoration only: the form never waits on any of it. */
(function(){
  'use strict';
  const fig = document.getElementById('atlas'), say = document.getElementById('say'),
        bubble = document.getElementById('bubble');
  if (!fig || !say) return;
  const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const look = fig.querySelector('.ax-look');
  const h = new Date().getHours();
  const dayPart = h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening';
  const lines = [
    {t: "Hi! I'm ATLAS 👋"},
    {t: dayPart + '. Ready when you are.'},
    {t: 'I keep an eye on every shipment’s ETA and ATA.'},
    {t: 'Sign in and I’ll show you today’s arrivals.'},
    {t: 'مرحباً! أنا أطلس 👋', rtl: true}
  ];
  let i = 0, timer = 0, typing = 0, mood = '', oopsUntil = 0;

  const set = (cls, on) => fig.classList.toggle(cls, on);
  function speak(line, hold){
    clearTimeout(timer); clearInterval(typing);
    say.dir = line.rtl ? 'rtl' : 'ltr';
    bubble.classList.remove('pop'); void bubble.offsetWidth; bubble.classList.add('pop');
    if (calm){ say.textContent = line.t; }
    else {
      const chars = Array.from(line.t); let n = 0; say.textContent = '';
      typing = setInterval(() => {
        say.textContent = chars.slice(0, ++n).join('');
        if (n >= chars.length) clearInterval(typing);
      }, 34);
    }
    if (hold) return;
    timer = setTimeout(() => { i = (i + 1) % lines.length; if (!mood || mood === 'watch') speak(lines[i]); },
                       Math.max(2600, line.t.length * 70));
  }
  function wave(){
    set('wave', false); void fig.offsetWidth; set('wave', true);
    setTimeout(() => set('wave', false), 2700);
  }
  function moodOff(){ mood = ''; set('shy', false); set('oops', false); look.style.transform = ''; }

  // Blink now and then; glance at whatever is being typed.
  setInterval(() => {
    if (mood === 'shy') return;
    set('blink', true); setTimeout(() => set('blink', false), 240);
  }, 4200);
  const email = document.getElementById('email'), pw = [...document.querySelectorAll('input[type=password]')];
  const narrow = () => matchMedia('(max-width:860px)').matches;
  const follow = () => {
    const k = Math.min(email.value.length, 28) / 28;
    look.style.transform = narrow() ? 'translate(' + (k * 5 - 2.5).toFixed(2) + 'px,3px)'
                                    : 'translate(' + (2 + k * 2.5).toFixed(2) + 'px,' + (k * 2.4).toFixed(2) + 'px)';
  };
  if (email){
    email.addEventListener('focus', () => { const was = mood; moodOff(); mood = 'watch'; follow();
      if (was === 'shy' || was === 'oops') speak(lines[i]); });
    email.addEventListener('input', follow);
    email.addEventListener('blur', () => { if (mood === 'watch') moodOff(); });
  }
  pw.forEach((el) => {
    const shy = () => { moodOff(); mood = 'shy'; set('shy', true); speak({t: 'I’m not looking 🙈'}, true); };
    // The form puts the cursor back here after a refusal: let ATLAS react to that first.
    el.addEventListener('focus', () => {
      const wait = oopsUntil - Date.now();
      if (wait > 0) setTimeout(() => { if (document.activeElement === el && mood === 'oops') shy(); }, wait);
      else shy();
    });
    el.addEventListener('blur', () => { if (mood === 'shy'){ moodOff(); speak(lines[i]); } });
  });

  document.addEventListener('atlas', (e) => {
    if (e.detail === 'oops'){
      moodOff(); mood = 'oops'; oopsUntil = Date.now() + 2200;
      set('oops', false); void fig.offsetWidth; set('oops', true);
      speak({t: 'Hmm, that didn’t work. Try again?'}, true);
      setTimeout(() => { if (mood === 'oops') set('oops', false); }, 2100);
    } else if (e.detail === 'welcome'){
      moodOff(); mood = 'welcome'; set('happy', true); set('hop', true);
      speak({t: 'Welcome back! 🎉'}, true);
    }
  });
  fig.addEventListener('click', () => {
    wave(); set('happy', true); setTimeout(() => set('happy', false), 1400);
    speak({t: ['Hi again! 👋', 'That tickles!', 'Need a hand? Sign in first.'][Math.floor(Math.random() * 3)]});
  });

  // First hello: wave and type, unless the cursor is already in a field.
  setTimeout(() => { wave(); if (!mood || mood === 'watch') speak(lines[0]); }, calm ? 0 : 450);
})();
