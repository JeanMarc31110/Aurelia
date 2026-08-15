# Intégrations AURELIA V5

## Gmail
OAuth 2.0. Import des pièces jointes et création de brouillons de relance. Les brouillons sont privilégiés afin de conserver une validation humaine.

## Pennylane
Connecteur REST configurable. L'URL de base et le jeton sont injectés par environnement afin de suivre les versions de l'API publique.

## Sage
Connecteur REST configurable. Sage Accounting utilise OAuth 2.0 ; la gestion complète du refresh token doit être ajoutée selon l'application enregistrée.

## Cegid
Connecteur configurable pour les API Cegid. Les en-têtes de clé API et de souscription sont séparés dans les variables d'environnement.

## EBP
Export CSV local. Le mapping exact doit être aligné sur la version EBP utilisée par le client.

## Plateforme agréée
Adaptateur volontairement abstrait : chaque fournisseur possède son authentification, ses endpoints et ses statuts. Le moteur AURELIA ne dépend donc pas d'un prestataire unique.
