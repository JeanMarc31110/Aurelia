AURELIA — EXÉCUTION WINDOWS PORTABLE
====================================

1. Extraire entièrement l'archive portable dans un dossier local.
2. Lancer app\Aurelia.exe.
3. Le navigateur s'ouvre sur http://127.0.0.1:8000 lorsque le serveur est prêt.
4. Au premier lancement, créer l'administrateur et renseigner l'entreprise.

Aucun Python, PowerShell, pip ou Tesseract séparé n'est requis à l'exécution.
Aucun compte ou mot de passe par défaut n'est créé.

DONNÉES
-------

Données internes : %LOCALAPPDATA%\Aurelia
Documents :        %USERPROFILE%\Documents\Aurelia

La base, les documents, les exports et les sauvegardes restent hors du dossier
applicatif portable. Déplacer ou remplacer ce dossier ne déplace pas les données.

OCR
---

Le produit embarque Tesseract 5.4 et les langues eng, fra, spa et osd. Les
notices sont disponibles dans THIRD_PARTY_NOTICES.txt.

SÉCURITÉ ET DIAGNOSTIC
----------------------

Aurelia écoute uniquement sur 127.0.0.1:8000. Une seule instance peut utiliser
un environnement de données. Les décisions financières restent humaines.

Journal : %LOCALAPPDATA%\Aurelia\Logs\aurelia.log

Si le port 8000 est occupé, Aurelia le signale et ne choisit pas silencieusement
un autre port.

Cette archive Phase 6D n'est pas l'installateur signé destiné à la distribution
finale. L'installateur et la signature sont réservés à la Phase 6E.
