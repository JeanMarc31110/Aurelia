# Jeu de données de sauvegarde Aurelia

Ce document définit le périmètre de la sauvegarde complète implémentée en Phase 6C. Le service produit une archive inspectable `.aurelia-backup` au format version 1, indépendante des binaires de l'application.

## Données à sauvegarder

- la base active `aurelia_v5.db`, via l'API de sauvegarde SQLite ;
- les documents métier sous `Documents\Aurelia` : Inbox, Processed, Errors, Archive, Exports et Generated ;
- les téléversements et pièces jointes conservés sous `%LOCALAPPDATA%\Aurelia\Data` ;
- la configuration locale sous `%LOCALAPPDATA%\Aurelia\Config` ;
- les métadonnées de configuration des intégrations nécessaires à une restauration ;
- un manifeste contenant la version produit issue de `VERSION.txt`, la version de schéma SQLite, la date, la liste des fichiers et leurs empreintes SHA-256.

## Format et intégrité

Chaque archive contient `manifest.json`, `database/aurelia_v5.db`, puis les arbres logiques `data/`, `documents/` et `config/`. Le manifeste contient la version Aurelia, la version du schéma, l'horodatage UTC, l'inventaire, les tailles et les empreintes SHA-256. La base est capturée avec l'API de sauvegarde SQLite et doit réussir `PRAGMA integrity_check`.

La construction se fait dans une zone temporaire. Le package complet est relu et vérifié avant sa publication atomique. La rétention des sauvegardes complètes est distincte des copies SQLite automatiques et conserve cinq archives par défaut (`AURELIA_COMPLETE_BACKUP_RETENTION`).

## Secrets

Les secrets sont séparés sous `%LOCALAPPDATA%\Aurelia\Secrets` et exclus de l'archive ordinaire. Les fichiers de configuration dont le nom indique un secret, un jeton ou des identifiants sont également exclus. Le manifeste déclare `excluded_reauthentication_required` : une réauthentification des intégrations peut donc être nécessaire après restauration. Aucun chiffrement réversible artisanal n'est utilisé.

## Éléments exclus par défaut

- binaires et ressources installées sous `%LOCALAPPDATA%\Programs\Aurelia` ;
- caches, fichiers temporaires et verrous d'instance ;
- journaux, sauf sélection explicite pour diagnostic ;
- environnements Python, artefacts de build et installateurs.

## Restauration sûre

La restauration est une opération hors ligne. Elle refuse un processus Aurelia actif, vérifie intégralement l'archive, la base, la compatibilité du schéma et l'espace disque avant mutation. Une sauvegarde de sécurité pré-restauration est créée sous le répertoire d'état. Les données candidates sont préparées à côté de leurs destinations puis échangées par renommage. Toute erreur de validation finale déclenche le rollback des données précédentes.

Un schéma plus récent que l'application est toujours refusé. Un schéma plus ancien n'est accepté que si chaque migration jusqu'à `CURRENT_SCHEMA_VERSION` est disponible ; la migration s'effectue sur la copie candidate avant publication.

## Diagnostic support

Le bundle diagnostic est distinct de la sauvegarde client. Il contient uniquement des métadonnées allowlistées et un extrait de journal expurgé. Il n'inclut ni base, ni facture, ni document, ni secret, ni chemin personnel complet. La redaction réduit les risques mais ne constitue pas un système DLP exhaustif.
