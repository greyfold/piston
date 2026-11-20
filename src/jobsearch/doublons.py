"""Doublons entre plateformes : une même offre vue sous deux fiches du registre.

L'ingestion rapproche déjà par l'identifiant de la plateforme, puis par
l'empreinte entreprise + titre. Elle laisse passer la même offre quand les
plateformes l'écrivent autrement : « Nexthink » et « Nexthink SA »,
« Logitech Europe S.A. (via ictjobs) », « R&amp;D », « Career Opportunities: … ».
Ce module compare les fiches entre elles, à l'écriture près.

Deux niveaux, calibrés le 26.10 sur les 449 fiches du registre :
- **sûr** : même employeur (forme juridique et intermédiaire retirés, l'un des
  noms contenu dans l'autre), même intitulé aux mentions creuses près, aucun
  numéro contradictoire, pas deux cantons différents, et pas deux candidatures.
  Fusion automatique à chaque passage.
- **à vérifier** : même employeur et intitulés qui se recouvrent largement, sans
  que les textes complets disent le contraire. Listé par `jobsearch doublons`,
  jamais fusionné sans décision explicite. Les paires où les deux fiches sont écartées (C) ne
  sont pas montrées : un doublon écarté ne coûte rien.

La ressemblance des textes ne prouve rien (un employeur met le même
paragraphe de présentation dans toutes ses offres) ; on ne s'en sert que pour
écarter une paire dont les deux textes complets diffèrent.

Fusionner ne supprime rien : la fiche secondaire passe en « doublon » (motif
« Même poste que … », convention de l'ancien registre), ses sources et ses
identifiants rejoignent la fiche principale, et « séparer » défait tout.
"""

import html
import re

from .db import ENVOYEES, PIPELINE, maintenant
from .fingerprint import normaliser

SCHEMA = """
CREATE TABLE IF NOT EXISTS doublons_verdicts (
  a TEXT NOT NULL, b TEXT NOT NULL, verdict TEXT NOT NULL, le TEXT NOT NULL, PRIMARY KEY (a, b)
);
"""

# Rang de la fiche qui reste principale : la plus avancée, puis la plus ancienne.
RANG = {s: i for i, s in enumerate(["offre", "entretien", "candidature", "retenue", "refus",
                                     "A", "B", "nouvelle", "C", "expiree"])}
ENGAGEES = set(PIPELINE) | set(ENVOYEES)
ECARTEES = {"C", "expiree", "doublon"}

_FORMES = re.compile(r"\b(sa|s a|sarl|ag|gmbh|ltd|limited|inc|llc|plc|bv|nv|spa|sas|co|cie|holding|"
                     r"group|groupe|suisse|switzerland|schweiz|svizzera|international|europe)\b")
_VIDES = set("de du des d la le les l et en a au aux of the and for in to with".split())
# Mentions qui n'en font pas un autre poste.
_CREUX = set("details detail poste career careers opportunities opportunity job jobs offre offres emploi "
             "urgent cdi cdd".split())
_MOTIF = re.compile(r"Même poste que ([0-9a-f]{12})")


def _schema(reg):
    reg.cx.executescript(SCHEMA)


def cle_entreprise(nom):
    """« Logitech Europe S.A. (via ictjobs) » → {logitech}."""
    t = re.sub(r"\((?:via|par|by)\b[^)]*\)", " ", html.unescape(nom or ""), flags=re.I)
    return frozenset(_FORMES.sub(" ", normaliser(t)).split())


_JURIDIQUES = set("sa sarl ag gmbh ltd limited inc llc plc bv nv spa sas co cie".split())


def sigle(nom):
    """« World Intellectual Property Organization » → « wipo » ; None sous trois mots.

    Les mots entre parenthèses (un sigle déjà écrit, « via … ») et les formes juridiques
    ne comptent pas ; « de », « la », « of »… non plus : « Organisation Mondiale de la
    Propriété Intellectuelle » → « ompi ».
    """
    t = re.sub(r"\([^)]*\)", " ", html.unescape(nom or ""))
    mots = [m for m in normaliser(t).split() if m not in _VIDES and m not in _JURIDIQUES and not m.isdigit()]
    return "".join(m[0] for m in mots) if len(mots) >= 3 else None


def meme_employeur(na, nb):
    """Vrai si les deux noms désignent le même employeur.

    L'un contenu dans l'autre (« Nexthink » et « Nexthink SA »), ou, quand ils n'ont aucun
    mot en commun, le sigle de l'un écrit dans l'autre : « World Intellectual Property
    Organization » et « Organisation Mondiale de la Propriété Intellectuelle (OMPI / WIPO) »
    (doublon du 05.11, candidature envoyée deux fois). Deux noms qui partagent des mots
    sans s'inclure restent deux employeurs : « Banque Cantonale Vaudoise (BCV) » et
    « Banque Cantonale du Valais ».
    """
    ea, eb = cle_entreprise(na), cle_entreprise(nb)
    if not ea or not eb:
        return False
    if ea <= eb or eb <= ea:
        return True
    if ea & eb:
        return False
    sa, sb = sigle(na), sigle(nb)
    return bool((sa and sa in eb) or (sb and sb in ea))


def mots_titre(titre):
    """(mots, numéros) d'un intitulé ; les taux et marques de genre sont déjà retirés."""
    mots = normaliser(html.unescape(titre or "")).split()
    return (frozenset(m for m in mots if not m.isdigit() and m not in _VIDES and len(m) > 1),
            frozenset(m for m in mots if m.isdigit()))


def _bardeaux(texte, k=5):
    m = normaliser(html.unescape(re.sub(r"<[^>]+>", " ", texte or ""))).split()
    return {" ".join(m[i:i + k]) for i in range(len(m) - k + 1)}


def ressemblance_textes(a, b):
    """Part des passages de 5 mots du texte le plus court qu'on retrouve dans l'autre ;
    None si l'un des deux textes est trop court pour juger."""
    sa, sb = _bardeaux(a.get("texte")), _bardeaux(b.get("texte"))
    if len(sa) < 40 or len(sb) < 40:
        return None
    return len(sa & sb) / min(len(sa), len(sb))


def comparer(a, b):
    """(niveau, raison) pour deux fiches : niveau « sur », « a_verifier » ou None."""
    if not meme_employeur(a.get("entreprise"), b.get("entreprise")):
        return None, ""
    (ta, na), (tb, nb) = mots_titre(a.get("titre")), mots_titre(b.get("titre"))
    if not ta or not tb or (na and nb and na != nb):
        return None, ""                    # « réf. 124903 » contre « réf. 124904 » : deux postes
    if ta == tb:
        egal, raison = True, "même intitulé"
    else:
        petit, grand = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
        egal = petit <= grand and (grand - petit) <= _CREUX
        raison = "même intitulé, mentions en plus (%s)" % ", ".join(sorted(grand - petit)) if egal else ""
    proche = egal or (min(len(ta), len(tb)) >= 2 and (ta <= tb or tb <= ta)) or \
        len(ta & tb) / len(ta | tb) >= 0.6
    if not proche:
        return None, ""
    sim = ressemblance_textes(a, b)
    if sim is not None and sim < 0.3 and not egal:
        # Deux textes complets sans rien en commun : deux postes, quand les intitulés ne sont
        # que proches. À intitulé égal, non : la même offre n'a pas le même texte d'une
        # plateforme à l'autre (résumé chez Indeed, constaté le 30.10 sur JTI, Richemont, Nexthink).
        return None, ""
    ca, cb = a.get("canton"), b.get("canton")
    if egal and not (ca and cb and ca != cb) and not (a.get("statut") in ENGAGEES and b.get("statut") in ENGAGEES):
        return "sur", raison
    detail = []
    if ca and cb and ca != cb:
        detail.append("cantons %s et %s" % (ca, cb))
    if sim is not None:
        detail.append("textes semblables à %d %%" % round(sim * 100))
    return "a_verifier", "intitulés proches" + (" ; " + ", ".join(detail) if detail else "")


def principal_de(f):
    """L'empreinte de la fiche principale d'un doublon (fusion ou ancien registre)."""
    if not f or f.get("statut") != "doublon":
        return None
    p = (f.get("extra") or {}).get("doublon_de")
    if p:
        return p
    m = _MOTIF.search(f.get("motif") or "")
    return m.group(1) if m else None


def _paire(a, b):
    return tuple(sorted((a, b)))


def verdicts(reg):
    _schema(reg)
    return {(r["a"], r["b"]): r["verdict"] for r in reg.cx.execute("SELECT * FROM doublons_verdicts")}


def ordonner(a, b):
    """(principale, secondaire) : la plus avancée, puis la plus ancienne."""
    def cle(f):
        return (RANG.get(f.get("statut"), 9), f.get("cree_le") or f.get("vue_le") or "", f["empreinte"])
    return (a, b) if cle(a) <= cle(b) else (b, a)


def detecter(reg, fiches=None):
    """{"surs": [(principale, secondaire, raison)], "a_verifier": [(a, b, raison)]}."""
    fiches = [f for f in (fiches if fiches is not None else reg.toutes()) if f.get("statut") != "doublon"]
    deja = verdicts(reg)
    index, par_sigle = {}, {}
    for i, f in enumerate(fiches):
        f["_cle"] = cle_entreprise(f.get("entreprise"))
        f["_sigle"] = sigle(f.get("entreprise"))
        for mot in f["_cle"]:
            index.setdefault(mot, []).append(i)
        if f["_sigle"]:
            par_sigle.setdefault(f["_sigle"], []).append(i)
    surs, a_verifier, vues = [], [], set()
    for i, a in enumerate(fiches):
        if not a["_cle"]:
            continue
        # Une paire « l'un contenu dans l'autre » partage forcément le mot le plus rare du plus court.
        rare = min(a["_cle"], key=lambda m: len(index[m]))
        # Et une paire reconnue au sigle : le sigle de l'un est un mot de l'autre.
        candidates = index[rare] + index.get(a["_sigle"], []) + \
            [j for m in a["_cle"] for j in par_sigle.get(m, [])]
        for j in candidates:
            if j == i or (min(i, j), max(i, j)) in vues:
                continue
            vues.add((min(i, j), max(i, j)))
            b = fiches[j]
            if _paire(a["empreinte"], b["empreinte"]) in deja:
                continue
            niveau, raison = comparer(a, b)
            if niveau == "sur":
                surs.append((*ordonner(a, b), raison))
            elif niveau == "a_verifier" and not (a["statut"] in ECARTEES and b["statut"] in ECARTEES):
                a_verifier.append((*ordonner(a, b), raison))
    for f in fiches:
        f.pop("_cle", None)
        f.pop("_sigle", None)
    return {"surs": surs, "a_verifier": a_verifier}


def meme_poste_engage(reg, f):
    """Les autres fiches du même poste déjà retenues ou envoyées : [(fiche, raison)].

    Même poste : même identifiant de plateforme, ou même employeur et même intitulé (le
    niveau « sûr » de `comparer`, quel que soit le statut). Une paire que la personne a dite
    « différentes » n'y figure pas. Sert de garde-fou avant le Go, à l'envoi, à la preuve
    Job-Room et à l'ajout manuel (doublon OMPI du 05.11).
    """
    deja = verdicts(reg)
    ids = set(f.get("ids_source") or [])
    neutre = dict(f, statut="nouvelle")
    out = []
    for g in reg.liste(sorted(ENGAGEES), ordre="postulee_le, vue_le"):
        if g["empreinte"] == f["empreinte"] or deja.get(_paire(f["empreinte"], g["empreinte"])) == "differentes":
            continue
        commun = ids & set(g.get("ids_source") or [])
        if commun:
            out.append((g, "même identifiant (%s)" % ", ".join(sorted(commun))))
            continue
        niveau, raison = comparer(neutre, dict(g, statut="nouvelle"))
        if niveau == "sur":
            out.append((g, raison))
    return out


def decrire(g):
    """« World Intellectual Property Organization — UX Lead (6c8c2078, postulée le 29.10.2026) »."""
    if g.get("postulee_le") and g.get("statut") in ENVOYEES:
        etat = "postulée le %s" % ".".join(reversed(g["postulee_le"][:10].split("-")))
    else:
        etat = "statut « %s »" % g.get("statut")
    if g.get("jobroom_saisie_le"):
        etat += ", preuve Job-Room du %s" % ".".join(reversed(g["jobroom_saisie_le"][:10].split("-")))
    return "%s — %s (%s, %s)" % (g.get("entreprise"), g.get("titre"), g["empreinte"][:8], etat)


def _union(*listes):
    out = []
    for l in listes:
        for x in l or []:
            if x not in out:
                out.append(x)
    return out


def fusionner(reg, principale, secondaire, raison="", auto=False):
    """La secondaire passe en « doublon » ; sources et identifiants rejoignent la principale."""
    p, s = reg.get(principale), reg.get(secondaire)
    if not p or not s or principale == secondaire:
        raise ValueError("fiches introuvables")
    if p["statut"] == "doublon" or s["statut"] == "doublon":
        raise ValueError("l'une des deux fiches est déjà un doublon")
    apport = {"sources": [x for x in s.get("sources") or [] if x not in (p.get("sources") or [])],
              "ids_source": [x for x in s.get("ids_source") or [] if x not in (p.get("ids_source") or [])]}
    champs = {"sources": _union(p.get("sources"), s.get("sources")),
              "ids_source": _union(p.get("ids_source"), s.get("ids_source")),
              "requetes": _union(p.get("requetes"), s.get("requetes")),
              "occurrences": (p.get("occurrences") or 0) + (s.get("occurrences") or 0)}
    for c in ("url", "url_postuler", "lieu", "canton", "publiee_le", "texte", "taux_min", "taux_max"):
        if p.get(c) in (None, "") and s.get(c) not in (None, ""):
            champs[c] = s[c]
            if c == "texte":
                champs["texte_origine"] = s.get("texte_origine")
    if s.get("fin_publication") and (s["fin_publication"] > (p.get("fin_publication") or "")):
        champs["fin_publication"] = s["fin_publication"]
    reg.maj(principale, **champs)
    reg.cx.execute("UPDATE ids_source SET empreinte=? WHERE empreinte=?", (principale, secondaire))
    extra = dict(s.get("extra") or {}, doublon_de=principale, statut_avant_doublon=s["statut"],
                 motif_avant_doublon=s.get("motif"), fusion="automatique" if auto else "manuelle",
                 raison_doublon=raison, apport=apport)
    reg.maj(secondaire, statut="doublon", extra=extra,
            motif="Même poste que %s — %s" % (principale[:12], p.get("titre") or ""))
    comment = "automatique" if auto else "à la main"
    reg.noter(secondaire, "doublon de %s (%s, fusion %s)" % (principale[:12], raison or "même offre", comment))
    reg.noter(principale, "reprend le doublon %s · %s — %s (%s)" % (
        secondaire[:12], s.get("entreprise"), s.get("titre"), ", ".join(s.get("sources") or [])))
    _schema(reg)
    reg.cx.execute("INSERT OR REPLACE INTO doublons_verdicts (a, b, verdict, le) VALUES (?,?,?,?)",
                   (*_paire(principale, secondaire), "meme", maintenant()))
    reg.commit()


def differentes(reg, a, b):
    """La personne dit : deux offres distinctes. La paire n'est plus jamais proposée."""
    _schema(reg)
    reg.cx.execute("INSERT OR REPLACE INTO doublons_verdicts (a, b, verdict, le) VALUES (?,?,?,?)",
                   (*_paire(a, b), "differentes", maintenant()))
    reg.commit()


def separer(reg, secondaire):
    """Défait une fusion : la fiche reprend son statut, ses sources et ses identifiants."""
    s = reg.get(secondaire)
    principale = principal_de(s)
    if not principale:
        raise ValueError("cette fiche n'est pas un doublon")
    extra = dict(s.get("extra") or {})
    apport = extra.pop("apport", {}) or {}
    p = reg.get(principale)
    if p:
        reg.maj(principale,
                sources=[x for x in p.get("sources") or [] if x not in apport.get("sources", [])],
                ids_source=[x for x in p.get("ids_source") or [] if x not in apport.get("ids_source", [])])
    for cle in s.get("ids_source") or []:
        reg.cx.execute("UPDATE ids_source SET empreinte=? WHERE cle=? AND empreinte=?", (secondaire, cle, principale))
    statut = extra.pop("statut_avant_doublon", None) or "nouvelle"
    motif = extra.pop("motif_avant_doublon", None)
    for k in ("doublon_de", "fusion", "raison_doublon"):
        extra.pop(k, None)
    reg.maj(secondaire, statut=statut, motif=motif, extra=extra)
    reg.noter(secondaire, "séparée de %s à la main (statut %s rétabli)" % (principale[:12], statut))
    if p:
        reg.noter(principale, "le doublon %s est séparé" % secondaire[:12])
    differentes(reg, principale, secondaire)
    return statut


def traiter(reg, fiches=None):
    """Étage du passage : fusionne les doublons sûrs, compte ceux à vérifier."""
    res = detecter(reg, fiches)
    fusionnees, principales = [], set()
    for p, s, raison in res["surs"]:
        # Pas de chaîne dans un même passage : une principale ne devient pas doublon,
        # un doublon ne devient pas principale (la suite au passage suivant).
        if s["empreinte"] in principales or s["empreinte"] in fusionnees or p["empreinte"] in fusionnees:
            continue
        fusionner(reg, p["empreinte"], s["empreinte"], raison, auto=True)
        principales.add(p["empreinte"])
        fusionnees.append(s["empreinte"])
    return {"fusionnees": fusionnees, "a_verifier": res["a_verifier"]}


def recentes(reg, n=20):
    """Les dernières fusions, pour pouvoir les défaire."""
    out = [f for f in reg.liste(["doublon"], ordre="maj_le DESC") if (f.get("extra") or {}).get("fusion")]
    return [(f, reg.get(principal_de(f))) for f in out[:n]]
