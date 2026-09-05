const $ = (s) => document.querySelector(s);
const form = $('#artistForm');
const statusBox = $('#editorStatus');
const indexBox = $('#artistIndex');
const search = $('#editorSearch');
const title = $('#editorTitle');
const dirtyFlag = $('#dirtyFlag');
const imagesField = $('#imagesField');

let db = { artists: {} };
let currentId = null;
let original = null;
let dirty = false;

function setDirty(v){
  dirty = v;
  dirtyFlag.textContent = v ? 'Modifications non enregistrées' : 'Aucune modification';
  dirtyFlag.className = 'badge ' + (v ? 'dirty' : '');
}
function parseGenres(v){
  return String(v || '').split(',').map(s => s.trim()).filter(Boolean);
}

function renderIndex(){
  const q = (search.value || '').toLowerCase();
  const items = Object.values(db.artists || {}).filter(a => `${a.name||''} ${a.id}`.toLowerCase().includes(q));
  indexBox.innerHTML = items.map(a => {
  const spotifyUrl = a.url || `https://open.spotify.com/artist/${a.id}`;
  return `
    <div class="artist-row">
      <div>
        <a href="${spotifyUrl}" target="_blank" rel="noopener noreferrer" class="artist-link">
          <strong>${a.name || a.id}</strong>
        </a>
        <div class="muted small">${a.id}</div>
      </div>
      <span class="muted">${a.monthly_listeners ?? '—'}</span>
    </div>
  `;
}).join('');
  indexBox.querySelectorAll('button[data-id]').forEach(btn => btn.addEventListener('click', () => select(btn.dataset.id)));
}

function fill(a){
  form.id.value = a.id || '';
  form.name.value = a.name || '';
  form.url.value = a.url || '';
  form.followers.value = a.followers ?? '';
  form.monthly_listeners.value = a.monthly_listeners ?? '';
  form.popularity.value = a.popularity ?? '';
  form.genres.value = Array.isArray(a.genres) ? a.genres.join(', ') : '';
  form.last_seen.value = a.last_seen || '';
  imagesField.value = JSON.stringify(a.images || [], null, 2);
}

function read(){
  return {
    ...original,
    name: form.name.value.trim() || null,
    url: form.url.value.trim() || null,
    followers: form.followers.value === '' ? null : Number(form.followers.value),
    monthly_listeners: form.monthly_listeners.value === '' ? null : Number(form.monthly_listeners.value),
    popularity: form.popularity.value === '' ? null : Number(form.popularity.value),
    genres: parseGenres(form.genres.value),
    last_seen: form.last_seen.value.trim() || null,
    images: (() => { try { return JSON.parse(imagesField.value || '[]'); } catch(e){ return original.images || []; } })(),
  };
}

function changed(){
  if(!original) return false;
  const now = read();
  return JSON.stringify(now) !== JSON.stringify(original);
}

function syncDirty(){ setDirty(changed()); }

function select(id){
  currentId = id;
  original = structuredClone(db.artists[id]);
  title.textContent = original.name || original.id;
  fill(original);
  setDirty(false);
  statusBox.textContent = `Sélection: ${id}`;
  location.hash = id;
}

async function load(){
  db = await (await fetch(`artistes.json?v=${Date.now()}`)).json();
  renderIndex();
  const hash = location.hash.slice(1);
  if(hash && db.artists[hash]) select(hash);
  else {
    const first = Object.keys(db.artists)[0];
    if(first) select(first);
  }
}

async function save(){
  if(!currentId) return;
  const payload = read();
  payload.id = currentId;
  statusBox.textContent = 'Envoi vers GitHub...';

  const res = await fetch('/.github/save-artist', {
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ id: currentId, artist: payload })
  });

  if(!res.ok){
    statusBox.textContent = `Erreur sauvegarde (${res.status})`;
    return;
  }

  const out = await res.json();
  db.artists[currentId] = payload;
  original = structuredClone(payload);
  setDirty(false);
  statusBox.textContent = `OK: ${out.message || 'sauvegardé'}`;
  renderIndex();
}

form.addEventListener('input', syncDirty);
imagesField.addEventListener('input', syncDirty);
search.addEventListener('input', renderIndex);
$('#saveBtn').addEventListener('click', save);
$('#saveBtnBottom').addEventListener('click', save);
$('#resetBtn').addEventListener('click', () => {
  if(original) {
    fill(original);
    setDirty(false);
    statusBox.textContent = 'Réinitialisé';
  }
});
load();
