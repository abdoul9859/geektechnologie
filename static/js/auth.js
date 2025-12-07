// Gestion de l'authentification basée sur cookies HttpOnly + base de données
class AuthManager {
    constructor() {
        // Plus de token en mémoire - tout est géré par cookies HttpOnly
        this.userData = {};
        this.isAuthenticated = false;
        this.setupAxiosInterceptors();
        this.checkAuthOnLoad();
    }

    showApp() {
        const body = document.getElementById('app-body');
        if (body) {
            body.style.visibility = 'visible';
            body.style.opacity = '1';
        }
    }

    setupAxiosInterceptors() {
        // Plus d'intercepteurs: la gestion 401 est faite dans /static/js/http.js
        // Rien à configurer ici.
    }

    // Authentification 100% base de données via cookies HttpOnly

    setUserData(user) {
        this.userData = user || {};
        this.isAuthenticated = !!user;
    }

    checkAuthOnLoad() {
        // Page de login: vérifier si déjà authentifié (en arrière-plan)
        if (window.location.pathname === '/login') {
            this.verifyAuth().then((isAuth) => {
                if (isAuth) {
                    window.location.href = '/';
                }
            }).catch(() => {
                // Pas authentifié, rester sur la page login
            });
            return;
        }

        // Afficher immédiatement l'application pour réduire le temps de chargement
        this.showApp();

        // Vérification d'auth en arrière-plan pour hydrater l'UI (sans bloquer ni rediriger)
        this.verifyAuth()
            .then((isAuth) => {
                if (isAuth) {
                    this.displayUserInfo();
                }
            })
            .catch(() => {
                // Laisser l'UI rendre; les appels API protégés géreront 401 via l'intercepteur
            });
    }

    async verifyAuth() {
        try {
            const response = await (window.api || window.axios)({ url: '/api/auth/verify', method: 'GET' });
            this.userData = response.data;
            this.isAuthenticated = true;
            return true;
        } catch (error) {
            // Vérification échouée: l'utilisateur n'est pas authentifié
            this.userData = {};
            this.isAuthenticated = false;
            return false;
        }
    }

    displayUserInfo() {
        const usernameElement = document.getElementById('username');
        if (usernameElement && this.userData.full_name) {
            usernameElement.textContent = this.userData.full_name;
        }
    }

    async logout() {
        try {
            // Appeler l'endpoint de déconnexion pour effacer le cookie HttpOnly
            await (window.api || window.axios)({ url: '/api/auth/logout', method: 'POST' });
        } catch (error) {
            console.error('Erreur lors de la déconnexion:', error);
        } finally {
            // Nettoyage mémoire
            this.userData = {};
            this.isAuthenticated = false;
            // Redirection vers login
            window.location.href = '/login';
        }
    }

    isAuthenticatedSync() {
        return this.isAuthenticated;
    }

    isAdmin() {
        return this.userData.role === 'admin';
    }

    getUser() {
        return this.userData;
    }

    get token() {
        return this.isAuthenticated ? 'cookie-based' : null;
    }

    async login(username, password) {
        try {
            const response = await (window.api || window.axios)({
                url: '/api/auth/login',
                method: 'POST',
                data: { username: username, password: password }
            });

            if (response.data.access_token && response.data.user) {
                this.setUserData(response.data.user);
                return { success: true, user: response.data.user };
            } else {
                return { success: false, error: 'Données manquantes dans la réponse' };
            }
        } catch (error) {
            console.error('Erreur de connexion:', error);
            return { 
                success: false, 
                error: error.response?.data?.detail || 'Erreur de connexion' 
            };
        }
    }
}

// Fonction globale de déconnexion
function logout() {
    if (confirm('Êtes-vous sûr de vouloir vous déconnecter ?')) {
        authManager.logout();
        window.location.href = '/login';
    }
}

// Fonction utilitaire pour les requêtes API authentifiées
async function apiRequest(url, options = {}) {
    try {
        // Supporter les signatures type fetch (body) et axios (data)
        const { method, params, data, body, headers, ...rest } = options;
        const response = await (window.api || window.axios)({
            url: url,
            method: method || 'GET',
            params: params,
            headers: headers,
            data: data !== undefined ? data : body, // alias body -> data
            ...rest
        });
        return response;
    } catch (error) {
        console.error('Erreur API:', error);
        throw error;
    }
}

// Initialiser le gestionnaire d'authentification
const authManager = new AuthManager();
window.authManager = authManager;
