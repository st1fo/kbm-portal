// Správa používateľov, prístupov a skupín (iba admin) a nastavenie hesla z pozývacieho odkazu.
(() => {
  const $ = id => document.getElementById(id);
  const APPS = { workplan: 'Workplan', 'vehicle-logbook': 'Kniha jázd' };
  const ACTIONS = { 'user.create': 'vytvoril účet', 'user.update': 'upravil účet', 'user.invite': 'poslal nový odkaz na heslo', 'user.delete': 'natrvalo zmazal účet', 'group.create': 'pridal skupinu', 'group.rename': 'premenoval skupinu', 'group.delete': 'zmazal skupinu', 'password.set': 'si nastavil heslo' };
  const FIELDS = { name: 'meno', username: 'prihlasovacie meno', email: 'e-mail', apps: 'aplikácie', groups: 'skupiny', password: 'heslo', confirm: 'potvrdenie' };
  let users = [], groups = [], settings = { smtp: false }, editing = null;

  async function call(path, method = 'GET', data) {
    const r = await fetch(path, { method, headers: data ? { 'Content-Type': 'application/json' } : {}, body: data ? JSON.stringify(data) : undefined });
    let v = null; try { v = await r.json(); } catch { /* prázdna odpoveď */ }
    if (!r.ok) {
      const d = v && v.detail;
      throw Error(typeof d === 'string' ? d : Array.isArray(d) ? 'Skontrolujte polia: ' + d.map(x => { const f = (x.loc || []).slice(-1)[0]; return FIELDS[f] || f; }).join(', ') : 'Požiadavka sa nepodarila.');
    }
    return v;
  }
  const el = (tag, props = {}, ...children) => { const e = Object.assign(document.createElement(tag), props); e.append(...children.filter(c => c !== null && c !== undefined)); return e; };
  const msg = (id, text) => { $(id).textContent = text; $(id).hidden = !text; };
  const when = ts => ts ? new Intl.DateTimeFormat('sk-SK', { dateStyle: 'short', timeStyle: 'short', timeZone: 'Europe/Bratislava' }).format(new Date(ts * 1000)) : '';

  // ------------------------------------------------------------ admin pohľad
  async function openAdmin(push = true) {
    document.body.dataset.view = 'admin';
    $('dashboard').hidden = true; $('admin-view').hidden = false;
    if (push) history.pushState(null, '', '/?admin=1');
    await refresh();
  }
  function closeAdmin(push = true) {
    delete document.body.dataset.view;
    $('admin-view').hidden = true; $('dashboard').hidden = false;
    if (push) history.pushState(null, '', '/');
  }
  async function refresh() {
    try {
      [users, groups, settings] = await Promise.all([call('/auth/admin/users'), call('/auth/admin/groups'), call('/auth/admin/settings')]);
      $('smtp-note').hidden = settings.smtp;
      renderUsers(); renderGroups();
      renderAudit(await call('/auth/admin/audit'));
    } catch (e) { alert(e.message); }
  }
  function renderUsers() {
    const q = $('user-search').value.trim().toLocaleLowerCase('sk');
    const list = users.filter(u => !q || [u.name, u.slug, u.email, ...u.groups.map(g => g.name)].some(v => v.toLocaleLowerCase('sk').includes(q)));
    $('user-count').textContent = String(users.length);
    $('user-rows').replaceChildren(...list.map(u => el('tr', { className: u.active ? '' : 'inactive' },
      el('td', {}, el('strong', { textContent: u.name }), el('small', { textContent: u.email })),
      el('td', { textContent: u.slug }),
      el('td', {}, ...(u.apps.length ? Object.keys(APPS).filter(a => u.apps.includes(a)).map(a => el('span', { className: 'chip app', textContent: APPS[a] })) : [el('span', { className: 'muted', textContent: 'žiadne' })])),
      el('td', {}, ...(u.groups.length ? u.groups.map(g => el('span', { className: 'chip', textContent: g.name })) : [el('span', { className: 'muted', textContent: '—' })])),
      el('td', {}, el('span', { className: 'status ' + (u.active ? (u.invite_pending ? 'pending' : 'ok') : 'off'), textContent: !u.active ? 'Neaktívny' : u.invite_pending === 'expired' ? 'Pozvánka vypršala' : u.invite_pending ? 'Čaká na heslo' : u.admin ? 'Administrátor' : 'Aktívny' })),
      el('td', { className: 'actions' }, el('button', { className: 'link', textContent: 'Upraviť', onclick: () => openUser(u) })))));
    if (!list.length) $('user-rows').append(el('tr', {}, el('td', { colSpan: 6, className: 'muted', textContent: 'Nikto nezodpovedá hľadaniu.' })));
  }
  function renderGroups() {
    $('group-list').replaceChildren(...groups.map(g => el('li', {},
      el('span', {}, el('strong', { textContent: g.name }), el('small', { textContent: ` · ${g.members} ${g.members === 1 ? 'člen' : g.members >= 2 && g.members <= 4 ? 'členovia' : 'členov'}` })),
      el('span', {}, el('button', { className: 'link', textContent: 'Premenovať', onclick: () => renameGroup(g) }),
        el('button', { className: 'link danger', textContent: 'Zmazať', onclick: () => deleteGroup(g) })))));
    if (!groups.length) $('group-list').append(el('li', { className: 'muted', textContent: 'Zatiaľ žiadne skupiny.' }));
  }
  function renderAudit(rows) {
    const name = (slug, fallback) => users.find(u => u.slug === slug)?.name || fallback || slug;
    $('audit-list').replaceChildren(...rows.slice(0, 20).map(r => el('li', {},
      el('span', { textContent: `${name(r.actor)} ${ACTIONS[r.action] || r.action}${r.detail.slug && r.detail.slug !== r.actor ? ' · ' + name(r.detail.slug, r.detail.name) : ''}${r.detail.name && !r.detail.slug ? ' · ' + r.detail.name : ''}` }),
      el('small', { textContent: when(r.ts) }))));
    if (!rows.length) $('audit-list').append(el('li', { className: 'muted', textContent: 'Zatiaľ bez zmien.' }));
  }
  async function renameGroup(g) {
    const name = prompt('Nový názov skupiny:', g.name);
    if (!name || name.trim() === g.name) return;
    try { await call('/auth/admin/groups/' + g.id, 'PATCH', { name }); await refresh(); } catch (e) { alert(e.message); }
  }
  async function deleteGroup(g) {
    if (!confirm(`Zmazať skupinu „${g.name}“? ${g.members ? `Počet ľudí, ktorí o ňu prídu: ${g.members}.` : ''}`)) return;
    try { await call('/auth/admin/groups/' + g.id, 'DELETE'); await refresh(); } catch (e) { alert(e.message); }
  }

  // ------------------------------------------------------------ dialóg používateľa
  function checks(host, items, name, selected) {
    host.replaceChildren(...items.map(([value, label]) => el('label', { className: 'check' }, el('input', { type: 'checkbox', name, value, checked: selected.includes(value) }), label)));
  }
  function openUser(u) {
    editing = u || null;
    const form = $('user-form'); form.reset(); msg('user-error', '');
    const f = form.elements;
    $('user-eyebrow').textContent = u ? 'ÚPRAVA ÚČTU' : 'NOVÝ ÚČET';
    $('user-title').textContent = u ? u.name : 'Pridať používateľa';
    $('user-submit').textContent = u ? 'Uložiť zmeny' : 'Vytvoriť účet';
    f.namedItem('name').value = u?.name || ''; f.username.value = u?.slug || ''; f.email.value = u?.email || '';
    f.username.readOnly = f.email.readOnly = !!u;   // prihlasovacie meno a e-mail sú identita v aplikáciách
    f.admin.checked = !!u?.admin; f.active.checked = u ? u.active : true;
    f.send_email.checked = settings.smtp; f.send_email.disabled = !settings.smtp;
    $('active-check').hidden = !u; $('send-check').hidden = !!u; $('user-reinvite').hidden = !u || !u.active;
    $('user-delete').hidden = !u || u.slug === (typeof current !== 'undefined' && current ? current.slug : '');
    checks($('app-checks'), Object.entries(APPS), 'apps', u ? u.apps : Object.keys(APPS));
    checks($('group-checks'), groups.map(g => [String(g.id), g.name]), 'groups', u ? u.groups.map(g => String(g.id)) : []);
    if (!groups.length) $('group-checks').append(el('span', { className: 'muted', textContent: 'Najprv pridajte skupinu.' }));
    $('user-dialog').showModal();
  }
  function values() {
    const form = $('user-form'), f = form.elements;
    const all = n => [...form.querySelectorAll(`input[name="${n}"]:checked`)].map(i => i.value);
    return { name: f.namedItem('name').value, admin: f.admin.checked, apps: all('apps'), groups: all('groups').map(Number), active: f.active.checked, username: f.username.value.trim().toLowerCase(), email: f.email.value.trim(), send_email: f.send_email.checked };
  }
  function showInvite(result, user, created) {
    $('invite-dialog-title').textContent = created ? `Účet ${user.name} je pripravený` : `Nový odkaz pre ${user.name}`;
    $('invite-dialog-text').textContent = result.emailed
      ? `Odkaz na nastavenie hesla bol odoslaný na ${user.email}. Platí ${result.expires_hours} hodín.`
      : `${result.email_error ? result.email_error + ' ' : ''}Pošlite tento odkaz človeku (e-mail, WhatsApp…). Platí ${result.expires_hours} hodín a dá sa použiť raz.`;
    $('invite-url').value = result.invite_url;
    $('invite-hint').textContent = `Prihlasovacie meno: ${user.slug}`;
    $('invite-copy').textContent = 'Kopírovať';
    $('invite-dialog').showModal();
  }
  $('user-form').addEventListener('submit', async ev => {
    ev.preventDefault(); msg('user-error', '');
    const v = values(), btn = $('user-submit'); btn.disabled = true;
    try {
      if (editing) {
        await call('/auth/admin/users/' + editing.slug, 'PATCH', { name: v.name, admin: v.admin, active: v.active, apps: v.apps, groups: v.groups });
        $('user-dialog').close();
      } else {
        const r = await call('/auth/admin/users', 'POST', { name: v.name, username: v.username, email: v.email, admin: v.admin, apps: v.apps, groups: v.groups, send_email: v.send_email });
        $('user-dialog').close(); showInvite(r, r.user, true);
      }
      await refresh();
    } catch (e) { msg('user-error', e.message); } finally { btn.disabled = false; }
  });
  $('user-reinvite').addEventListener('click', async () => {
    if (!editing || !confirm(`Vytvoriť nový odkaz na nastavenie hesla pre ${editing.name}? Doterajšie heslo platí, kým si nastaví nové.`)) return;
    try { const r = await call(`/auth/admin/users/${editing.slug}/invite`, 'POST', { send_email: settings.smtp }); $('user-dialog').close(); showInvite(r, editing, false); await refresh(); }
    catch (e) { msg('user-error', e.message); }
  });
  $('user-delete').addEventListener('click', () => {
    if (!editing) return;
    const target = editing;
    $('user-dialog').close();
    const form = $('delete-form'); form.reset(); msg('delete-error', '');
    $('delete-title').textContent = `Zmazať ${target.name}?`;
    $('delete-slug').textContent = target.slug;
    $('delete-submit').disabled = true;
    form.dataset.slug = target.slug;
    $('delete-dialog').showModal();
  });
  $('delete-form').elements.namedItem('confirm').addEventListener('input', e => { $('delete-submit').disabled = e.target.value.trim().toLowerCase() !== $('delete-form').dataset.slug; });
  $('delete-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const slug = ev.target.dataset.slug, btn = $('delete-submit'); btn.disabled = true; msg('delete-error', '');
    try {
      await call('/auth/admin/users/' + encodeURIComponent(slug), 'DELETE', { confirm: ev.target.elements.namedItem('confirm').value });
      $('delete-dialog').close(); await refresh();
    } catch (e) { msg('delete-error', e.message); btn.disabled = false; }
  });
  $('invite-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('invite-url').value); $('invite-copy').textContent = 'Skopírované ✓'; }
    catch { $('invite-url').select(); document.execCommand('copy'); $('invite-copy').textContent = 'Skopírované ✓'; }
  });
  document.querySelectorAll('dialog [data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));
  $('group-form').addEventListener('submit', async ev => {
    ev.preventDefault();
    try { await call('/auth/admin/groups', 'POST', { name: ev.target.elements.namedItem('name').value }); ev.target.reset(); await refresh(); } catch (e) { alert(e.message); }
  });
  $('user-search').addEventListener('input', renderUsers);
  $('user-add').addEventListener('click', () => openUser(null));
  $('admin-open').addEventListener('click', () => void openAdmin());
  $('admin-close').addEventListener('click', () => closeAdmin());
  window.addEventListener('popstate', () => { if (new URLSearchParams(location.search).has('admin')) void openAdmin(false); else if (document.body.dataset.view === 'admin') closeAdmin(false); });

  // ------------------------------------------------------------ nastavenie hesla z pozvánky
  async function inviteView(token) {
    $('loading').hidden = true; $('invite-view').hidden = false;
    try {
      const info = await call('/auth/invite?token=' + encodeURIComponent(token));
      $('invite-eyebrow').textContent = info.kind === 'invite' ? 'VITAJTE V PORTÁLI KBM' : 'NOVÉ HESLO';
      $('invite-title').textContent = info.kind === 'invite' ? `Dobrý deň, ${info.name.split(' ')[0]}` : 'Nastavte si nové heslo';
      $('invite-text').textContent = `Vaše prihlasovacie meno je ${info.username}. Zvoľte si heslo s aspoň 12 znakmi.`;
      $('invite-form').addEventListener('submit', async ev => {
        ev.preventDefault();
        const f = ev.target.elements;
        if (f.password.value !== f.confirm.value) { msg('invite-error', 'Heslá sa nezhodujú.'); return; }
        const btn = ev.target.querySelector('button'); btn.disabled = true; msg('invite-error', '');
        try {
          const r = await call('/auth/invite', 'POST', { token, password: f.password.value });
          // heslo je už uložené a odkaz spotrebovaný; ak zlyhá len prihlásenie, človek sa prihlási ručne
          try { await call('/auth/login', 'POST', { username: r.username, password: f.password.value }); } catch { /* prihlasovací formulár */ }
          location.replace('/');
        } catch (e) { msg('invite-error', e.message); btn.disabled = false; }
      });
    } catch (e) {
      $('invite-title').textContent = 'Odkaz nie je platný';
      $('invite-text').replaceChildren(e.message + ' Ak ste si heslo už nastavili, ', el('a', { href: '/', className: 'text-link', textContent: 'prihláste sa' }), '.');
      $('invite-form').hidden = true;
    }
  }
  const params = new URLSearchParams(location.search);
  if (params.get('invite')) void inviteView(params.get('invite'));
  else if (params.has('admin')) {
    // otvoriť správu po načítaní používateľa (napr. odkaz z Workplanu)
    fetch('/auth/me').then(r => r.ok ? r.json() : null).then(u => { if (u?.admin) void openAdmin(false); });
  }
})();
