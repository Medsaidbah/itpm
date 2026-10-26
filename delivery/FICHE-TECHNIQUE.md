# Fiche technique : مشاريعي (gestion des projets du service informatique)

Livraison selon le « Manuel de Livraison des Plateformes Digitales (MTNIMA) ». Application développée pour la Direction du Développement et de l'Interopérabilité (DDI), MTNMA.

## 1. Résumé

| Élément | Valeur |
|---|---|
| Application | مشاريعي : suivi des projets, tâches, équipes, rappels par e-mail (interface en français, arabe et anglais) |
| Technologie | Django 5.2, Python 3.13, serveur Waitress, base PostgreSQL |
| Image | `registry-mtnima.gov.mr/[NOM_INSTITUTION]/[NOM_APP]:[VERSION]` (base `python:3.13-slim-bookworm`), un seul conteneur |
| Port | 8000 (HTTP). **Le HTTPS et le reverse proxy sont gérés par le MTNIMA** (section 4 du manuel) |
| Utilisateur dans le conteneur | non-root (uid 10001) |
| Santé | `GET /healthz/` → `ok` (200) si la base répond, sinon 503. Contrôle déjà inclus dans l'image |
| Logs | sortie standard (stdout/stderr) uniquement |
| Secrets dans l'image | **aucun** : tout passe par des variables d'environnement |

## 2. Variables d'environnement

Liste complète et commentée dans `manifest.yml`. Les obligatoires : `SECRET_KEY`, `ALLOWED_HOSTS`, `HTTPS`, `SITE_URL`, `TRUSTED_PROXY`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`.
Secrets (à fournir par le coffre du MTNIMA) : `SECRET_KEY`, `DB_PASSWORD`, `EMAIL_HOST_PASSWORD` ou `GRAPH_CLIENT_SECRET`.
Un exemple sans valeur réelle est fourni : `docker.env.example`.

## 3. Besoins en ressources (minimum)

| | Minimum | Conseillé |
|---|---|---|
| CPU | 0,5 vCPU | 1 vCPU |
| Mémoire | 512 Mo | 1 Go |
| Disque (volume de fichiers) | 5 Go | selon l'usage (pièces jointes jusqu'à 10 Mo chacune) |
| PostgreSQL | 16 ou plus récent ; 1 Go de disque au départ | |

Ces valeurs sont des estimations prudentes : l'application au repos occupe environ 60 Mo de mémoire, avec des pointes lors des exports Excel et du traitement des photos. À confirmer par le MTNIMA après quelques jours d'utilisation réelle.

## 4. Volumes de stockage (à sauvegarder)

| Volume | Contenu | Sauvegarde |
|---|---|---|
| `/app/media` | pièces jointes et photos de profil des utilisateurs | **oui** : persistance obligatoire |
| Base PostgreSQL | toutes les données de l'application | **oui** : `pg_dump`, avec le volume `/app/media`, ensemble |

L'image elle-même ne contient aucune donnée à sauvegarder.

## 5. Tâches planifiées (même image et mêmes variables)

| Tâche | Commande | Fréquence |
|---|---|---|
| Rappels quotidiens par e-mail | `python manage.py send_reminders` | 1 fois par jour (par ex. 07:00) |
| Renvoi des e-mails échoués | `python manage.py send_outbox` | toutes les 10 minutes |

Elles sont déclarées dans `manifest.yml` (profil `jobs`). Sans elles, l'application fonctionne, mais sans rappels quotidiens et sans nouvel essai automatique des e-mails échoués.

## 6. Premier démarrage

1. Démarrer le conteneur avec une base PostgreSQL vide et accessible : il attend la base (jusqu'à 60 s), applique les migrations, puis sert l'application.
2. Créer le premier administrateur : renseigner `DJANGO_SUPERUSER_USERNAME`, `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD`, démarrer une fois, **puis supprimer `DJANGO_SUPERUSER_PASSWORD`**. Les autres comptes sont créés dans l'application (Administration > Utilisateurs).
3. E-mail : voir `EMAIL-M365-SETUP.md` (Microsoft 365). Test : `python manage.py send_test_email adresse@exemple.mr`.

## 7. Reverse proxy (rappel, géré par le MTNIMA)

Terminer le HTTPS ; transmettre vers le port 8000 ; envoyer les en-têtes `Host`, `X-Forwarded-Proto: https` et `X-Forwarded-For` ; autoriser des corps de requête d'au moins 20 Mo. Détails : `DEPLOY-DOCKER.md`, section 3.

## 8. Mise à jour

Sauvegarder la base, déployer la nouvelle version de l'image avec les mêmes variables et volumes (les migrations s'appliquent au démarrage). Garder l'étiquette précédente pour un retour en arrière.
