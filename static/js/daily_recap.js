// Récap Quotidien - Gestion de l'interface
let currentRecapData = null;

document.addEventListener('DOMContentLoaded', function() {
    // Initialiser la date d'aujourd'hui
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('recapDate').value = today;
    
    // Charger automatiquement le récap d'aujourd'hui
    loadDailyRecap();
    
    // Event listener pour le changement de date
    document.getElementById('recapDate').addEventListener('change', loadDailyRecap);
    
    // Initialiser les dates du résumé de période par défaut sur aujourd'hui
    try {
        const periodStart = document.getElementById('periodStart');
        const periodEnd = document.getElementById('periodEnd');
        if (periodStart) periodStart.value = today;
        if (periodEnd) periodEnd.value = today;
    } catch (e) {}
});

async function loadDailyRecap() {
    try {
        const selectedDate = document.getElementById('recapDate').value;
        if (!selectedDate) {
            showError('Veuillez sélectionner une date');
            return;
        }
        
        showLoading();
        
        // Appel API pour récupérer les données
        const response = await axios.get('/api/daily-recap/stats', {
            params: { target_date: selectedDate }
        });
        
        const data = response.data;
        currentRecapData = data;
        
        // Mettre à jour l'affichage
        updateDateDisplay(data.date_formatted);
        updateFinancialSummary(data.finances);
        updateDailyPurchases(data.daily_purchases);
        updateQuickStats(data);
        updateDetailedTables(data);
        updateBankTables(data.finances);
        updateDebtsSection(data.debts || {});
        updateDashboardSection(data.dashboard || {});
        
        hideLoading();
        
    } catch (error) {
        console.error('Erreur lors du chargement du récap:', error);
        showError('Erreur lors du chargement du récap quotidien');
        hideLoading();
    }
}

// Helpers de navigation de date
function setRecapDateToday() {
    try {
        const today = new Date().toISOString().split('T')[0];
        const input = document.getElementById('recapDate');
        if (!input) return;
        input.value = today;
        loadDailyRecap();
    } catch (e) { console.error(e); }
}

// Section Clients & Dettes / Achats & Fournisseurs / Produits & Stock / KPIs avancés
function updateDebtsSection(debts) {
    try {
        if (!debts || typeof debts !== 'object') debts = {};
        const clientRemaining = document.getElementById('debtsClientRemaining');
        const supplierRemaining = document.getElementById('debtsSupplierRemaining');
        const totalRemaining = document.getElementById('debtsTotalRemaining');
        const overdueAmount = document.getElementById('debtsOverdueAmount');
        const overdueCount = document.getElementById('debtsOverdueCount');

        if (clientRemaining) clientRemaining.textContent = formatCurrency(debts.client_total_remaining || 0);
        if (supplierRemaining) supplierRemaining.textContent = formatCurrency(debts.supplier_total_remaining || 0);
        if (totalRemaining) totalRemaining.textContent = formatCurrency(debts.total_remaining || 0);
        if (overdueAmount) overdueAmount.textContent = formatCurrency(debts.overdue_amount || 0);
        if (overdueCount) overdueCount.textContent = `${debts.overdue_count || 0} dettes`;

        const supplierDebtsRemaining = document.getElementById('supplierDebtsRemaining');
        const supplierDebtsCount = document.getElementById('supplierDebtsCount');
        if (supplierDebtsRemaining) supplierDebtsRemaining.textContent = formatCurrency(debts.supplier_total_remaining || 0);
        if (supplierDebtsCount) supplierDebtsCount.textContent = String(debts.supplier_debts_count || 0);
    } catch (e) { console.error(e); }
}

function updateDashboardSection(dashboard) {
    try {
        if (!dashboard || typeof dashboard !== 'object') dashboard = {};

        // Produits & Stock (global)
        const totalStockEl = document.getElementById('dashboardTotalStock');
        const criticalStockEl = document.getElementById('dashboardCriticalStock');
        const outOfStockEl = document.getElementById('dashboardOutOfStock');
        const lowStockEl = document.getElementById('dashboardLowStock');
        if (totalStockEl) totalStockEl.textContent = String(dashboard.total_stock ?? '-');
        if (criticalStockEl) criticalStockEl.textContent = String(dashboard.critical_stock ?? '-');
        if (outOfStockEl) outOfStockEl.textContent = String(dashboard.out_of_stock ?? '-');
        if (lowStockEl) lowStockEl.textContent = String(dashboard.low_stock ?? '-');

        const topProductsBody = document.getElementById('topProductsTable');
        if (topProductsBody) {
            const list = Array.isArray(dashboard.top_products) ? dashboard.top_products : [];
            topProductsBody.innerHTML = list.length ? list.map(p => `
                <tr>
                    <td>${escapeHtml(p.name || '-') }</td>
                    <td>${formatCurrency(p.revenue || 0)}</td>
                </tr>
            `).join('') : '<tr><td colspan="2" class="text-center text-muted">Aucune donnée</td></tr>';
        }

        // Achats & Fournisseurs (mois)
        const monthlyDailyPurchases = document.getElementById('monthlyDailyPurchases');
        const supplierMonthlyPayments = document.getElementById('supplierMonthlyPayments');
        if (monthlyDailyPurchases) monthlyDailyPurchases.textContent = formatCurrency(dashboard.monthly_daily_purchases || 0);
        if (supplierMonthlyPayments) supplierMonthlyPayments.textContent = formatCurrency(dashboard.monthly_supplier_payments || 0);

        // Indicateurs avancés
        const monthlyRevenueEl = document.getElementById('dashboardMonthlyRevenue');
        const unpaidAmountEl = document.getElementById('dashboardUnpaidAmount');
        const avgTicketEl = document.getElementById('dashboardAvgTicket');
        const conversionRateEl = document.getElementById('dashboardConversionRate');
        const activeCustomersEl = document.getElementById('dashboardActiveCustomers');
        if (monthlyRevenueEl) monthlyRevenueEl.textContent = formatCurrency(dashboard.monthly_revenue || 0);
        if (unpaidAmountEl) unpaidAmountEl.textContent = formatCurrency(dashboard.unpaid_amount || 0);
        if (avgTicketEl) avgTicketEl.textContent = formatCurrency(dashboard.avg_ticket || 0);
        if (conversionRateEl) conversionRateEl.textContent = `${(dashboard.conversion_rate || 0).toFixed(1)} %`;
        if (activeCustomersEl) activeCustomersEl.textContent = String(dashboard.active_customers || 0);

        const paymentMethodsBody = document.getElementById('paymentMethodsTable');
        if (paymentMethodsBody) {
            const list = Array.isArray(dashboard.payment_methods) ? dashboard.payment_methods : [];
            paymentMethodsBody.innerHTML = list.length ? list.map(pm => `
                <tr>
                    <td>${escapeHtml(pm.method || 'Non spécifié')}</td>
                    <td>${formatCurrency(pm.amount || 0)}</td>
                </tr>
            `).join('') : '<tr><td colspan="2" class="text-center text-muted">Aucune donnée</td></tr>';
        }
    } catch (e) { console.error(e); }
}

function setRecapDateRelative(offsetDays) {
    try {
        const input = document.getElementById('recapDate');
        if (!input || !input.value) { setRecapDateToday(); return; }
        const current = new Date(input.value);
        if (isNaN(current.getTime())) { setRecapDateToday(); return; }
        current.setDate(current.getDate() + Number(offsetDays || 0));
        const next = current.toISOString().split('T')[0];
        input.value = next;
        loadDailyRecap();
    } catch (e) { console.error(e); }
}

// Résumé de période
async function loadPeriodSummary() {
    try {
        const startInput = document.getElementById('periodStart');
        const endInput = document.getElementById('periodEnd');
        if (!startInput || !endInput) return;
        const start = startInput.value;
        const end = endInput.value;
        if (!start || !end) {
            showError('Veuillez choisir une date de début et une date de fin');
            return;
        }

        const resp = await axios.get('/api/daily-recap/period-summary', {
            params: { start_date: start, end_date: end }
        });
        const summary = resp.data || {};

        try {
            const daysEl = document.getElementById('periodDaysCount');
            const payEl = document.getElementById('periodTotalPayments');
            const invEl = document.getElementById('periodInvoicesCount');
            const quoEl = document.getElementById('periodQuotationsCount');
            if (daysEl) daysEl.textContent = String(summary.days_count ?? '-');
            if (payEl) payEl.textContent = formatCurrency(summary.total_payments || 0);
            if (invEl) invEl.textContent = String(summary.invoices_created ?? 0);
            if (quoEl) quoEl.textContent = String(summary.quotations_created ?? 0);
        } catch (e) { console.error(e); }
    } catch (error) {
        console.error('Erreur résumé période:', error);
        showError('Erreur lors du chargement du résumé de période');
    }
}

function updateDateDisplay(dateFormatted) {
    document.getElementById('currentDate').textContent = `Récap du ${dateFormatted}`;
}

function updateFinancialSummary(finances) {
    // Mise à jour de la vue caisse
    document.getElementById('paymentsReceived').textContent = formatCurrency(finances.payments_received);
    document.getElementById('bankEntries').textContent = formatCurrency(finances.bank_entries);
    document.getElementById('bankExits').textContent = formatCurrency(finances.bank_exits);
    
    const balanceElement = document.getElementById('dailyBalance');
    const balance = finances.daily_balance;
    balanceElement.textContent = formatCurrency(balance);
    
    // Couleur du solde selon positif/négatif
    balanceElement.className = balance >= 0 ? 'h4 text-success mb-1' : 'h4 text-danger mb-1';

    // Achats quotidiens (déduits) et CA net
    const dpOut = document.getElementById('dailyPurchasesOut');
    if (dpOut) dpOut.textContent = formatCurrency(finances.daily_purchases_total || 0);
    const netRevEl = document.getElementById('netRevenue');
    if (netRevEl) {
        const net = (finances.net_revenue !== undefined && finances.net_revenue !== null)
            ? finances.net_revenue
            : (Number(finances.potential_revenue || 0) - Number(finances.daily_purchases_total || 0));
        netRevEl.textContent = formatCurrency(net);
    }
}

function updateQuickStats(data) {
    // Statistiques rapides
    document.getElementById('invoicesCreated').textContent = data.invoices.created_count;
    document.getElementById('quotationsCreated').textContent = data.quotations.created_count;
    document.getElementById('stockEntries').textContent = data.stock.entries_count;
    document.getElementById('stockExits').textContent = data.stock.exits_count;
}

function updateDetailedTables(data) {
    // Table des factures
    updateInvoicesTable(data.invoices.created_list);
    
    // Table des paiements
    updatePaymentsTable(data.payments.list);
    
    // Table des devis
    updateQuotationsTable(data.quotations.created_list);
    
    // Tables des mouvements de stock
    updateStockEntriesTable(data.stock.entries_list);
    updateStockExitsTable(data.stock.exits_list);
}

function updateDailyPurchases(dp) {
    try {
        const totalEl = document.getElementById('dailyPurchasesTotal');
        const chips = document.getElementById('dailyPurchasesByCategory');
        const tbody = document.getElementById('dailyPurchasesTable');
        if (!dp) {
            totalEl && (totalEl.textContent = formatCurrency(0));
            if (chips) chips.innerHTML = '<span class="text-muted">Aucune dépense</span>';
            if (tbody) tbody.innerHTML = '<tr><td colspan="6" class="text-center text-muted">Aucun achat</td></tr>';
            return;
        }
        totalEl && (totalEl.textContent = formatCurrency(dp.total || 0));
        if (chips) {
            const list = Array.isArray(dp.by_category) ? dp.by_category : [];
            chips.innerHTML = list.length ? list.map(x => `
                <span class="badge bg-light text-dark">
                    <span class="text-uppercase">${escapeHtml(x.category || '')}</span>
                    <span class="ms-1 fw-semibold">${formatCurrency(x.amount || 0)}</span>
                </span>
            `).join('') : '<span class="text-muted">Aucune dépense</span>';
        }
        if (tbody) {
            const items = Array.isArray(dp.list) ? dp.list : [];
            tbody.innerHTML = items.length ? items.map(it => `
                <tr>
                    <td>${escapeHtml(it.time || '')}</td>
                    <td class="text-uppercase"><span class="badge bg-secondary">${escapeHtml(it.category || '')}</span></td>
                    <td>${escapeHtml(it.description || '')}</td>
                    <td class="fw-semibold">${formatCurrency(it.amount || 0)}</td>
                    <td>${escapeHtml(it.method || '')}</td>
                    <td>${escapeHtml(it.reference || '')}</td>
                </tr>
            `).join('') : '<tr><td colspan="6" class="text-center text-muted">Aucun achat</td></tr>';
        }
    } catch (e) { console.error(e); }
}

function updateInvoicesTable(invoices) {
    const tbody = document.getElementById('invoicesTable');
    
    if (!invoices || invoices.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aucune facture créée ce jour</td></tr>';
        return;
    }
    
    tbody.innerHTML = invoices.map(invoice => `
        <tr>
            <td>${invoice.time}</td>
            <td>
                <button type="button" class="btn btn-link btn-sm p-0" onclick="goToInvoiceFromRecap('${invoice.id || ''}', '${escapeHtml(invoice.number)}')">
                    <strong>${escapeHtml(invoice.number)}</strong>
                </button>
            </td>
            <td>${escapeHtml(invoice.client_name)}</td>
            <td>${formatCurrency(invoice.total)}</td>
            <td>
                <span class="badge bg-${getStatusBadgeColor(invoice.status)}">
                    ${getStatusLabel(invoice.status)}
                </span>
            </td>
        </tr>
    `).join('');
}

function updatePaymentsTable(payments) {
    const tbody = document.getElementById('paymentsTable');
    
    if (!payments || payments.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">Aucun paiement reçu ce jour</td></tr>';
        return;
    }
    
    tbody.innerHTML = payments.map(payment => `
        <tr>
            <td>${payment.time}</td>
            <td>
                ${payment.invoice_number ? `
                    <button type="button" class="btn btn-link btn-sm p-0" onclick="goToInvoiceFromRecap('${payment.invoice_id || ''}', '${escapeHtml(payment.invoice_number)}')">
                        ${escapeHtml(payment.invoice_number)}
                    </button>
                ` : escapeHtml(payment.invoice_number || '')}
            </td>
            <td><strong class="text-success">${formatCurrency(payment.amount)}</strong></td>
            <td>${escapeHtml(payment.method || 'Non spécifié')}</td>
        </tr>
    `).join('');
}

function updateQuotationsTable(quotations) {
    const tbody = document.getElementById('quotationsTable');
    
    if (!quotations || quotations.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="text-center text-muted">Aucun devis créé ce jour</td></tr>';
        return;
    }
    
    tbody.innerHTML = quotations.map(quotation => `
        <tr>
            <td>${quotation.time}</td>
            <td>
                <button type="button" class="btn btn-link btn-sm p-0" onclick="goToQuotationFromRecap('${quotation.id || ''}', '${escapeHtml(quotation.number)}')">
                    <strong>${escapeHtml(quotation.number)}</strong>
                </button>
            </td>
            <td>${escapeHtml(quotation.client_name)}</td>
            <td>${formatCurrency(quotation.total)}</td>
            <td>
                <span class="badge bg-${getStatusBadgeColor(quotation.status)}">
                    ${getStatusLabel(quotation.status)}
                </span>
            </td>
        </tr>
    `).join('');
}

function updateStockEntriesTable(entries) {
    const tbody = document.getElementById('stockEntriesTable');
    
    if (!entries || entries.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">Aucune entrée de stock ce jour</td></tr>';
        return;
    }
    
    tbody.innerHTML = entries.map(entry => `
        <tr>
            <td>${entry.time}</td>
            <td>${escapeHtml(entry.product_name)}</td>
            <td><span class="badge bg-success">+${entry.quantity}</span></td>
            <td>
                ${entry.invoice_id || entry.invoice_number ? `
                    <button type="button" class="btn btn-link btn-sm p-0" onclick="goToInvoiceFromRecap('${entry.invoice_id || ''}', '${escapeHtml(entry.invoice_number || '')}')">
                        ${escapeHtml(entry.reference || 'INVOICE')}
                    </button>
                ` : escapeHtml(entry.reference || '')}
            </td>
        </tr>
    `).join('');
}

function updateStockExitsTable(exits) {
    const tbody = document.getElementById('stockExitsTable');
    
    if (!exits || exits.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center text-muted">Aucune sortie de stock ce jour</td></tr>';
        return;
    }
    
    tbody.innerHTML = exits.map(exit => `
        <tr>
            <td>${exit.time}</td>
            <td>${escapeHtml(exit.product_name)}</td>
            <td><span class="badge bg-danger">-${exit.quantity}</span></td>
            <td>
                ${exit.invoice_id || exit.invoice_number ? `
                    <button type="button" class="btn btn-link btn-sm p-0" onclick="goToInvoiceFromRecap('${exit.invoice_id || ''}', '${escapeHtml(exit.invoice_number || '')}')">
                        ${escapeHtml(exit.reference || 'INVOICE')}
                    </button>
                ` : escapeHtml(exit.reference || '')}
            </td>
        </tr>
    `).join('');
}

function updateBankTables(finances) {
    if (!finances) return;
    try {
        const entriesBody = document.getElementById('bankEntriesTable');
        const exitsBody = document.getElementById('bankExitsTable');

        if (entriesBody) {
            const list = Array.isArray(finances.bank_entries_list) ? finances.bank_entries_list : [];
            entriesBody.innerHTML = list.length ? list.map(t => `
                <tr>
                    <td>${escapeHtml(t.motif || '')}</td>
                    <td>${escapeHtml(t.description || '')}</td>
                    <td class="fw-semibold">${formatCurrency(t.amount || 0)}</td>
                    <td>${escapeHtml(t.method || '')}</td>
                    <td>${escapeHtml(t.reference || '')}</td>
                </tr>
            `).join('') : '<tr><td colspan="5" class="text-center text-muted">Aucune entrée bancaire</td></tr>';
        }

        if (exitsBody) {
            const list = Array.isArray(finances.bank_exits_list) ? finances.bank_exits_list : [];
            exitsBody.innerHTML = list.length ? list.map(t => `
                <tr>
                    <td>${escapeHtml(t.motif || '')}</td>
                    <td>${escapeHtml(t.description || '')}</td>
                    <td class="fw-semibold">${formatCurrency(t.amount || 0)}</td>
                    <td>${escapeHtml(t.method || '')}</td>
                    <td>${escapeHtml(t.reference || '')}</td>
                </tr>
            `).join('') : '<tr><td colspan="5" class="text-center text-muted">Aucune sortie bancaire</td></tr>';
        }
    } catch (e) { console.error(e); }
}

// Fonctions utilitaires
function formatCurrency(amount) {
    try {
        return new Intl.NumberFormat('fr-FR', {
            style: 'currency',
            currency: 'XOF',
            maximumFractionDigits: 0
        }).format(amount || 0);
    } catch {
        return `${amount || 0} F CFA`;
    }
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function getStatusBadgeColor(status) {
    const statusColors = {
        'en attente': 'warning',
        'payée': 'success',
        'partiellement payée': 'info',
        'en retard': 'danger',
        'annulée': 'secondary',
        'brouillon': 'secondary',
        'envoyé': 'primary',
        'accepté': 'success',
        'refusé': 'danger',
        'expiré': 'dark'
    };
    return statusColors[status?.toLowerCase()] || 'secondary';
}

function getStatusLabel(status) {
    const statusLabels = {
        'en attente': 'En attente',
        'payée': 'Payée',
        'partiellement payée': 'Partiellement payée',
        'en retard': 'En retard',
        'annulée': 'Annulée',
        'brouillon': 'Brouillon',
        'envoyé': 'Envoyé',
        'accepté': 'Accepté',
        'refusé': 'Refusé',
        'expiré': 'Expiré'
    };
    return statusLabels[status?.toLowerCase()] || status || 'Inconnu';
}

function showLoading() {
    // Afficher des spinners dans les éléments principaux
    const elements = [
        'invoicesCreated', 'quotationsCreated', 'stockEntries', 'stockExits',
        'paymentsReceived', 'bankEntries', 'bankExits', 'dailyBalance'
    ];
    
    elements.forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.innerHTML = '<div class="spinner-border spinner-border-sm" role="status"></div>';
        }
    });
}

function hideLoading() {
    // Le chargement sera masqué par la mise à jour des données
}

function showError(message) {
    // Utiliser la fonction showAlert si elle existe, sinon alert simple
    if (typeof showAlert === 'function') {
        showAlert(message, 'danger');
    } else {
        alert(message);
    }
}

function showSuccess(message) {
    // Utiliser la fonction showAlert si elle existe, sinon console.log
    if (typeof showAlert === 'function') {
        showAlert(message, 'success');
    } else {
        console.log(message);
    }
}

function goToInvoiceFromRecap(invoiceId, invoiceNumber) {
    try {
        const idVal = invoiceId && String(invoiceId).trim();
        const numVal = invoiceNumber && String(invoiceNumber).trim();
        if (!idVal && !numVal) return;

        // Si on connaît l'ID de la facture, demander explicitement l'ouverture du détail
        if (idVal) {
            sessionStorage.setItem('open_invoice_detail_id', idVal);
        }
        // Conserver le filtrage par numéro pour faciliter la navigation dans la liste
        if (numVal) {
            sessionStorage.setItem('invoiceSearchQuery', numVal);
        }
    } catch (e) {}
    window.location.href = '/invoices';
}

function goToQuotationFromRecap(quotationId, quotationNumber) {
    try {
        const val = (quotationNumber && String(quotationNumber).trim()) || (quotationId && String(quotationId).trim());
        if (!val) return;
        // On utilisera une recherche côté page devis via le numéro
        sessionStorage.setItem('quotationSearchQuery', val);
    } catch (e) {}
    window.location.href = '/quotations';
}
