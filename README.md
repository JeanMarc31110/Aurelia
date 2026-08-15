# AURELIA V5 — Agent autonome de facturation numérique

AURELIA V5 ajoute une couche d'automatisation opérationnelle à la V4.

## Nouveautés V5

### Entrées
- dépôt manuel PDF / XML / UBL / CII ;
- lecture Factur-X si XML embarqué ;
- OCR automatique de secours pour PDF image ;
- récupération Gmail OAuth2 des pièces jointes ;
- import bancaire CSV.

### Gestion
- création/mise à jour automatique des fiches fournisseurs et clients ;
- contrôle doublons ;
- contrôle HT / TVA / TTC ;
- détection changements de RIB ;
- score de risque ;
- pré-comptabilisation ;
- apprentissage après validation humaine ;
- échéancier ;
- rapprochement bancaire ;
- journal d'audit.

### Facturation client
- création de brouillons de factures de vente ;
- génération PDF ;
- génération XML UBL de travail ;
- séquence de numérotation ;
- suivi échéances et encaissements.

### Relances
- génération de relances clients ;
- création de brouillons Gmail par défaut ;
- aucun envoi automatique par défaut.

### Comptabilité
- export CSV d'écritures ;
- connecteurs configurables :
  - Pennylane ;
  - Sage ;
  - Cegid ;
  - EBP via export ;
  - plateforme agréée via adaptateur.

## Limites volontaires

- aucun paiement bancaire automatique ;
- aucune modification automatique de RIB ;
- pas de dépôt fiscal automatique ;
- les XML générés doivent être validés contre les schémas/normes applicables avant usage de production ;
- les connecteurs externes exigent les identifiants/API du compte de l'entreprise.

## Installation Windows

Double-cliquer sur `DEMARRER_AURELIA_V5.bat`.

Compte de démonstration :
- utilisateur : `admin`
- mot de passe : `Aurelia-ChangeMe!`

Changez ce mot de passe avant usage réel.

## Gmail OAuth

1. Créer/configurer un projet Google Cloud.
2. Activer Gmail API.
3. Créer un client OAuth Desktop.
4. Placer le fichier OAuth dans `config/google_client_secret.json`.
5. Dans `.env`, vérifier `GMAIL_CREDENTIALS_FILE`.
6. Cliquer sur **Connecter Gmail** dans AURELIA.

Les relances Gmail sont créées comme brouillons par défaut.

## OCR

AURELIA tente d'abord :
1. XML structuré ;
2. couche texte PDF ;
3. OCR si nécessaire.

Pour OCR local, installer Tesseract sur Windows et renseigner `TESSERACT_CMD` si nécessaire.

## API

- `/api/invoices/process`
- `/api/invoices/upload`
- `/api/gmail/import`
- `/api/bank/matches`
- `/api/integrations/status`
- `/api/customers/{id}/reminder-draft`

## Production

Avant déploiement réel :
- PostgreSQL ;
- migrations Alembic ;
- MFA/SSO ;
- sauvegardes ;
- stockage immuable/chiffré ;
- validation EN16931 / schémas officiels ;
- validation métier par le cabinet comptable/DAF ;
- secrets dans un coffre ;
- connecteurs testés en sandbox.
