AURELIA V5.0.1 — DISTRIBUTION WINDOWS PROFESSIONNELLE
======================================================

OBJECTIF
--------
Cette distribution remplace l'installation client par fichiers .BAT.

Le client final doit recevoir uniquement :

    AURELIA_Setup_5.0.1.exe

Le Setup installe AURELIA dans Program Files avec ses dépendances, crée les raccourcis et fournit un désinstalleur Windows. Le client n'a pas besoin d'installer Python.

IMPORTANT — SMARTSCREEN / MICROSOFT DEFENDER
---------------------------------------------
Un installateur EXE nouvellement créé mais NON SIGNE peut encore déclencher un avertissement Microsoft SmartScreen.

Pour une distribution commerciale FEWURA :
1. Obtenir un certificat de signature de code pour FEWURA.
2. Construire AURELIA_Setup_5.0.1.exe.
3. Signer l'installateur avec SIGNER_SETUP_FEWURA.bat.
4. Vérifier la signature avant publication.
5. Distribuer toujours les versions signées avec la même identité éditeur.

Ne demandez pas aux clients de désactiver Microsoft Defender, SmartScreen ou Smart App Control.

CONSTRUCTION
------------
Sur un PC Windows de développement :
1. Installer Python 3.11+.
2. Installer Inno Setup 6.
3. Lancer CONSTRUIRE_SETUP_WINDOWS.bat.

Le résultat est créé dans :
    installer\output\AURELIA_Setup_5.0.1.exe

SIGNATURE
---------
Installer le Windows SDK / SignTool, configurer FEWURA_CERT_SHA1 avec l'empreinte du certificat puis lancer SIGNER_SETUP_FEWURA.bat.

CLIENT FINAL
------------
Le client reçoit uniquement le Setup signé, l'installe avec l'assistant Windows et lance AURELIA depuis le Bureau ou le Menu Démarrer.

SECURITE
--------
Cette correction ne désactive ni ne contourne aucune protection Windows. Elle remplace le packaging de développement par une chaîne de distribution standard et prévoit la signature Authenticode appropriée.
