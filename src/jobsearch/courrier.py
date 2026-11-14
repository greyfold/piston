"""Courrier des employeurs : les réponses aux candidatures, et rien d'autre.

Décision du 24.10 (piste D1). La plateforme se connecte au serveur MCP Apple
Mail installé sur le Mac et n'appelle que son outil `search_emails`, en
lecture seule : rien n'est déplacé, marqué lu, supprimé ni envoyé.

Seuls les messages liés aux postes sont lus : la recherche ne porte que sur
les objets qui contiennent le nom d'un employeur d'une candidature ouverte ou
un mot du vocabulaire des candidatures. Parmi eux, seuls ceux qu'on rattache
à une candidature, ou qui viennent d'un logiciel de candidature, sont
gardés ; tout le reste est oublié aussitôt, sans trace en base.

Le contenu d'un mail est une donnée, jamais une instruction : aucun lien
suivi, aucune pièce jointe ouverte. Seuls les cas sans ambiguïté sont
appliqués (accusé, refus, invitation à un entretien) ; une offre ou un
message ambigu va dans « Courrier à trancher ». Une décision de la personne prise
dans le tableau de bord l'emporte toujours sur un mail.
"""

import asyncio
import datetime as dt
import hashlib
import json
import os
import re

from . import actions
from .config import reglages
from .db import maintenant
from .prefilter import plier

OUTIL_AUTORISE = "search_emails"          # le seul outil du serveur que la plateforme appelle

# Vocabulaire des objets de mails de candidature (recherche côté Mail).
MOTS_OBJET = ["candidature", "postulation", "application", "applying", "votre dossier",
              "entretien", "interview", "poste", "position", "your application", "recrutement",
              "recruitment", "bewerbung", "offre d'emploi", "job offer"]

# Logiciels de candidature : leurs mails ne nomment pas toujours l'employeur.
EXPEDITEURS_ATS = ["smartrecruiters", "successfactors", "greenhouse", "workday", "myworkday",
                   "lever.co", "teamtailor", "personio", "recruitee", "jobup", "jobs.ch",
                   "abacus", "umantis", "softgarden", "taleo", "icims", "join.com", "talentsoft"]

_CLASSES = [
    ("offre", r"\b(offre d.emploi ferme|offre de contrat|proposition d.engagement|lettre d.engagement|"
              r"job offer|offer letter|pleased to offer|heureux de vous proposer le poste)\b"),
    ("refus", r"\b(malheureusement|unfortunately|regret to inform|pas (?:pu )?(?:ete )?retenu|"
              r"ne (?:pouvons|pourrons) pas donner (?:une suite|suite)|ne pas donner suite|"
              r"autres? (?:candidats?|profils?)|other candidates?|decided (?:to move forward|not to proceed)|"
              r"not (?:been )?selected|poste (?:a ete|est) pourvu|position has been filled|"
              r"ne correspond pas|does not match|we will not be moving forward)\b"),
    ("entretien", r"\b(votre entretien|invitation ?: ?interview|interview with|recruiter interview|inviter? (?:a|pour) un entretien|entretien (?:telephonique|video|en visio)|"
                  r"vous rencontrer|convenir d.un (?:rendez-vous|entretien)|vos disponibilites|"
                  r"invite you (?:to|for) an? (?:interview|call)|schedule an? (?:interview|call)|"
                  r"interview invitation|first round|premier entretien|next step in the process)\b"),
    ("accuse", r"\b(bien recu votre (?:candidature|dossier)|accuse de reception de votre candidature|avons (?:bien )?recu votre candidature|"
               r"merci (?:pour|de) votre candidature|thanks? (?:you )?for (?:your application|applying)|"
               r"we have received your application|application (?:has been )?received|"
               r"votre candidature .{0,80}?a ete (?:bien )?(?:recue|envoyee|transmise|enregistree)|"
               r"(?:nous vous )?remercions d.avoir postule|merci d.avoir postule|candidature sera examinee|"
               r"your application (?:has been )?(?:submitted|sent))\b"),
]
_RE = [(c, re.compile(r, re.I)) for c, r in _CLASSES]

# Motif d'un refus, tel que le mail le donne (pour le registre et la preuve Job-Room).
# Dans l'ordre : la première famille reconnue l'emporte. Sans aucune : « aucun motif donné ».
_MOTIFS_REFUS = [(m, re.compile(r, re.I)) for m, r in [
    ("Poste pourvu", r"\b(poste (?:a ete |est )?(?:deja )?pourvu|position (?:has )?(?:already )?been filled|"
                     r"role has been filled|position is (?:now )?filled)\b"),
    ("Poste annulé ou suspendu", r"\b(poste (?:a ete )?(?:annule|supprime|gele)|recrutement (?:est |a ete )?"
                                 r"(?:suspendu|annule|interrompu|gele)|position (?:has been )?(?:cancell?ed|put on hold|"
                                 r"closed)|hiring freeze|no longer (?:recruiting|hiring))\b"),
    ("Autre candidature retenue", r"\b(autres? (?:candidats?|candidatures?|profils?)|other (?:candidates?|applicants?)|"
                                  r"another candidate|(?:retenu|choisi|privilegie) (?:un|une|d.autres?) (?:autre|candidat|profil)|"
                                  r"move forward with (?:other|another|a different)|proceed with (?:other|another)|"
                                  r"(?:profil|profile|experience)s? (?:qui )?(?:correspond(?:ent)?|match(?:es)?) (?:davantage|mieux|more closely|better))\b"),
    ("Profil ne correspondant pas aux exigences du poste",
     r"\b(ne correspond(?:ent)? pas|pas en adequation|ne repond pas (?:entierement |pleinement )?aux|"
     r"does not (?:fully )?(?:match|meet|align)|do not (?:fully )?(?:match|meet)|not (?:the right |a )?(?:fit|match) for|"
     r"(?:exigences|criteres|requirements) (?:du poste|of the (?:role|position)))\b"),
]]
AUCUN_MOTIF = "Aucun motif donné"


# « If you are not selected… », « Si votre candidature n'est pas retenue… » : une hypothèse, pas une réponse.
_CONDITIONNEL = re.compile(r"^\W*(if|should|in case|si|au cas ou|dans le cas ou|falls)\b")


def _phrases(texte):
    """Les phrases d'un mail, sans les hypothèses ni les salutations collées devant."""
    morceaux = re.split(r"(?<=[.!?])\s+|\n+|\s{2,}", (texte or "").replace("\ufffc", " "))
    phrases = [re.sub(r"\s+", " ", p).strip() for p in morceaux]
    phrases = [re.sub(r"^(?:(?:bonjour|dear|hello|hi|madame|monsieur|cher|chere)\b[^,]{0,40},\s*)+", "", p, flags=re.I)
               for p in phrases]
    return [p for p in phrases if p and not _CONDITIONNEL.match(plier(p))]


def motif_refus(objet, contenu):
    """(motif, phrase) : la famille du motif, et la phrase du mail qui le dit (ou "")."""
    phrases = _phrases(objet) + _phrases(contenu)
    for motif, rx in _MOTIFS_REFUS:
        for p in phrases:
            if rx.search(plier(p)):
                return motif, p.strip()[:220]
    refus = dict(_RE)["refus"]
    return AUCUN_MOTIF, next((p.strip()[:220] for p in phrases if refus.search(plier(p))), "")


def texte_motif(objet, contenu, recu):
    """Le motif tel qu'il est noté au registre (champ motif_refus)."""
    motif, phrase = motif_refus(objet, contenu)
    return "%s · « %s » (mail du %s)" % (motif, phrase, recu) if phrase else "%s (mail du %s)" % (motif, recu)


def motif_court(motif_refus_):
    """La famille seule, pour la preuve Job-Room : « Autre candidature retenue »."""
    return re.split(r" · | \(mail du ", motif_refus_ or "")[0].strip() or AUCUN_MOTIF


# Vocabulaire qui doit apparaître dans l'objet ou le texte pour qu'un message compte
# comme une réponse à une candidature (en plus de l'employeur).
_VOCABULAIRE = re.compile(
    r"\b(candidature|postulation|postule|application|applied|applying|apply|dossier de candidature|"
    r"recrutement|recruitment|recruiting|hiring|entretien|interview|poste de|position of|role of|"
    r"your profile|votre profil|bewerbung)\b", re.I)

# Alertes d'offres, rappels de candidature non envoyée, publicités : jamais des réponses.
_PAS_UNE_REPONSE = re.compile(
    r"(new jobs? posted|jobs? matching your profile|recrute au poste|job alerte?|alerte emploi|"
    r"nouvelles? offres?|deposez votre candidature|completez votre candidature|pas encore envoyee|"
    r"n.oubliez pas de postuler|recommended jobs|offres? qui pourraient vous interesser)", re.I)

# Employeurs qui sont aussi des fournisseurs de messagerie ou de comptes : leur nom
# seul ne suffit pas, il ne sert pas de mot de recherche.
_FOURNISSEURS = {"google", "apple", "microsoft", "linkedin", "amazon", "meta", "facebook", "yahoo", "proton"}

FORMES_JURIDIQUES = r"\b(sa|ag|sarl|sàrl|gmbh|s a|ltd|inc|group|groupe|holding|suisse|switzerland|schweiz|ch)\b"


class CourrierImpossible(Exception):
    pass


class CourrierNonConfigure(CourrierImpossible):
    """Le serveur MCP d'Apple Mail n'est pas réglé : ce n'est pas une panne, l'étape est ignorée."""


SCHEMA = """
CREATE TABLE IF NOT EXISTS courriers (
  id TEXT PRIMARY KEY, recu_le TEXT, compte TEXT, expediteur TEXT, objet TEXT,
  extrait TEXT, lien TEXT, empreinte TEXT, classe TEXT, etat TEXT NOT NULL,
  message TEXT, lu_le TEXT NOT NULL
);
"""


# --- Rapprochement et classement ------------------------------------------------


def nom_court(entreprise):
    """« Banque Pictet & Cie SA » → « banque pictet cie » ; sert à reconnaître l'employeur."""
    n = plier(entreprise or "")
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(FORMES_JURIDIQUES, " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def jetons_employeur(entreprise):
    """Mots distinctifs du nom (« pictet », « sicpa », « yverdon »)."""
    vides = {"banque", "bank", "de", "la", "le", "des", "du", "et", "the", "of", "cie", "ville",
             "etat", "association", "fondation", "groupe", "international", "services", "digital",
             "technology", "solutions", "management", "asset", "communes", "comite"}
    return [m for m in nom_court(entreprise).split() if len(m) >= 4 and m not in vides]


def classer(objet, contenu):
    t = plier("\n".join(_phrases(objet) + _phrases(contenu)))
    trouvees = [c for c, rx in _RE if rx.search(t)]
    if "offre" in trouvees:
        return "offre", trouvees
    if "refus" in trouvees and "entretien" not in trouvees:
        return "refus", trouvees
    if "entretien" in trouvees and "refus" not in trouvees:
        return "entretien", trouvees
    if trouvees == ["accuse"]:
        return "accuse", trouvees
    return ("ambigu" if trouvees else "inconnu"), trouvees


# Ce qui suit ces mots n'est plus la réponse : des offres recommandées, dont les employeurs
# ne sont pas celui qui écrit (jobup : « Offres d'emploi similaires … Losinger Marazzi »).
_RECOMMANDATIONS = re.compile(r"(offres? d.emploi similaires|emplois? similaires|offres? similaires|similar jobs|"
                              r"recommended (?:jobs|for you)|voir toutes les recommandations|vous pourriez aussi|"
                              r"you might also like|jobs you may be interested in)", re.I)


def sans_recommandations(texte):
    m = _RECOMMANDATIONS.search(texte or "")
    return texte[:m.start()] if m else (texte or "")


def rattacher(message, ouvertes):
    """Les candidatures dont l'employeur apparaît dans l'expéditeur, l'objet ou le texte."""
    t = plier(" ".join((message.get("sender") or "", message.get("subject") or "",
                        sans_recommandations(message.get("content_preview")))))
    coups = []
    for f in ouvertes:
        jetons = jetons_employeur(f["entreprise"])
        if jetons and all(j in t for j in jetons[:2]):
            coups.append(f)
    if len(coups) > 1:                         # plusieurs postes chez le même employeur : par l'intitulé
        texte_norme = nom_court(t)
        par_titre = [f for f in coups if nom_court(re.sub(r"\(.*?\)", "", f["titre"]))[:25] in texte_norme]
        coups = par_titre or coups
    return coups


def _domaine(adresse):
    """« RH <rh@jobs.valjob.digital> » ou « https://www.valjob.digital/job/… » → « valjob.digital »."""
    m = re.search(r"@([\w.-]+)|https?://([\w.-]+)", adresse or "")
    hote = (m.group(1) or m.group(2)).lower() if m else ""
    return ".".join(hote.split(".")[-2:]) if hote else ""


def suggestions(objet, texte, expediteur, recu, ouvertes):
    """Les candidatures classées de la plus probable à la moins probable pour un mail :
    [(score, candidature, raisons)]. L'intitulé du poste cité pèse le plus, puis l'employeur
    nommé, le site qui écrit (celui de l'annonce ou du formulaire), et la date (un mail ne
    répond pas à une candidature partie après lui)."""
    brut = " ".join((objet or "", sans_recommandations(texte)))
    norme = " %s " % nom_court(brut)
    dom = _domaine(expediteur)
    out = []
    for f in ouvertes:
        s, raisons = 0, []
        titre = nom_court(re.sub(r"\(.*?\)", " ", f.get("titre") or ""))
        mots = [m for m in titre.split() if len(m) >= 4]
        if titre and " %s " % titre in norme:
            s += 5
            raisons.append("intitulé du poste")
        elif mots and sum(m in norme for m in mots) / len(mots) >= .6:
            s += 2
            raisons.append("mots de l'intitulé")
        jetons = jetons_employeur(f.get("entreprise"))
        if jetons and all(j in norme or j in plier(expediteur or "") for j in jetons[:2]):
            s += 4
            raisons.append("employeur")
        if dom and dom in {_domaine(f.get("url")), _domaine(f.get("url_postuler"))}:
            s += 3
            raisons.append("même site que l'annonce")
        postulee = (f.get("postulee_le") or "")[:10]
        if postulee and recu:
            if recu < postulee:
                s -= 3
            elif (dt.date.fromisoformat(recu[:10]) - dt.date.fromisoformat(postulee)).days <= 3:
                s += 1
                raisons.append("reçu juste après l'envoi")
        out.append((s, f, raisons))
    return sorted(out, key=lambda x: (-x[0], x[1].get("entreprise") or ""))


def la_plus_probable(classement):
    """La candidature si elle se détache nettement (score d'au moins 5, 3 points devant la suivante)."""
    if not classement or classement[0][0] < 5:
        return None
    if len(classement) > 1 and classement[1][0] > classement[0][0] - 3:
        return None
    return classement[0][1]


def parle_de_candidature(message):
    """Une réponse à une candidature : le vocabulaire est dans l'objet, ou le texte contient
    une formule de réponse (accusé, refus, entretien, offre). Les alertes sont écartées."""
    objet = plier(message.get("subject") or "")
    if _PAS_UNE_REPONSE.search(objet):
        return False
    return bool(_VOCABULAIRE.search(objet)) or \
        classer(message.get("subject"), message.get("content_preview"))[0] not in ("inconnu",)


def lie_aux_postes(message, ouvertes):
    """Les candidatures concernées, ou None si le message n'est pas une réponse à une candidature.
    Il faut le vocabulaire d'une candidature, et un employeur reconnu ou un logiciel de candidature."""
    if not parle_de_candidature(message):
        return None
    coups = rattacher(message, ouvertes)
    if coups or vient_d_un_ats(message):
        return coups
    return None


def vient_d_un_ats(message):
    s = plier(message.get("sender") or "")
    return any(a in s for a in EXPEDITEURS_ATS)


def extrait(contenu, n=2):
    """Deux phrases au plus : assez pour comprendre, pas plus."""
    phrases = re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", contenu or "").strip())
    return " ".join(phrases[:n])[:400]


# --- Client MCP ---------------------------------------------------------------------


def commande_serveur():
    """Le script du serveur MCP d'Apple Mail, tel que le réglage courrier.serveur_mcp le donne.
    Aucune recherche dans un dossier extérieur à l'application (cache de plugins de Claude Code) :
    sans réglage, erreur claire."""
    chemin = os.path.expanduser(str((reglages().get("courrier") or {}).get("serveur_mcp") or "").strip())
    if not chemin:
        raise CourrierNonConfigure("serveur MCP Apple Mail non réglé (courrier.serveur_mcp : chemin de son script de démarrage)")
    if not os.path.isfile(chemin):
        raise CourrierImpossible("serveur MCP Apple Mail introuvable : %s" % chemin)
    return chemin


async def _chercher(args):
    """Une session par recherche : le serveur tient mal deux longues recherches d'affilée."""
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
    params = StdioServerParameters(command="/bin/bash", args=[commande_serveur()])
    async with stdio_client(params) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            await session.initialize()
            r = await session.call_tool(OUTIL_AUTORISE, args)
            texte = "".join(getattr(c, "text", "") for c in r.content)
            if getattr(r, "is_error", False) or not texte.lstrip().startswith("{"):
                raise CourrierImpossible("Apple Mail : %s" % texte[:200])
            return json.loads(texte).get("items", [])


def _une_recherche(args, delai_s):
    try:
        return asyncio.run(asyncio.wait_for(_chercher(args), delai_s))
    except asyncio.TimeoutError as e:
        raise CourrierImpossible("Apple Mail ne répond pas (délai dépassé)") from e
    except Exception as e:  # serveur absent, Mail fermé, autorisation refusée…
        while isinstance(e, BaseExceptionGroup) and e.exceptions:
            e = e.exceptions[0]
        if isinstance(e, CourrierImpossible):
            raise e
        raise CourrierImpossible("Apple Mail injoignable : %s: %s" % (type(e).__name__, e)) from e


def lire_boite(mots_objet, depuis, comptes, limite=80, delai_s=600, erreurs=None, longueur=1500):
    """Messages dont l'objet contient un des mots, reçus depuis `depuis`, dans INBOX.
    Un compte en panne n'empêche pas de lire les autres ; tous en panne : exception."""
    messages, pannes = [], []
    for compte in comptes:
        try:
            messages += _une_recherche({"account": compte, "mailbox": "INBOX", "subject_keywords": mots_objet,
                                        "date_from": depuis, "include_content": True, "max_content_length": longueur,
                                        "output_format": "json", "limit": limite, "sort": "date_desc"}, delai_s)
        except CourrierNonConfigure:
            raise                                    # serveur non réglé : valable pour tous les comptes
        except CourrierImpossible as e:
            pannes.append("%s : %s" % (compte, e))
    if pannes and erreurs is not None:
        erreurs.extend(pannes)
    if pannes and len(pannes) == len(comptes):
        raise CourrierImpossible(" ; ".join(pannes))
    return messages


# --- Passage ------------------------------------------------------------------------


def _schema(reg):
    reg.cx.executescript(SCHEMA)


def _ident(m):
    cle = m.get("internet_message_id") or "%s|%s|%s" % (m.get("sender"), m.get("subject"), m.get("received_date"))
    return hashlib.sha1(cle.encode("utf-8")).hexdigest()[:20]


def _date(m):
    """Date de réception au format AAAA-MM-JJ, ou aujourd'hui si illisible."""
    brut = m.get("received_date") or ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(brut[:19], fmt).date().isoformat()
        except ValueError:
            pass
    m2 = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", brut)
    if m2:
        return "%s-%02d-%02d" % (m2.group(3), int(m2.group(2)), int(m2.group(1)))
    for fmt in ("%A, %B %d, %Y at %I:%M:%S %p", "%A %d %B %Y à %H:%M:%S"):
        try:
            return dt.datetime.strptime(brut, fmt).date().isoformat()
        except ValueError:
            pass
    return dt.date.today().isoformat()


def depuis(reg, aujourdhui=None):
    """Depuis le dernier courrier lu (au moins 2 jours) ; au premier passage, depuis la
    plus ancienne candidature encore ouverte (120 jours au plus)."""
    auj = aujourdhui or dt.date.today()
    _schema(reg)
    dernier = reg.meta("courrier_lu_le")
    if dernier:
        return (min(dt.date.fromisoformat(dernier[:10]), auj - dt.timedelta(days=2))).isoformat()
    r = reg.cx.execute("SELECT MIN(postulee_le) FROM annonces WHERE statut IN ('candidature','entretien','offre') "
                       "AND postulee_le IS NOT NULL").fetchone()[0]
    plancher = auj - dt.timedelta(days=120)
    return max(dt.date.fromisoformat(r[:10]), plancher).isoformat() if r else plancher.isoformat()


def traiter(reg, messages):
    """Classe et applique. Renvoie le bilan ; n'enregistre que les messages liés aux postes."""
    _schema(reg)
    ouvertes = reg.liste(["candidature", "entretien", "offre"])
    bilan = {"lus": len(messages), "lies": 0, "appliques": 0, "a_trancher": 0, "ignores": 0, "deja_vus": 0}
    for m in messages:
        ident = _ident(m)
        if reg.cx.execute("SELECT 1 FROM courriers WHERE id=?", (ident,)).fetchone():
            bilan["deja_vus"] += 1
            continue
        coups = lie_aux_postes(m, ouvertes)
        if coups is None:
            bilan["ignores"] += 1            # pas une réponse à une candidature : oublié, aucune trace
            continue
        bilan["lies"] += 1
        classe, _ = classer(m.get("subject"), sans_recommandations(m.get("content_preview")))
        recu = _date(m)
        f = coups[0] if len(coups) == 1 else None
        if not f:                               # logiciel de candidature, ou plusieurs postes possibles
            f = la_plus_probable(suggestions(m.get("subject"), m.get("content_preview"), m.get("sender"), recu,
                                             coups or ouvertes))
            coups = [f] if f else coups
        etat, message = "a_trancher", None
        if f and classe in ("accuse", "refus", "entretien"):
            etat, message = _appliquer(reg, f, classe, recu, m)
        elif not f:
            message = "employeur non identifié" if not coups else "plusieurs candidatures possibles"
        else:
            message = {"offre": "offre ou proposition : à lire",
                       "ambigu": "formulation ambiguë", "inconnu": "pas de réponse claire"}[classe]
        reg.cx.execute(
            "INSERT INTO courriers (id, recu_le, compte, expediteur, objet, extrait, lien, empreinte, classe, "
            "etat, message, lu_le) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, recu, m.get("account"), m.get("sender"), m.get("subject"),
             extrait(m.get("content_preview")), m.get("mail_link"),
             f["empreinte"] if f else (coups[0]["empreinte"] if coups else None),
             classe, etat, message, maintenant()))
        bilan["appliques" if etat == "applique" else "a_trancher"] += 1
    reg.commit()
    return bilan


def _appliquer(reg, f, classe, recu, m):
    """Applique un mail sans ambiguïté par les règles du tableau de bord."""
    statut = reg.get(f["empreinte"])["statut"]
    if statut in ("refus", "offre") or (classe == "entretien" and statut == "entretien"):
        return "applique", "déjà à jour au registre"
    if classe == "accuse" and reg.get(f["empreinte"]).get("accuse_le"):
        return "applique", "accusé déjà noté"
    donnees = {"date": recu, "note": "courrier du %s : « %s »" % (recu, (m.get("subject") or "")[:120])}
    if classe == "refus":
        donnees["motif"] = texte_motif(m.get("subject"), m.get("content_preview"), recu)
    if classe == "entretien":
        donnees["prochaine"] = "Répondre à l'invitation (mail du %s)" % recu
    try:
        actions.appliquer(reg, f["empreinte"], {"accuse": "accuse", "refus": "refus",
                                                "entretien": "entretien"}[classe], donnees)
        return "applique", None
    except actions.ActionRefusee as e:
        return "a_trancher", str(e)


def mots_recherche(reg):
    """Mots d'objet : le vocabulaire des candidatures, plus le nom de chaque employeur ouvert."""
    mots = list(MOTS_OBJET)
    for f in reg.liste(["candidature", "entretien", "offre"]):
        j = jetons_employeur(f["entreprise"])
        if j and j[0] not in mots and j[0] not in _FOURNISSEURS:
            mots.append(j[0])
    return mots


def lire_gmail(mots, debut, longueur=1500):
    """Gmail par le connecteur du compte Claude : sur ce Mac, ou dans la machine virtuelle."""
    from .travailleur import client as travailleur
    if travailleur.distant():
        return travailleur.Distant.depuis_reglages().gmail(mots, debut, longueur=longueur)
    from . import gmail
    return gmail.lire(mots, debut, longueur=longueur)


def relever(reg, lire=None, lire_gmail_=None):
    """Relève le courrier lié aux postes et l'applique, dans chaque connecteur de courrier
    actif (Apple Mail, Gmail). Un connecteur en panne n'empêche pas de lire l'autre.
    `lire` et `lire_gmail_` sont remplaçables pour les tests."""
    from . import connecteurs
    r = reglages().get("courrier") or {}
    comptes = r.get("comptes") or []                 # choisis dans le profil ; aucun par défaut
    debut = depuis(reg)
    mots = mots_recherche(reg)
    messages, pannes, lus, ignores = [], [], [], []
    if connecteurs.actif("apple-mail") and not comptes:
        pannes.append("Apple Mail : aucun compte choisi dans le profil")
    elif connecteurs.actif("apple-mail"):
        try:
            messages += (lire or lire_boite)(mots, debut, comptes)
            lus.append("Apple Mail")
        except CourrierNonConfigure as e:
            ignores.append(str(e))                   # pas une panne : Apple Mail n'est pas installé côté réglages
        except CourrierImpossible as e:
            pannes.append(str(e))
    if connecteurs.actif("gmail"):
        try:
            messages += (lire_gmail_ or lire_gmail)(mots, debut)
            lus.append("Gmail")
        except Exception as e:
            pannes.append("Gmail : %s" % e)
    if pannes and not lus:
        raise CourrierImpossible(" ; ".join(pannes))
    if not lus:
        return {"ignore": "; ".join(ignores) or "aucun connecteur de courrier actif (page Connecteurs)"}
    bilan = traiter(reg, messages)
    bilan["depuis"] = debut
    bilan["boites"] = lus
    if pannes:
        bilan["pannes"] = pannes
    if ignores:
        bilan["ignore"] = "; ".join(ignores)
    reg.poser_meta("courrier_lu_le", maintenant())
    reg.commit()
    return bilan


def relire_a_trancher(reg):
    """Oublie le courrier encore à trancher et repart de sa date la plus ancienne (après un
    changement des règles de rattachement). Le courrier déjà appliqué n'est pas touché."""
    _schema(reg)
    plus_ancien = reg.cx.execute("SELECT MIN(recu_le) FROM courriers WHERE etat='a_trancher'").fetchone()[0]
    reg.cx.execute("DELETE FROM courriers WHERE etat='a_trancher'")
    if plus_ancien:
        reg.poser_meta("courrier_lu_le", plus_ancien)
    reg.commit()


def a_trancher(reg):
    _schema(reg)
    return [dict(r) for r in reg.cx.execute(
        "SELECT * FROM courriers WHERE etat='a_trancher' ORDER BY recu_le DESC")]


def pour_le_tableau(reg, ouvertes):
    """Le courrier à trancher avec, pour chacun, les candidatures dans l'ordre du plus probable
    (`classement`), celle à présélectionner (`proposee`, si elle se détache) et le type de
    réponse relu avec les règles du jour (`classe_proposee`, vide si rien n'est sûr)."""
    out = []
    for c in a_trancher(reg):
        classement = suggestions(c["objet"], c["extrait"], c["expediteur"], c["recu_le"], ouvertes)
        f = la_plus_probable(classement)
        classe = classer(c["objet"], c["extrait"])[0]
        out.append({**c, "classement": classement, "proposee": f["empreinte"] if f else "",
                    "classe_proposee": classe if classe in ("refus", "entretien", "accuse", "offre") else ""})
    return out


def recents(reg, jours=14):
    _schema(reg)
    lim = (dt.date.today() - dt.timedelta(days=jours)).isoformat()
    return [dict(r) for r in reg.cx.execute(
        "SELECT c.*, a.entreprise, a.titre FROM courriers c LEFT JOIN annonces a ON a.empreinte=c.empreinte "
        "WHERE c.etat='applique' AND c.recu_le >= ? ORDER BY c.recu_le DESC", (lim,))]


def clore(reg, ident, etat="ignore", empreinte=None, classe=None):
    """La personne tranche un courrier : l'appliquer à une candidature, ou l'ignorer."""
    _schema(reg)
    c = reg.cx.execute("SELECT * FROM courriers WHERE id=?", (ident,)).fetchone()
    if not c:
        raise actions.ActionRefusee("courrier introuvable")
    if etat == "applique":
        emp = empreinte or c["empreinte"]
        if not emp or classe not in ("accuse", "refus", "entretien", "offre"):
            raise actions.ActionRefusee("choisir la candidature et le type de réponse")
        donnees = {"date": c["recu_le"], "note": "courrier du %s : « %s »" % (c["recu_le"], (c["objet"] or "")[:120])}
        if classe == "refus":
            donnees["motif"] = texte_motif(c["objet"], c["extrait"], c["recu_le"])
        actions.appliquer(reg, emp, classe, donnees)
        reg.cx.execute("UPDATE courriers SET etat='applique', empreinte=?, classe=?, message='tranché à la main' "
                       "WHERE id=?", (emp, classe, ident))
    else:
        reg.cx.execute("UPDATE courriers SET etat='ignore', message='ignoré à la main' WHERE id=?", (ident,))
    reg.commit()


def completer_motifs(reg, lire=None, essai=False, sauf=()):
    """Refus notés avant que le motif ne soit lu (« Refus reçu par e-mail le … ») : relit leurs
    mails dans Apple Mail et note le motif. Renvoie [(entreprise, ancien, nouveau)]."""
    _schema(reg)
    lire = lire or lire_boite
    vieux = {r["empreinte"]: dict(r) for r in reg.cx.execute(
        "SELECT c.*, a.entreprise, a.motif_refus FROM courriers c JOIN annonces a ON a.empreinte=c.empreinte "
        "WHERE c.classe='refus' AND c.etat='applique' AND a.statut='refus' "
        "AND (a.motif_refus IS NULL OR a.motif_refus LIKE 'Refus reçu par e-mail%')")}
    vieux = {e: c for e, c in vieux.items() if e not in sauf}
    if not vieux:
        return []
    par_id = {c["id"]: c for c in vieux.values()}
    mots = list(MOTS_OBJET) + sorted({j for c in vieux.values() for j in jetons_employeur(c["entreprise"])[:1]
                                      if j not in _FOURNISSEURS})
    comptes = (reglages().get("courrier") or {}).get("comptes") or []
    debut = min(c["recu_le"] for c in vieux.values())
    messages = {_ident(m): m for m in lire(mots, debut, comptes)}
    faits = []
    for ident, c in par_id.items():
        m = messages.get(ident)
        nouveau = texte_motif(m.get("subject"), m.get("content_preview"), c["recu_le"]) if m else \
            texte_motif(c["objet"], c["extrait"], c["recu_le"])
        faits.append((c["entreprise"], c["motif_refus"], nouveau))
        if not essai:
            reg.maj(c["empreinte"], motif_refus=nouveau)
            reg.noter(c["empreinte"], "motif du refus lu dans le mail · %s" % nouveau)
    if not essai:
        reg.commit()
    return faits
