/* Sign-in and set-password. Talks only to this origin. */
(function(){
  'use strict';
  const $ = (id) => document.getElementById(id);
  try {
    const t = localStorage.getItem('ata-theme');
    if (t === 'light' || t === 'dark') document.documentElement.dataset.theme = t;
  } catch (e) {}

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
    return n.startsWith('/') && !n.startsWith('//') ? n : '/';
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
      if (d.ok){ location.replace(next); return; }
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
