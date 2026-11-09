"""Le travailleur : ce qui tourne dans la machine virtuelle.

Le Mac garde la base, le tableau de bord et l'orchestration du passage : une base
SQLite ne se partage pas sans risque entre le Mac et une machine virtuelle (les
verrous ne traversent pas le dossier partagé). Le travailleur exécute ce qui doit
être isolé : les tâches Claude (le moteur) et les sources qui passent par Claude
ou par un navigateur (Indeed, pages lues par l'IA). Il répond en HTTP, sur le
seul port que la VM ouvre vers le Mac, avec un jeton partagé.

- `serveur.py` : l'application du travailleur (`jobsearch travailleur`).
- `client.py` : ce que le Mac utilise pour lui parler (moteur et sources distants).
"""
