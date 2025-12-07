// Gestion des rapports et analyses
let reportData = {
    overview: {},
    stock: {},
    finance: {},
    sales: {}
};

let charts = {};

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    setupEventListeners();
    generateReport();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    document.getElementById('reportType').addEventListener('change', handleReportTypeChange);
    document.getElementById('periodType').addEventListener('change', handlePeriodTypeChange);
}

// Gérer le changement de type de rapport
function handleReportTypeChange() {
    const reportType = document.getElementById('reportType').value;
    showReportSection(reportType);
}

// Gérer le changement de période
function handlePeriodTypeChange() {
    const periodType = document.getElementById('periodType').value;
    const dateFromContainer = document.getElementById('dateFromContainer');
    const dateToContainer = document.getElementById('dateToContainer');
    
    if (periodType === 'custom') {
        dateFromContainer.style.display = 'block';
        dateToContainer.style.display = 'block';
    } else {
        dateFromContainer.style.display = 'none';
        dateToContainer.style.display = 'none';
    }
}

// Afficher la section de rapport appropriée
function showReportSection(reportType) {
    // Masquer toutes les sections
    document.getElementById('overviewReport').style.display = 'none';
    document.getElementById('stockReport').style.display = 'none';
    document.getElementById('financeReport').style.display = 'none';
    document.getElementById('salesReport').style.display = 'none';
    
    // Afficher la section appropriée
    document.getElementById(reportType + 'Report').style.display = 'block';
}

// Générer le rapport
async function generateReport() {
    const reportType = document.getElementById('reportType').value;
    const period = getPeriodDates();
    
    try {
        // Afficher l'indicateur de chargement
        showLoading();
        
        // Masquer toutes les sections de rapport
        document.getElementById('overviewReport').style.display = 'none';
        document.getElementById('stockReport').style.display = 'none';
        document.getElementById('financeReport').style.display = 'none';
        document.getElementById('salesReport').style.display = 'none';
        
        // Charger les données selon le type de rapport
        // Note: loadOverviewData gère maintenant son propre hideLoading()
        switch (reportType) {
            case 'overview':
                await loadOverviewData(period);
                renderOverviewReport();
                break;
            case 'stock':
                await loadStockData(period);
                renderStockReport();
                hideLoading(); // Restaurer les métriques après le chargement
                break;
            case 'finance':
                await loadFinanceData(period);
                renderFinanceReport();
                hideLoading(); // Restaurer les métriques après le chargement
                break;
            case 'sales':
                await loadSalesData(period);
                renderSalesReport();
                hideLoading(); // Restaurer les métriques après le chargement
                break;
        }
        
        // Afficher la section de rapport appropriée
        showReportSection(reportType);
    } catch (error) {
        console.error('Erreur lors de la génération du rapport:', error);
        showError('Erreur lors de la génération du rapport');
        hideLoading(); // S'assurer que l'interface est restaurée même en cas d'erreur
    }
}

// Obtenir les dates de période
function getPeriodDates() {
    const periodType = document.getElementById('periodType').value;
    const today = new Date();
    let startDate, endDate;
    
    switch (periodType) {
        case 'today':
            startDate = new Date(today);
            endDate = new Date(today);
            break;
        case 'week':
            startDate = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 7);
            endDate = new Date(today);
            break;
        case 'month':
            startDate = new Date(today.getFullYear(), today.getMonth(), 1);
            endDate = new Date(today);
            break;
        case 'quarter':
            const quarterStart = Math.floor(today.getMonth() / 3) * 3;
            startDate = new Date(today.getFullYear(), quarterStart, 1);
            endDate = new Date(today);
            break;
        case 'year':
            startDate = new Date(today.getFullYear(), 0, 1);
            endDate = new Date(today);
            break;
        case 'custom':
            startDate = new Date(document.getElementById('dateFrom').value);
            endDate = new Date(document.getElementById('dateTo').value);
            break;
        default:
            startDate = new Date(today.getFullYear(), today.getMonth(), 1);
            endDate = new Date(today);
    }
    
    return {
        start: startDate.toISOString().split('T')[0],
        end: endDate.toISOString().split('T')[0]
    };
}

// Charger les données de vue d'ensemble
async function loadOverviewData(period) {
    try {
        // D'abord, masquer le rapport précédent et afficher le chargement
        const overviewReport = document.getElementById('overviewReport');
        if (overviewReport) {
            overviewReport.style.display = 'none';
        }
        
        // Charger les données
        const [products, invoices, clients, movements] = await Promise.all([
            fetchData('/api/products'),
            fetchData('/api/invoices'),
            fetchData('/api/clients'),
            fetchData('/api/stock-movements')
        ]);
        
        reportData.overview = {
            products: products || [],
            invoices: invoices || [],
            clients: clients || [],
            movements: movements || [],
            period
        };
        
        // Restaurer d'abord la structure des métriques
        hideLoading();
        
        // Ensuite mettre à jour les métriques avec les données chargées
        updateOverviewMetrics();
        
        // Enfin, afficher le rapport
        if (overviewReport) {
            overviewReport.style.display = 'block';
        }
    } catch (error) {
        console.error('Erreur lors du chargement des données d\'aperçu:', error);
        hideLoading(); // S'assurer que l'interface est restaurée même en cas d'erreur
    }
}

// Charger les données de stock
async function loadStockData(period) {
    const [products, movements] = await Promise.all([
        fetchData('/api/products'),
        fetchData('/api/stock-movements')
    ]);
    
    reportData.stock = {
        products: products || [],
        movements: movements || [],
        period
    };
}

// Charger les données financières
async function loadFinanceData(period) {
    const [invoices, transactions] = await Promise.all([
        fetchData('/api/invoices'),
        fetchData('/api/bank-transactions')
    ]);
    
    reportData.finance = {
        invoices: invoices || [],
        transactions: transactions || [],
        period
    };
}

// Charger les données de ventes
async function loadSalesData(period) {
    const [invoices, products, movements] = await Promise.all([
        fetchData('/api/invoices'),
        fetchData('/api/products'),
        fetchData('/api/stock-movements')
    ]);
    
    reportData.sales = {
        invoices: invoices || [],
        products: products || [],
        movements: movements || [],
        period
    };
}

// Fonction utilitaire pour récupérer les données
async function fetchData(endpoint) {
    try {
        const response = await fetch(endpoint, {
            credentials: 'include'
        });
        
        if (response.ok) {
            return await response.json();
        }
        return [];
    } catch (error) {
        console.error(`Erreur lors du chargement de ${endpoint}:`, error);
        return [];
    }
}

// Mettre à jour les métriques de vue d'ensemble
function updateOverviewMetrics() {
    const data = reportData.overview;
    
    // Calculer le chiffre d'affaires
    const totalRevenue = (data.invoices || [])
        .filter(inv => isInvoicePaid(inv))
        .reduce((sum, inv) => sum + getInvoiceTotal(inv), 0);
    
    // Vérifier l'existence des éléments avant de modifier leur contenu
    const totalRevenueEl = document.getElementById('totalRevenue');
    const totalProductsEl = document.getElementById('totalProducts');
    const totalInvoicesEl = document.getElementById('totalInvoices');
    const totalClientsEl = document.getElementById('totalClients');
    
    if (totalRevenueEl) totalRevenueEl.textContent = formatCurrency(totalRevenue);
    if (totalProductsEl) totalProductsEl.textContent = (data.products || []).length;
    if (totalInvoicesEl) totalInvoicesEl.textContent = (data.invoices || []).length;
    if (totalClientsEl) totalClientsEl.textContent = (data.clients || []).length;
    
    // Si un élément est manquant, afficher un avertissement dans la console
    if (!totalRevenueEl || !totalProductsEl || !totalInvoicesEl || !totalClientsEl) {
        console.warn('Certains éléments métriques sont manquants dans le DOM');
    }
}

// Rendre le rapport de vue d'ensemble
function renderOverviewReport() {
    renderRevenueChart();
    renderSalesChart();
}

// Rendre le graphique de revenus
function renderRevenueChart() {
    const ctx = document.getElementById('revenueChart').getContext('2d');
    
    // Détruire le graphique existant s'il existe
    if (charts.revenueChart) {
        charts.revenueChart.destroy();
    }
    
    const data = reportData.overview;
    const invoices = data.invoices || [];
    
    // Grouper les factures par mois
    const monthlyRevenue = {};
    invoices.forEach(invoice => {
        if (isInvoicePaid(invoice)) {
            const d = getInvoiceDate(invoice);
            if (!d) return;
            const month = d.toLocaleDateString('fr-FR', { 
                year: 'numeric', 
                month: 'short' 
            });
            monthlyRevenue[month] = (monthlyRevenue[month] || 0) + getInvoiceTotal(invoice);
        }
    });
    
    const labels = Object.keys(monthlyRevenue).sort();
    const values = labels.map(label => monthlyRevenue[label]);
    
    charts.revenueChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Chiffre d\'affaires (XOF)',
                data: values,
                borderColor: 'rgb(75, 192, 192)',
                backgroundColor: 'rgba(75, 192, 192, 0.2)',
                tension: 0.1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return formatCurrency(value);
                        }
                    }
                }
            }
        }
    });
}

// Rendre le graphique de répartition des ventes
function renderSalesChart() {
    const ctx = document.getElementById('salesChart').getContext('2d');
    
    if (charts.salesChart) {
        charts.salesChart.destroy();
    }
    
    const data = reportData.overview;
    const invoices = data.invoices || [];
    
    // Calculer la répartition par statut
    const statusCounts = { 'Payées': 0, 'En attente': 0, 'Brouillon': 0, 'Annulées': 0 };
    invoices.forEach(inv => {
        const st = normalizeInvoiceStatus(inv && inv.status);
        if (st === 'paid') statusCounts['Payées']++;
        else if (st === 'pending' || st === 'overdue' || st === 'partially_paid' || st === 'sent') statusCounts['En attente']++;
        else if (st === 'draft') statusCounts['Brouillon']++;
        else if (st === 'cancelled') statusCounts['Annulées']++;
    });
    
    const labels = Object.keys(statusCounts);
    const values = Object.values(statusCounts);
    const colors = ['#28a745', '#ffc107', '#6c757d', '#dc3545'];
    
    charts.salesChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors,
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom'
                }
            }
        }
    });
}

// Rendre le rapport de stock
function renderStockReport() {
    renderStockChart();
    renderStockTable();
    renderStockAlerts();
}

// Rendre le graphique de stock
function renderStockChart() {
    const ctx = document.getElementById('stockChart').getContext('2d');
    
    if (charts.stockChart) {
        charts.stockChart.destroy();
    }
    
    const data = reportData.stock;
    const products = (data.products || []).slice(0, 10); // Top 10
    
    const labels = products.map(p => p.name);
    const quantities = products.map(p => p.quantity || 0);
    
    charts.stockChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Quantité en stock',
                data: quantities,
                backgroundColor: 'rgba(54, 162, 235, 0.6)',
                borderColor: 'rgba(54, 162, 235, 1)',
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true
                }
            }
        }
    });
}

// Rendre le tableau de stock
function renderStockTable() {
    const tbody = document.getElementById('stockTableBody');
    const products = reportData.stock.products || [];
    
    tbody.innerHTML = '';
    
    products.forEach(product => {
        const quantity = product.quantity || 0;
        const price = product.price || 0;
        const value = quantity * price;
        
        let statusClass = 'success';
        let statusText = 'En stock';
        
        if (quantity === 0) {
            statusClass = 'danger';
            statusText = 'Rupture';
        } else if (quantity <= 5) {
            statusClass = 'warning';
            statusText = 'Stock faible';
        }
        
        const row = document.createElement('tr');
        row.innerHTML = `
            <td>${escapeHtml(product.name)}</td>
            <td>${quantity}</td>
            <td>${formatCurrency(price)}</td>
            <td>${formatCurrency(value)}</td>
            <td>
                <span class="badge bg-${statusClass}">${statusText}</span>
            </td>
        `;
        tbody.appendChild(row);
    });
}

// Rendre les alertes de stock
function renderStockAlerts() {
    const container = document.getElementById('stockAlerts');
    const products = reportData.stock.products || [];
    
    const lowStockProducts = products.filter(p => (p.quantity || 0) <= 5);
    const outOfStockProducts = products.filter(p => (p.quantity || 0) === 0);
    
    container.innerHTML = '';
    
    if (outOfStockProducts.length === 0 && lowStockProducts.length === 0) {
        container.innerHTML = `
            <div class="text-center py-3">
                <i class="bi bi-check-circle text-success display-4"></i>
                <p class="text-muted mt-2 mb-0">Aucune alerte de stock</p>
            </div>
        `;
        return;
    }
    
    if (outOfStockProducts.length > 0) {
        const alertDiv = document.createElement('div');
        alertDiv.className = 'alert alert-danger';
        alertDiv.innerHTML = `
            <h6><i class="bi bi-exclamation-triangle me-2"></i>Rupture de stock (${outOfStockProducts.length})</h6>
            <ul class="mb-0">
                ${outOfStockProducts.slice(0, 5).map(p => `<li>${escapeHtml(p.name)}</li>`).join('')}
                ${outOfStockProducts.length > 5 ? `<li>... et ${outOfStockProducts.length - 5} autres</li>` : ''}
            </ul>
        `;
        container.appendChild(alertDiv);
    }
    
    if (lowStockProducts.length > 0) {
        const alertDiv = document.createElement('div');
        alertDiv.className = 'alert alert-warning';
        alertDiv.innerHTML = `
            <h6><i class="bi bi-exclamation-circle me-2"></i>Stock faible (${lowStockProducts.length})</h6>
            <ul class="mb-0">
                ${lowStockProducts.slice(0, 5).map(p => `<li>${escapeHtml(p.name)} (${p.quantity || 0})</li>`).join('')}
                ${lowStockProducts.length > 5 ? `<li>... et ${lowStockProducts.length - 5} autres</li>` : ''}
            </ul>
        `;
        container.appendChild(alertDiv);
    }
}

// Rendre le rapport financier
function renderFinanceReport() {
    renderFinanceChart();
    renderPaymentMethodsChart();
}

// Rendre le graphique financier
function renderFinanceChart() {
    const ctx = document.getElementById('financeChart').getContext('2d');
    
    if (charts.financeChart) {
        charts.financeChart.destroy();
    }
    
    const data = reportData.finance;
    const transactions = data.transactions || [];
    
    // Grouper par mois
    const monthlyData = {};
    transactions.forEach(transaction => {
        if (transaction.date) {
            const month = new Date(transaction.date).toLocaleDateString('fr-FR', { 
                year: 'numeric', 
                month: 'short' 
            });
            if (!monthlyData[month]) {
                monthlyData[month] = { income: 0, expense: 0 };
            }
            
            if (transaction.type === 'entree') {
                monthlyData[month].income += transaction.amount || 0;
            } else {
                monthlyData[month].expense += transaction.amount || 0;
            }
        }
    });
    
    const labels = Object.keys(monthlyData).sort();
    const incomeData = labels.map(label => monthlyData[label].income);
    const expenseData = labels.map(label => monthlyData[label].expense);
    
    charts.financeChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Revenus',
                    data: incomeData,
                    backgroundColor: 'rgba(75, 192, 192, 0.6)',
                    borderColor: 'rgba(75, 192, 192, 1)',
                    borderWidth: 1
                },
                {
                    label: 'Dépenses',
                    data: expenseData,
                    backgroundColor: 'rgba(255, 99, 132, 0.6)',
                    borderColor: 'rgba(255, 99, 132, 1)',
                    borderWidth: 1
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return formatCurrency(value);
                        }
                    }
                }
            }
        }
    });
}

// Rendre le graphique des méthodes de paiement
function renderPaymentMethodsChart() {
    const ctx = document.getElementById('paymentMethodsChart').getContext('2d');
    
    if (charts.paymentMethodsChart) {
        charts.paymentMethodsChart.destroy();
    }
    
    const data = reportData.finance;
    const transactions = data.transactions || [];
    
    // Compter par méthode de paiement
    const methodCounts = {};
    transactions.forEach(transaction => {
        const method = transaction.payment_method || 'Autre';
        methodCounts[method] = (methodCounts[method] || 0) + 1;
    });
    
    const labels = Object.keys(methodCounts);
    const values = Object.values(methodCounts);
    const colors = ['#FF6384', '#36A2EB', '#FFCE56', '#4BC0C0', '#9966FF'];
    
    charts.paymentMethodsChart = new Chart(ctx, {
        type: 'pie',
        data: {
            labels: labels,
            datasets: [{
                data: values,
                backgroundColor: colors.slice(0, labels.length),
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom'
                }
            }
        }
    });
}

// Rendre le rapport des ventes
function renderSalesReport() {
    renderSalesTrendChart();
    renderTopProducts();
}

// Rendre le graphique de tendance des ventes
function renderSalesTrendChart() {
    const ctx = document.getElementById('salesTrendChart').getContext('2d');
    
    if (charts.salesTrendChart) {
        charts.salesTrendChart.destroy();
    }
    
    const data = reportData.sales;
    const invoices = data.invoices || [];
    
    // Grouper par semaine
    const weeklyData = {};
    invoices.forEach(invoice => {
        if (isInvoicePaid(invoice)) {
            const d = getInvoiceDate(invoice);
            if (!d) return;
            const week = getWeekNumber(d);
            const weekKey = `S${week}`;
            weeklyData[weekKey] = (weeklyData[weekKey] || 0) + getInvoiceTotal(invoice);
        }
    });
    
    const labels = Object.keys(weeklyData).sort();
    const values = labels.map(label => weeklyData[label]);
    
    charts.salesTrendChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Ventes hebdomadaires (XOF)',
                data: values,
                borderColor: 'rgb(255, 99, 132)',
                backgroundColor: 'rgba(255, 99, 132, 0.2)',
                tension: 0.1,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: function(value) {
                            return formatCurrency(value);
                        }
                    }
                }
            }
        }
    });
}

// Rendre les top produits
function renderTopProducts() {
    const container = document.getElementById('topProducts');
    const data = reportData.sales;
    const movements = data.movements || [];
    
    // Calculer les ventes par produit
    const productSales = {};
    movements.forEach(movement => {
        if (movement.type === 'sortie' && movement.product_id) {
            const productId = movement.product_id;
            productSales[productId] = (productSales[productId] || 0) + (movement.quantity || 0);
        }
    });
    
    // Obtenir les noms des produits
    const products = data.products || [];
    const topProducts = Object.entries(productSales)
        .map(([productId, quantity]) => {
            const product = products.find(p => p.id == productId);
            return {
                name: product ? product.name : `Produit #${productId}`,
                quantity: quantity
            };
        })
        .sort((a, b) => b.quantity - a.quantity)
        .slice(0, 5);
    
    container.innerHTML = '';
    
    if (topProducts.length === 0) {
        container.innerHTML = `
            <div class="text-center py-3">
                <i class="bi bi-box text-muted display-4"></i>
                <p class="text-muted mt-2 mb-0">Aucune vente enregistrée</p>
            </div>
        `;
        return;
    }
    
    topProducts.forEach((product, index) => {
        const item = document.createElement('div');
        item.className = 'd-flex justify-content-between align-items-center mb-2';
        item.innerHTML = `
            <div>
                <span class="badge bg-primary me-2">${index + 1}</span>
                <span>${escapeHtml(product.name)}</span>
            </div>
            <span class="fw-bold">${product.quantity}</span>
        `;
        container.appendChild(item);
    });
}

// Actualiser les rapports
function refreshReports() {
    generateReport();
}

// Exporter le rapport
function exportReport() {
    showInfo('Fonctionnalité d\'export en cours de développement');
}

// Utilitaires
function getWeekNumber(date) {
    const d = new Date(Date.UTC(date.getFullYear(), date.getMonth(), date.getDate()));
    const dayNum = d.getUTCDay() || 7;
    d.setUTCDate(d.getUTCDate() + 4 - dayNum);
    const yearStart = new Date(Date.UTC(d.getUTCFullYear(), 0, 1));
    return Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
}

function formatCurrency(amount) {
    return new Intl.NumberFormat('fr-FR', { 
        style: 'currency', 
        currency: 'XOF',
        minimumFractionDigits: 0,
        maximumFractionDigits: 0
    }).format(Math.round(amount || 0));
}

// Helpers de normalisation pour compatibilité FR/EN et différents schémas
function normalizeInvoiceStatus(status) {
    const s = String(status || '').trim().toLowerCase();
    if (!s) return 'unknown';
    if (['payée', 'paid', 'payee', 'payeé', 'paid_in_full', 'paid-in-full', 'paid ', ' paid'].includes(s)) return 'paid';
    if (['en attente', 'pending', 'sent'].includes(s)) return 'pending';
    if (['partiellement payée', 'partially paid', 'partially_paid'].includes(s)) return 'partially_paid';
    if (['en retard', 'overdue'].includes(s)) return 'overdue';
    if (['brouillon', 'draft'].includes(s)) return 'draft';
    if (['annulée', 'annulee', 'cancelled', 'canceled'].includes(s)) return 'cancelled';
    return s;
}

function isInvoicePaid(inv) {
    // On considère le CA basé sur factures payées uniquement
    return normalizeInvoiceStatus(inv && inv.status) === 'paid';
}

function getInvoiceTotal(inv) {
    // Supporte 'total' (backend) ou 'total_amount' (ancienne UI)
    const t = inv && (inv.total ?? inv.total_amount);
    const num = Number(t);
    return Number.isFinite(num) ? num : 0;
}

function getInvoiceDate(inv) {
    const raw = inv && (inv.date || inv.created_at || inv.createdAt);
    if (!raw) return null;
    const d = new Date(raw);
    return isNaN(d.getTime()) ? null : d;
}

function showLoading() {
    const metricsRow = document.getElementById('metricsRow');
    metricsRow.innerHTML = `
        <div class="col-12 text-center py-5">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Génération du rapport...</span>
            </div>
            <p class="text-muted mt-2 mb-0">Génération du rapport en cours...</p>
        </div>
    `;
}

function hideLoading() {
    // Restaurer la structure des métriques
    const metricsRow = document.getElementById('metricsRow');
    if (metricsRow) {
        metricsRow.innerHTML = `
            <div class="col-md-3 mb-3">
                <div class="card text-white metric-card">
                    <div class="card-body">
                        <div class="d-flex justify-content-between">
                            <div>
                                <h4 class="mb-0" id="totalRevenue">0 XOF</h4>
                                <p class="mb-0">Chiffre d'affaires</p>
                            </div>
                            <i class="bi bi-currency-exchange display-6"></i>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white stock-card">
                    <div class="card-body">
                        <div class="d-flex justify-content-between">
                            <div>
                                <h4 class="mb-0" id="totalProducts">0</h4>
                                <p class="mb-0">Produits en stock</p>
                            </div>
                            <i class="bi bi-box display-6"></i>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white finance-card">
                    <div class="card-body">
                        <div class="d-flex justify-content-between">
                            <div>
                                <h4 class="mb-0" id="totalInvoices">0</h4>
                                <p class="mb-0">Factures émises</p>
                            </div>
                            <i class="bi bi-receipt display-6"></i>
                        </div>
                    </div>
                </div>
            </div>
            <div class="col-md-3 mb-3">
                <div class="card text-white sales-card">
                    <div class="card-body">
                        <div class="d-flex justify-content-between">
                            <div>
                                <h4 class="mb-0" id="totalClients">0</h4>
                                <p class="mb-0">Clients actifs</p>
                            </div>
                            <i class="bi bi-people display-6"></i>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }
}
