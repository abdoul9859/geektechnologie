#!/usr/bin/env python3
"""
Script CLI pour valider un Google Sheet avant synchronisation
"""
import os
import sys

# Ajouter le répertoire parent au path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.services.google_sheets_validator import validate_google_sheet
from dotenv import load_dotenv

# Charger les variables d'environnement
load_dotenv()


def main():
    """Point d'entrée principal"""
    print("=" * 80)
    print("🔍 VALIDATEUR GOOGLE SHEETS")
    print("=" * 80)
    print()

    # Récupérer la configuration
    spreadsheet_id = os.getenv('GOOGLE_SHEETS_SPREADSHEET_ID')
    worksheet_name = os.getenv('GOOGLE_SHEETS_WORKSHEET_NAME', 'Tableau1')
    credentials_path = os.getenv('GOOGLE_SHEETS_CREDENTIALS_PATH')

    # Vérifier la configuration
    if not credentials_path or not os.path.exists(credentials_path):
        print("❌ ERREUR: Credentials Google Sheets non configurés")
        print(f"   Chemin: {credentials_path}")
        print()
        print("💡 Solution:")
        print("   1. Créez un fichier de credentials Google (Service Account)")
        print("   2. Configurez GOOGLE_SHEETS_CREDENTIALS_PATH dans .env")
        print("   3. Consultez GOOGLE_SHEETS_SETUP.md pour plus d'infos")
        sys.exit(1)

    if not spreadsheet_id:
        print("❌ ERREUR: Spreadsheet ID non configuré")
        print()
        print("💡 Solution:")
        print("   Configurez GOOGLE_SHEETS_SPREADSHEET_ID dans .env")
        sys.exit(1)

    print(f"📋 Configuration:")
    print(f"   Spreadsheet ID: {spreadsheet_id}")
    print(f"   Feuille: {worksheet_name}")
    print(f"   Credentials: {credentials_path}")
    print()

    # Lancer la validation
    print("🔄 Validation en cours...")
    print()

    try:
        result = validate_google_sheet(spreadsheet_id, worksheet_name)

        if not result.get('success'):
            print(f"❌ ERREUR: {result.get('error')}")
            sys.exit(1)

        # Afficher le rapport
        print(result['report'])

        # Code de sortie selon le nombre de problèmes
        total_issues = result['total_issues']
        if total_issues == 0:
            print("✅ Votre Google Sheet est prêt pour la synchronisation!")
            sys.exit(0)
        else:
            print(f"⚠️  {total_issues} problème(s) détecté(s)")
            print()
            print("💡 Corrigez ces problèmes avant de synchroniser pour éviter les erreurs.")
            sys.exit(1)

    except KeyboardInterrupt:
        print()
        print("⏹️  Validation interrompue")
        sys.exit(130)
    except Exception as e:
        print(f"❌ ERREUR INATTENDUE: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
