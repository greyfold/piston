"""Numéros de téléphone au format du composant PhoneField de Nexus.

Indicatifs, masques par pays et « 0 » national repris de `PhoneField.jsx` : le numéro est
enregistré en format international lisible (« +41 79 123 45 67 »), celui des CV et des lettres.
"""

import re

INDICATIFS = dict(p.split() for p in """
AD 376,AE 971,AF 93,AG 1,AI 1,AL 355,AM 374,AO 244,AR 54,AS 1,AT 43,AU 61,AW 297,AZ 994,BA 387,BB 1,BD 880,BE 32,
BF 226,BG 359,BH 973,BI 257,BJ 229,BL 590,BM 1,BN 673,BO 591,BR 55,BS 1,BT 975,BW 267,BY 375,BZ 501,CA 1,CD 243,
CF 236,CG 242,CH 41,CI 225,CK 682,CL 56,CM 237,CN 86,CO 57,CR 506,CU 53,CV 238,CW 599,CY 357,CZ 420,DE 49,DJ 253,
DK 45,DM 1,DO 1,DZ 213,EC 593,EE 372,EG 20,ER 291,ES 34,ET 251,FI 358,FJ 679,FK 500,FM 691,FO 298,FR 33,GA 241,
GB 44,GD 1,GE 995,GF 594,GG 44,GH 233,GI 350,GL 299,GM 220,GN 224,GP 590,GQ 240,GR 30,GT 502,GU 1,GW 245,GY 592,
HK 852,HN 504,HR 385,HT 509,HU 36,ID 62,IE 353,IL 972,IM 44,IN 91,IQ 964,IR 98,IS 354,IT 39,JE 44,JM 1,JO 962,
JP 81,KE 254,KG 996,KH 855,KI 686,KM 269,KN 1,KP 850,KR 82,KW 965,KY 1,KZ 7,LA 856,LB 961,LC 1,LI 423,LK 94,
LR 231,LS 266,LT 370,LU 352,LV 371,LY 218,MA 212,MC 377,MD 373,ME 382,MF 590,MG 261,MH 692,MK 389,ML 223,MM 95,
MN 976,MO 853,MP 1,MQ 596,MR 222,MS 1,MT 356,MU 230,MV 960,MW 265,MX 52,MY 60,MZ 258,NA 264,NC 687,NE 227,NG 234,
NI 505,NL 31,NO 47,NP 977,NR 674,NU 683,NZ 64,OM 968,PA 507,PE 51,PF 689,PG 675,PH 63,PK 92,PL 48,PM 508,PR 1,
PS 970,PT 351,PW 680,PY 595,QA 974,RE 262,RO 40,RS 381,RU 7,RW 250,SA 966,SB 677,SC 248,SD 249,SE 46,SG 65,SI 386,
SK 421,SL 232,SM 378,SN 221,SO 252,SR 597,SS 211,ST 239,SV 503,SX 1,SY 963,SZ 268,TC 1,TD 235,TG 228,TH 66,TJ 992,
TL 670,TM 993,TN 216,TO 676,TR 90,TT 1,TV 688,TW 886,TZ 255,UA 380,UG 256,US 1,UY 598,UZ 998,VA 39,VC 1,VE 58,
VG 1,VI 1,VN 84,VU 678,WS 685,XK 383,YE 967,YT 262,ZA 27,ZM 260,ZW 263""".replace("\n", "").split(","))

MASQUES = {
    "CH": "## ### ## ##", "FR": "# ## ## ## ##", "DE": "### ########", "GB": "#### ######",
    "BE": "### ## ## ##", "NL": "# ########", "IT": "### ### ####", "ES": "### ## ## ##",
    "PT": "### ### ###", "AT": "### #######", "LU": "### ### ###", "IE": "## ### ####",
    "DK": "## ## ## ##", "NO": "### ## ###", "PL": "### ### ###", "AU": "### ### ###",
    "JP": "## #### ####", "CN": "### #### ####", "IN": "##### #####", "BR": "## #####-####",
    "MX": "## #### ####",
}
MASQUE_NANP = "(###) ###-####"
ZERO_NATIONAL = {"CH", "FR", "DE", "GB", "GG", "JE", "IM", "BE", "NL", "AT", "IE", "AU", "NZ", "JP", "IN", "ZA"}
# Pays retenu pour un indicatif partagé (+1, +7, +44…) quand rien d'autre ne le dit.
PAR_INDICATIF = {"1": "US", "7": "RU", "44": "GB", "39": "IT", "590": "GP", "262": "RE", "599": "CW"}
MAX_CHIFFRES = 15


def masque(pays):
    return MASQUES.get(pays) or (MASQUE_NANP if INDICATIFS.get(pays) == "1" else None)


def _max_chiffres(pays):
    m = masque(pays)
    return m.count("#") if m else MAX_CHIFFRES - len(INDICATIFS.get(pays, ""))


def appliquer_masque(chiffres, m):
    if not m:
        return chiffres
    out, i = "", 0
    for car in m:
        if i >= len(chiffres):
            break
        if car == "#":
            out += chiffres[i]
            i += 1
        else:
            out += car
    return out


def national(brut, pays):
    """Les chiffres du numéro national : sans +41 / 0041 collé ni le 0 initial des pays qui en ont un."""
    code = INDICATIFS.get(pays, "")
    t = str(brut or "").strip()
    if code and t.startswith("+" + code):
        t = t[len(code) + 1:]
    elif code and t.startswith("00" + code):
        t = t[len(code) + 2:]
    chiffres = re.sub(r"\D", "", t)
    if pays in ZERO_NATIONAL and chiffres.startswith("0"):
        chiffres = chiffres[1:]
    return chiffres[:_max_chiffres(pays)]


def pays_de(numero, defaut="CH"):
    """Le pays d'un numéro international (indicatif le plus long), sinon `defaut`."""
    chiffres = re.sub(r"\D", "", str(numero or ""))
    t = str(numero or "").strip()
    if not (t.startswith("+") or t.startswith("00")):
        return defaut
    if t.startswith("00"):
        chiffres = chiffres[2:]
    for n in (3, 2, 1):
        code = chiffres[:n]
        candidats = sorted(p for p, c in INDICATIFS.items() if c == code)
        if candidats:
            if INDICATIFS.get(defaut) == code:
                return defaut
            return PAR_INDICATIF.get(code) or next((p for p in candidats if p in MASQUES), candidats[0])
    return defaut


def decomposer(numero, defaut="CH"):
    """(pays, chiffres nationaux) d'un numéro enregistré, pour remplir le champ."""
    p = pays_de(numero, defaut)
    return p, national(numero, p)


def normaliser(brut, pays=None, defaut="CH"):
    """« +41 79 123 45 67 » d'après ce qui a été saisi (E.164, national, avec ou sans espaces) ; "" si vide."""
    if not re.sub(r"\D", "", str(brut or "")):
        return ""
    pays = pays if pays in INDICATIFS else pays_de(brut, defaut)
    chiffres = national(brut, pays)
    if not chiffres:
        return ""
    return "+%s %s" % (INDICATIFS[pays], appliquer_masque(chiffres, masque(pays)))
