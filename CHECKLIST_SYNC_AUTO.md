# ✅ Checklist de Vérification - Synchronisation Automatique

## 🔍 Vérification de l'installation

### 1. Application en cours d'exécution
```bash
cd /opt/Geek Technologie
docker compose ps
```

**Résultat attendu** :
```
✅ Geek Technologie_app   Up (healthy)
✅ Geek Technologie_db    Up (healthy)
```

### 2. Configuration présente
```bash
docker compose exec app env | grep GOOGLE_SHEETS
```

**Résultat attendu** :
```
✅ GOOGLE_SHEETS_CREDENTIALS_PATH=/opt/Geek Technologie/credentials/...
✅ GOOGLE_SHEETS_SPREADSHEET_ID=1VHMujdZw...
✅ GOOGLE_SHEETS_WORKSHEET_NAME=Les produits
✅ GOOGLE_SHEETS_AUTO_SYNC=true
✅ GOOGLE_SHEETS_SYNC_INTERVAL=10
```

### 3. Fichier credentials présent
```bash
docker compose exec app ls -la /opt/Geek Technologie/credentials/
```

**Résultat attendu** :
```
✅ symbolic-folio-470422-v3-71562b79a03f.json existe
```

### 4. Dépendances installées
```bash
docker compose exec app pip list | grep -i apscheduler
```

**Résultat attendu** :
```
✅ APScheduler   3.10.4
```

### 5. Application accessible
```bash
curl -s http://localhost:8000/api
```

**Résultat attendu** :
```json
✅ {"message":"API Geek Technologie","status":"running",...}
```

## 🎯 Test de fonctionnement

### Test 1 : Accès à l'interface

- [ ] Ouvrir http://localhost:8000
- [ ] Se connecter avec admin/admin123
- [ ] Aller dans Paramètres
- [ ] Cliquer sur "Synchronisation Google Sheets"
- [ ] ✅ La page se charge sans erreur

### Test 2 : Vérification du statut

Sur la page de synchronisation :

- [ ] Section "Statut de Configuration" affiche :
  - [ ] ✅ Credentials Google : Configuré (badge vert)
  - [ ] ✅ Spreadsheet ID : 1VHMujdZw... (badge vert)

- [ ] Section "Synchronisation Automatique" affiche :
  - [ ] Statut : Inactif (badge gris) OU Actif (badge vert)
  - [ ] Boutons visibles : Démarrer, Arrêter, Synchroniser Maintenant, Actualiser

### Test 3 : Démarrage de la synchronisation

- [ ] Cliquer sur le bouton "Démarrer" (vert)
- [ ] Attendre 2-3 secondes
- [ ] ✅ Message de succès apparaît
- [ ] ✅ Statut passe à "Actif" avec badge "EN COURS"
- [ ] ✅ Les détails s'affichent :
  - [ ] Intervalle : 10 minutes
  - [ ] Dernière sync : [horodatage]
  - [ ] Prochaine sync : [horodatage]

### Test 4 : Synchronisation manuelle

**Préparation** :
1. Noter la quantité d'un produit dans l'application (ex: iPhone 14 Pro = 10)
2. Ouvrir Google Sheets
3. Modifier cette quantité (ex: 10 → 15)

**Test** :
- [ ] Retourner dans l'application
- [ ] Cliquer sur "Synchroniser Maintenant"
- [ ] Attendre 2-3 secondes
- [ ] ✅ Message de succès avec statistiques
- [ ] Aller dans "Gestion des Produits"
- [ ] ✅ La quantité est mise à jour (15)

### Test 5 : Vérification des logs

```bash
docker compose logs app | grep -i "sync" | tail -20
```

**Résultat attendu** :
```
✅ 🔄 Début de la synchronisation depuis Google Sheets...
✅ ✅ Produit mis à jour: [nom] - Quantité: [ancien] → [nouveau]
✅ ✅ Synchronisation terminée: X mis à jour, Y créés, Z ignorés, 0 erreurs
```

### Test 6 : Synchronisation automatique (optionnel)

- [ ] Modifier une quantité dans Google Sheets
- [ ] Attendre 10 minutes (ou l'intervalle configuré)
- [ ] Vérifier dans l'application que la quantité a changé
- [ ] ✅ La synchronisation s'est faite automatiquement

## 🔧 Tests de robustesse

### Test 7 : Gestion des erreurs

**Test avec produit sans code-barres** :
- [ ] Ajouter une ligne dans Google Sheets sans code-barres
- [ ] Cliquer sur "Synchroniser Maintenant"
- [ ] ✅ La synchronisation continue (produit ignoré)
- [ ] ✅ Pas d'erreur bloquante

**Test avec code-barres inexistant** :
- [ ] Ajouter une ligne avec un nouveau code-barres
- [ ] Cliquer sur "Synchroniser Maintenant"
- [ ] ✅ Un nouveau produit est créé

### Test 8 : Arrêt et redémarrage

- [ ] Cliquer sur "Arrêter"
- [ ] ✅ Statut passe à "Inactif"
- [ ] ✅ Bouton "Démarrer" redevient actif
- [ ] Cliquer sur "Démarrer"
- [ ] ✅ La synchronisation redémarre

### Test 9 : Redémarrage de l'application

```bash
docker compose restart app
```

- [ ] Attendre 10 secondes
- [ ] Vérifier que l'application redémarre
- [ ] ✅ L'application est accessible
- [ ] Note : La synchronisation automatique doit être redémarrée manuellement

## 📊 Vérification des fonctionnalités

### Fonctionnalité 1 : Mise à jour de quantité
- [ ] ✅ Détection des changements de quantité
- [ ] ✅ Mise à jour dans l'application
- [ ] ✅ Logs corrects

### Fonctionnalité 2 : Mise à jour de prix
- [ ] ✅ Détection des changements de prix
- [ ] ✅ Mise à jour dans l'application

### Fonctionnalité 3 : Création de produit
- [ ] ✅ Nouveau produit dans Google Sheets
- [ ] ✅ Création dans l'application
- [ ] ✅ Tous les champs remplis

### Fonctionnalité 4 : Synchronisation bidirectionnelle
- [ ] ✅ Vente dans l'application → Google Sheets mis à jour
- [ ] ✅ Modification dans Google Sheets → Application mise à jour

## 🎯 Checklist finale

### Configuration
- [ ] ✅ Variables d'environnement configurées
- [ ] ✅ Fichier credentials présent
- [ ] ✅ Permissions Google Sheets (Éditeur)
- [ ] ✅ APScheduler installé

### Fonctionnement
- [ ] ✅ Application démarre sans erreur
- [ ] ✅ Interface de synchronisation accessible
- [ ] ✅ Synchronisation automatique démarre
- [ ] ✅ Synchronisation manuelle fonctionne
- [ ] ✅ Logs détaillés visibles

### Tests
- [ ] ✅ Mise à jour de quantité testée
- [ ] ✅ Création de produit testée
- [ ] ✅ Gestion des erreurs testée
- [ ] ✅ Synchronisation bidirectionnelle testée

### Documentation
- [ ] ✅ SYNC_AUTO_RESUME.md créé
- [ ] ✅ QUICK_START_AUTO_SYNC.md créé
- [ ] ✅ UTILISATION_SYNC_AUTO.md créé
- [ ] ✅ GOOGLE_SHEETS_BIDIRECTIONAL_SYNC.md créé
- [ ] ✅ CHECKLIST_SYNC_AUTO.md créé (ce fichier)

## 🎉 Validation finale

Si tous les tests ci-dessus sont ✅, alors :

**🎊 LA SYNCHRONISATION AUTOMATIQUE EST OPÉRATIONNELLE ! 🎊**

Vous pouvez maintenant :
1. Utiliser l'application normalement
2. Modifier les quantités dans Google Sheets
3. Les changements seront automatiquement synchronisés

## 📞 En cas de problème

Si un test échoue :

1. **Consulter les logs** :
   ```bash
   docker compose logs app | tail -50
   ```

2. **Vérifier la configuration** :
   ```bash
   docker compose exec app env | grep GOOGLE_SHEETS
   ```

3. **Redémarrer l'application** :
   ```bash
   docker compose restart app
   ```

4. **Consulter la documentation** :
   - `UTILISATION_SYNC_AUTO.md` pour l'utilisation
   - `GOOGLE_SHEETS_BIDIRECTIONAL_SYNC.md` pour les détails techniques

5. **Vérifier les permissions Google Sheets** :
   - Le service account doit être "Éditeur"
   - Email du service account dans le fichier credentials JSON

---

**Date** : 15 octobre 2025
**Version** : 1.0.0
**Statut** : ✅ Prêt pour production
