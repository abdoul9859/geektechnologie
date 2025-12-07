// Page détail client

document.addEventListener('DOMContentLoaded', async function() {
    const url = new URL(window.location.href);
    const clientId = Number(url.searchParams.get('id')) || null;
    if (!clientId) {
        showError('Identifiant client manquant');
        return;
    }
    try {
        const { data } = await axios.get(`/api/clients/${clientId}/details`);
        renderClientDetails(data);
    } catch (e) {
        console.error('Erreur chargement détails client:', e);
        showError(e.response?.data?.detail || 'Erreur lors du chargement des détails client');
    }
});

function renderClientDetails(payload) {
    document.getElementById('clientDetailLoading').classList.add('d-none');
    document.getElementById('clientDetailRoot').classList.remove('d-none');

    const c = payload.client;
    const stats = payload.stats || {};
    document.getElementById('clientInfo').innerHTML = `
        <div class="mb-2"><strong>Nom:</strong> ${escapeHtml(c.name || '-')}</div>
        <div class="mb-2"><strong>Contact:</strong> ${escapeHtml(c.contact || c.contact_person || '-')}</div>
        <div class="mb-2"><strong>Email:</strong> ${c.email ? `<a href="mailto:${c.email}">${escapeHtml(c.email)}</a>` : '-'}</div>
        <div class="mb-2"><strong>Téléphone:</strong> ${c.phone ? `<a href="tel:${c.phone}">${escapeHtml(c.phone)}</a>` : '-'}</div>
        <div class="mb-2"><strong>Adresse:</strong> ${escapeHtml(c.address || '-')}</div>
        <div class="text-muted small">Créé le: ${formatDateTime(c.created_at)}</div>
    `;

    document.getElementById('statTotalInvoiced').textContent = formatCurrency(stats.total_invoiced || 0);
    document.getElementById('statTotalPaid').textContent = formatCurrency(stats.total_paid || 0);
    document.getElementById('statTotalDue').textContent = formatCurrency(stats.total_due || 0);
    document.getElementById('statTotalDebts').textContent = formatCurrency(stats.total_debts || 0);

    const invoices = Array.isArray(payload.invoices) ? payload.invoices : [];
    const invBody = document.getElementById('invoicesBody');
    invBody.innerHTML = invoices.length ? invoices.map(inv => `
        <tr>
            <td><a href="/invoices?view=${inv.invoice_id}">${escapeHtml(inv.invoice_number)}</a></td>
            <td>${formatDateTime(inv.date)}</td>
            <td><span class="badge ${badgeForStatus(inv.status)}">${escapeHtml(inv.status)}</span></td>
            <td class="text-end">${formatCurrency(inv.total)}</td>
            <td class="text-end">${formatCurrency(inv.paid)}</td>
            <td class="text-end">${formatCurrency(inv.remaining)}</td>
        </tr>
    `).join('') : '<tr><td colspan="6" class="text-center py-4 text-muted">Aucune facture</td></tr>';

    const debts = Array.isArray(payload.debts) ? payload.debts : [];
    const debtBody = document.getElementById('debtsBody');
    debtBody.innerHTML = debts.length ? debts.map(d => `
        <tr>
            <td>${escapeHtml(String(d.debt_id || '-'))}</td>
            <td>${d.due_date ? formatDateTime(d.due_date) : '-'}</td>
            <td><span class="badge ${badgeForDebtStatus(d.status)}">${escapeHtml(d.status || '-')}</span></td>
            <td class="text-end">${formatCurrency(d.amount || 0)}</td>
        </tr>
    `).join('') : '<tr><td colspan="4" class="text-center py-4 text-muted">Aucune dette</td></tr>';

    const newInvoiceBtn = document.getElementById('newInvoiceBtn');
    if (newInvoiceBtn) newInvoiceBtn.href = `/invoices?create_for=${c.client_id}`;

    const clientDebtsBtn = document.getElementById('clientDebtsBtn');
    if (clientDebtsBtn) clientDebtsBtn.href = `/clients/debts?client_id=${c.client_id}`;

    const manageDebtsBtn = document.getElementById('manageDebtsBtn');
    if (manageDebtsBtn) manageDebtsBtn.href = `/clients/debts?client_id=${c.client_id}`;
}

function badgeForStatus(status) {
    const s = (status || '').toLowerCase();
    if (s.includes('pay')) return 'bg-success';
    if (s.includes('retard') || s.includes('over')) return 'bg-danger';
    if (s.includes('part')) return 'bg-warning';
    return 'bg-secondary';
}

function badgeForDebtStatus(status) {
    const s = (status || '').toLowerCase();
    if (s.includes('paid') || s.includes('régl')) return 'bg-success';
    if (s.includes('due') || s.includes('ouvert') || s.includes('open')) return 'bg-warning';
    if (s.includes('late') || s.includes('retard')) return 'bg-danger';
    return 'bg-secondary';
}


