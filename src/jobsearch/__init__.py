"""Recherche d'emploi : la plateforme Python embarquée dans l'application Mac.

Tout fichier créé par la plateforme (base, sauvegardes, profil, journaux, dossiers de
candidature) n'est lisible que par le compte de la personne : ils contiennent ses
coordonnées et sa recherche.
"""

import os

os.umask(0o077)
