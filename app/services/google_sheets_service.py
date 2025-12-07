"""
Service d'intégration Google Sheets pour l'importation de produits
"""
import gspread
from google.oauth2.service_account import Credentials
from typing import List, Dict, Optional
import os
import json
import re
from datetime import datetime
from decimal import Decimal
from sqlalchemy.orm import Session
from app.database import Product, ProductVariant, ProductVariantAttribute, Category, StockMovement
from app.schemas import ProductCreate
import requests
import hashlib
from pathlib import Path
import unicodedata


class GoogleSheetsService:
    """Service pour synchroniser les produits depuis Google Sheets"""

    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',  # Accès en lecture ET écriture
        'https://www.googleapis.com/auth/drive.readonly'
    ]

    # Mapping des colonnes Google Sheets vers les champs Product
    # Support des noms avec et sans accents
    COLUMN_MAPPING = {
        'Nom du produit': 'name',
        'Categorie': 'category',
        'Catégorie': 'category',  # Version avec accent
        'Etat': 'condition',
        'État': 'condition',  # Version avec accent
        'Marque': 'brand',
        'Modele': 'model',
        'Modèle': 'model',  # Version avec accent
        "Prix d'achat (FCFA)": 'purchase_price',
        'Prix en gros (FCFA)': 'wholesale_price',
        'Prix unitaire (FCFA)': 'price',
        'Code-barres produit': 'barcode',
        'Quantite en stock': 'quantity',
        'Quantité en stock': 'quantity',  # Version avec accent
        'Description': 'description',
        'Notes': 'notes',
        'Lieu ou Image du produit': 'image_path',
        # Nouvelle colonne pour les IMEI/numéros de série (une par ligne)
        'IMEI': 'imei_serial'
    }

    def __init__(self, credentials_path: Optional[str] = None):
        """
        Initialise le service Google Sheets

        Args:
            credentials_path: Chemin vers le fichier JSON des credentials Google
        """
        self.credentials_path = credentials_path or os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')
        self.client = None

    def authenticate(self) -> bool:
        """
        Authentifie le service avec Google Sheets API

        Returns:
            True si l'authentification réussit, False sinon
        """
        try:
            if not self.credentials_path or not os.path.exists(self.credentials_path):
                raise ValueError(f"Fichier credentials non trouvé: {self.credentials_path}")

            creds = Credentials.from_service_account_file(
                self.credentials_path,
                scopes=self.SCOPES
            )
            self.client = gspread.authorize(creds)
            return True
        except Exception as e:
            print(f"Erreur d'authentification Google Sheets: {str(e)}")
            return False

    def get_sheet_data(self, spreadsheet_id: str, worksheet_name: str = 'Tableau1') -> List[Dict]:
        """
        Récupère les données d'une feuille Google Sheets

        Args:
            spreadsheet_id: ID du Google Spreadsheet
            worksheet_name: Nom de la feuille (par défaut 'Tableau1')

        Returns:
            Liste de dictionnaires représentant les lignes
        """
        if not self.client:
            if not self.authenticate():
                raise Exception("Impossible de s'authentifier avec Google Sheets")

        try:
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)

            # Récupère toutes les données avec les en-têtes
            data = worksheet.get_all_records()
            return data
        except Exception as e:
            raise Exception(f"Erreur lors de la récupération des données: {str(e)}")

    def get_sheet_preview(self, spreadsheet_id: str, worksheet_name: str = 'Tableau1', limit: int = 10) -> Dict[str, any]:
        """
        Récupère un aperçu d'une feuille: en-têtes + premières lignes.

        Returns:
            {
              'headers': [str, ...],
              'rows': [[...], ...],
              'suggested_imei_headers': [str, ...]
            }
        """
        if not self.client:
            if not self.authenticate():
                raise Exception("Impossible de s'authentifier avec Google Sheets")

        try:
            spreadsheet = self.client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)

            all_values = worksheet.get_all_values()
            if not all_values:
                return {'headers': [], 'rows': [], 'suggested_imei_headers': []}

            headers = all_values[0]
            rows = all_values[1:limit+1]

            # Suggestion: toutes les colonnes contenant "imei"
            def _norm(s: str) -> str:
                import unicodedata, re
                s = (s or '').strip().lower()
                s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
                s = re.sub(r"\s+", " ", s)
                return s

            suggested = [h for h in headers if 'imei' in _norm(h)]

            return {
                'headers': headers,
                'rows': rows,
                'suggested_imei_headers': suggested
            }
        except Exception as e:
            raise Exception(f"Erreur lors de la récupération de l'aperçu: {str(e)}")

    def _normalize_value(self, value: any, field_type: str) -> any:
        """
        Normalise une valeur selon le type de champ

        Args:
            value: Valeur à normaliser
            field_type: Type de champ (price, integer, text, etc.)

        Returns:
            Valeur normalisée
        """
        if value is None or value == '':
            return None

        try:
            if field_type == 'price':
                # Convertit en string et nettoie
                value_str = str(value).strip()

                # Si vide après strip, retourne 0
                if not value_str:
                    return Decimal('0.00')

                # Supprime d'abord les suffixes de devise
                value_str = value_str.replace('F CFA', '').replace('FCFA', '').replace('CFA', '')
                # Supprime les espaces et remplace virgules par points
                value_str = value_str.replace(' ', '').replace(',', '.')
                # Nettoie les caractères non numériques sauf le point et le tiret (pour les négatifs)
                value_str = re.sub(r'[^\d.-]', '', value_str)

                # Si vide après nettoyage, retourne 0
                if not value_str or value_str == '-':
                    return Decimal('0.00')

                try:
                    return Decimal(value_str)
                except Exception:
                    print(f"⚠️ Impossible de convertir '{value}' en prix, utilisation de 0.00")
                    return Decimal('0.00')

            elif field_type == 'integer':
                value_str = str(value).replace(' ', '').replace(',', '.')
                # Nettoie les caractères non numériques
                value_str = re.sub(r'[^\d.-]', '', value_str)
                if not value_str or value_str == '-':
                    return 0
                return int(float(value_str)) if value_str else 0

            elif field_type == 'text':
                result = str(value).strip() if value else ''
                return result if result else None
            else:
                return value
        except (ValueError, TypeError) as e:
            print(f"⚠️ Erreur de normalisation pour '{value}' ({field_type}): {str(e)}")
            if field_type == 'price':
                return Decimal('0.00')
            elif field_type == 'integer':
                return 0
            return None

    def _download_and_save_image(self, image_url: str, product_name: str) -> Optional[str]:
        """Télécharge une image depuis une URL et la sauvegarde localement"""
        try:
            # Vérifier si c'est une URL valide
            if not image_url or not image_url.startswith(('http://', 'https://')):
                # Si ce n'est pas une URL, considérer que c'est déjà un chemin local
                return image_url if image_url else None
            
            # Créer le dossier de destination s'il n'existe pas
            upload_dir = Path("static/uploads/products")
            upload_dir.mkdir(parents=True, exist_ok=True)
            
            # Télécharger l'image
            response = requests.get(image_url, timeout=10, stream=True)
            response.raise_for_status()
            
            # Déterminer l'extension du fichier
            content_type = response.headers.get('content-type', '')
            extension = '.jpg'  # Par défaut
            if 'png' in content_type:
                extension = '.png'
            elif 'jpeg' in content_type or 'jpg' in content_type:
                extension = '.jpg'
            elif 'webp' in content_type:
                extension = '.webp'
            elif 'gif' in content_type:
                extension = '.gif'
            
            # Générer un nom de fichier unique basé sur le nom du produit et un hash
            safe_name = "".join(c for c in product_name if c.isalnum() or c in (' ', '-', '_')).strip()
            safe_name = safe_name.replace(' ', '_')[:50]  # Limiter la longueur
            timestamp = int(datetime.now().timestamp())
            url_hash = hashlib.md5(image_url.encode()).hexdigest()[:8]
            filename = f"{safe_name}_{timestamp}_{url_hash}{extension}"
            
            # Chemin complet du fichier
            file_path = upload_dir / filename
            
            # Sauvegarder l'image
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            # Retourner le chemin relatif pour la base de données
            return f"static/uploads/products/{filename}"
            
        except requests.exceptions.RequestException as e:
            print(f"❌ Erreur lors du téléchargement de l'image {image_url}: {e}")
            return None
        except Exception as e:
            print(f"❌ Erreur lors de la sauvegarde de l'image: {e}")
            return None

    def map_sheet_row_to_product(self, row: Dict, imei_columns: Optional[List[str]] = None, custom_mapping: Optional[Dict[str, str]] = None) -> Dict:
        """
        Mappe une ligne Google Sheets vers un dict de produit

        Args:
            row: Dictionnaire représentant une ligne du Google Sheet

        Returns:
            Dictionnaire avec les champs mappés pour Product
        """
        product_data = {}
        image_url_to_download = None

        # Build a normalized view of row headers for tolerant lookup
        def _norm_header(s: str) -> str:
            try:
                s = (s or "").strip().lower()
                # remove accents
                s = ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))
                # collapse whitespace
                s = re.sub(r"\s+", " ", s)
                return s
            except Exception:
                return str(s or '').strip().lower()

        normalized_row = {}
        try:
            for k, v in (row or {}).items():
                normalized_row[_norm_header(k)] = v
        except Exception:
            normalized_row = {}

        # Préparer le mapping (peut être surchargé)
        base_mapping = dict(self.COLUMN_MAPPING)
        if custom_mapping:
            try:
                base_mapping.update(custom_mapping)
            except Exception:
                pass
        # Precompute normalized mapping keys
        normalized_mapping = { _norm_header(k): v for k, v in base_mapping.items() }

        for sheet_col, db_field in normalized_mapping.items():
            # IMEI traité séparément si imei_columns fourni
            if db_field == 'imei_serial' and imei_columns:
                continue

            # tolerant value fetch: prefer normalized match, fallback to exact
            value = normalized_row.get(sheet_col)
            if value is None:
                # try exact header key if present
                original_key = next((orig for orig in base_mapping.keys() if _norm_header(orig) == sheet_col), None)
                if original_key is not None:
                    value = (row or {}).get(original_key)

            # Normalisation selon le type de champ
            if db_field in ['price', 'wholesale_price', 'purchase_price']:
                product_data[db_field] = self._normalize_value(value, 'price')
            elif db_field == 'quantity':
                product_data[db_field] = self._normalize_value(value, 'integer')
            elif db_field == 'image_path':
                image_url_to_download = self._normalize_value(value, 'text')
            elif db_field == 'imei_serial':
                product_data[db_field] = self._normalize_value(value, 'text')
            else:
                product_data[db_field] = self._normalize_value(value, 'text')

        # Si plusieurs colonnes IMEI sont spécifiées, collecter jusqu'à 3 IMEIs non vides
        if imei_columns:
            def _get_val(col_name: str):
                key = _norm_header(col_name)
                v = normalized_row.get(key)
                if v is None:
                    v = (row or {}).get(col_name)
                return v
            imei_values: List[str] = []
            for col in imei_columns:
                try:
                    val = _get_val(col)
                    if val is None:
                        continue
                    s = str(val).strip()
                    if not s:
                        continue
                    # Eviter les doublons et limiter à 3
                    if s not in imei_values:
                        imei_values.append(s)
                        if len(imei_values) >= 3:
                            break
                except Exception:
                    continue
            # Conserver la compatibilité: premier IMEI comme champ simple
            product_data['imei_serial'] = self._normalize_value(imei_values[0] if imei_values else None, 'text')
            product_data['imei_serials'] = imei_values
        
        # Télécharger l'image maintenant qu'on a le nom du produit
        if image_url_to_download:
            product_name = product_data.get('name', 'product')
            downloaded_path = self._download_and_save_image(image_url_to_download, product_name)
            product_data['image_path'] = downloaded_path
        else:
            product_data['image_path'] = None

        # Nettoyer le code-barres (clé d'appariement) pour éviter les échecs liés aux espaces
        if 'barcode' in product_data and isinstance(product_data['barcode'], str):
            product_data['barcode'] = product_data['barcode'].strip()

        # Valeurs par défaut
        if 'quantity' not in product_data or product_data['quantity'] is None:
            product_data['quantity'] = 0
        if 'price' not in product_data or product_data['price'] is None:
            product_data['price'] = Decimal('0.00')
        if 'purchase_price' not in product_data or product_data['purchase_price'] is None:
            product_data['purchase_price'] = Decimal('0.00')
        if 'condition' not in product_data or not product_data['condition']:
            product_data['condition'] = 'neuf'

        # Ajout de la date d'entrée
        product_data['entry_date'] = datetime.now()

        return product_data

    def sync_products(self, db: Session, spreadsheet_id: str, worksheet_name: str = 'Tableau1',
                     update_existing: bool = False, imei_columns: Optional[List[str]] = None,
                     custom_mapping: Optional[Dict[str, str]] = None) -> Dict[str, int]:
        """
        Synchronise les produits depuis Google Sheets vers la base de données

        Args:
            db: Session SQLAlchemy
            spreadsheet_id: ID du Google Spreadsheet
            worksheet_name: Nom de la feuille
            update_existing: Si True, met à jour les produits existants (par code-barres)

        Returns:
            Statistiques de synchronisation (created, updated, errors)
        """
        stats = {
            'total': 0,
            'created': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'error_details': []
        }

        try:
            # Récupère les données du Google Sheet
            rows = self.get_sheet_data(spreadsheet_id, worksheet_name)
            stats['total'] = len(rows)

            # Précharger les catégories (name -> requires_variants)
            try:
                categories = {c.name: c.requires_variants for c in db.query(Category).all()}
            except Exception:
                categories = {}

            for idx, row in enumerate(rows, start=1):
                try:
                    # Mappe la ligne vers un dict de produit
                    product_data = self.map_sheet_row_to_product(row, imei_columns=imei_columns, custom_mapping=custom_mapping)

                    # Ignore les lignes sans nom de produit
                    if not product_data.get('name'):
                        print(f"⚠️ Ligne {idx}: Ignorée (pas de nom de produit)")
                        stats['skipped'] += 1
                        continue

                    # Déterminer si cette ligne doit créer/mettre à jour un produit à variantes
                    category_name = (product_data.get('category') or '').strip()
                    requires_variants = bool(categories.get(category_name))
                    has_imei = bool((product_data.get('imei_serial') or '').strip())
                    has_barcode = bool((product_data.get('barcode') or '').strip())

                    if (requires_variants or has_imei) and has_barcode and has_imei:
                        # Mode variantes par code-barres produit partagé
                        # 1) Trouver ou créer le produit parent via Product.barcode
                        existing_product = db.query(Product).filter(Product.barcode == product_data['barcode']).first()
                        if existing_product:
                            # Si on ne souhaite pas mettre à jour les produits existants,
                            # ignorer simplement cette ligne.
                            if not update_existing:
                                stats['skipped'] += 1
                                continue

                            # Mettre à jour quelques champs de base si demandé
                            if update_existing:
                                for key in ['name','description','price','wholesale_price','purchase_price','category','brand','model','condition','image_path','notes']:
                                    val = product_data.get(key)
                                    if val is not None and val != '':
                                        setattr(existing_product, key, val)
                            # Créer les variantes pour chaque IMEI non existant
                            imeis: List[str] = product_data.get('imei_serials') or ([] if not product_data.get('imei_serial') else [product_data.get('imei_serial')])
                            added = 0
                            if imeis:
                                from app.database import ProductVariant
                                for imei in imeis:
                                    if not imei:
                                        continue
                                    already = db.query(ProductVariant).filter(ProductVariant.imei_serial == imei).first()
                                    if not already:
                                        v = ProductVariant(
                                            product_id=existing_product.product_id,
                                            imei_serial=imei,
                                            barcode=None,
                                            condition=product_data.get('condition') or existing_product.condition
                                        )
                                        db.add(v)
                                        # Incrémente le stock du produit parent
                                        try:
                                            existing_product.quantity = (existing_product.quantity or 0) + 1
                                        except Exception:
                                            pass
                                        # Mouvement de stock IN unitaire
                                        sm = StockMovement(
                                            product_id=existing_product.product_id,
                                            quantity=1,
                                            movement_type='IN',
                                            reference_type='GOOGLE_SHEETS_IMPORT',
                                            notes=f"Import IMEI {imei} depuis Google Sheets",
                                            unit_price=existing_product.purchase_price or Decimal('0.00')
                                        )
                                        db.add(sm)
                                        added += 1
                            db.commit()
                            stats['updated'] += 1 if added > 0 or update_existing else 0
                            stats['skipped'] += 0 if added > 0 or update_existing else 1
                        else:
                            # Créer le produit parent avec le code-barres partagé
                            from app.database import ProductVariant
                            imeis: List[str] = product_data.get('imei_serials') or ([] if not product_data.get('imei_serial') else [product_data.get('imei_serial')])
                            qty_init = max(1, len(imeis)) if imeis else 1
                            parent = Product(
                                name=product_data.get('name'),
                                description=product_data.get('description'),
                                quantity=qty_init,  # commence avec N variantes
                                price=product_data.get('price') or Decimal('0.00'),
                                wholesale_price=product_data.get('wholesale_price'),
                                purchase_price=product_data.get('purchase_price') or Decimal('0.00'),
                                category=product_data.get('category'),
                                brand=product_data.get('brand'),
                                model=product_data.get('model'),
                                barcode=product_data.get('barcode'),
                                condition=product_data.get('condition') or 'neuf',
                                has_unique_serial=True,
                                entry_date=product_data.get('entry_date'),
                                notes=product_data.get('notes'),
                                image_path=product_data.get('image_path')
                            )
                            db.add(parent)
                            db.flush()
                            # Créer les variantes pour chaque IMEI (ou une variante vide si pas d'IMEI)
                            created_any = False
                            if imeis:
                                for imei in imeis:
                                    if not imei:
                                        continue
                                    var = ProductVariant(
                                        product_id=parent.product_id,
                                        imei_serial=imei,
                                        barcode=None,
                                        condition=parent.condition
                                    )
                                    db.add(var)
                                    # Mouvement de stock IN unitaire
                                    sm = StockMovement(
                                        product_id=parent.product_id,
                                        quantity=1,
                                        movement_type='IN',
                                        reference_type='GOOGLE_SHEETS_IMPORT',
                                        notes=f'Import initial variante IMEI {imei} depuis Google Sheets',
                                        unit_price=parent.purchase_price or Decimal('0.00')
                                    )
                                    db.add(sm)
                                    created_any = True
                            else:
                                # Fallback: une variante sans IMEI
                                var = ProductVariant(
                                    product_id=parent.product_id,
                                    imei_serial=product_data.get('imei_serial'),
                                    barcode=None,
                                    condition=parent.condition
                                )
                                db.add(var)
                                sm = StockMovement(
                                    product_id=parent.product_id,
                                    quantity=1,
                                    movement_type='IN',
                                    reference_type='GOOGLE_SHEETS_IMPORT',
                                    notes='Import initial variante depuis Google Sheets',
                                    unit_price=parent.purchase_price or Decimal('0.00')
                                )
                                db.add(sm)
                            db.commit()
                            stats['created'] += 1
                    else:
                        # Mode produit simple (pas de variante/IMEI)
                        existing_product = None
                        if product_data.get('barcode'):
                            existing_product = db.query(Product).filter(
                                Product.barcode == product_data['barcode']
                            ).first()

                        if existing_product:
                            if update_existing:
                                # Met à jour le produit existant
                                for key, value in product_data.items():
                                    if value is not None and key != 'barcode':
                                        setattr(existing_product, key, value)
                                db.commit()
                                stats['updated'] += 1
                            else:
                                stats['skipped'] += 1
                        else:
                            # Crée un nouveau produit
                            new_product = Product(**{k: v for k, v in product_data.items() if k != 'imei_serial'})
                            db.add(new_product)
                            db.commit()
                            db.refresh(new_product)

                            # Crée un mouvement de stock IN si quantité > 0
                            if new_product.quantity > 0:
                                stock_movement = StockMovement(
                                    product_id=new_product.product_id,
                                    quantity=new_product.quantity,
                                    movement_type='IN',
                                    reference_type='GOOGLE_SHEETS_IMPORT',
                                    notes=f'Import initial depuis Google Sheets',
                                    unit_price=new_product.purchase_price or Decimal('0.00')
                                )
                                db.add(stock_movement)
                                db.commit()

                            stats['created'] += 1

                except Exception as e:
                    stats['errors'] += 1
                    error_msg = f"Ligne {idx}: {str(e)}"
                    stats['error_details'].append(error_msg)
                    print(error_msg)
                    db.rollback()
                    continue

            return stats

        except Exception as e:
            error_msg = f"Erreur globale de synchronisation: {str(e)}"
            stats['errors'] += 1
            stats['error_details'].append(error_msg)
            print(error_msg)
            return stats

    def test_connection(self, spreadsheet_id: str) -> Dict[str, any]:
        """
        Test la connexion au Google Sheet

        Args:
            spreadsheet_id: ID du Google Spreadsheet

        Returns:
            Dict avec le statut de connexion et les infos
        """
        try:
            if not self.client:
                if not self.authenticate():
                    return {
                        'success': False,
                        'error': 'Impossible de s\'authentifier avec Google Sheets'
                    }

            spreadsheet = self.client.open_by_key(spreadsheet_id)
            worksheets = [ws.title for ws in spreadsheet.worksheets()]

            return {
                'success': True,
                'spreadsheet_title': spreadsheet.title,
                'worksheets': worksheets
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e)
            }

    def update_product_stock_in_sheet(self, spreadsheet_id: str, worksheet_name: str,
                                     product_barcode: str, new_quantity: int) -> bool:
        """
        Met à jour le stock d'un produit dans Google Sheets par son code-barres

        Args:
            spreadsheet_id: ID du Google Spreadsheet
            worksheet_name: Nom de la feuille
            product_barcode: Code-barres du produit
            new_quantity: Nouvelle quantité en stock

        Returns:
            True si la mise à jour réussit, False sinon
        """
        try:
            if not self.client:
                if not self.authenticate():
                    print("❌ Impossible de s'authentifier avec Google Sheets")
                    return False

            spreadsheet = self.client.open_by_key(spreadsheet_id)
            worksheet = spreadsheet.worksheet(worksheet_name)

            # Récupère toutes les données
            all_data = worksheet.get_all_values()

            if not all_data:
                print("❌ Aucune donnée trouvée dans le Google Sheet")
                return False

            # Trouve l'index des colonnes
            headers = all_data[0]
            barcode_col_idx = None
            quantity_col_idx = None

            # Chercher les colonnes de code-barres et quantité
            for idx, header in enumerate(headers):
                if header == 'Code-barres produit':
                    barcode_col_idx = idx
                elif header in ['Quantite en stock', 'Quantité en stock']:
                    quantity_col_idx = idx

            if barcode_col_idx is None or quantity_col_idx is None:
                print(f"❌ Colonnes requises non trouvées (barcode:{barcode_col_idx}, qty:{quantity_col_idx})")
                return False

            # Chercher la ligne du produit
            for row_idx, row in enumerate(all_data[1:], start=2):  # start=2 car ligne 1 = headers
                if len(row) > barcode_col_idx:
                    row_barcode = str(row[barcode_col_idx]).strip()
                    if row_barcode == str(product_barcode).strip():
                        # Mise à jour de la cellule (colonne + ligne)
                        # Convertir l'index en lettre de colonne (A, B, C, etc.)
                        col_letter = self._column_index_to_letter(quantity_col_idx + 1)
                        cell_address = f"{col_letter}{row_idx}"

                        worksheet.update(cell_address, [[new_quantity]])
                        print(f"✅ Stock mis à jour dans Google Sheets: {product_barcode} → {new_quantity}")
                        return True

            print(f"⚠️ Produit non trouvé dans Google Sheets: {product_barcode}")
            return False

        except Exception as e:
            print(f"❌ Erreur lors de la mise à jour du stock dans Google Sheets: {str(e)}")
            return False

    def _column_index_to_letter(self, col_idx: int) -> str:
        """
        Convertit un index de colonne (1-indexed) en lettre Excel (A, B, C, ..., Z, AA, AB, ...)

        Args:
            col_idx: Index de colonne (1 = A, 2 = B, etc.)

        Returns:
            Lettre de colonne
        """
        result = ""
        while col_idx > 0:
            col_idx -= 1
            result = chr(65 + (col_idx % 26)) + result
            col_idx //= 26
        return result

    def sync_stock_to_sheets(self, db: Session, spreadsheet_id: str,
                            worksheet_name: str) -> Dict[str, int]:
        """
        Synchronise tous les stocks de la base de données vers Google Sheets

        Args:
            db: Session SQLAlchemy
            spreadsheet_id: ID du Google Spreadsheet
            worksheet_name: Nom de la feuille

        Returns:
            Statistiques de synchronisation (updated, errors)
        """
        stats = {
            'total': 0,
            'updated': 0,
            'not_found': 0,
            'errors': 0,
            'error_details': []
        }

        try:
            # Récupère tous les produits avec un code-barres
            products = db.query(Product).filter(Product.barcode.isnot(None)).all()
            stats['total'] = len(products)

            for product in products:
                try:
                    success = self.update_product_stock_in_sheet(
                        spreadsheet_id=spreadsheet_id,
                        worksheet_name=worksheet_name,
                        product_barcode=product.barcode,
                        new_quantity=product.quantity or 0
                    )

                    if success:
                        stats['updated'] += 1
                    else:
                        stats['not_found'] += 1

                except Exception as e:
                    stats['errors'] += 1
                    error_msg = f"Produit {product.name} ({product.barcode}): {str(e)}"
                    stats['error_details'].append(error_msg)
                    print(f"❌ {error_msg}")
                    continue

            return stats

        except Exception as e:
            stats['errors'] += 1
            stats['error_details'].append(f"Erreur globale: {str(e)}")
            return stats
