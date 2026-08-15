AURELIA V5
===========

AGENT DE GESTION AUTOMATISEE DES FACTURES NUMERIQUES

CONTENU DU PACK
---------------

INSTALLER_AURELIA.bat
    Installe automatiquement AURELIA sous Windows.

LANCER_AURELIA.bat
    Lance le programme après installation.

ARRETER_AURELIA.bat
    Arrête le serveur AURELIA local.

INSTALLER_AURELIA.ps1
    Installateur PowerShell de secours.

README.md
    Documentation technique complète.

.env.example
    Configuration des connexions Gmail, OCR, Sage, Cegid,
    Pennylane et plateforme agréée.

INSTALLATION RAPIDE
-------------------

1. Décompressez entièrement le fichier ZIP.

2. Ouvrez le dossier AURELIA_V5_INSTALL.

3. Double-cliquez sur :

       INSTALLER_AURELIA.bat

4. L'installateur :
   - vérifie la présence de Python ;
   - crée l'environnement virtuel ;
   - installe les bibliothèques nécessaires ;
   - prépare la configuration.

5. Ensuite double-cliquez sur :

       LANCER_AURELIA.bat

6. AURELIA s'ouvrira automatiquement dans votre navigateur :

       http://127.0.0.1:8000

IDENTIFIANTS INITIAUX
---------------------

Utilisateur :
    admin

Mot de passe :
    Aurelia-ChangeMe!

IMPORTANT :
Changez ce mot de passe avant toute utilisation réelle.

FONCTIONS PRINCIPALES
---------------------

- Lecture des factures PDF.
- Lecture Factur-X.
- Lecture UBL.
- Lecture CII.
- OCR automatique de secours.
- Détection des doublons.
- Contrôle HT / TVA / TTC.
- Pré-comptabilisation.
- Affectation comptable avec score de confiance.
- Apprentissage après validation.
- Gestion fournisseurs.
- Gestion clients.
- Factures clients.
- Génération PDF.
- Relances clients.
- Brouillons Gmail.
- Import bancaire.
- Rapprochement bancaire.
- Export comptable.
- Connecteurs Sage, Cegid, Pennylane et EBP.
- Connecteur plateforme agréée.
- Journal d'audit.
- Contrôle fraude et changement de RIB.

GMAIL
-----

Pour connecter Gmail, il faut créer un identifiant OAuth Google.

Placer ensuite le fichier :

    google_client_secret.json

dans :

    config\

Puis compléter le fichier :

    .env

OCR
---

Pour lire les PDF scannés, AURELIA peut utiliser Tesseract OCR.

Si Tesseract est installé mais non détecté automatiquement,
renseignez son chemin dans .env :

    TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe

SECURITE
--------

AURELIA ne réalise PAS automatiquement :

- de paiement bancaire ;
- de changement de RIB ;
- de déclaration fiscale ;
- de validation automatique d'une TVA incertaine.

Ces opérations nécessitent volontairement une validation humaine.

DONNEES
-------

La base locale se trouve dans :

    data\aurelia_v5.db

Les documents importés se trouvent dans :

    data\uploads\

Les documents générés se trouvent dans :

    data\generated\

Les exports comptables se trouvent dans :

    data\exports\

INSTALLATION SUR UN AUTRE PC
----------------------------

Le ZIP peut être copié sur un autre ordinateur Windows.

Décompressez-le puis exécutez simplement :

    INSTALLER_AURELIA.bat

Aucune installation manuelle des bibliothèques Python n'est nécessaire.

VERSION
-------

AURELIA V5
Pack Windows
Août 2026
