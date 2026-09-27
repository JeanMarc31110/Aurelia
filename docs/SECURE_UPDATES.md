# Mises à jour sécurisées et rollback applicatif

## Architecture

Aurélia lit un manifeste JSON v1 depuis la source configurée par
`AURELIA_UPDATE_MANIFEST_URL`. En production cette URL doit être HTTPS. Le package est
téléchargé sous `%LOCALAPPDATA%\Aurelia\Updates\staging`, dans un fichier temporaire,
puis contrôlé par taille, SHA-256 et Authenticode avant publication atomique dans le
staging.

L'installation ne démarre qu'après une action explicite de l'administrateur dans
**Paramètres → Mise à jour**. Aurélia crée alors :

- une sauvegarde complète Phase 6C des données ;
- un point de récupération de l'application courante ;
- un état persistant `update-state.json`.

Le helper est lancé depuis la copie de récupération de l'application. Il attend la
fermeture du processus principal, exécute l'installateur Inno Setup, relance Aurélia et
vérifie `/health`, la version attendue, l'accès à l'état login/setup et les ressources
critiques. Un code retour installateur à zéro ne suffit jamais à déclarer le succès.

## Manifeste JSON v1

Champs obligatoires :

```json
{
  "manifest_format_version": 1,
  "aurelia_version": "5.3.1",
  "release_timestamp": "2026-09-27T20:00:00Z",
  "package_url": "https://updates.example.com/Aurelia-Setup-5.3.1.exe",
  "file_size": 123456789,
  "sha256": "...64 caractères hexadécimaux...",
  "source_commit": "...40 caractères hexadécimaux...",
  "minimum_app_version": "5.3.0",
  "minimum_schema_version": 1,
  "maximum_schema_version": 1,
  "target_schema_version": 1,
  "rollback_schema_compatible": true,
  "release_summary": "Résumé destiné à l'utilisateur",
  "signature_required": true,
  "publisher": "FEWURA"
}
```

Les versions utilisent strictement `X.Y.Z` et sont comparées numériquement. Une version
identique, inférieure, mal formée ou un format de manifeste futur est rejeté.

## Signature

Le mode par défaut est `production`. Il exige : HTTPS, statut Authenticode `Valid`,
identité du certificat contenant l'éditeur attendu (`FEWURA` par défaut), et, dès qu'ils
sont configurés, un thumbprint présent dans `AURELIA_UPDATE_SIGNER_THUMBPRINTS`.

Après acquisition du certificat commercial, publier ses thumbprints SHA-1 autorisés
par configuration de déploiement, séparés par des virgules. Ne jamais les inventer ni
auto-signer une release client.

Pour les fixtures uniquement :

```text
AURELIA_UPDATE_MODE=test
AURELIA_ALLOW_UNSIGNED_UPDATES=1
```

Les deux valeurs sont nécessaires. L'override est journalisé comme avertissement. Une
mise à jour non signée ainsi acceptée n'est jamais une release client valide.

## Application et données

Le rollback restaure uniquement `%LOCALAPPDATA%\Programs\Aurelia`. Les bases,
documents, secrets et configurations restent dans les chemins runtime Phase 6B et ne
sont jamais remplacés automatiquement.

Si l'échec intervient après une modification de schéma et que le manifeste ne garantit
pas la compatibilité descendante, le helper passe à `RECOVERY_REQUIRED`. Il ne relance
pas aveuglément l'ancienne application contre la nouvelle base. L'opérateur conserve la
sauvegarde pré-update Phase 6C pour une récupération explicite et contrôlée.

Une seule version applicative précédente est conservée. Elle n'est supprimée qu'après
création vérifiée du point de récupération suivant.

## Publication future

1. construire et tester le bundle 6D et l'installateur 6E ;
2. signer l'installateur avec le certificat commercial FEWURA ;
3. vérifier Authenticode et le thumbprint ;
4. calculer taille et SHA-256 du fichier signé ;
5. publier le package sur HTTPS ;
6. publier ensuite le manifeste correspondant ;
7. tester le parcours depuis la version minimale prise en charge.

Le fournisseur HTTP(S) actuel est volontairement simple. Un futur endpoint de release
doit seulement servir le manifeste et le package ; aucune intégration GitHub ou service
externe n'est requise par le cœur de mise à jour.

## Récupération opérateur

Consulter `Logs\aurelia.log` et `Updates\update-state.json`. Les états terminaux sont
`COMPLETED`, `ROLLED_BACK`, `RECOVERY_REQUIRED` et `ROLLBACK_FAILED`. Une restauration
de données reste une opération Phase 6C explicite ; elle ne fait jamais partie du
rollback applicatif automatique.
