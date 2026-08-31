AURELIA 5.1.0 — INSTALLATION WINDOWS
=====================================

1. Lancer AureliaSetup.exe.
2. Conserver le dossier proposé ou choisir un autre dossier programme.
3. Lancer Aurelia depuis le menu Démarrer ou le raccourci Bureau optionnel.
4. Le navigateur s'ouvre sur http://127.0.0.1:8000 lorsque le serveur est prêt.
5. À la première installation, créer l'administrateur et renseigner l'entreprise.

Aucun Python, PowerShell, pip ou Tesseract séparé n'est requis sur le PC client.
Aucun compte ou mot de passe par défaut n'est créé.

DONNÉES
-------

Données internes : %LOCALAPPDATA%\Aurelia
Documents :        %USERPROFILE%\Documents\Aurelia

La mise à jour et la désinstallation conservent ces deux emplacements. La base,
les factures, les originaux, les erreurs, les exports et les backups ne sont pas
supprimés par le désinstalleur.

OCR
---

Le produit embarque Tesseract 5.4 et les langues eng, fra, spa et osd. Les
notices sont disponibles dans THIRD_PARTY_NOTICES.txt.

SÉCURITÉ
--------

Aurelia écoute uniquement sur 127.0.0.1:8000. Une seule instance peut utiliser
un environnement de données. Les décisions financières sensibles restent
humaines.

DIAGNOSTIC
----------

Journal : %LOCALAPPDATA%\Aurelia\logs\aurelia.log

Si le port 8000 est occupé par un autre logiciel, Aurelia le signale et ne
choisit pas silencieusement un autre port.
