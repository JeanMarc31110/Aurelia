# Packaging Windows Aurelia

## Autorités et prérequis

- `VERSION.txt` est l'unique version produit.
- `requirements.lock.txt` verrouille le produit.
- `requirements-build.txt` verrouille PyInstaller et la chaîne de build Python.
- Windows 10/11 x64, Git, Python correspondant exactement au lockfile.
- Inno Setup 6 est requis pour la Phase 6E.
- Un certificat Authenticode réel est requis pour une livraison signée.

## Phase 6D : artefact portable

Depuis un dépôt propre :

```bat
BUILD_WINDOWS_RELEASE.bat
```

Cette commande exécute la suite complète et produit le bundle PyInstaller
`onedir`, son archive portable, le manifest, l'inventaire tiers et les hashes
sous `release/`. Le runtime inclut templates, statiques, configuration et
Tesseract avec `eng`, `fra`, `spa` et `osd`. Il exclut bases, secrets, logs,
sauvegardes, tests, caches et fichiers Git.

## Phase 6E : installateur

L'installateur consomme uniquement le dossier Phase 6D déjà validé :

```bat
BUILD_WINDOWS_INSTALLER.bat
```

La commande canonique appelle `tools/build_windows_installer.py`. Elle vérifie
le commit et toutes les empreintes de l'artefact Phase 6D, exécute la suite de
tests, puis compile `installer/Aurelia.iss` vers :

```text
release/Aurelia-Setup-X.Y.Z.exe
```

L'installation est par utilisateur dans
`%LOCALAPPDATA%\Programs\Aurelia`, sans élévation administrative. Les données
restent sous LocalAppData/Documents et ne sont pas supprimées à la
désinstallation.

Validation locale isolée :

```bat
.buildvenv\Scripts\python.exe tools\validate_windows_installer.py
```

Elle installe le vrai setup, contrôle le contenu contre la Phase 6D, teste le
produit et l'OCR sans Python/Tesseract système dans `PATH`, réinstalle,
désinstalle et vérifie la conservation des données synthétiques. Les résultats
sont écrits dans `release/installer-build-report.json`,
`release/installer-validation-report.json` et
`release/SHA256SUMS-INSTALLER.txt`.

## Signature

`SIGNER_SETUP_FEWURA.bat` signe dynamiquement le setup versionné avec SHA-256,
horodatage RFC 3161 et le certificat désigné par `FEWURA_CERT_SHA1`. Le script
échoue si SignTool ou le certificat réel est absent. Il ne génère jamais de
certificat factice. Sans signature valide, l'artefact reste réservé à la
validation technique et ne constitue pas une livraison client finale.

## Vérification manuelle d'une empreinte

```powershell
Get-FileHash -Algorithm SHA256 release\Aurelia-Setup-X.Y.Z.exe
```

Les anciennes chaînes `INSTALLER_AURELIA.*` et
`CONSTRUIRE_SETUP_WINDOWS.bat` sont dépréciées. Aucun mécanisme de mise à jour
automatique n'est ajouté en Phase 6E.
