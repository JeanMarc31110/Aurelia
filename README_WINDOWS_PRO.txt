AURELIA — CHAÎNE WINDOWS DE PRODUCTION
======================================

VERSION.txt est l'autorité de version. requirements.lock.txt et
requirements-build.txt verrouillent les dépendances produit et de build.

PHASE 6D — PORTABLE PYINSTALLER ONEDIR
--------------------------------------
Depuis un dépôt propre :

    BUILD_WINDOWS_RELEASE.bat

Sorties principales :

    release\Aurelia-X.Y.Z\app\Aurelia.exe
    release\Aurelia-X.Y.Z-portable.zip
    release\Aurelia-X.Y.Z\build-manifest.json

PHASE 6E — INSTALLATEUR INNO SETUP
----------------------------------
Après validation de l'artefact Phase 6D exact :

    BUILD_WINDOWS_INSTALLER.bat

Le script vérifie le dépôt, les empreintes Phase 6D, exécute la suite de tests,
puis compile :

    release\Aurelia-Setup-X.Y.Z.exe

Validation installateur isolée :

    .buildvenv\Scripts\python.exe tools\validate_windows_installer.py

La validation produit installer-validation-report.json et
SHA256SUMS-INSTALLER.txt. La signature s'effectue séparément avec le certificat
Authenticode officiel via SIGNER_SETUP_FEWURA.bat. En l'absence de certificat,
la signature reste explicitement bloquée : aucun certificat factice n'est créé.

ANCIENNES CHAÎNES
-----------------
INSTALLER_AURELIA.bat/.ps1 et CONSTRUIRE_SETUP_WINDOWS.bat sont dépréciés.
Ils ne doivent pas être utilisés pour produire une livraison client.
