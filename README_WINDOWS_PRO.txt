AURELIA — BUILD WINDOWS PORTABLE PHASE 6D
==========================================

AUTORITÉ
--------
VERSION.txt est l'unique source de version produit. requirements.lock.txt et
requirements-build.txt verrouillent respectivement les dépendances applicatives
et la chaîne PyInstaller. Le build exige la version Python exacte déclarée en
tête de requirements.lock.txt.

PRÉREQUIS
---------
- Windows 10/11 x64
- Python 3.14.6 accessible via le lanceur `py -3.14`
- Git
- accès à l'index Python uniquement si .buildvenv doit être créé/complété

COMMANDE DE PRODUCTION
----------------------
Depuis un dépôt Git propre :

    BUILD_WINDOWS_RELEASE.bat

Le script installe les versions verrouillées dans .buildvenv, vérifie le dépôt,
la version Python et les dépendances, exécute tous les tests, puis produit deux
builds PyInstaller onedir propres. Tout échec arrête la chaîne.

SORTIES
-------
Pour une VERSION.txt contenant X.Y.Z :

    release\Aurelia-X.Y.Z\app\Aurelia.exe
    release\Aurelia-X.Y.Z\build-manifest.json
    release\Aurelia-X.Y.Z\THIRD_PARTY_COMPONENTS.json
    release\Aurelia-X.Y.Z\SHA256SUMS.txt
    release\Aurelia-X.Y.Z-portable.zip

Le manifest contient la provenance Git, les versions de Python/PyInstaller, les
empreintes du lockfile et du spec, la version du schéma, les métriques du bundle,
les résultats des smokes et le classement de reproductibilité.

VÉRIFICATION DES EMPREINTES
---------------------------
Comparer chaque SHA-256 de SHA256SUMS.txt avec :

    Get-FileHash -Algorithm SHA256 <chemin>

CONTENU
-------
Le dossier portable inclut templates, fichiers statiques, configuration,
notices et Tesseract avec eng/fra/spa/osd. Il exclut tests, bases, secrets,
journaux, sauvegardes, caches, documentation de développement et dépôts Git.
Les données mutables sont écrites sous LocalAppData/Documents, jamais dans le
dossier applicatif.

ANCIENNES CHAÎNES
-----------------
- BUILD_WINDOWS_RELEASE.bat : CANONICAL pour Phase 6D.
- installer\aurelia.spec : CANONICAL, invoqué par le script Python.
- CONSTRUIRE_SETUP_WINDOWS.bat : DEPRECATED, arrêt explicite.
- installer\Aurelia.iss et scripts de signature : LEGACY/RESERVED pour Phase 6E.

LIMITES DE PHASE
----------------
Phase 6D ne produit ni installateur Inno Setup, ni signature Authenticode, ni
publication, ni mécanisme de mise à jour/rollback. Ces travaux relèvent de 6E.
