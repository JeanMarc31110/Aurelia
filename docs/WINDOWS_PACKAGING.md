# Aurelia 5.1.0 — packaging Windows

## Chaîne retenue

- PyInstaller 6.22.2 en mode `onedir` et sans console pour `Aurelia.exe`.
- Inno Setup 6 pour `AureliaSetup.exe`.
- Tesseract 5.4.0.20240606 UB Mannheim embarqué avec `eng`, `fra`, `spa`, `osd`.
- Version unique lue depuis `VERSION.txt` par l’application, le fichier `.spec`
  et le script Inno Setup.

## Construction

Depuis une invite de commandes développeur :

```bat
CONSTRUIRE_SETUP_WINDOWS.bat
```

Le script crée `.buildvenv`, installe les dépendances verrouillées, exécute les
tests, construit `dist\Aurelia\Aurelia.exe`, puis compile
`installer\output\AureliaSetup.exe` si Inno Setup 6 est installé.

Commandes équivalentes :

```powershell
.\.buildvenv\Scripts\python.exe -m PyInstaller --noconfirm --clean installer\aurelia.spec
& "$env:ProgramFiles\Inno Setup 6\ISCC.exe" installer\Aurelia.iss
```

## Ressources packagées

- `app/templates` et `app/static` ;
- `config/policy.json` et `config/account_mapping.json` ;
- `VERSION.txt`, documentation d’installation et notices ;
- runtime Tesseract et quatre modèles de langue.

Aucun `.env`, token OAuth, base SQLite, upload ou document utilisateur n’entre
dans le build.

## Données et migration

Le programme est installé dans `%LOCALAPPDATA%\Programs\Aurelia`. Les données
internes sont dans `%LOCALAPPDATA%\Aurelia` et les documents dans
`Documents\Aurelia`.

Lorsqu’une ancienne base `data\aurelia_v5.db` est détectée et que la nouvelle
base n’existe pas, Aurelia vérifie et sauvegarde l’ancienne base, crée une copie
via l’API SQLite, compare les données critiques, puis publie atomiquement la
nouvelle base.

L’ancienne base n’est jamais supprimée. Une base LocalAppData existante n’est
jamais écrasée. Les administrateurs d’une base historique doivent renouveler
leur mot de passe une fois après migration.

## Distribution

Le setup produit n’est pas signé automatiquement. Avant diffusion commerciale,
signer le setup avec le certificat FEWURA et conserver l’original Tesseract,
son SHA-256 et l’inventaire complet des licences natives.
