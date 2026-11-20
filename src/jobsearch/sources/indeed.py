"""Indeed, par le connecteur Indeed de Claude (claude.ai), pas par le site.

Le site refuse toute lecture par script (HTTP 403, anti-robot, constaté le
26.10) et on ne le contourne pas. Le connecteur Indeed, lui, n'est joignable
que depuis une session Claude : le passage lance donc `claude -p` sans tête,
flux JSON dans les deux sens, avec pour seuls outils ceux d'Indeed
(search_jobs, get_job_details). Le modèle ne fait qu'appeler les outils :
Python lit leurs réponses brutes dans le flux, rien n'est recopié par le modèle.

Deux tours dans la même session, parce que les identifiants JOBSEARCH_… ne
valent que pour la connexion en cours (ils se suivent d'un appel à l'autre)
et que les liens to.indeed.com changent à chaque appel :
1. toutes les recherches (requêtes du sourcing × lieux des réglages) ;
2. le détail des seules annonces que le passage veut lire (nouvelles, pas
   écartées sur l'intitulé).
Faute d'identifiant stable, l'identifiant source est l'empreinte employeur +
intitulé. Indeed rend aussi des annonces de plusieurs mois : on garde celles
publiées depuis `anciennete_max_jours`.
"""

import copy
import datetime as dt
import re
import time

from ..fingerprint import empreinte
from ..moteur.session import SessionClaude as _Session, texte_outil
from .base import Annonce, ConfigurationRequise, RouteCassee, code_canton

ETIQUETTE = "indeed:%s"
_CHAMP = re.compile(r"\*\*(Job Title|Job Id|Company|Location|Posted on|Job Type|Compensation|View Job URL):\*\*[ \t]*(.*)")


_texte_outil = texte_outil


def _champs(bloc):
    return {m.group(1): m.group(2).strip() for m in _CHAMP.finditer(bloc)}


def _date(v):
    try:
        return dt.datetime.strptime((v or "").strip(), "%B %d, %Y").date().isoformat()
    except ValueError:
        return ""


def convertir(f, requete=None):
    """Champs d'un résultat Indeed → Annonce (None si incomplet)."""
    titre, entreprise = (f.get("Job Title") or "").strip(), (f.get("Company") or "").strip()
    if not titre or not entreprise:
        return None
    ville, _, canton = (f.get("Location") or "").partition(",")
    canton = code_canton(canton.strip()) or code_canton(ville.strip())
    taux = re.search(r"(\d{2,3})\s*%", f.get("Job Type") or "")
    type_ = (f.get("Job Type") or "").lower()
    return Annonce(
        source="indeed", id_source=empreinte(entreprise, titre), entreprise=entreprise, titre=titre,
        lieu=" ".join(x for x in (ville.strip(), canton) if x), canton=canton,
        url=f.get("View Job URL") or "", url_postuler=f.get("View Job URL") or "",
        publiee_le=_date(f.get("Posted on")), taux_max=int(taux.group(1)) if taux else None,
        permanent=True if "permanent" in type_ else None,
        requetes=[requete] if requete else [])


def lire_recherche(texte):
    """Réponse de search_jobs → [(identifiant de session, champs)]."""
    out = []
    for bloc in re.split(r"(?=\*\*Job Title:\*\*)", texte or ""):
        f = _champs(bloc)
        if f.get("Job Title") and f.get("Job Id"):
            out.append((f["Job Id"], f))
    return out


def lire_detail(texte):
    """Réponse de get_job_details → (identifiant, texte de l'annonce)."""
    f = _champs(texte or "")
    corps = re.split(r"\*\*Compensation:\*\*[^\n]*\n", texte or "", maxsplit=1)
    corps = corps[1] if len(corps) == 2 else ""
    lignes = [l.strip() for l in corps.strip().split("\n")]
    return f.get("Job Id"), re.sub(r"\n{3,}", "\n\n", "\n".join(lignes)).strip()


class SessionClaude(_Session):
    """La session du moteur, avec ce qui est propre à Indeed : ses erreurs sont des
    routes cassées, et sa limite de débit se lit dans le journal de débogage MCP."""

    def __init__(self, commande, cwd=None, journal=None, debogage=None):
        from ..moteur import environnement_connecteurs
        super().__init__(commande, cwd=cwd, journal=journal, debogage=debogage, erreur=RouteCassee, etiquette="indeed",
                         env=environnement_connecteurs())

    def attente_conseillee(self):
        """Secondes à attendre selon Indeed (« Rate limit exceeded … Try again in N
        seconds », code -32429), lues dans le journal de débogage MCP de la session."""
        try:
            texte = open(self.debogage, encoding="utf-8", errors="replace").read() if self.debogage else ""
        except OSError:
            return None
        m = re.findall(r"Rate limit exceeded.*?Try again in (\d+) seconds?", texte)
        return int(m[-1]) if m else None


CONSIGNE_RECHERCHES = """Fais exactement ces recherches Indeed : un appel de l'outil search_jobs par ligne, avec country_code « CH », sans autre outil, sans commentaire ni résumé.
{lignes}
Quand tous les appels sont faits, réponds seulement « fini »."""

CONSIGNE_DETAILS = """Appelle l'outil get_job_details une fois pour chacun de ces identifiants, sans autre outil, sans commentaire ni résumé :
{ids}
Quand tous les appels sont faits, réponds seulement « fini »."""


class Indeed:
    nom = "indeed"
    detail_en_session = True       # l'étape « details » du passage n'a rien à relire

    def __init__(self, reglages, requetes, cantons, ouvrir=None, connecte=None, aujourdhui=None):
        self.r = reglages or {}
        self.requetes, self.cantons = list(requetes), set(cantons)
        self._ouvrir = ouvrir or self._session
        self._connecte = connecte
        self.aujourdhui = aujourdhui

    def _session(self):
        from ..moteur import executable
        commande = [executable(), "-p", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--verbose", "--no-session-persistence", "--tools", "",
                    "--model", str(self.r.get("modele", "haiku")), "--permission-mode", "dontAsk",
                    # Les deux outils seulement : pas get_resume (le CV de la personne sur Indeed).
                    "--allowedTools", ",".join("%s__%s" % (self.r.get("outils", "mcp__claude_ai_Indeed"), o)
                                               for o in ("search_jobs", "get_job_details"))]
        from ..config import DATA
        (DATA / "logs").mkdir(parents=True, exist_ok=True)
        debogage = DATA / "logs" / "indeed-mcp.txt"          # réécrit à chaque session
        debogage.unlink(missing_ok=True)
        commande += ["--debug", "mcp", "--debug-file", str(debogage)]
        return SessionClaude(commande, journal=DATA / "logs" / ("indeed-%s.jsonl" % dt.date.today().isoformat()),
                             debogage=debogage)

    def tester(self):
        if not self.r.get("actif", True):
            raise RouteCassee("indeed : désactivé dans config/settings.yaml")
        from ..moteur import LancementImpossible, connecte, executable
        try:
            executable()
        except LancementImpossible as e:
            raise RouteCassee("indeed : %s" % e)
        if not (self._connecte or connecte)():
            raise RouteCassee("indeed : Claude Code n'est pas connecté (« Se connecter à Claude… » dans le menu de l'application)")
        return 1

    def parametres(self):
        """Ce que la recherche tient du profil : requêtes, cantons, lieux. Le Mac l'envoie au
        travailleur, car la machine virtuelle n'a pas le profil (elle ne voit que partage-vm/)."""
        from ..profil import identite
        return {"requetes": list(self.requetes), "cantons": sorted(self.cantons),
                "lieux": list(self.r.get("lieux") or [identite().get("ville") or ""])}

    def selon(self, p):
        """La même source, avec les paramètres reçus du Mac (voir `parametres`)."""
        s = copy.copy(self)
        s.requetes, s.cantons = list(p.get("requetes") or []), set(p.get("cantons") or [])
        s.r = dict(self.r, lieux=[l for l in p.get("lieux") or [] if l])
        return s

    def recherches(self):
        from ..profil import identite
        lieux = self.r.get("lieux") or [identite().get("ville") or ""]     # sans lieu : la ville du profil
        toutes = [(q, l) for q in self.requetes for l in lieux]
        n = int(self.r.get("recherches_max", 20))
        if n >= len(toutes):
            return toutes
        # Roulement : chaque jour la suite de la veille, toutes les combinaisons en
        # quelques jours, sans dépasser le quota d'appels d'Indeed par passage.
        debut = ((self.aujourdhui or dt.date.today()).toordinal() * n) % len(toutes)
        return (toutes[debut:] + toutes[:debut])[:n]

    def _etat_connecteur(self, s):
        return next((v for k, v in (s.serveurs or {}).items() if "indeed" in (k or "").lower()), None)

    def _pause(self, s, defaut):
        """Ce qu'Indeed demande d'attendre, plus une marge ; sinon `defaut`."""
        conseil = getattr(s, "attente_conseillee", lambda: None)()
        return min(180.0, conseil + 5.0) if conseil is not None else float(defaut)

    def _ouvrir_et_chercher(self, paquet, delai):
        """Ouvre une session et lance le premier paquet de recherches.

        Indeed limite l'usage du connecteur par compte, pour toutes les sessions
        Claude à la fois (« Rate limit exceeded for account … on toolset claude.
        Try again in N seconds », code -32429, constaté le 26.10) ; les sessions de
        l'app Claude ouvertes en même temps comptent aussi, et une tentative refusée
        compte sans doute encore. On attend donc le délai indiqué par Indeed avant
        de rouvrir, `essais` fois au plus."""
        essais, etat, absences = int(self.r.get("essais", 4)), None, 0
        for n in range(essais):
            s = self._ouvrir()
            try:
                reponses = s.tour(CONSIGNE_RECHERCHES.format(lignes=paquet), delai)
            except Exception:
                s.fermer()
                raise
            recherches = [r for r in reponses if r["outil"].endswith("search_jobs") and not r["erreur"]]
            if recherches:
                return s, recherches
            etat = self._etat_connecteur(s)
            s.fermer()
            if etat == "needs-auth":
                raise ConfigurationRequise("Indeed est à réautoriser sur claude.ai (Réglages › Connecteurs)",
                                           connecteur="indeed")
            if s.outils is not None and etat is None and not any("search_jobs" in o for o in s.outils):
                # Pas d'Indeed dans la session : connecteur pas activé, ou pas encore branché au
                # premier tour. On ne conclut qu'à la deuxième fois de suite.
                absences += 1
                if absences >= 2 or n + 1 >= essais:
                    raise ConfigurationRequise("le connecteur Indeed n'est pas activé sur ton compte Claude "
                                               "(claude.ai › Réglages › Connecteurs)", connecteur="indeed")
                time.sleep(float(self.r.get("pause_absent_s", 10)))
                continue
            absences = 0
            conseil = getattr(s, "attente_conseillee", lambda: None)()
            if conseil is not None:
                etat = "limite de débit d'Indeed, réessayer dans %d s" % conseil
            if n + 1 < essais:
                time.sleep(self._pause(s, self.r.get("pause_essais_s", 60)))
        raise RouteCassee("indeed : aucune recherche n'a abouti en %d essais (connecteur Indeed : %s)"
                          % (essais, etat or "état inconnu"))

    def _rechercher(self, delai):
        """Toutes les recherches, par paquets espacés : cinquante appels d'affilée
        ont suffi à déclencher la limite d'Indeed (premier essai réel, 26.10)."""
        debut = time.monotonic()
        lignes = ["- search: %s ; location: %s" % rl for rl in self.recherches()]
        if not lignes:
            # Sans poste visé, rien à chercher (et pas de session à ouvrir pour rien).
            raise ConfigurationRequise("Indeed n'a rien à chercher : aucun poste visé dans ton profil")
        taille = max(1, int(self.r.get("recherches_par_tour", 10)))
        paquets = ["\n".join(lignes[i:i + taille]) for i in range(0, len(lignes), taille)]
        s, recherches = self._ouvrir_et_chercher(paquets[0], delai)
        try:
            for paquet in paquets[1:]:
                time.sleep(float(self.r.get("pause_tours_s", 20)))
                reste = delai - (time.monotonic() - debut)
                if reste < 60:
                    break
                lot = [r for r in s.tour(CONSIGNE_RECHERCHES.format(lignes=paquet), reste)
                       if r["outil"].endswith("search_jobs") and not r["erreur"]]
                if not lot:
                    # Limite atteinte en cours de route : on attend ce qu'Indeed demande, une fois.
                    time.sleep(self._pause(s, 60))
                    reste = delai - (time.monotonic() - debut)
                    if reste < 60:
                        break
                    lot = [r for r in s.tour(CONSIGNE_RECHERCHES.format(lignes=paquet), reste)
                           if r["outil"].endswith("search_jobs") and not r["erreur"]]
                recherches += lot
        except RouteCassee:
            pass                                  # on garde ce qui a déjà été trouvé
        return s, recherches

    def offres(self, a_lire=None):
        """Toutes les recherches, puis le détail de celles que `a_lire` retient."""
        delai = float(self.r.get("duree_max_s", 600))
        debut = time.monotonic()
        limite = ((self.aujourdhui or dt.date.today())
                  - dt.timedelta(days=int(self.r.get("anciennete_max_jours", 60)))).isoformat()
        s, recherches = self._rechercher(delai)
        try:
            par_id, vues = {}, {}
            for r in recherches:
                for ident, f in lire_recherche(r["texte"]):
                    a = convertir(f, ETIQUETTE % (r["entree"].get("search") or ""))
                    if not a or (a.canton not in self.cantons) or (a.publiee_le and a.publiee_le < limite):
                        continue
                    if a.id_source in vues:
                        for q in a.requetes:
                            if q not in vues[a.id_source].requetes:
                                vues[a.id_source].requetes.append(q)
                        continue
                    vues[a.id_source] = a
                    par_id[ident] = a
            choisies = [i for i, a in par_id.items() if a_lire is None or a_lire(a)]
            choisies = choisies[:int(self.r.get("details_max", 25))]
            # Par paquets : un modèle à qui l'on donne vingt identifiants d'un coup
            # s'arrête parfois à dix (constaté au premier essai réel, 26.10).
            paquet = int(self.r.get("details_par_tour", 8))
            try:
                for i in range(0, len(choisies), paquet):
                    reste = delai - (time.monotonic() - debut)
                    if reste < 30:
                        break
                    if i:
                        time.sleep(float(self.r.get("pause_tours_s", 20)))
                    lot = choisies[i:i + paquet]
                    for r in s.tour(CONSIGNE_DETAILS.format(ids="\n".join("- " + x for x in lot)), reste):
                        if not r["outil"].endswith("get_job_details") or r["erreur"]:
                            continue
                        ident, texte = lire_detail(r["texte"])
                        a = par_id.get(ident or r["entree"].get("job_id"))
                        if a and texte:
                            a.texte, a.detail_charge = texte, True
            except RouteCassee:
                pass                              # les annonces trouvées restent, sans leur texte
            return list(vues.values())
        finally:
            s.fermer()

    def detail(self, a: Annonce):
        """Le détail ne se lit que dans la session de recherche (voir `offres`)."""
        return a
