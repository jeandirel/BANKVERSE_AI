"""Fictitious internal procedures of the DEMO bank (French). They exist only to demonstrate the private
knowledge-base scope of the copilot and the agents. They are NOT real bank policies, not legal or regulatory
guidance, and every document is labelled DEMO."""

KB_DOCUMENTS: dict[str, str] = {
    "DEMO_procedure_alertes_fraude_carte.txt": """PROCÉDURE DEMO — TRAITEMENT DES ALERTES DE FRAUDE CARTE
Document fictif de la Banque Démo BANKVERSE. Version 3 — usage de démonstration uniquement.

1. Objet
Cette procédure décrit le traitement des alertes de fraude sur les paiements par carte générées par le moteur de détection. Une alerte est un signal de risque : elle ne prouve pas une fraude.

2. Niveaux de priorité et délais de traitement
Les alertes de niveau critique doivent être prises en charge dans un délai de 15 minutes. Les alertes de niveau élevé doivent être traitées sous 2 heures. Les alertes de niveau moyen doivent être traitées sous 24 heures ouvrées. Le délai court à partir de la création de l'alerte.

3. Premières vérifications
L'analyste consulte l'historique de la carte sur 30 jours, la cohérence géographique (pays du commerçant, distance avec les transactions précédentes), le code MCC du commerçant, le montant par rapport au montant habituel du client, et les erreurs techniques (code PIN erroné, données de carte invalides).

4. Contact client
Pour une alerte critique, l'analyste peut demander le blocage préventif de la carte. Le blocage est une action humaine réalisée dans l'outil de gestion des cartes : ni le moteur de détection ni les agents d'IA ne bloquent de carte de façon autonome. Le client est contacté par un canal authentifié (application ou appel sortant depuis un numéro officiel). On ne demande jamais au client son code PIN ou son code de validation.

5. Qualification
L'analyste qualifie l'alerte : fraude confirmée, faux positif, ou escalade. Une escalade vers le responsable des risques est obligatoire au-delà de 5 000 euros de préjudice potentiel ou si plusieurs cartes du même client sont concernées.

6. Ouverture de dossier
Toute fraude confirmée donne lieu à l'ouverture d'un dossier d'investigation. Le dossier contient la chronologie des transactions, les motifs de l'alerte, les échanges avec le client et la décision motivée.

7. Retour d'expérience
Les faux positifs fréquents sur un même type de commerçant sont signalés à l'équipe data science afin d'ajuster les seuils. Les seuils de décision ne sont jamais modifiés directement par les analystes.
""",
    "DEMO_procedure_revue_alertes_LCB-FT.txt": """PROCÉDURE DEMO — REVUE DES ALERTES LCB-FT (LUTTE CONTRE LE BLANCHIMENT)
Document fictif de la Banque Démo BANKVERSE. Usage de démonstration uniquement ; ne constitue pas une interprétation de la réglementation.

1. Principe
Une alerte LCB-FT signale un comportement inhabituel (structuration de dépôts, flux circulaires, comptes de passage, dispersion des fonds). Une alerte n'est pas une preuve de blanchiment. Seule l'analyse humaine permet de conclure.

2. Typologies surveillées
Les règles de la Banque Démo couvrent notamment : les virements fractionnés juste sous le seuil de vigilance de 10 000 euros, les cycles de virements revenant au compte d'origine, les comptes recevant de nombreux émetteurs puis renvoyant rapidement les fonds (comptes de passage), et la dispersion vers de nombreux bénéficiaires.

3. Analyse
L'analyste examine le réseau de contreparties sur deux niveaux, la cohérence des montants avec l'activité déclarée du client, l'ancienneté de la relation et les justificatifs disponibles. Il documente chaque constat dans le dossier.

4. Principe des quatre yeux
La clôture d'un dossier LCB-FT, qu'il s'agisse d'un classement sans suite ou d'une proposition de déclaration, requiert la validation d'une seconde personne habilitée (responsable conformité). La personne qui a instruit le dossier ne peut pas le valider seule.

5. Déclaration
La décision de déclarer un soupçon appartient exclusivement au responsable conformité désigné. Aucun outil, modèle ou agent d'IA ne prend cette décision et ne transmet de déclaration.

6. Délais internes
Une alerte de niveau élevé est instruite sous 5 jours ouvrés. Une alerte de niveau moyen est instruite sous 15 jours ouvrés.

7. Confidentialité
Les informations relatives à une alerte ne doivent jamais être communiquées au client concerné.
""",
    "DEMO_politique_credit_consommation.txt": """POLITIQUE DEMO — ÉTUDE DES DEMANDES DE CRÉDIT RENOUVELABLE
Document fictif de la Banque Démo BANKVERSE. Usage de démonstration uniquement.

1. Rôle du score
Le score de probabilité de défaut (PD) est une aide à la décision. Aucune demande n'est accordée ou refusée automatiquement : la décision revient à l'analyste crédit, qui la motive.

2. Grades de risque
Les demandes sont classées en grades A à E selon la PD estimée. Les grades A et B relèvent d'une étude standard. Le grade C requiert une vérification des justificatifs de revenus. Les grades D et E requièrent l'avis d'un second analyste avant toute décision favorable.

3. Justificatifs
Pour un plafond supérieur à 3 000 euros, l'analyste demande les deux derniers bulletins de paie et un relevé de compte récent. Les justificatifs sont contrôlés dans le module de revue documentaire.

4. Taux d'effort
Le taux d'effort cible, charges de crédit incluses, ne dépasse pas 35 % des revenus nets. Un dépassement doit être justifié dans la décision.

5. Critères interdits
Les décisions ne doivent jamais se fonder sur le sexe, la situation familiale, l'origine ou l'âge en tant que tel. Le modèle de score n'utilise pas ces attributs ; leur effet est suivi dans le rapport d'équité.

6. Motivation et traçabilité
Chaque décision indique les principaux facteurs du score, les éléments vérifiés et la décision. Le client peut demander une réexamen humain.
""",
    "DEMO_procedure_justificatifs_KYC_KYB.txt": """PROCÉDURE DEMO — JUSTIFICATIFS KYC / KYB ACCEPTÉS
Document fictif de la Banque Démo BANKVERSE. Usage de démonstration uniquement.

1. Principe
Les contrôles automatiques (lecture des champs, clé IBAN, validité du SIREN, rapprochement des soldes, cohérence des totaux) aident l'analyste. Ils ne constituent pas une vérification d'identité réglementaire ni une certification d'authenticité.

2. Justificatif de domicile
Une facture d'énergie ou de télécommunication de moins de 3 mois est acceptée. Au-delà de 3 mois, le justificatif est refusé.

3. Justificatifs de revenus
Les deux derniers bulletins de paie sont demandés. Le salaire net ne peut pas être supérieur au salaire brut : une telle incohérence entraîne un rejet et une demande de nouveau document.

4. Relevés de compte
Le relevé doit couvrir un mois complet. Le solde final doit être égal au solde initial augmenté des crédits et diminué des débits ; tout écart supérieur à un centime est signalé.

5. Entreprises (KYB)
L'extrait d'immatriculation doit dater de moins de 3 mois. Le numéro SIREN doit être valide. La dénomination doit correspondre à celle du contrat.

6. Environnement de démonstration
Aucune vraie pièce d'identité ni aucun vrai document client ne doit être importé dans l'environnement de démonstration. Seuls les spécimens marqués DEMO sont autorisés.

7. Validation
Un document est validé par une personne différente de celle qui l'a importé (principe des quatre yeux). Toute correction de champ est tracée.
""",
    "DEMO_charte_IA_supervision_humaine.txt": """CHARTE DEMO — USAGE DE L'IA ET SUPERVISION HUMAINE
Document fictif de la Banque Démo BANKVERSE. Usage de démonstration uniquement.

1. Rôle des modèles
Les modèles d'IA de la Banque Démo produisent des scores, des alertes, des prévisions et des synthèses. Ils assistent les équipes ; ils ne prennent aucune décision réglementaire ou financière critique.

2. Agents
Les agents d'IA peuvent rechercher des informations, préparer des synthèses et proposer des actions. Toute action ayant un effet (ouverture de dossier, envoi d'un rapport, modification d'un statut) nécessite l'approbation explicite d'une personne habilitée. Les agents n'effectuent jamais de virement.

3. Prévisions
Les prévisions sont présentées avec leurs intervalles d'incertitude. Elles ne sont jamais présentées comme des certitudes.

4. Mise en production des modèles
Un modèle est mis en production après validation par une personne différente de celle qui l'a entraîné. La performance, la dérive et l'équité sont suivies. Un retour à la version précédente est possible à tout moment.

5. Incidents
Tout comportement anormal d'un modèle (dérive, hausse des faux positifs, indisponibilité) est déclaré comme incident dans le module de gouvernance.
""",
}
