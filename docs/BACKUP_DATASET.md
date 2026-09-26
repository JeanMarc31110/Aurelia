# Jeu de données de sauvegarde Aurelia

Ce document définit le périmètre fonctionnel d'une future sauvegarde complète. La Phase 6B ne remplace pas le mécanisme actuel de sauvegarde SQLite et n'implémente pas encore l'archive complète.

## Données à sauvegarder

- la base active `aurelia_v5.db`, via l'API de sauvegarde SQLite ;
- les documents métier sous `Documents\Aurelia` : Inbox, Processed, Errors, Archive, Exports et Generated ;
- les téléversements et pièces jointes conservés sous `%LOCALAPPDATA%\Aurelia\Data` ;
- la configuration locale sous `%LOCALAPPDATA%\Aurelia\Config` ;
- les métadonnées de configuration des intégrations nécessaires à une restauration ;
- un manifeste contenant la version produit issue de `VERSION.txt`, la version de schéma SQLite, la date, la liste des fichiers et leurs empreintes SHA-256.

## Secrets

Les secrets sont séparés sous `%LOCALAPPDATA%\Aurelia\Secrets`. Une sauvegarde future doit les traiter comme un conteneur sensible : chiffrement authentifié, accès explicitement autorisé et aucune valeur secrète dans les journaux ou le manifeste en clair. Ils ne doivent pas être copiés dans une archive non chiffrée.

## Éléments exclus par défaut

- binaires et ressources installées sous `%LOCALAPPDATA%\Programs\Aurelia` ;
- caches, fichiers temporaires et verrous d'instance ;
- journaux, sauf sélection explicite pour diagnostic ;
- environnements Python, artefacts de build et installateurs.

## Restauration future

La restauration devra vérifier le manifeste et toutes les empreintes avant publication, refuser un schéma plus récent que l'application, ne jamais écraser silencieusement une installation existante et conserver un point de retour. Cette politique sera implémentée dans une phase ultérieure dédiée.
