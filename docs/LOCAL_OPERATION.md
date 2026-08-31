# Aurelia Phase 4 — exploitation locale

## Architecture locale

Aurelia reste une application FastAPI locale, servie exclusivement sur
`127.0.0.1:8000`. Son cycle de vie crée les répertoires, initialise SQLite,
effectue si nécessaire un backup sûr, puis démarre un watcher et un worker
uniques. À l'arrêt, les deux threads sont arrêtés proprement.

Le watcher utilise un polling simple. Ce choix évite une dépendance système et
reste prévisible sous Windows. Il attend plusieurs observations identiques de la
taille et de la date de modification avant de lire un fichier. Il appelle ensuite
le même service d'import que l'upload web : aucun moteur d'extraction parallèle
n'a été ajouté.

## Répertoires

Par défaut, les données internes sont dans `%LOCALAPPDATA%\Aurelia` :

- `aurelia_v5.db` — base SQLite ;
- `logs\aurelia.log` — journal rotatif ;
- `uploads` — fichiers issus des imports web existants.

Les documents utilisateur sont dans `%USERPROFILE%\Documents\Aurelia` :

- `Inbox` — dépôt surveillé ;
- `Processed` — originaux importés et doublons traçables ;
- `Errors` — fichiers invalides ou non supportés ;
- `Archive` — réservé à l'archivage futur ;
- `Exports` — exports produits par Aurelia ;
- `Backups` — sauvegardes SQLite cohérentes.

Compatibilité : si une installation existante possède déjà
`data\aurelia_v5.db` à côté du programme, Aurelia continue à utiliser ce dossier
tant qu'aucun chemin explicite n'est configuré. Cela évite de masquer la base
Phase 3.1 lors de la mise à jour.

## Configuration

Les chemins et paramètres peuvent être surchargés par variables d'environnement :

| Variable | Rôle |
| --- | --- |
| `AURELIA_DATA_DIR` | données internes |
| `AURELIA_DOCUMENTS_DIR` | racine des documents |
| `AURELIA_INBOX_DIR` | Inbox |
| `AURELIA_PROCESSED_DIR` | fichiers traités |
| `AURELIA_ERRORS_DIR` | fichiers en erreur |
| `AURELIA_ARCHIVE_DIR` | archives |
| `AURELIA_EXPORTS_PATH` | exports |
| `AURELIA_BACKUPS_DIR` | backups |
| `AURELIA_UPLOADS_PATH` | uploads web |
| `AURELIA_DB_PATH` | fichier SQLite |
| `AURELIA_LOGS_DIR` | logs |
| `AURELIA_WATCHER_ENABLED` | active/désactive le watcher (`1`/`0`) |
| `AURELIA_WATCHER_POLL_SECONDS` | intervalle du polling |
| `AURELIA_WATCHER_STABLE_CHECKS` | observations stables requises |
| `AURELIA_WATCHER_STABLE_SECONDS` | intervalle entre observations |
| `AURELIA_BACKUPS_ENABLED` | active/désactive les backups (`1`/`0`) |
| `AURELIA_BACKUP_INTERVAL_HOURS` | périodicité |
| `AURELIA_BACKUP_RETENTION` | nombre de backups conservés |

## Ingestion, erreurs et doublons

Les extensions surveillées sont `.pdf`, `.xml`, `.ubl` et `.cii`. Après
stabilisation, Aurelia calcule un SHA-256 et le réserve dans la table SQLite
`ingested_files`. Un worker unique et une transaction `BEGIN IMMEDIATE`
empêchent deux imports concurrents du même contenu.

Un succès déplace l'original, octet pour octet, vers `Processed`. Un fichier
invalide ou non supporté va dans `Errors`. Aucun fichier existant n'est écrasé :
un suffixe déterministe est ajouté en cas de collision. Un contenu déjà importé
n'est pas réimporté ; sa nouvelle copie est conservée dans `Processed` avec un
nom préfixé `duplicate-` et l'événement est persisté et journalisé.

Les fichiers présents dans `Inbox` au démarrage sont repris automatiquement.
Une erreur n'arrête pas le traitement des fichiers suivants.

## Backups et restauration développeur

Les backups utilisent l'API SQLite `backup`, sont validés avec
`PRAGMA integrity_check`, puis publiés par remplacement atomique. La rétention
simple conserve par défaut les 14 derniers fichiers. Un backup automatique est
créé au démarrage s'il est dû, puis toutes les 24 heures.

Backup immédiat :

```powershell
.\.venv\Scripts\python.exe .\aurelia_backup.py
```

Restauration manuelle réservée au développeur : arrêter Aurelia, sauvegarder le
fichier DB courant, vérifier le backup choisi avec `PRAGMA integrity_check`, puis
copier ce backup à l'emplacement configuré par `AURELIA_DB_PATH`. Ne jamais
restaurer pendant que le serveur fonctionne.

## Lancement développeur

Depuis la racine du projet :

```powershell
.\.venv\Scripts\python.exe .\aurelia_launcher.py
```

Ouvrir ensuite `http://127.0.0.1:8000/login`. Le point d'entrée packaging futur
est `aurelia_launcher.py`; il conserve explicitement l'écoute sur localhost.

## Scénario manuel Phase 4

1. Démarrer Aurelia et repérer le chemin `Documents\Aurelia\Inbox`.
2. Déposer `TEST_PHASE4_FACTURE.pdf`. Vérifier sa disparition de `Inbox`, sa
   présence dans `Processed` et la facture dans l'interface.
3. Redéposer les mêmes octets sous `TEST_PHASE4_FACTURE_COPY.pdf`. Vérifier
   qu'aucune seconde facture n'est créée et qu'une copie `duplicate-*` existe
   dans `Processed`.
4. Déposer un faux PDF invalide. Vérifier qu'Aurelia reste accessible, que le
   fichier apparaît dans `Errors` et que l'erreur figure dans `aurelia.log`.
5. Arrêter Aurelia, déposer un document synthétique dans `Inbox`, puis redémarrer.
   Vérifier qu'il est repris.

Ces essais doivent employer exclusivement des documents de test sans donnée
réelle ou secret.

## Société et premier lancement

La société active reste gérée par le service et l'écran de configuration
existants. Si aucune société n'est active, le tableau de bord affiche désormais
un avertissement et un lien de configuration ; aucune société de démonstration
n'est supposée silencieusement en production.

## Limitations et packaging futur

- Pas encore d'installateur, démarrage automatique Windows ni interface de
  restauration.
- Une installation existante reste volontairement sur son dossier `data` ; la
  migration physique vers LocalAppData devra être assistée lors du packaging.
- Tesseract reste local. Sa recherche utilise la configuration existante et
  `PATH`; l'installateur devra embarquer ou détecter l'exécutable et les langues.
- Templates, fichiers `static`, configuration et ressources OCR devront être
  inclus par le futur packager.
- Aucun cloud, paiement, rapprochement, validation financière ou changement de
  RIB automatique n'a été ajouté.

La prochaine étape possible est la Phase 4.1, consacrée au packaging Windows et
à l'expérience « installer et utiliser » ; elle n'est pas incluse ici.
