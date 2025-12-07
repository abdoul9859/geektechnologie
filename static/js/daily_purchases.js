let DP_STATE = {
  list: [],
  editingId: null,
  filters: { from: null, to: null, category: '', method: '', search: '' },
  categories: [],
};

function initializeDailyPurchases(){
  // Default dates: current month
  try {
    const now = new Date();
    const first = new Date(now.getFullYear(), now.getMonth(), 1);
    document.getElementById('filterFrom').value = first.toISOString().slice(0,10);
    document.getElementById('filterTo').value = now.toISOString().slice(0,10);
  } catch {}

  document.getElementById('filterFrom').addEventListener('change', applyFilters);
  document.getElementById('filterTo').addEventListener('change', applyFilters);
  document.getElementById('filterCategory').addEventListener('change', applyFilters);
  document.getElementById('filterMethod').addEventListener('change', applyFilters);
  document.getElementById('filterSearch').addEventListener('input', debounce(applyFilters, 300));
  document.getElementById('dpForm').addEventListener('submit', handleDpFormSubmit);

  loadCategories();
  loadSummary();
  loadList();
}

function resetFilters(){
  try {
    document.getElementById('filterFrom').value = '';
    document.getElementById('filterTo').value = '';
    document.getElementById('filterCategory').value = '';
    document.getElementById('filterMethod').value = '';
    document.getElementById('filterSearch').value = '';
  } catch {}
  applyFilters();
}

function getFilterParams(){
  const params = {};
  const f = document.getElementById('filterFrom').value;
  const t = document.getElementById('filterTo').value;
  const c = document.getElementById('filterCategory').value;
  const m = document.getElementById('filterMethod').value;
  const s = document.getElementById('filterSearch').value.trim();
  if (f) params.date_from = f;
  if (t) params.date_to = t;
  if (c) params.category = c;
  if (m) params.payment_method = m;
  if (s) params.search = s;
  return params;
}

async function loadSummary(){
  try {
    const { data } = await axios.get('/api/daily-purchases/stats/summary', { params: getFilterParams() });
    const total = data.total || 0;
    const list = Array.isArray(data.by_category) ? data.by_category : [];
    document.getElementById('kpiTotal').textContent = formatCurrency(total);
    const box = document.getElementById('kpiByCategory');
    box.innerHTML = list.map(x => `
      <span class="badge bg-light text-dark">
        <span class="text-uppercase">${escapeHtml(x.category || '')}</span>
        <span class="ms-1 fw-semibold">${formatCurrency(x.amount || 0)}</span>
      </span>
    `).join('') || '<span class="text-white-50">Aucune dépense</span>';
  } catch (e) {
    console.error(e);
  }
}

async function loadList(){
  try {
    const tbody = document.getElementById('dpTableBody');
    tbody.innerHTML = `<tr><td colspan="8" class="text-center py-4"><div class="spinner-border" role="status"><span class="visually-hidden">Chargement...</span></div></td></tr>`;
    const { data } = await axios.get('/api/daily-purchases/', { params: getFilterParams() });
    DP_STATE.list = data || [];
    renderTable();
  } catch (e) {
    console.error(e);
    showError('Erreur lors du chargement');
  }
}

function renderTable(){
  const tbody = document.getElementById('dpTableBody');
  const items = DP_STATE.list || [];
  if (items.length === 0){
    tbody.innerHTML = `<tr><td colspan="7" class="text-center py-4"><i class="bi bi-inbox text-muted" style="font-size:2rem;"></i><div class="text-muted">Aucun achat</div></td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(it => `
    <tr>
      <td>${escapeHtml((it.date||'').toString().slice(0,10))}</td>
      <td class="text-uppercase"><span class="badge bg-secondary">${escapeHtml(it.category||'')}</span></td>
      <td>${escapeHtml(it.description||'')}</td>
      <td class="fw-semibold">${formatCurrency(it.amount||0)}</td>
      <td>${escapeHtml(it.payment_method||'')}</td>
      <td>${escapeHtml(it.reference||'')}</td>
      <td class="text-end">
        <div class="btn-group btn-group-sm">
          <button class="btn btn-outline-primary" onclick="openEditModal(${it.id})"><i class="bi bi-pencil"></i></button>
          <button class="btn btn-outline-danger" onclick="deleteDp(${it.id})"><i class="bi bi-trash"></i></button>
        </div>
      </td>
    </tr>
  `).join('');
}

async function loadCategories(){
  try {
    const { data } = await axios.get('/api/daily-purchases/categories');
    DP_STATE.categories = Array.isArray(data) ? data : [];
  } catch { DP_STATE.categories = []; }
  // Defaults if none
  if (DP_STATE.categories.length === 0) {
    DP_STATE.categories = [
      { id: -1, name: 'cafe' },
      { id: -2, name: 'eau' },
      { id: -3, name: 'electricite' },
      { id: -4, name: 'transport' },
      { id: -5, name: 'fournitures' },
      { id: -6, name: 'autres' },
    ];
  }
  // Populate filter and modal selects
  const filterSel = document.getElementById('filterCategory');
  const dpSel = document.getElementById('dpCategory');
  if (filterSel) {
    const current = filterSel.value;
    filterSel.innerHTML = '<option value="">Toutes</option>' + DP_STATE.categories.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
    if (current) filterSel.value = current;
  }
  if (dpSel) {
    const current = dpSel.value;
    dpSel.innerHTML = '<option value="">Choisir...</option>' + DP_STATE.categories.map(c => `<option value="${escapeHtml(c.name)}">${escapeHtml(c.name)}</option>`).join('');
    if (current) dpSel.value = current;
  }
}

async function quickAddCategory(){
  const name = prompt('Nouvelle catégorie (ex: eau, cafe, ...):');
  if (!name || !name.trim()) return;
  try {
    await axios.post('/api/daily-purchases/categories', { name: name.trim() });
  } catch {}
  await loadCategories();
}

async function quickDeleteCategory(){
  const sel = document.getElementById('dpCategory');
  const name = sel && sel.value ? sel.value : '';
  if (!name) { showError('Sélectionnez une catégorie à supprimer'); return; }
  // Find by name from state
  const cat = (DP_STATE.categories||[]).find(c => (c.name||'') === name);
  if (!cat || !cat.id || cat.id < 0) {
    // default/fallback categories cannot be deleted in backend; just remove locally
    DP_STATE.categories = (DP_STATE.categories||[]).filter(c => (c.name||'') !== name);
    await loadCategories();
    return;
  }
  if (!confirm(`Supprimer la catégorie "${name}" ?`)) return;
  try { await axios.delete(`/api/daily-purchases/categories/${cat.id}`); } catch {}
  await loadCategories();
}

function applyFilters(){
  loadSummary();
  loadList();
}

function openCreateModal(){
  DP_STATE.editingId = null;
  document.getElementById('dpModalTitle').textContent = 'Nouvel achat';
  document.getElementById('dpSaveText').textContent = 'Enregistrer';
  document.getElementById('dpForm').reset();
  try {
    document.getElementById('dpDate').value = new Date().toISOString().slice(0,10);
  } catch {}
  const modal = new bootstrap.Modal(document.getElementById('dpModal'));
  modal.show();
}

function openEditModal(id){
  const it = (DP_STATE.list||[]).find(x => x.id === id);
  if (!it){ return; }
  DP_STATE.editingId = id;
  document.getElementById('dpModalTitle').textContent = 'Modifier l\'achat';
  document.getElementById('dpSaveText').textContent = 'Mettre à jour';
  document.getElementById('dpDate').value = (it.date||'').toString().slice(0,10);
  document.getElementById('dpCategory').value = it.category || '';
  document.getElementById('dpAmount').value = it.amount || 0;
  document.getElementById('dpMethod').value = it.payment_method || 'espece';
  document.getElementById('dpReference').value = it.reference || '';
  document.getElementById('dpDescription').value = it.description || '';
  const modal = new bootstrap.Modal(document.getElementById('dpModal'));
  modal.show();
}

async function handleDpFormSubmit(e){
  e.preventDefault();
  const payload = collectFormValues();
  try {
    if (DP_STATE.editingId){
      await axios.put(`/api/daily-purchases/${DP_STATE.editingId}`, payload);
      showSuccess('Achat mis à jour');
    } else {
      await axios.post('/api/daily-purchases/', payload);
      showSuccess('Achat créé');
    }
    bootstrap.Modal.getInstance(document.getElementById('dpModal')).hide();
    applyFilters();
  } catch (err){
    console.error(err);
    try { console.error('Server response:', err?.response?.data); } catch{}
    const detail = err?.response?.data?.detail || err?.response?.data || err?.message;
    showError('Erreur lors de l\'enregistrement: ' + (typeof detail === 'string' ? detail : JSON.stringify(detail)));
  }
}

function collectFormValues(){
  let dateStr = document.getElementById('dpDate').value || '';
  if (dateStr.length > 10) dateStr = dateStr.slice(0,10);
  let amountRaw = document.getElementById('dpAmount').value || '0';
  amountRaw = String(amountRaw).replace(',', '.');
  let amountNum = parseFloat(amountRaw);
  if (!Number.isFinite(amountNum) || amountNum < 0) amountNum = 0;
  amountNum = Math.floor(amountNum);
  return {
    date: dateStr,
    category: document.getElementById('dpCategory').value,
    description: document.getElementById('dpDescription').value || null,
    amount: amountNum,
    payment_method: document.getElementById('dpMethod').value || 'espece',
    reference: document.getElementById('dpReference').value || null,
  };
}

async function deleteDp(id){
  if (!confirm('Supprimer cet achat ?')) return;
  try {
    await axios.delete(`/api/daily-purchases/${id}`);
    showSuccess('Supprimé');
    applyFilters();
  } catch (e){
    console.error(e);
    showError(e?.response?.data?.detail || 'Erreur lors de la suppression');
  }
}
