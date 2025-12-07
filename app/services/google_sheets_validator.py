"""
Validateur pour Google Sheets - Détecte les problèmes de données
"""
import os
from typing import Dict, List, Optional
from collections import Counter
from app.services.google_sheets_service import GoogleSheetsService


class GoogleSheetsValidator:
    """Validateur pour détecter les problèmes dans les données Google Sheets"""

    def __init__(self):
        self.service = GoogleSheetsService()
        self.issues = {
            'empty_rows': [],
            'missing_names': [],
            'missing_barcodes': [],
            'duplicate_barcodes': {},
            'invalid_prices': [],
            'invalid_quantities': [],
            'warnings': [],
            'errors': []
        }

    def validate_sheet(self, spreadsheet_id: str, worksheet_name: str) -> Dict:
        """
        Valide un Google Sheet et retourne un rapport complet

        Args:
            spreadsheet_id: ID du Google Spreadsheet
            worksheet_name: Nom de la feuille

        Returns:
            Dictionnaire avec le rapport de validation
        """
        try:
            # Authentification
            if not self.service.authenticate():
                return {
                    'success': False,
                    'error': 'Impossible de s\'authentifier avec Google Sheets'
                }

            # Récupère les données
            data = self.service.get_sheet_data(spreadsheet_id, worksheet_name)

            if not data:
                return {
                    'success': False,
                    'error': 'Aucune donnée trouvée dans le Google Sheet'
                }

            # Validation des données
            self._check_empty_rows(data)
            self._check_missing_names(data)
            self._check_missing_barcodes(data)
            self._check_duplicate_barcodes(data)
            self._check_invalid_prices(data)
            self._check_invalid_quantities(data)

            # Génère le rapport
            report = self._generate_report(data)

            return {
                'success': True,
                'report': report,
                'issues': self.issues,
                'total_issues': sum([
                    len(self.issues['empty_rows']),
                    len(self.issues['missing_names']),
                    len(self.issues['missing_barcodes']),
                    len(self.issues['duplicate_barcodes']),
                    len(self.issues['invalid_prices']),
                    len(self.issues['invalid_quantities'])
                ])
            }

        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def _check_empty_rows(self, data: List[Dict]):
        """Détecte les lignes complètement vides"""
        for idx, row in enumerate(data, start=2):  # start=2 car ligne 1 = headers
            # Vérifie si toutes les colonnes importantes sont vides
            def safe_strip(val):
                return str(val).strip() if val is not None else ''

            is_empty = all([
                not safe_strip(row.get('Nom du produit', '')),
                not safe_strip(row.get('Code-barres produit', '')),
                not safe_strip(row.get('Marque', '')),
                not safe_strip(row.get('Modèle', ''))
            ])

            if is_empty:
                self.issues['empty_rows'].append({
                    'row': idx,
                    'message': f'Ligne {idx} est complètement vide'
                })

    def _check_missing_names(self, data: List[Dict]):
        """Détecte les produits sans nom"""
        for idx, row in enumerate(data, start=2):
            nom_val = row.get('Nom du produit', '')
            nom = str(nom_val).strip() if nom_val is not None else ''
            if not nom:
                self.issues['missing_names'].append({
                    'row': idx,
                    'barcode': row.get('Code-barres produit', 'N/A'),
                    'message': f'Ligne {idx}: Nom de produit manquant'
                })

    def _check_missing_barcodes(self, data: List[Dict]):
        """Détecte les produits sans code-barres"""
        for idx, row in enumerate(data, start=2):
            barcode_val = row.get('Code-barres produit', '')
            barcode = str(barcode_val).strip() if barcode_val is not None else ''
            nom = str(row.get('Nom du produit', '')).strip()[:50]

            if nom and not barcode:  # Seulement si le produit a un nom
                self.issues['missing_barcodes'].append({
                    'row': idx,
                    'name': nom,
                    'message': f'Ligne {idx}: "{nom}" n\'a pas de code-barres',
                    'impact': 'Ne peut pas être synchronisé automatiquement'
                })

    def _check_duplicate_barcodes(self, data: List[Dict]):
        """Détecte les code-barres en double, mais accepte les doublons si chaque ligne a un IMEI unique."""
        barcodes: dict[str, list[dict]] = {}

        for idx, row in enumerate(data, start=2):
            barcode_val = row.get('Code-barres produit', '')
            barcode = str(barcode_val).strip() if barcode_val is not None else ''
            nom = str(row.get('Nom du produit', '')).strip()[:50]
            imei_val = row.get('IMEI', '')
            imei = str(imei_val).strip() if imei_val is not None else ''

            if barcode:  # Ignore les lignes sans code-barres
                if barcode not in barcodes:
                    barcodes[barcode] = []
                barcodes[barcode].append({
                    'row': idx,
                    'name': nom,
                    'imei': imei
                })

        # Trouve les doublons
        for barcode, occurrences in barcodes.items():
            if len(occurrences) > 1:
                imeis = [o.get('imei', '') for o in occurrences]
                has_any_empty_imei = any(not (i or '').strip() for i in imeis)
                unique_imeis = len(set([i for i in imeis if (i or '').strip()]))
                total_with_imei = len([i for i in imeis if (i or '').strip()])

                if not has_any_empty_imei and unique_imeis == total_with_imei:
                    # Acceptable groupe: même code-barres, IMEIs tous présents et uniques
                    self.issues['warnings'].append({
                        'barcode': barcode,
                        'message': f'Code-barres {barcode} partagé par {len(occurrences)} lignes (IMEI uniques) — sera groupé en un seul produit avec variantes.'
                    })
                else:
                    # Problème: IMEI manquants ou en double pour le même code-barres
                    self.issues['duplicate_barcodes'][barcode] = {
                        'count': len(occurrences),
                        'occurrences': [{'row': o['row'], 'name': o['name'], 'imei': o.get('imei') or 'N/A'} for o in occurrences],
                        'message': f'Code-barres {barcode} apparaît {len(occurrences)} fois avec IMEI manquants ou en double',
                        'impact': 'Ces lignes doivent avoir des IMEI uniques ou être fusionnées.'
                    }

    def _check_invalid_prices(self, data: List[Dict]):
        """Détecte les prix invalides ou manquants"""
        for idx, row in enumerate(data, start=2):
            nom = str(row.get('Nom du produit', '')).strip()[:50]
            if not nom:  # Ignore les lignes vides
                continue

            # Vérifie le prix unitaire
            prix_val = row.get('Prix unitaire (FCFA)', '')
            prix_str = str(prix_val).strip() if prix_val is not None else ''
            prix_str = prix_str.replace('F CFA', '').replace('FCFA', '').replace(' ', '')

            if not prix_str or prix_str == '0':
                self.issues['invalid_prices'].append({
                    'row': idx,
                    'name': nom,
                    'value': row.get('Prix unitaire (FCFA)', 'N/A'),
                    'message': f'Ligne {idx}: "{nom}" a un prix invalide ou nul'
                })

    def _check_invalid_quantities(self, data: List[Dict]):
        """Détecte les quantités invalides"""
        for idx, row in enumerate(data, start=2):
            nom = str(row.get('Nom du produit', '')).strip()[:50]
            if not nom:  # Ignore les lignes vides
                continue

            qty_val = row.get('Quantité en stock', '')
            qty_str = str(qty_val).strip() if qty_val is not None else ''

            try:
                qty = int(qty_str) if qty_str else None
                if qty is None or qty < 0:
                    self.issues['invalid_quantities'].append({
                        'row': idx,
                        'name': nom,
                        'value': qty_str or 'Vide',
                        'message': f'Ligne {idx}: "{nom}" a une quantité invalide'
                    })
            except ValueError:
                self.issues['invalid_quantities'].append({
                    'row': idx,
                    'name': nom,
                    'value': qty_str,
                    'message': f'Ligne {idx}: "{nom}" a une quantité non numérique'
                })

    def _generate_report(self, data: List[Dict]) -> str:
        """Génère un rapport texte lisible"""
        lines = []
        lines.append("=" * 80)
        lines.append("RAPPORT DE VALIDATION GOOGLE SHEETS")
        lines.append("=" * 80)
        lines.append("")

        # Statistiques générales
        lines.append(f"📊 Total de lignes: {len(data)}")
        total_issues = sum([
            len(self.issues['empty_rows']),
            len(self.issues['missing_names']),
            len(self.issues['missing_barcodes']),
            len(self.issues['duplicate_barcodes']),
            len(self.issues['invalid_prices']),
            len(self.issues['invalid_quantities'])
        ])
        lines.append(f"⚠️  Total de problèmes: {total_issues}")
        lines.append("")

        # Lignes vides
        if self.issues['empty_rows']:
            lines.append("🔴 LIGNES VIDES")
            lines.append("-" * 80)
            for issue in self.issues['empty_rows']:
                lines.append(f"   • {issue['message']}")
            lines.append("")

        # Noms manquants
        if self.issues['missing_names']:
            lines.append("🔴 NOMS DE PRODUITS MANQUANTS")
            lines.append("-" * 80)
            for issue in self.issues['missing_names']:
                lines.append(f"   • {issue['message']}")
            lines.append("")

        # Code-barres manquants
        if self.issues['missing_barcodes']:
            lines.append("⚠️  CODE-BARRES MANQUANTS")
            lines.append("-" * 80)
            for issue in self.issues['missing_barcodes']:
                lines.append(f"   • {issue['message']}")
                lines.append(f"      Impact: {issue['impact']}")
            lines.append("")

        # Code-barres dupliqués
        if self.issues['duplicate_barcodes']:
            lines.append("🔴 CODE-BARRES DUPLIQUÉS")
            lines.append("-" * 80)
            for barcode, info in self.issues['duplicate_barcodes'].items():
                lines.append(f"   • {info['message']}")
                for occ in info['occurrences']:
                    lines.append(f"      - Ligne {occ['row']}: {occ['name']}")
                lines.append(f"      Impact: {info['impact']}")
            lines.append("")

        # Prix invalides
        if self.issues['invalid_prices']:
            lines.append("⚠️  PRIX INVALIDES")
            lines.append("-" * 80)
            for issue in self.issues['invalid_prices']:
                lines.append(f"   • {issue['message']} (valeur: {issue['value']})")
            lines.append("")

        # Quantités invalides
        if self.issues['invalid_quantities']:
            lines.append("⚠️  QUANTITÉS INVALIDES")
            lines.append("-" * 80)
            for issue in self.issues['invalid_quantities']:
                lines.append(f"   • {issue['message']} (valeur: {issue['value']})")
            lines.append("")

        # Recommandations
        if total_issues > 0:
            lines.append("💡 RECOMMANDATIONS")
            lines.append("-" * 80)

            if self.issues['empty_rows']:
                lines.append("   ✓ Supprimez les lignes vides du Google Sheet")

            if self.issues['missing_names']:
                lines.append("   ✓ Ajoutez des noms de produits ou supprimez ces lignes")

            if self.issues['missing_barcodes']:
                lines.append("   ✓ Ajoutez des code-barres uniques pour chaque produit")
                lines.append("     (Les produits sans code-barres ne seront pas synchronisés)")

            if self.issues['duplicate_barcodes']:
                lines.append("   ✓ Corrigez les code-barres dupliqués:")
                lines.append("      - Fusionnez les lignes identiques (additionnez les stocks)")
                lines.append("      - OU créez des code-barres uniques pour chaque variante")
                lines.append("        Exemple: 850037489404-NOIR, 850037489404-GRIS")

            if self.issues['invalid_prices']:
                lines.append("   ✓ Corrigez les prix manquants ou invalides")

            if self.issues['invalid_quantities']:
                lines.append("   ✓ Corrigez les quantités (doivent être des nombres >= 0)")

            lines.append("")
        else:
            lines.append("✅ AUCUN PROBLÈME DÉTECTÉ")
            lines.append("-" * 80)
            lines.append("   Votre Google Sheet est prêt pour la synchronisation!")
            lines.append("")

        lines.append("=" * 80)

        return "\n".join(lines)


def validate_google_sheet(spreadsheet_id: str, worksheet_name: str) -> Dict:
    """
    Fonction helper pour valider un Google Sheet

    Args:
        spreadsheet_id: ID du Google Spreadsheet
        worksheet_name: Nom de la feuille

    Returns:
        Rapport de validation
    """
    validator = GoogleSheetsValidator()
    return validator.validate_sheet(spreadsheet_id, worksheet_name)
