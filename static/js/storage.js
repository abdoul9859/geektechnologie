// Système de stockage unifié pour Geek Technologie (API SQLite)

class ApiStorage {
    constructor() {
        this.baseUrl = '/api/user-settings';
        this.cache = new Map();
    }

    // ==================== USER SETTINGS ====================

async setItem(key, value) {
        try {
            const response = await apiRequest(`${this.baseUrl}/${key}`, {
                method: 'POST',
                data: { value }
            });
            return response;
        } catch (error) {
            console.error('Erreur lors de la sauvegarde:', error);
            throw error;
        }
    }

    async getItem(key) {
        try {
            const response = await apiRequest(`${this.baseUrl}/${key}`);
            let value = response.data;
            // API renvoie { data: ... } → déballer pour retourner directement la valeur
            if (value && typeof value === 'object' && Object.prototype.hasOwnProperty.call(value, 'data')) {
                value = value.data;
            }
            return value;
        } catch (error) {
            console.error('Erreur lors de la récupération:', error);
            return null;
        }
    }

    async removeItem(key) {
        try {
            await apiRequest(`${this.baseUrl}/${key}`, {
                method: 'DELETE'
            });
        } catch (error) {
            console.error('Erreur lors de la suppression:', error);
            throw error;
        }
    }

    async getAllSettings() {
        try {
            const response = await apiRequest(this.baseUrl);
            let settings = response.data;
            if (settings && typeof settings === 'object' && Object.prototype.hasOwnProperty.call(settings, 'data')) {
                settings = settings.data;
            }
            return settings;
        } catch (error) {
            console.error('Erreur lors de la récupération des paramètres:', error);
            return {};
        }
    }

    // ==================== SCAN HISTORY ====================

async addScanHistory(scanData) {
        try {
            const response = await apiRequest(`${this.baseUrl}/scan-history`, {
                method: 'POST',
                data: scanData
            });
            return response;
        } catch (error) {
            console.error('Erreur lors de l\'ajout du scan:', error);
            throw error;
        }
    }

    async getScanHistory(limit = 50) {
        try {
            const response = await apiRequest(`${this.baseUrl}/scan-history?limit=${limit}`);
            return response.data;
        } catch (error) {
            console.error('Erreur lors de la récupération de l\'historique:', error);
            return [];
        }
    }

    async clearScanHistory() {
        try {
            await apiRequest(`${this.baseUrl}/scan-history`, {
                method: 'DELETE'
            });
        } catch (error) {
            console.error('Erreur lors de la suppression de l\'historique:', error);
            throw error;
        }
    }

    // ==================== CACHE ====================

async setCacheItem(key, value, expiresInHours = 24) {
        try {
            const response = await apiRequest(`${this.baseUrl}/cache/${key}`, {
                method: 'POST',
                data: {
                    value: value,
                    expires_in_hours: expiresInHours
                }
            });
            return response;
        } catch (error) {
            console.error('Erreur lors de la mise en cache:', error);
            throw error;
        }
    }

    async getCacheItem(key) {
        try {
            const response = await apiRequest(`${this.baseUrl}/cache/${key}`);
            return response.data;
        } catch (error) {
            console.error('Erreur lors de la récupération du cache:', error);
            return null;
        }
    }

    async removeCacheItem(key) {
        try {
            await apiRequest(`${this.baseUrl}/cache/${key}`, {
                method: 'DELETE'
            });
        } catch (error) {
            console.error('Erreur lors de la suppression du cache:', error);
            throw error;
        }
    }

    // ==================== MÉTHODES PARAMÈTRES ====================
    async setAppSettings(settings) {
        return await this.setItem('appSettings', settings);
    }

    async getAppSettings() {
        const settings = await this.getItem('appSettings');
        return settings || {};
    }

    async updateAppSetting(key, value) {
        const currentSettings = await this.getAppSettings();
        currentSettings[key] = value;
        return await this.setAppSettings(currentSettings);
    }

    // Méthode pour charger tous les paramètres au démarrage
    async preloadSettings() {
        try {
            await this.getAllSettings();
            console.log('Paramètres utilisateur préchargés depuis SQLite');
        } catch (error) {
            console.error('Erreur lors du préchargement des paramètres:', error);
        }
    }

    // Récupère uniquement les méthodes de paiement formatées côté serveur
    async getInvoicePaymentMethods() {
        try {
            const response = await apiRequest(`${this.baseUrl}/invoice/payment-methods`);
            const data = response && response.data;
            if (Array.isArray(data)) return data;
            if (data && Array.isArray(data.data)) return data.data;
            return ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"];
        } catch (e) {
            return ["Espèces", "Virement bancaire", "Mobile Money", "Chèque", "Carte bancaire"];
        }
    }
    // Méthode pour vider tout le cache local (pas la base de données)
    clearLocalCache() {
        this.cache.clear();
    }
}

// Instance globale
const apiStorage = new ApiStorage();

// Préchargement simple des paramètres au chargement
document.addEventListener('DOMContentLoaded', async function() {
    const isAuthenticated = !!(window.authManager && window.authManager.token);
    if (!isAuthenticated) return;
    await apiStorage.preloadSettings();
});

// Export global
window.apiStorage = apiStorage;
