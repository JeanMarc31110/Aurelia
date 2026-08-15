# Codex — AURELIA V5 vers production

1. Remplacer SQLite par PostgreSQL + Alembic.
2. Ajouter RBAC complet, MFA, rotation des secrets.
3. Sécuriser les sessions et cookies.
4. Valider UBL/CII/Factur-X avec schémas EN16931 et artefacts officiels.
5. Rendre le générateur de facture conforme à la norme choisie ; le XML V5 actuel est un brouillon de travail.
6. Ajouter extraction OCR structurée (zones, lignes, TVA, SIREN, IBAN) avec scores de confiance.
7. Ajouter détection/validation SIREN et TVA intracommunautaire.
8. Ajouter Gmail push/watch ou polling planifié.
9. Ajouter Microsoft Graph.
10. Implémenter les endpoints exacts Pennylane, Sage, Cegid et plateforme agréée à partir des comptes sandbox du client.
11. Ajouter EBP/Sage/Cegid/Pennylane mappings configurables.
12. Ajouter rapprochement bancaire intelligent et confirmation manuelle.
13. Ajouter génération d'avoirs.
14. Ajouter statuts de cycle de vie e-invoicing/e-reporting.
15. Ajouter tests de sécurité, sauvegardes et stockage immuable.
16. Ne jamais activer paiement automatique ou changement de RIB automatique.
