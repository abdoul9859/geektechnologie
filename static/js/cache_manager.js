// Gestionnaire de cache
let cacheEntries = [];
let filteredEntries = [];
let currentEntryKey = null;
let performanceChart = null;
let memoryChart = null;

// Initialisation
document.addEventListener('DOMContentLoaded', function() {
    loadCacheData();
    setupEventListeners();
    initializeCharts();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Filtres
    document.getElementById('typeFilter').addEventListener('change', filterEntries);
    document.getElementById('statusFilter').addEventListener('change', filterEntries);
    document.getElementById('searchInput').addEventListener('input', debounce(filterEntries, 300));
}

// Charger les données de cache
async function loadCacheData() {
    try {
        showLoading();
        console.log('🔄 Chargement des données de cache depuis l\'API...');
        
        // TODO: Remplacer par un vrai appel API quand disponible
        // const { data } = await axios.get('/api/cache/entries');
        // cacheEntries = Array.isArray(data) ? data : [];
        
        // Pour l'instant, afficher un état vide propre
        console.log('⚠️ API cache non disponible - affichage état vide');
        cacheEntries = [];
        
        filteredEntries = [...cacheEntries];
        displayCacheEntries();
        updateStatistics();
        updateCharts();
        hideLoading();
    } catch (error) {
        console.error('❌ Erreur lors du chargement du cache:', error?.response?.data?.detail || error.message);
        cacheEntries = [];
        filteredEntries = [];
        displayCacheEntries();
        updateStatistics();
        hideLoading();
    }
}

// Afficher les entrées de cache
function displayCacheEntries() {
    const container = document.getElementById('cacheEntriesContainer');
    container.innerHTML = '';

    if (filteredEntries.length === 0) {
        container.innerHTML = `
            <div class="text-center py-5">
                <i class="bi bi-hdd-stack display-1 text-muted"></i>
                <h4 class="text-muted mt-3">Aucune entrée trouvée</h4>
                <p class="text-muted">Le cache est vide ou aucune entrée ne correspond aux filtres</p>
            </div>
        `;
        return;
    }

    filteredEntries.forEach(entry => {
        const card = createCacheEntryCard(entry);
        container.appendChild(card);
    });

    updateEntriesCount();
}

// Créer une carte d'entrée de cache
function createCacheEntryCard(entry) {
    const card = document.createElement('div');
    card.className = `cache-card ${entry.status} mb-3`;
    
    const statusInfo = getStatusInfo(entry.status);
    const hitRate = entry.hits + entry.misses > 0 ? 
        Math.round((entry.hits / (entry.hits + entry.misses)) * 100) : 0;
    const isExpired = new Date(entry.expires_at) < new Date();
    
    card.innerHTML = `
        <div class="card">
            <div class="card-body">
                <div class="row">
                    <div class="col-md-8">
                        <div class="d-flex align-items-start">
                            <div class="me-3">
                                <i class="bi ${getTypeIcon(entry.type)} display-6 text-${statusInfo.color}"></i>
                            </div>
                            <div class="flex-grow-1">
                                <h6 class="card-title mb-1">
                                    <code>${escapeHtml(entry.key)}</code>
                                </h6>
                                <div class="row text-sm mb-2">
                                    <div class="col-md-4">
                                        <small class="text-muted">
                                            <i class="bi bi-tag me-1"></i>
                                            ${entry.type.toUpperCase()}
                                        </small>
                                    </div>
                                    <div class="col-md-4">
                                        <small class="text-muted">
                                            <i class="bi bi-hdd me-1"></i>
                                            ${formatBytes(entry.size)}
                                        </small>
                                    </div>
                                    <div class="col-md-4">
                                        <small class="text-muted">
                                            <i class="bi bi-clock me-1"></i>
                                            ${formatRelativeTime(entry.last_accessed)}
                                        </small>
                                    </div>
                                </div>
                                
                                <div class="row text-sm">
                                    <div class="col-md-6">
                                        <small class="text-muted">
                                            Créé: ${formatDateTime(entry.created_at)}
                                        </small>
                                    </div>
                                    <div class="col-md-6">
                                        <small class="${isExpired ? 'text-danger' : 'text-muted'}">
                                            Expire: ${formatDateTime(entry.expires_at)}
                                        </small>
                                    </div>
                                </div>

                                <div class="mt-2">
                                    <div class="progress" style="height: 4px;">
                                        <div class="progress-bar bg-success" style="width: ${hitRate}%"></div>
                                    </div>
                                    <small class="text-muted">
                                        Taux de réussite: ${hitRate}% (${entry.hits} hits, ${entry.misses} misses)
                                    </small>
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="col-md-4">
                        <div class="text-end">
                            <span class="badge bg-${statusInfo.color} mb-2">${statusInfo.text}</span>
                            
                            <div class="btn-group-vertical btn-group-sm w-100">
                                <button class="btn btn-outline-primary" onclick="showCacheDetails('${entry.key}')">
                                    <i class="bi bi-info-circle me-2"></i>
                                    Détails
                                </button>
                                <button class="btn btn-outline-warning" onclick="refreshCacheEntry('${entry.key}')">
                                    <i class="bi bi-arrow-clockwise me-2"></i>
                                    Actualiser
                                </button>
                                <button class="btn btn-outline-danger" onclick="deleteCacheEntry('${entry.key}')">
                                    <i class="bi bi-trash me-2"></i>
                                    Supprimer
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;

    return card;
}

// Obtenir les informations de statut
function getStatusInfo(status) {
    const statusMap = {
        active: { color: 'success', text: 'Actif' },
        expired: { color: 'warning', text: 'Expiré' },
        disabled: { color: 'secondary', text: 'Désactivé' }
    };
    return statusMap[status] || { color: 'secondary', text: 'Inconnu' };
}

// Obtenir l'icône du type
function getTypeIcon(type) {
    const iconMap = {
        database: 'bi-database',
        api: 'bi-cloud',
        session: 'bi-person-circle',
        static: 'bi-file-earmark'
    };
    return iconMap[type] || 'bi-hdd';
}

// Filtrer les entrées
function filterEntries() {
    const typeFilter = document.getElementById('typeFilter').value;
    const statusFilter = document.getElementById('statusFilter').value;
    const searchTerm = document.getElementById('searchInput').value.toLowerCase();

    filteredEntries = cacheEntries.filter(entry => {
        const matchesType = !typeFilter || entry.type === typeFilter;
        const matchesStatus = !statusFilter || entry.status === statusFilter;
        const matchesSearch = !searchTerm || 
            entry.key.toLowerCase().includes(searchTerm) ||
            entry.data_preview.toLowerCase().includes(searchTerm);

        return matchesType && matchesStatus && matchesSearch;
    });

    displayCacheEntries();
    updateStatistics();
}

// Effacer les filtres
function clearFilters() {
    document.getElementById('typeFilter').value = '';
    document.getElementById('statusFilter').value = '';
    document.getElementById('searchInput').value = '';
    filterEntries();
}

// Mettre à jour les statistiques
function updateStatistics() {
    const totalSize = filteredEntries.reduce((sum, entry) => sum + (entry.size || 0), 0);
    const totalHits = filteredEntries.reduce((sum, entry) => sum + (entry.hits || 0), 0);
    const totalMisses = filteredEntries.reduce((sum, entry) => sum + (entry.misses || 0), 0);
    const activeEntries = filteredEntries.filter(e => e.status === 'active').length;
    
    const hitRate = totalHits + totalMisses > 0 ? 
        Math.round((totalHits / (totalHits + totalMisses)) * 100) : 0;
    
    // Calculs réels basés sur les données disponibles
    const memoryPercent = totalSize > 0 ? Math.round((totalSize / (100 * 1024 * 1024)) * 100) : 0;
    const avgResponseTime = filteredEntries.length > 0 ? 
        Math.round(filteredEntries.reduce((sum, e) => sum + (e.avg_response_time || 0), 0) / filteredEntries.length) : 0;

    document.getElementById('memoryUsage').textContent = formatBytes(totalSize);
    document.getElementById('memoryPercent').textContent = `${memoryPercent}% utilisé`;
    document.getElementById('hitRate').textContent = `${hitRate}%`;
    document.getElementById('hitCount').textContent = `${totalHits} hits`;
    document.getElementById('activeEntries').textContent = activeEntries;
    document.getElementById('totalEntries').textContent = `sur ${filteredEntries.length} total`;
    document.getElementById('avgResponseTime').textContent = `${avgResponseTime}ms`;
}

// Mettre à jour le compteur d'entrées
function updateEntriesCount() {
    document.getElementById('entriesCount').textContent = `${filteredEntries.length} entrées`;
}

// Initialiser les graphiques
function initializeCharts() {
    initializePerformanceChart();
    initializeMemoryChart();
}

// Initialiser le graphique de performance
function initializePerformanceChart() {
    const ctx = document.getElementById('performanceChart').getContext('2d');
    
    // Initialiser avec des données vides - seront remplies par updateCharts()
    const labels = [];
    const hitData = [];
    const missData = [];
    
    // Ajouter un point initial avec l'heure actuelle
    const currentTime = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    labels.push(currentTime);
    hitData.push(0);
    missData.push(0);

    performanceChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: 'Cache Hits',
                data: hitData,
                borderColor: '#198754',
                backgroundColor: 'rgba(25, 135, 84, 0.1)',
                tension: 0.4
            }, {
                label: 'Cache Misses',
                data: missData,
                borderColor: '#dc3545',
                backgroundColor: 'rgba(220, 53, 69, 0.1)',
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                y: {
                    beginAtZero: true
                }
            },
            plugins: {
                legend: {
                    position: 'top'
                }
            }
        }
    });
}

// Initialiser le graphique de mémoire
function initializeMemoryChart() {
    const ctx = document.getElementById('memoryChart').getContext('2d');
    
    const totalSize = cacheEntries.reduce((sum, entry) => sum + entry.size, 0);
    const usedMemory = totalSize;
    const freeMemory = (100 * 1024 * 1024) - totalSize; // 100MB total simulé

    memoryChart = new Chart(ctx, {
        type: 'doughnut',
        data: {
            labels: ['Utilisé', 'Libre'],
            datasets: [{
                data: [usedMemory, freeMemory],
                backgroundColor: ['#0d6efd', '#e9ecef'],
                borderWidth: 0
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

// Mettre à jour les graphiques
function updateCharts() {
    if (performanceChart && filteredEntries.length > 0) {
        // Utiliser les vraies données si disponibles
        const totalHits = filteredEntries.reduce((sum, entry) => sum + (entry.hits || 0), 0);
        const totalMisses = filteredEntries.reduce((sum, entry) => sum + (entry.misses || 0), 0);
        
        // Mettre à jour avec les données réelles
        const currentTime = new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
        
        // Garder seulement les 24 derniers points
        if (performanceChart.data.labels.length >= 24) {
            performanceChart.data.labels.shift();
            performanceChart.data.datasets[0].data.shift();
            performanceChart.data.datasets[1].data.shift();
        }
        
        performanceChart.data.labels.push(currentTime);
        performanceChart.data.datasets[0].data.push(totalHits);
        performanceChart.data.datasets[1].data.push(totalMisses);
        
        performanceChart.update();
    }

    if (memoryChart) {
        const totalSize = filteredEntries.reduce((sum, entry) => sum + (entry.size || 0), 0);
        const maxMemory = 100 * 1024 * 1024; // 100MB par défaut
        const freeMemory = Math.max(0, maxMemory - totalSize);
        
        memoryChart.data.datasets[0].data = [totalSize, freeMemory];
        memoryChart.update();
    }
}

// Afficher les détails d'une entrée
function showCacheDetails(key) {
    const entry = cacheEntries.find(e => e.key === key);
    if (!entry) return;

    currentEntryKey = key;
    const content = document.getElementById('cacheDetailsContent');
    
    content.innerHTML = `
        <div class="row">
            <div class="col-md-6">
                <h6>Informations Générales</h6>
                <table class="table table-sm">
                    <tr><td><strong>Clé :</strong></td><td><code>${escapeHtml(entry.key)}</code></td></tr>
                    <tr><td><strong>Type :</strong></td><td>${entry.type.toUpperCase()}</td></tr>
                    <tr><td><strong>Statut :</strong></td><td><span class="badge bg-${getStatusInfo(entry.status).color}">${getStatusInfo(entry.status).text}</span></td></tr>
                    <tr><td><strong>Taille :</strong></td><td>${formatBytes(entry.size)}</td></tr>
                    <tr><td><strong>Créé le :</strong></td><td>${formatDateTime(entry.created_at)}</td></tr>
                    <tr><td><strong>Expire le :</strong></td><td>${formatDateTime(entry.expires_at)}</td></tr>
                    <tr><td><strong>Dernier accès :</strong></td><td>${formatDateTime(entry.last_accessed)}</td></tr>
                </table>
            </div>
            <div class="col-md-6">
                <h6>Statistiques d'Accès</h6>
                <table class="table table-sm">
                    <tr><td><strong>Hits :</strong></td><td class="text-success">${entry.hits}</td></tr>
                    <tr><td><strong>Misses :</strong></td><td class="text-danger">${entry.misses}</td></tr>
                    <tr><td><strong>Taux de réussite :</strong></td><td>${Math.round((entry.hits / (entry.hits + entry.misses)) * 100)}%</td></tr>
                    <tr><td><strong>Total accès :</strong></td><td>${entry.hits + entry.misses}</td></tr>
                </table>
                
                <div class="progress mb-2" style="height: 20px;">
                    <div class="progress-bar bg-success" style="width: ${(entry.hits / (entry.hits + entry.misses)) * 100}%">
                        ${entry.hits} hits
                    </div>
                    <div class="progress-bar bg-danger" style="width: ${(entry.misses / (entry.hits + entry.misses)) * 100}%">
                        ${entry.misses} misses
                    </div>
                </div>
            </div>
        </div>
        
        <div class="mt-3">
            <h6>Aperçu des Données</h6>
            <pre class="bg-light p-3 rounded" style="max-height: 200px; overflow-y: auto;"><code>${escapeHtml(entry.data_preview)}</code></pre>
        </div>
    `;

    const modal = new bootstrap.Modal(document.getElementById('cacheDetailsModal'));
    modal.show();
}

// Actualiser une entrée de cache
async function refreshCacheEntry(key) {
    try {
        // TODO: Appel API pour actualiser l'entrée
        // await axios.post(`/api/cache/entries/${key}/refresh`);
        
        // Pour l'instant, simuler une actualisation locale
        const entry = cacheEntries.find(e => e.key === key);
        if (entry) {
            entry.last_accessed = new Date().toISOString();
            entry.hits = (entry.hits || 0) + 1;
            displayCacheEntries();
            updateStatistics();
        }
        showSuccess(`Entrée "${key}" actualisée`);
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors de l\'actualisation';
        showError(msg);
    }
}

// Supprimer une entrée de cache
async function deleteCacheEntry(key) {
    if (!key) key = currentEntryKey;
    if (!key) return;

    if (!confirm(`Êtes-vous sûr de vouloir supprimer l'entrée "${key}" ?`)) return;
    
    try {
        // TODO: Appel API pour supprimer l'entrée
        // await axios.delete(`/api/cache/entries/${key}`);
        
        // Pour l'instant, suppression locale
        cacheEntries = cacheEntries.filter(e => e.key !== key);
        filterEntries();
        
        // Fermer la modal si elle est ouverte
        const modal = bootstrap.Modal.getInstance(document.getElementById('cacheDetailsModal'));
        if (modal) modal.hide();
        
        showSuccess(`Entrée "${key}" supprimée`);
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors de la suppression';
        showError(msg);
    }
}

// Vider tout le cache
async function clearAllCache() {
    if (!confirm('Êtes-vous sûr de vouloir vider tout le cache ? Cette action est irréversible.')) return;
    
    try {
        // TODO: Appel API pour vider le cache
        // await axios.delete('/api/cache/entries');
        
        // Pour l'instant, vidage local
        cacheEntries = [];
        filterEntries();
        updateCharts();
        showSuccess('Cache vidé avec succès');
    } catch (error) {
        const msg = error?.response?.data?.detail || error.message || 'Erreur lors du vidage du cache';
        showError(msg);
    }
}

// Actualiser les données de cache
function refreshCacheData() {
    loadCacheData();
    showSuccess('Données actualisées');
}

// Sauvegarder la configuration
async function saveConfiguration() {
    const config = {
        maxSize: document.getElementById('maxSize').value,
        defaultTTL: document.getElementById('defaultTTL').value,
        autoCleanup: document.getElementById('autoCleanup').checked,
        compressionEnabled: document.getElementById('compressionEnabled').checked
    };

    // Sauvegarder dans SQLite via API
    await apiStorage.setCacheItem('cacheConfig', config, 24 * 30); // 30 jours
    
    const modal = bootstrap.Modal.getInstance(document.getElementById('configModal'));
    modal.hide();
    
    showSuccess('Configuration sauvegardée');
}

// Utilitaires
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

function formatRelativeTime(dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const diffMs = now - date;
    const diffMins = Math.floor(diffMs / 60000);
    
    if (diffMins < 1) return 'À l\'instant';
    if (diffMins < 60) return `Il y a ${diffMins}min`;
    if (diffMins < 1440) return `Il y a ${Math.floor(diffMins / 60)}h`;
    return `Il y a ${Math.floor(diffMins / 1440)}j`;
}

function formatDateTime(dateString) {
    return new Date(dateString).toLocaleString('fr-FR');
}

function showLoading() {
    const container = document.getElementById('cacheEntriesContainer');
    container.innerHTML = `
        <div class="text-center py-5">
            <div class="spinner-border text-primary" role="status">
                <span class="visually-hidden">Chargement...</span>
            </div>
            <p class="text-muted mt-2 mb-0">Chargement des données de cache...</p>
        </div>
    `;
}

function hideLoading() {
    // Le loading sera masqué par displayCacheEntries()
}

// Actualisation automatique toutes les 30 secondes
setInterval(() => {
    if (document.visibilityState === 'visible') {
        updateStatistics();
        updateCharts();
    }
}, 30000);
