const $ = (s) => document.querySelector(s);
const API = {
  artists: 'artistes.json',
  progress: 'progress.json',
  logs: 'last-run.json'
};

let artistsData = { artists: {} };
let progressData = {};

function formatNumber(n){
  return n === null || n === undefined || n === '' ? '—' : new Intl.NumberFormat('fr-FR').format(Number(n));
}
function pct(n){
  return `${Math.max(0, Math.min(100, n || 0)).toFixed(1)}%`;
}
function byLastSeen(a,b){
  return (b.last_seen || '').localeCompare(a.last_seen || '');
}

function render(){
  const artists = Object.values(artistsData.artists || {}).sort(byLastSeen);
  const total = artists.length;
  const completed = artists.filter(a => a.name || a.monthly_listeners !== null || a.followers !== null).length;
  const pending = Math.max(0, total - completed);
  const newCount = artists.filter(a => a.first_seen && a.last_seen && a.first_seen === a.last_seen).length;
  const progress = progressData.progress_pct ?? (total ? completed / total * 100 : 0);

  $('#totalArtists').textContent = formatNumber(total);
  $('#doneArtists').textContent = formatNumber(completed);
  $('#pendingArtists').textContent = formatNumber(pending);
  $('#newArtists').textContent = formatNumber(newCount);
  $('#progressText').textContent = pct(progress);
  $('#progressFill').style.width = `${Math.max(0, Math.min(100, progress || 0))}%`;
  $('#ghStatus').textContent = progressData.status || 'Inconnu';
  $('#ghMeta').textContent = progressData.current_artist ? `Artiste en cours: ${progressData.current_artist}` : 'Aucune exécution en cours';
  $('#lastSave').textContent = progressData.last_update || '—';
  $('#runState').textContent = JSON.stringify(progressData, null, 2);

  const recent = artists.slice(0, 8).map(a =>
    `<div class="artist-row"><div><strong>${a.name || a.id}</strong><div class="muted small">${a.id}</div></div><div class="muted">${formatNumber(a.monthly_listeners)} ML</div></div>`
  ).join('');
  $('#recentArtists').innerHTML = recent || '<div class="muted">Aucune donnée.</div>';

  $('#quickList').innerHTML = artists.slice(0, 20).map(a =>
    `<div class="artist-row"><div><strong>${a.name || a.id}</strong><div class="muted small">${a.id}</div></div><a class="secondary-link" href="editor.html#${a.id}">Modifier</a></div>`
  ).join('');
}

async function load(){
  try { artistsData = await (await fetch(`${API.artists}?v=${Date.now()}`)).json(); } catch(e) {}
  try { progressData = await (await fetch(`${API.progress}?v=${Date.now()}`)).json(); } catch(e) {}
  try {
    const log = await (await fetch(`${API.logs}?v=${Date.now()}`)).json();
    localStorage.setItem('lastRunCache', JSON.stringify(log));
    $('#logsBox').textContent = JSON.stringify(log, null, 2);
  } catch(e) {
    $('#logsBox').textContent = '{}';
  }
  render();
}

['dashboard','editor','logs'].forEach(v => {
  const btn = document.querySelector(`.nav-btn[data-view="${v}"]`);
  if(btn) btn.addEventListener('click', () => {
    document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.view').forEach(el => el.classList.remove('active'));
    document.getElementById(v + 'View').classList.add('active');
  });
});

$('#refreshBtn').addEventListener('click', load);
load();
setInterval(load, 15000);
