"""Pays et nationalités pour le profil : la liste des États (noms français), et le pays
de l'ordinateur, lu dans ses réglages de région (rien ne sort de l'ordinateur).

La nationalité se choisit par le nom du pays (« Suisse », « Portugal ») : une seule forme,
sans accord, et celle que la plupart des formulaires d'employeurs proposent.
"""

import os
import subprocess
import sys
import unicodedata
from functools import lru_cache

# Pays où la plateforme cherche (étape Identité, champ Pays) : code ISO → nom.
PAYS_DE_RECHERCHE = {"CH": "Suisse", "FR": "France"}

# Proposés en tête de la liste des nationalités.
FREQUENTES = ["Suisse", "France", "Italie", "Portugal", "Espagne", "Allemagne", "Belgique"]

ETATS = """
Afghanistan
Afrique du Sud
Albanie
Algérie
Allemagne
Andorre
Angola
Antigua-et-Barbuda
Arabie saoudite
Argentine
Arménie
Australie
Autriche
Azerbaïdjan
Bahamas
Bahreïn
Bangladesh
Barbade
Belgique
Belize
Bénin
Bhoutan
Biélorussie
Birmanie
Bolivie
Bosnie-Herzégovine
Botswana
Brésil
Brunei
Bulgarie
Burkina Faso
Burundi
Cambodge
Cameroun
Canada
Cap-Vert
Chili
Chine
Chypre
Colombie
Comores
Congo
Corée du Nord
Corée du Sud
Costa Rica
Côte d'Ivoire
Croatie
Cuba
Danemark
Djibouti
Dominique
Égypte
Émirats arabes unis
Équateur
Érythrée
Espagne
Estonie
Eswatini
États-Unis
Éthiopie
Fidji
Finlande
France
Gabon
Gambie
Géorgie
Ghana
Grèce
Grenade
Guatemala
Guinée
Guinée équatoriale
Guinée-Bissau
Guyana
Haïti
Honduras
Hongrie
Îles Marshall
Îles Salomon
Inde
Indonésie
Irak
Iran
Irlande
Islande
Israël
Italie
Jamaïque
Japon
Jordanie
Kazakhstan
Kenya
Kirghizistan
Kiribati
Kosovo
Koweït
Laos
Lesotho
Lettonie
Liban
Liberia
Libye
Liechtenstein
Lituanie
Luxembourg
Macédoine du Nord
Madagascar
Malaisie
Malawi
Maldives
Mali
Malte
Maroc
Maurice
Mauritanie
Mexique
Micronésie
Moldavie
Monaco
Mongolie
Monténégro
Mozambique
Namibie
Nauru
Népal
Nicaragua
Niger
Nigeria
Norvège
Nouvelle-Zélande
Oman
Ouganda
Ouzbékistan
Pakistan
Palaos
Palestine
Panama
Papouasie-Nouvelle-Guinée
Paraguay
Pays-Bas
Pérou
Philippines
Pologne
Portugal
Qatar
République centrafricaine
République démocratique du Congo
République dominicaine
République tchèque
Roumanie
Royaume-Uni
Russie
Rwanda
Saint-Christophe-et-Niévès
Saint-Marin
Saint-Vincent-et-les-Grenadines
Sainte-Lucie
Salvador
Samoa
Sao Tomé-et-Principe
Sénégal
Serbie
Seychelles
Sierra Leone
Singapour
Slovaquie
Slovénie
Somalie
Soudan
Soudan du Sud
Sri Lanka
Suède
Suisse
Suriname
Syrie
Tadjikistan
Taïwan
Tanzanie
Tchad
Thaïlande
Timor oriental
Togo
Tonga
Trinité-et-Tobago
Tunisie
Turkménistan
Turquie
Tuvalu
Ukraine
Uruguay
Vanuatu
Vatican
Venezuela
Viêt Nam
Yémen
Zambie
Zimbabwe
"""


def _plier(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


TOUS = sorted({l.strip() for l in ETATS.splitlines() if l.strip()}, key=_plier)


def region_systeme(env=None, lire_defaults=None):
    """Code du pays des réglages de région (« CH » pour fr_CH), ou ""."""
    env = os.environ if env is None else env
    valeur = ""
    if sys.platform == "darwin" or lire_defaults:
        try:
            valeur = (lire_defaults or (lambda: subprocess.run(
                ["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=3).stdout))()
        except (OSError, subprocess.SubprocessError):
            valeur = ""
    valeur = (valeur or env.get("LC_ALL") or env.get("LANG") or "").strip()
    partie = valeur.split(".")[0].split("@")[0]
    code = partie.split("_")[-1] if "_" in partie else ""
    return code.upper() if len(code) == 2 and code.isalpha() else ""


@lru_cache
def code_telephone_defaut():
    """Le pays proposé devant un numéro de téléphone : celui de l'ordinateur, sinon la Suisse."""
    from .telephone import INDICATIFS
    code = region_systeme()
    return code if code in INDICATIFS else "CH"


@lru_cache
def pays_detecte():
    """Le pays de recherche de cet ordinateur (« Suisse », « France »), "" s'il n'en est pas un."""
    return PAYS_DE_RECHERCHE.get(region_systeme(), "")


# Cantons suisses : code → nom, tel que les sites d'emploi l'attendent comme lieu.
CANTONS = {
    "AG": "Argovie", "AI": "Appenzell Rhodes-Intérieures", "AR": "Appenzell Rhodes-Extérieures", "BE": "Berne",
    "BL": "Bâle-Campagne", "BS": "Bâle-Ville", "FR": "Fribourg", "GE": "Genève", "GL": "Glaris", "GR": "Grisons",
    "JU": "Jura", "LU": "Lucerne", "NE": "Neuchâtel", "NW": "Nidwald", "OW": "Obwald", "SG": "Saint-Gall",
    "SH": "Schaffhouse", "SO": "Soleure", "SZ": "Schwytz", "TG": "Thurgovie", "TI": "Tessin", "UR": "Uri",
    "VD": "Vaud", "VS": "Valais", "ZG": "Zoug", "ZH": "Zurich",
}
# Proposés en tête du choix de la région (Suisse romande d'abord).
CANTONS_ROMANDS = ["GE", "VD", "VS", "FR", "NE", "JU"]
