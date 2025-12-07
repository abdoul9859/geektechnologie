// Guide utilisateur
document.addEventListener('DOMContentLoaded', function() {
    setupEventListeners();
});

// Configuration des écouteurs d'événements
function setupEventListeners() {
    // Navigation dans les sections
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', function(e) {
            e.preventDefault();
            const sectionId = this.getAttribute('onclick').match(/'([^']+)'/)[1];
            showSection(sectionId);
        });
    });
}

// Afficher une section spécifique
function showSection(sectionId) {
    // Masquer toutes les sections
    document.querySelectorAll('.guide-section').forEach(section => {
        section.classList.remove('active');
    });

    // Afficher la section demandée
    const targetSection = document.getElementById(sectionId);
    if (targetSection) {
        targetSection.classList.add('active');
    }

    // Mettre à jour la navigation
    document.querySelectorAll('.nav-link').forEach(link => {
        link.classList.remove('active');
    });

    // Activer le lien correspondant
    const activeLink = document.querySelector(`[onclick="showSection('${sectionId}')"]`);
    if (activeLink) {
        activeLink.classList.add('active');
    }

    // Faire défiler vers le haut
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

// Imprimer le guide
function printGuide() {
    // Créer une nouvelle fenêtre pour l'impression
    const printWindow = window.open('', '_blank');
    
    // Récupérer le contenu de toutes les sections
    const sections = document.querySelectorAll('.guide-section');
    let printContent = '';
    
    sections.forEach(section => {
        printContent += section.outerHTML;
    });

    // Créer le document d'impression
    const printDocument = `
        <!DOCTYPE html>
        <html lang="fr">
        <head>
            <meta charset="UTF-8">
            <title>Guide Utilisateur - Geek Technologie</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet">
            <link href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css" rel="stylesheet">
            <style>
                @media print {
                    .no-print { display: none !important; }
                    .guide-section { display: block !important; page-break-after: always; }
                    .card { break-inside: avoid; }
                    .workflow-step { break-inside: avoid; }
                    body { font-size: 12px; }
                    h1, h2, h3 { color: #000 !important; }
                    .text-primary, .text-success, .text-warning, .text-info, .text-danger { color: #000 !important; }
                }
                .guide-section { display: block; margin-bottom: 30px; }
                .workflow-step { padding: 15px; text-align: center; }
                .step-number { 
                    width: 30px; height: 30px; border-radius: 50%; 
                    display: inline-flex; align-items: center; justify-content: center; 
                    font-weight: bold; margin-bottom: 10px; 
                }
                .feature-card { margin-bottom: 20px; }
            </style>
        </head>
        <body>
            <div class="container-fluid">
                <div class="text-center mb-4">
                    <h1>Guide Utilisateur</h1>
                    <h2>Geek Technologie</h2>
                    <p class="text-muted">Documentation complète de l'application</p>
                    <hr>
                </div>
                ${printContent}
            </div>
        </body>
        </html>
    `;

    // Écrire le contenu dans la nouvelle fenêtre
    printWindow.document.write(printDocument);
    printWindow.document.close();

    // Attendre le chargement et lancer l'impression
    printWindow.onload = function() {
        printWindow.print();
        printWindow.close();
    };
}

// Recherche dans le guide
function searchGuide(query) {
    if (!query || query.length < 2) {
        // Réinitialiser l'affichage
        document.querySelectorAll('.guide-section').forEach(section => {
            section.style.display = 'none';
        });
        showSection('workflow');
        return;
    }

    query = query.toLowerCase();
    let hasResults = false;

    // Rechercher dans toutes les sections
    document.querySelectorAll('.guide-section').forEach(section => {
        const content = section.textContent.toLowerCase();
        if (content.includes(query)) {
            section.style.display = 'block';
            section.classList.add('active');
            hasResults = true;
            
            // Surligner les résultats
            highlightText(section, query);
        } else {
            section.style.display = 'none';
            section.classList.remove('active');
        }
    });

    if (!hasResults) {
        showNoResults();
    }
}

// Surligner le texte trouvé
function highlightText(element, query) {
    // Supprimer les anciens surlignages
    element.querySelectorAll('.highlight').forEach(highlight => {
        const parent = highlight.parentNode;
        parent.replaceChild(document.createTextNode(highlight.textContent), highlight);
        parent.normalize();
    });

    // Ajouter les nouveaux surlignages
    const walker = document.createTreeWalker(
        element,
        NodeFilter.SHOW_TEXT,
        null,
        false
    );

    const textNodes = [];
    let node;
    while (node = walker.nextNode()) {
        textNodes.push(node);
    }

    textNodes.forEach(textNode => {
        const content = textNode.textContent;
        const lowerContent = content.toLowerCase();
        const index = lowerContent.indexOf(query);
        
        if (index !== -1) {
            const beforeText = content.substring(0, index);
            const matchText = content.substring(index, index + query.length);
            const afterText = content.substring(index + query.length);
            
            const fragment = document.createDocumentFragment();
            
            if (beforeText) {
                fragment.appendChild(document.createTextNode(beforeText));
            }
            
            const highlight = document.createElement('mark');
            highlight.className = 'highlight bg-warning';
            highlight.textContent = matchText;
            fragment.appendChild(highlight);
            
            if (afterText) {
                fragment.appendChild(document.createTextNode(afterText));
            }
            
            textNode.parentNode.replaceChild(fragment, textNode);
        }
    });
}

// Afficher un message "Aucun résultat"
function showNoResults() {
    // Masquer toutes les sections
    document.querySelectorAll('.guide-section').forEach(section => {
        section.style.display = 'none';
        section.classList.remove('active');
    });

    // Créer et afficher le message "Aucun résultat"
    let noResultsSection = document.getElementById('no-results');
    if (!noResultsSection) {
        noResultsSection = document.createElement('div');
        noResultsSection.id = 'no-results';
        noResultsSection.className = 'guide-section text-center py-5';
        noResultsSection.innerHTML = `
            <i class="bi bi-search display-1 text-muted"></i>
            <h3 class="text-muted mt-3">Aucun résultat trouvé</h3>
            <p class="text-muted">Essayez avec d'autres mots-clés</p>
        `;
        document.querySelector('.col-md-9').appendChild(noResultsSection);
    }
    
    noResultsSection.style.display = 'block';
    noResultsSection.classList.add('active');
}

// Navigation rapide vers une section
function navigateToSection(sectionId) {
    showSection(sectionId);
    
    // Mettre à jour l'URL sans recharger la page
    if (history.pushState) {
        history.pushState(null, null, `#${sectionId}`);
    }
}

// Gérer la navigation par URL
window.addEventListener('load', function() {
    const hash = window.location.hash.substring(1);
    if (hash && document.getElementById(hash)) {
        showSection(hash);
    }
});

// Gérer le retour en arrière
window.addEventListener('popstate', function() {
    const hash = window.location.hash.substring(1);
    if (hash && document.getElementById(hash)) {
        showSection(hash);
    } else {
        showSection('workflow');
    }
});

// Raccourcis clavier
document.addEventListener('keydown', function(e) {
    // Ctrl/Cmd + P pour imprimer
    if ((e.ctrlKey || e.metaKey) && e.key === 'p') {
        e.preventDefault();
        printGuide();
    }
    
    // Échap pour revenir au début
    if (e.key === 'Escape') {
        showSection('workflow');
    }
});

// Smooth scroll pour les liens internes
document.addEventListener('click', function(e) {
    if (e.target.matches('a[href^="#"]')) {
        e.preventDefault();
        const targetId = e.target.getAttribute('href').substring(1);
        const targetElement = document.getElementById(targetId);
        
        if (targetElement) {
            targetElement.scrollIntoView({
                behavior: 'smooth',
                block: 'start'
            });
        }
    }
});

// Utilitaires pour l'accessibilité
function announceSection(sectionName) {
    // Créer un élément pour les lecteurs d'écran
    const announcement = document.createElement('div');
    announcement.setAttribute('aria-live', 'polite');
    announcement.setAttribute('aria-atomic', 'true');
    announcement.className = 'sr-only';
    announcement.textContent = `Section ${sectionName} affichée`;
    
    document.body.appendChild(announcement);
    
    // Supprimer l'annonce après un délai
    setTimeout(() => {
        document.body.removeChild(announcement);
    }, 1000);
}

// Initialiser les tooltips Bootstrap si disponible
document.addEventListener('DOMContentLoaded', function() {
    if (typeof bootstrap !== 'undefined' && bootstrap.Tooltip) {
        const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
        tooltipTriggerList.map(function(tooltipTriggerEl) {
            return new bootstrap.Tooltip(tooltipTriggerEl);
        });
    }
});

// Export des fonctions pour utilisation externe
window.GuideUtils = {
    showSection,
    printGuide,
    searchGuide,
    navigateToSection
};
