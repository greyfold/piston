"""Pré-remplissage dans Chrome : formulaire de l'employeur, preuve Job-Room.

Tourne dans la machine virtuelle (preremplir_vm.py), dans son Chrome, celui que la personne
voit dans la fenêtre « Navigateur » de l'application : elle s'y connecte une fois à Job-Room
et aux portails des employeurs, les sessions restent dans le profil de la VM (le même que
celui des sessions Claude). La plateforme ouvre son propre onglet, remplit ce qu'elle
reconnaît et affiche en haut de la page ce qui est fait et ce qui reste. Elle continue à
remplir au fil des étapes (portails en plusieurs pages) jusqu'à ce que la personne ferme
l'onglet.

Ce qu'elle ne fait jamais : cliquer Envoyer, Soumettre, Postuler ou
Sauvegarder ; cocher un consentement ou une case ; toucher un champ de mot de
passe ; créer un compte ; résoudre un captcha. Les champs se remplissent sans
recevoir le focus (le gestionnaire de mots de passe ouvre sinon une
fenêtre qui bloque la page, constat de la fiche « Envoi assisté »). Le seul
bouton cliqué est le refus des cookies non essentiels.
"""

import datetime as dt
import fcntl
import re
import time
import unicodedata
from pathlib import Path

from .config import DATA

PORT_DEVTOOLS = 9222                # le Chrome de la VM (vm.py), sur 127.0.0.1 de la VM
DUREE_MAX_S = 45 * 60
JOBROOM = "https://www.job-room.ch/"

# Clé de valeur → motif cherché dans l'étiquette, le nom, l'identifiant, le placeholder…
# L'ordre compte : la première règle qui correspond l'emporte.
REGLES = [
    ("email", r"e-?mail|courriel|mail address"),
    ("telephone", r"phone|t[ée]l[ée]phone|mobile|portable|natel|telefon"),
    ("linkedin", r"linkedin"),
    ("site", r"website|site web|site internet|portfolio|homepage|url personnelle"),
    ("prenom", r"first.?name|given.?name|pr[ée]nom|vorname|forename"),
    ("nom", r"last.?name|family.?name|surname|nom de famille|nachname|^nom\b|\bnom$"),
    ("nom_complet", r"full.?name|nom complet|^name$|^your name"),
    ("adresse", r"street|\brue\b|adresse|address(?! e-?mail)|strasse|stra[ßs]e"),
    ("npa", r"zip|postal|post.?code|\bnpa\b|code postal|\bplz\b"),
    ("ville", r"\bcity\b|ville|localit[ée]|\bort\b|town"),
    ("pays", r"country|pays|\bland\b"),
    ("nationalite", r"nationalit|citizenship|staatsangeh"),
    ("disponibilite", r"availab|disponibilit|notice period|pr[ée]avis|start.?date|entr[ée]e en fonction|k[üu]ndigungsfrist"),
    ("salaire", r"salary|salaire|pr[ée]tention|r[ée]mun[ée]ration|compensation|lohn|gehalt"),
    ("lettre", r"cover.?letter|lettre de motivation|anschreiben|motivationsschreiben"),
    ("motivation", r"motivation|why .*(us|join|role|apply)|pourquoi|message"),
]
PAYS = ["Suisse", "Switzerland", "Schweiz", "Svizzera", "CH"]

_JS_REMPLIR = r"""
([valeurs, regles, pays]) => {
  const rapport = {remplis: [], restants: [], mdp: false, captcha: false, fichiers: []};
  const norm = t => (t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().replace(/\s+/g, " ").trim();
  const visible = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const description = el => {
    const morceaux = [];
    if (el.labels) for (const l of el.labels) morceaux.push(l.innerText);
    const lb = el.getAttribute("aria-labelledby");
    if (lb) for (const id of lb.split(/\s+/)) { const x = document.getElementById(id); if (x) morceaux.push(x.innerText); }
    for (const a of ["aria-label", "name", "id", "placeholder", "autocomplete", "data-automation-id", "title"])
      if (el.getAttribute(a)) morceaux.push(el.getAttribute(a));
    const bloc = el.closest("[class*=field], [class*=Field], [class*=form-group], [class*=question], fieldset, li, td");
    if (bloc && morceaux.join("").length < 3) morceaux.push((bloc.innerText || "").slice(0, 120));
    return norm(morceaux.join(" | "));
  };
  // Titre de la section où se trouve le champ (« Ajouter votre CV », « Autres documents ») :
  // le titre le plus proche qui le précède, en remontant les conteneurs.
  const section = el => {
    let n = el;
    for (let i = 0; i < 8 && n; i++) {
      n = n.parentElement;
      if (!n) break;
      const titres = [...n.querySelectorAll("h1,h2,h3,h4,h5,legend,[class*=title],[class*=Title],[class*=heading],[class*=Heading]")]
        .filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (titres.length) return norm(titres[titres.length - 1].innerText).slice(0, 80);
    }
    return "";
  };
  if (document.querySelector("input[type=password]")) rapport.mdp = true;
  if (document.querySelector("iframe[src*=recaptcha], iframe[src*=hcaptcha], .g-recaptcha, .h-captcha, iframe[title*=captcha i]")) rapport.captcha = true;
  const poser = (el, v) => {
    if (el.tagName === "SELECT") {
      const voulus = (Array.isArray(v) ? v : [v]).map(norm);
      const opt = [...el.options].find(o => voulus.some(x => x && (norm(o.text) === x || norm(o.value) === x)))
               || [...el.options].find(o => voulus.some(x => x && x.length > 2 && norm(o.text).includes(x)));
      if (!opt) return false;
      el.value = opt.value;
    } else {
      const proto = el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, v);
    }
    for (const t of ["input", "change", "blur"]) el.dispatchEvent(new Event(t, {bubbles: true}));
    return true;
  };
  const champs = document.querySelectorAll("input:not([type=hidden]):not([type=password]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=submit]):not([type=button]):not([type=image]):not([type=search]), textarea, select");
  for (const el of champs) {
    if (el.disabled || el.readOnly || !visible(el) || el.dataset.jobsearch) continue;
    const d = description(el);
    const deja = el.tagName === "SELECT" ? (el.selectedIndex > 0 && norm(el.options[el.selectedIndex].text) !== "") : el.value;
    let cle = null;
    for (const [k, motif] of regles) { if (new RegExp(motif, "i").test(d)) { cle = k; break; } }
    if (el.type === "email") cle = "email";
    if (el.type === "tel") cle = "telephone";
    if (cle === "lettre" && el.tagName !== "TEXTAREA") cle = null;
    if (cle === "motivation" && el.tagName !== "TEXTAREA") cle = null;
    if (!cle || deja) {
      if (!deja && (el.required || el.getAttribute("aria-required") === "true")) rapport.restants.push(d.slice(0, 60) || el.tagName.toLowerCase());
      continue;
    }
    let v = cle === "pays" ? pays : valeurs[cle];
    if (!v) { if (el.required) rapport.restants.push(d.slice(0, 60)); continue; }
    if (cle === "motivation" && el.maxLength > 0 && v.length > el.maxLength) v = v.slice(0, el.maxLength);
    if (poser(el, v)) { el.dataset.jobsearch = cle; el.style.outline = "2px solid #008254"; rapport.remplis.push(cle); }
  }
  const fichiers = document.querySelectorAll("input[type=file]");
  fichiers.forEach((el, i) => {
    rapport.fichiers.push({index: i, section: section(el), description: description(el), seul: fichiers.length === 1,
                           accepte: el.accept || "", fait: !!el.dataset.jobsearch, rempli: el.files && el.files.length > 0});
  });
  return rapport;
}
"""

_JS_BANDEAU = r"""
([titre, lignes, couleur]) => {
  let b = document.getElementById("jobsearch-bandeau");
  if (!b) {
    b = document.createElement("div"); b.id = "jobsearch-bandeau";
    // En bas à droite, compact, repliable d'un clic : il ne doit pas gêner la navigation.
    b.style.cssText = "position:fixed;bottom:12px;right:12px;z-index:2147483647;max-width:320px;max-height:45vh;overflow:auto;" +
      "font:12px/1.4 -apple-system,sans-serif;background:rgba(255,255,255,.97);color:#1c2326;border:2px solid #008254;" +
      "border-radius:10px;padding:8px 10px;box-shadow:0 4px 18px rgba(0,0,0,.18);cursor:pointer";
    b.title = "Cliquer pour replier ou déplier";
    b.addEventListener("click", () => { b.dataset.replie = b.dataset.replie ? "" : "1"; b.dataset.majForcee = "1"; });
    document.documentElement.appendChild(b);
  }
  b.style.borderColor = couleur;
  const contenu = "<b style='color:" + couleur + "'>" + titre + "</b>";
  if (b.dataset.replie) { b.innerHTML = contenu + " <span style='color:#7b878d'>▴</span>"; return; }
  b.innerHTML = contenu + " <span style='color:#7b878d;float:right'>▾</span><ul style='margin:6px 0 0;padding-left:16px'>" +
    lignes.map(l => "<li>" + l.replace(/</g, "&lt;") + "</li>").join("") + "</ul>" +
    "<div style='margin-top:6px;color:#7b878d;font-size:11px'>Plateforme Recherche d'emploi · rien n'est envoyé sans toi</div>";
}
"""

_NOMS_CHAMPS = {"email": "e-mail", "telephone": "téléphone", "linkedin": "LinkedIn", "site": "site web",
                "prenom": "prénom", "nom": "nom", "nom_complet": "nom complet", "adresse": "adresse",
                "npa": "NPA", "ville": "ville", "pays": "pays", "nationalite": "nationalité",
                "disponibilite": "disponibilité", "salaire": "prétentions", "lettre": "lettre (texte)",
                "motivation": "motivation"}


def _plier(t):
    t = unicodedata.normalize("NFKD", t or "")
    return "".join(c for c in t if not unicodedata.combining(c)).lower()


class NavigateurOccupe(Exception):
    pass


def _verrou():
    DATA.mkdir(parents=True, exist_ok=True)
    v = open(DATA / "navigateur.lock", "w")
    try:
        fcntl.flock(v, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        v.close()
        raise NavigateurOccupe("une fenêtre de pré-remplissage est déjà ouverte : ferme-la d'abord")
    return v


def occupe():
    try:
        _verrou().close()
        return False
    except NavigateurOccupe:
        return True


class Onglets:
    """Les onglets de ce pré-remplissage dans le Chrome partagé de la VM : celui qu'il ouvre et
    ceux que ses pages ouvrent. Fermer ces onglets termine le pré-remplissage ; Chrome reste."""

    def __init__(self, ctx):
        self.ctx, self._pages = ctx, []
        ctx.on("page", self._nouvelle)

    def _nouvelle(self, page):
        try:
            if page.opener() in self._pages:
                self._pages.append(page)
        except Exception:
            pass

    @property
    def pages(self):
        return [pg for pg in self._pages if not pg.is_closed()]

    def new_page(self):
        pg = self.ctx.new_page()
        self._pages.append(pg)
        return pg

    def close(self):
        for pg in self.pages:
            try:
                pg.close()
            except Exception:
                pass


def _ouvrir(p):
    """Le contexte du profil de Chrome de la VM (ses connexions aux sites), par DevTools."""
    b = p.chromium.connect_over_cdp("http://127.0.0.1:%d" % PORT_DEVTOOLS)
    return Onglets(b.contexts[0] if b.contexts else b.new_context())


def _refuser_cookies(page):
    for frame in page.frames:
        for motif in (r"refuser|tout refuser|decline|reject all|reject|ablehnen|nur notwendige|only necessary|nécessaires uniquement"):
            try:
                b = frame.get_by_role("button", name=re.compile(motif, re.I)).first
                if b.is_visible(timeout=300):
                    b.click(timeout=1000)
                    return True
            except Exception:
                pass
    return False


_AUTRE = r"autres? documents?|other documents?|additional|suppl[ée]mentaire|weitere|certificat|zeugnis|dipl[oô]me|" \
         r"portfolio|recommandation|reference|transcript|relev[ée]|attestation"


def _classer_fichier(section, desc):
    """Le titre de section décide d'abord (c'est ce que la personne voit), puis l'étiquette du champ."""
    for texte in (_plier(section), _plier(desc)):
        if not texte:
            continue
        if re.search(r"photo|picture|image|avatar|portrait", texte):
            return None
        if re.search(_AUTRE, texte):
            return None                     # jamais le CV ni la lettre dans « autres documents »
        if re.search(r"cover|lettre|motivation|anschreiben|letter", texte):
            return "lettre"
        if re.search(r"\bcv\b|resume|curriculum|lebenslauf", texte):
            return "cv"
    return "inconnu"


# Vérifications de sécurité comprises (LinkedIn /checkpoint/challenge, 27.10 : le téléphone de la personne
# y avait été inscrit) : rien ne s'y remplit.
_CONNEXION = re.compile(r"/(login|log-in|signin|sign-in|auth|connexion|anmelden|identifier|password|register|signup|"
                       r"checkpoint|challenges?(v2)?|verify|verification|two-step|2fa|mfa|otp)\b|"
                       r"^https?://(auth|login|accounts?|id|sso)\.", re.I)


def _page_de_connexion(page):
    """Page de connexion ou de création de compte : on n'y remplit rien, pas même l'e-mail."""
    try:
        return bool(_CONNEXION.search(page.url)) or page.locator("input[type=password]").count() > 0
    except Exception:
        return True


def choisir_format(formats, accepte):
    """Le Word si le champ l'accepte (mieux lu par les logiciels de tri), sinon le PDF.
    `formats` : {"docx": chemin, "pdf": chemin} ou un chemin seul."""
    if not isinstance(formats, dict):
        return formats
    a = (accepte or "").lower()
    word_ok = not a or any(x in a for x in (".docx", "word", "officedocument", "*/*", "application/*"))
    pdf_ok = not a or any(x in a for x in (".pdf", "pdf", "*/*", "application/*"))
    if word_ok and formats.get("docx"):
        return formats["docx"]
    if pdf_ok and formats.get("pdf"):
        return formats["pdf"]
    return formats.get("docx") or formats.get("pdf")


def _remplir_page(page, valeurs, docs, deja_fichiers):
    """Une passe sur tous les cadres de la page. Renvoie le rapport cumulé."""
    total = {"remplis": [], "restants": [], "mdp": False, "captcha": False, "fichiers": []}
    if _page_de_connexion(page):
        total["mdp"] = True
        return total
    regles = [[k, m] for k, m in REGLES]
    for frame in page.frames:
        try:
            r = frame.evaluate(_JS_REMPLIR, [valeurs, regles, PAYS])
        except Exception:
            continue
        for k in ("remplis", "restants"):
            total[k] += r[k]
        total["mdp"] |= r["mdp"]
        total["captcha"] |= r["captcha"]
        for fi in r["fichiers"]:
            if fi["fait"] or fi["rempli"]:
                continue
            genre = _classer_fichier(fi["section"], fi["description"])
            if genre == "inconnu":
                # Champ sans indication : le CV seulement s'il est l'unique champ fichier de la page.
                genre = "cv" if fi["seul"] and "cv" not in deja_fichiers else None
            chemin = choisir_format(docs.get(genre), fi["accepte"]) if genre else None
            if not chemin or genre in deja_fichiers:
                continue
            try:
                loc = frame.locator("input[type=file]").nth(fi["index"])
                loc.set_input_files(str(chemin), timeout=3000)
                loc.evaluate("el => el.dataset.jobsearch = 'fichier'")
                deja_fichiers.add(genre)
                total["remplis"].append("fichier " + ("CV" if genre == "cv" else "lettre"))
            except Exception:
                pass
    return total


def _bandeau(page, titre, lignes, couleur="#008254"):
    try:
        page.evaluate(_JS_BANDEAU, [titre, lignes, couleur])
    except Exception:
        pass


# Page « candidature envoyée ». L'adresse suffit chez jobup (/application/ok/<id>/<annonce>/) ;
# ailleurs, l'adresse ou le titre principal de la page, et plus aucun formulaire à l'écran.
_URL_ENVOYEE = re.compile(r"/application/ok/|/applications?/(success|confirm|thank)|thank-?you|merci|/confirmation|"
                          r"/success\b|application-?submitted|candidature-?envoyee|/done\b|bewerbung-?(gesendet|erfolgreich)", re.I)
_TEXTE_ENVOYEE = re.compile(
    r"(votre candidature (a bien )?[ée]t[ée] (envoy|transmis|enregistr|soumis)|candidature (envoy|transmis)[ée]e|"
    r"merci (pour|de) votre candidature|nous avons bien re[çc]u votre candidature|"
    r"your application (has been|was) (sent|submitted|received)|application (submitted|sent|received)|"
    r"thank you for (your application|applying)|ihre bewerbung (wurde|ist) (versendet|gesendet|eingegangen)|"
    r"vielen dank f[üu]r ihre bewerbung)", re.I)


def candidature_envoyee(url, titres, formulaire_visible):
    """Vrai si la page confirme l'envoi. `titres` : texte des titres principaux de la page."""
    if _URL_ENVOYEE.search(url or ""):
        return True
    return bool(_TEXTE_ENVOYEE.search(_plier(titres or ""))) and not formulaire_visible


def _etat_envoi(page):
    try:
        titres = page.evaluate("""() => [...document.querySelectorAll('h1, h2, [role=heading], [role=alert], .alert, [class*=success], [class*=confirm]')]
                                   .slice(0, 12).map(e => e.innerText).join(' | ')""")
        formulaire = page.evaluate("""() => [...document.querySelectorAll('input[type=file], textarea, button[type=submit]')]
                                       .some(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })""")
    except Exception:
        return False
    return candidature_envoyee(page.url, titres, formulaire)


def _journal(texte):
    print(texte, flush=True)


def preremplir_employeur(url, valeurs, docs, journal=_journal, a_l_envoi=None):
    """Ouvre le formulaire de l'employeur et le pré-remplit jusqu'à fermeture de la fenêtre.
    `a_l_envoi(url)` est appelé une fois quand une page semble confirmer l'envoi ; il ne note rien : la personne confirme."""
    from playwright.sync_api import sync_playwright
    verrou = _verrou()
    try:
        with sync_playwright() as p:
            ctx = _ouvrir(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            debut, fichiers, remplis = time.monotonic(), set(), set()
            cookies_faits, envoyee, message_envoi = False, False, ""
            while time.monotonic() - debut < DUREE_MAX_S:
                if not ctx.pages:
                    break
                page = ctx.pages[-1]
                if not envoyee and _etat_envoi(page):
                    envoyee = True
                    journal("%s | candidature envoyée : %s" % (dt.datetime.now().strftime("%H:%M:%S"), page.url[:120]))
                    try:
                        message_envoi = a_l_envoi(page.url) if a_l_envoi else ""
                    except Exception as e:
                        message_envoi = "Mise à jour du tableau de bord impossible : %s" % e
                if envoyee:
                    _bandeau(page, "La candidature semble envoyée", [message_envoi or "Confirme dans l'application : « Marquer envoyée ».",
                             "Ensuite : la preuve Job-Room (bouton « Pré-remplir » dans le tableau de bord).",
                             "Tu peux fermer cette fenêtre."])
                    time.sleep(2)
                    continue
                try:
                    if not cookies_faits:
                        cookies_faits = _refuser_cookies(page)
                    r = _remplir_page(page, valeurs, docs, fichiers)
                except Exception:
                    time.sleep(2)
                    continue
                nouveaux = set(r["remplis"]) - remplis
                remplis.update(r["remplis"])
                etat_page = (page.url, tuple(sorted(set(r["restants"]))), r["mdp"], r["captcha"])
                if nouveaux or etat_page != getattr(preremplir_employeur, "_dernier", None):
                    preremplir_employeur._dernier = etat_page
                    journal("%s | page %s | nouveaux : %s | mot de passe : %s | captcha : %s | restants : %s" % (
                        dt.datetime.now().strftime("%H:%M:%S"), page.url[:90], ", ".join(sorted(nouveaux)) or "-",
                        r["mdp"], r["captcha"], "; ".join(sorted(set(r["restants"])))[:200] or "-"))
                lignes = ["Rempli : " + (", ".join(sorted(_NOMS_CHAMPS.get(x, x) for x in remplis)) or "rien encore")]
                if r["mdp"]:
                    lignes.insert(0, "Connexion demandée : connecte-toi (le mot de passe reste à toi), le remplissage reprend ensuite.")
                if r["captcha"]:
                    lignes.append("Captcha présent : à toi.")
                if r["restants"]:
                    lignes.append("Champs obligatoires à remplir : " + "; ".join(sorted(set(r["restants"])))[:300])
                joints = {k: Path(choisir_format(x, "")).name for k, x in docs.items() if x}
                if "fichier CV" not in remplis and joints.get("cv"):
                    lignes.append("CV pas encore joint : si le portail propose le CV de ton profil, clique "
                                  "« Remplacer » ou « Ajouter », le bon CV se joint alors tout seul (%s)." % joints["cv"])
                elif any(x.startswith("fichier") for x in remplis):
                    lignes.append("Fichiers joints : " + ", ".join(joints.values()) +
                                  ". Retire un ancien CV que le portail aurait présélectionné.")
                lignes.append("Coche toi-même les consentements, relis, puis clique Envoyer.")
                lignes.append("Ensuite : « Marquer envoyée » dans le tableau de bord.")
                _bandeau(page, "Pré-remplissage", lignes)
                time.sleep(2)
            journal("Pré-remplissage terminé : %s" % ", ".join(sorted(remplis)))
            try:
                ctx.close()
            except Exception:
                pass
    finally:
        verrou.close()


# --- Job-Room ------------------------------------------------------------------------

# Champs du formulaire « Preuves de recherches personnelles d'emploi » (Angular) : pas d'étiquettes
# liées, seulement des placeholders sous des titres de section. On les reconnaît par placeholder,
# formcontrolname, name ou aria-label, et seulement à l'intérieur du formulaire.
CHAMPS_JOBROOM = [
    ("date", r"dd\.mm\.yyyy|^date$|date|datum"),
    ("entreprise", r"^entreprise$|entreprise|company|firma|unternehmen|employer|name"),
    ("rue", r"^rue$|rue|street|strasse"),
    ("numero", r"^n°$|^no\.?$|n°|house|hausnummer|number"),
    ("npa", r"npa|code postal|zip|postal|plz"),               # « NPA / Lieu » : liste de suggestions
    ("contact", r"personne de contact|contact|kontakt"),
    ("courriel_contact", r"courriel|e-?mail"),
    ("poste", r"d[ée]signation du poste|poste|occupation|stelle|beruf"),
    ("lien", r"lien vers le formulaire|lien|url|link"),
]
CASES_JOBROOM = [
    # (clé, étiquette de la case, titre de la section où elle doit se trouver)
    ("canal électronique", r"par voie electronique|^electronique$|elektronisch|electronic", None),
    ("plein temps", r"a plein temps|plein temps|vollzeit|full.?time", None),
    ("résultat en suspens", r"en suspens|offen|pending|en attente", None),
    ("assignation ORP non", r"^non$|^nein$|^no$", r"assign|\borp\b|\brav\b|zugewiesen"),
]

_JS_CHAMPS_FORM = r"""
() => {
  // La question propre au formulaire de saisie (le titre de la page est le même sur la liste).
  const question = /quand avez-vous postul|wann haben sie sich beworben|when did you apply|quando si [eè] candidat/i;
  const visible = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const q = [...document.querySelectorAll("h1,h2,h3,h4,h5,legend,label,div,span,p")]
    .find(e => e.children.length === 0 && question.test(e.innerText || "") && visible(e));
  if (!q) return {trouve: false, champs: []};
  // Le plus petit conteneur qui porte cette question ET un champ de date ou d'entreprise.
  let racine = q.parentElement;
  while (racine && !(racine.querySelectorAll("input").length >= 4 && racine.innerText.length > 200)) racine = racine.parentElement;
  if (!racine) return {trouve: false, champs: []};
  const form = q.closest("form");
  if (form && form.contains(racine) === false && racine.contains(form) === false) racine = form;
  if (form && racine.contains(form)) racine = form;
  racine.setAttribute("data-jobsearch-formulaire", "1");
  const champs = [...racine.querySelectorAll("input, textarea, select")].map((el, i) => {
    let sec = "", n = el;
    for (let k = 0; k < 8 && n && !sec; k++) { n = n.parentElement; if (!n) break;
      const t = [...n.querySelectorAll("h1,h2,h3,h4,legend,[class*=title],[class*=header]")].filter(h => h.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING);
      if (t.length) sec = t[t.length - 1].innerText.trim().slice(0, 60); }
    const lab = ((el.labels && el.labels.length ? [...el.labels].map(l => l.innerText).join(" ") : "") ||
                 (el.closest("label") ? el.closest("label").innerText : "")).trim().slice(0, 60);
    return {i, tag: el.tagName.toLowerCase(), type: el.type || "", name: el.name || "", id: el.id || "",
            fcn: el.getAttribute("formcontrolname") || "", ph: el.placeholder || "", aria: el.getAttribute("aria-label") || "",
            label: lab, section: sec, checked: !!el.checked, value: (el.value || "").slice(0, 30)};
  });
  return {trouve: true, champs};
}
"""


_JS_CHOISIR = r"""
([section, option]) => {
  const rs = new RegExp(section, "i"), ro = new RegExp(option, "i");
  const titres = [...document.querySelectorAll("[data-jobsearch-formulaire] *")].filter(e =>
    e.children.length < 4 && rs.test(e.innerText || "") && (e.innerText || "").length < 160);
  for (const t of titres) {
    let n = t;
    for (let k = 0; k < 5 && n; k++, n = n.parentElement) {
      const cible = [...n.querySelectorAll("label, mat-radio-button, mat-checkbox, [role=radio], [role=checkbox], span, button")]
        .find(e => ro.test((e.innerText || "").trim()) && (e.innerText || "").trim().length < 40 &&
                   (t.compareDocumentPosition(e) & Node.DOCUMENT_POSITION_FOLLOWING));
      if (cible) { cible.click(); return (cible.innerText || "").trim(); }
    }
  }
  return null;
}
"""

_JS_ERREURS = r"""
() => {
  const f = document.querySelector("[data-jobsearch-formulaire]");
  if (!f) return ["formulaire introuvable"];
  const visibles = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const erreurs = [...f.querySelectorAll("mat-error, .mat-error, .invalid-feedback, .error, [class*=error-message]")]
    .filter(visibles).map(e => e.innerText.trim()).filter(Boolean);
  const vides = [...f.querySelectorAll("input[required], textarea[required], select[required], [aria-required=true]")]
    .filter(e => visibles(e) && !e.value && !["checkbox", "radio"].includes(e.type))
    .map(e => e.placeholder || e.getAttribute("formcontrolname") || e.name || "champ obligatoire");
  const invalides = [...f.querySelectorAll("[aria-invalid=true], .ng-invalid.ng-touched")]
    .filter(e => visibles(e) && e.tagName !== "FORM").map(e => e.placeholder || e.getAttribute("formcontrolname") || e.tagName.toLowerCase());
  return [...new Set([...erreurs, ...vides.map(x => "vide : " + x), ...invalides.map(x => "invalide : " + x)])];
}
"""


def _cliquer(page, selecteur_ou_loc, attente=5000):
    """Clic normal ; si un élément le recouvre, clic par le script de la page (même effet
    pour un lien ou un bouton de l'application Angular de Job-Room)."""
    loc = page.locator(selecteur_ou_loc) if isinstance(selecteur_ou_loc, str) else selecteur_ou_loc
    loc = loc.first
    try:
        loc.click(timeout=attente)
    except Exception:
        loc.evaluate("e => e.click()")


def erreurs_formulaire(page):
    try:
        return page.evaluate(_JS_ERREURS)
    except Exception as e:
        return ["vérification impossible : %s" % e]


def _sauvegarder(page):
    """Clique « Sauvegarder » du formulaire de preuve (et rien d'autre)."""
    bouton = page.locator("[data-jobsearch-formulaire='1'] button[type=submit]").filter(
        has_text=re.compile(r"sauvegarder|speichern|salvare|save", re.I))
    if bouton.count() == 0:
        bouton = page.locator("[data-jobsearch-formulaire='1']").get_by_role(
        "button", name=re.compile(r"^\s*(sauvegarder|enregistrer|speichern|salvare|save)\s*$", re.I)).first
    if bouton.count() == 0:
        bouton = page.get_by_role("button", name=re.compile(r"^\s*(sauvegarder|speichern|salvare|save)\s*$", re.I)).first
    _cliquer(page, bouton)


def _remplir_jobroom(page, v, journal=None):
    """Remplit le formulaire de saisie, et lui seul. Renvoie (faits, restants)."""
    faits, restants = [], []
    info = page.evaluate(_JS_CHAMPS_FORM)
    if not info["trouve"]:
        return [], ["formulaire introuvable"]
    if journal:
        journal("structure du formulaire : " + " || ".join(
            "%(i)s %(tag)s/%(type)s fcn=%(fcn)s ph=%(ph)s name=%(name)s aria=%(aria)s label=%(label)s sec=%(section)s" % c
            for c in info["champs"])[:3000])
    formulaire = page.locator("[data-jobsearch-formulaire='1']").first
    textes = [c for c in info["champs"] if c["type"] not in ("checkbox", "radio", "hidden", "submit", "button")]
    pris = set()
    for cle, motif in CHAMPS_JOBROOM:
        val = v.get(cle)
        if not val:
            continue
        rx = re.compile(motif, re.I)
        cand = next((c for c in textes if c["i"] not in pris and not c["value"] and
                     any(rx.search(_plier(c[k])) for k in ("ph", "fcn", "name", "aria", "label"))), None)
        if not cand:
            restants.append(cle)
            continue
        pris.add(cand["i"])
        champ = formulaire.locator("input, textarea, select").nth(cand["i"])
        try:
            champ.click(timeout=2000)
            if cle == "npa":
                champ.press_sequentially(val, delay=90)
                try:
                    page.get_by_role("option", name=re.compile(re.escape(v.get("ville", "")), re.I)).first.click(timeout=4000)
                except Exception:
                    page.get_by_role("option").first.click(timeout=2500)
            else:
                champ.fill(val, timeout=2500)
                champ.press("Tab")
            faits.append(cle)
        except Exception:
            restants.append(cle)
    cases = [c for c in info["champs"] if c["type"] in ("checkbox", "radio")]
    for cle, motif, section in CASES_JOBROOM:
        rx = re.compile(motif, re.I)
        rs = re.compile(section, re.I) if section else None
        c = next((c for c in cases if (rx.search(_plier(c["label"]).strip()) or rx.search(_plier(c["aria"]).strip()))
                  and (not rs or rs.search(_plier(c["section"])))), None)
        if c:
            try:
                el = formulaire.locator("input, textarea, select").nth(c["i"])
                if not el.is_checked():
                    try:
                        el.check(timeout=2000)
                    except Exception:
                        el.evaluate("e => e.click()")
                faits.append(cle)
                continue
            except Exception:
                pass
        restants.append(cle)                 # aucun clic hors des cases identifiées du formulaire
    return faits, restants


_JS_DOUBLON = r"""
([employeur, poste, seuil]) => {
  const norm = t => (t || "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase();
  const blocs = document.querySelectorAll("tr, li, mat-card, article, [class*=card], [class*=Card], [class*=row], [class*=item], [class*=entry]");
  const trouves = [];
  for (const b of blocs) {
    const t = norm(b.innerText);
    if (!t || t.length > 700) continue;
    if (!employeur.every(j => t.includes(j))) continue;
    const communs = poste.filter(j => t.includes(j));
    if (communs.length >= seuil) trouves.push(b);
  }
  // Les plus petits conteneurs seulement (une ligne de tableau contient ses cellules, etc.).
  const lignes = trouves.filter(b => !trouves.some(c => c !== b && b.contains(c)));
  return {nombre: lignes.length, texte: lignes.length ? lignes[0].innerText.slice(0, 200) : null};
}
"""


def jetons_poste(titre):
    """Mots distinctifs de l'intitulé (« directeur », « adjoint », « projets », « innovation »)."""
    vides = {"pour", "dans", "avec", "des", "les", "une", "the", "and", "for", "with", "poste", "h/f", "f/h",
             "100", "80", "trice", "teur"}
    mots = re.findall(r"[a-z0-9]{4,}", _plier(titre))
    return [m for m in dict.fromkeys(mots) if m not in vides]


def doublon(page, jetons_employeur, jetons_titre):
    """Le texte de la ligne de la liste Job-Room qui porte déjà ce poste chez cet employeur, ou None.
    Même employeur ET même poste (60 % des mots distinctifs de l'intitulé, deux au moins) :
    une autre candidature chez le même employeur n'est pas un doublon."""
    return lignes_identiques(page, jetons_employeur, jetons_titre)["texte"]


def lignes_identiques(page, jetons_employeur, jetons_titre):
    """{nombre, texte} : lignes de la liste avec le même employeur et le même poste."""
    seuil = max(2, -(-len(jetons_titre) * 6 // 10))
    total = {"nombre": 0, "texte": None}
    for frame in page.frames:
        try:
            r = frame.evaluate(_JS_DOUBLON, [jetons_employeur, jetons_titre, seuil])
            total["nombre"] += r["nombre"]
            total["texte"] = total["texte"] or r["texte"]
        except Exception:
            pass
    return total


SAISIE = JOBROOM + "work-efforts/create"
LISTE = JOBROOM + "work-efforts"


def _connecte(page):
    """Sur Job-Room et plus sur une page de connexion (eIAM, AGOV, CH-Login)."""
    url = page.url
    return "job-room.ch" in url and page.locator("input[type=password]").count() == 0 and \
        "/home/job-seeker" not in url or "work-efforts" in url or "dashboard" in url


def _verifier_doublon(ctx, jetons_doublon, jetons_titre, journal):
    """Ouvre la liste des recherches d'emploi dans un onglet à part, cherche une ligne avec le même
    employeur ET le même poste, referme l'onglet. Renvoie la ligne trouvée, ou None."""
    onglet = ctx.new_page()
    try:
        onglet.goto(JOBROOM, wait_until="domcontentloaded", timeout=60000)
        onglet.wait_for_timeout(3000)
        lien = onglet.get_by_role("link", name=re.compile(r"recherches d.emploi|preuves de recherches|arbeitsbem|work effort", re.I))
        if lien.count():
            _cliquer(onglet, lien)
        else:
            onglet.goto(LISTE, wait_until="domcontentloaded", timeout=60000)
        onglet.wait_for_timeout(5000)                        # liste chargée par l'application
        if "work-efforts" not in onglet.url or "create" in onglet.url:
            journal("anti-doublon NON VÉRIFIÉ : la liste ne s'est pas affichée (%s)" % onglet.url[:80])
            return "NON_VERIFIE"
        ligne = doublon(onglet, jetons_doublon, jetons_titre)
        journal("anti-doublon sur %s : %s" % (onglet.url[:80], ligne.replace("\n", " ")[:200] if ligne else "aucun"))
        return ligne
    finally:
        onglet.close()


def preremplir_jobroom(v, jetons_doublon, journal=_journal, apres_sauvegarde=None):
    """Connexion par la personne (la seule chose qu'on lui demande), contrôle anti-doublon sur la liste,
    saisie de https://www.job-room.ch/work-efforts/create, puis sauvegarde automatique si tout est
    sûr : anti-doublon vérifié, aucun champ manquant ni en erreur. Après la sauvegarde, la ligne doit
    apparaître une seule fois dans la liste ; alors seulement `apres_sauvegarde()` note la preuve."""
    from playwright.sync_api import sync_playwright
    verrou = _verrou()
    try:
        with sync_playwright() as p:
            ctx = _ouvrir(p)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(SAISIE, wait_until="domcontentloaded", timeout=60000)
            debut, etape, faits, restants, derniere = time.monotonic(), "connexion", [], [], None
            non_verifie = False
            while time.monotonic() - debut < DUREE_MAX_S and ctx.pages:
                page = next((pg for pg in ctx.pages if "work-efforts/create" in pg.url), ctx.pages[-1])
                if (etape, page.url) != derniere:
                    derniere = (etape, page.url)
                    journal("%s | étape %s | page %s" % (dt.datetime.now().strftime("%H:%M:%S"), etape, page.url[:110]))
                try:
                    if etape == "connexion":
                        _refuser_cookies(page)
                        if _connecte(page) and "job-room.ch" in page.url:
                            etape = "doublon"
                        else:
                            _bandeau(page, "Job-Room", ["Connecte-toi à Job-Room (le mot de passe reste à toi).",
                                                        "Le pré-remplissage reprend tout seul ensuite."], "#9a6700")
                    elif etape == "doublon":
                        ligne = _verifier_doublon(ctx, jetons_doublon, jetons_poste(v.get("poste", "")), journal)
                        non_verifie = ligne == "NON_VERIFIE"
                        if non_verifie:
                            ligne = None
                        if non_verifie:
                            _bandeau(page, "Anti-doublon impossible : rien n'est saisi", [
                                "La liste de tes recherches d'emploi ne s'est pas affichée : sans ce contrôle, "
                                "pas de saisie automatique (règle anti-doublon).",
                                "Ferme la fenêtre et relance « Pré-remplir » ; si ça se répète, dis-le-moi."], "#b42318")
                            etape = "arret"
                        elif ligne:
                            _bandeau(page, "Doublon : rien n'est rempli", [
                                "Ce poste chez cet employeur figure déjà dans tes recherches d'emploi :",
                                ligne.replace("\n", " · ")[:160],
                                "Une preuve en double est une fausse déclaration. Si c'est vraiment une autre "
                                "candidature, saisis-la à la main."], "#b42318")
                            etape = "arret"
                        else:
                            etape = "saisie"
                            if "work-efforts/create" not in page.url:
                                # L'adresse directe renvoie au tableau de bord : liste, puis « + Saisir ».
                                lien = page.get_by_role("link", name=re.compile(r"recherches d.emploi", re.I))
                                _cliquer(page, lien) if lien.count() else page.goto(LISTE)
                                page.wait_for_timeout(5000)
                                saisir = page.get_by_role("link", name=re.compile(r"^\s*\+?\s*saisir\s*$", re.I))
                                if saisir.count():
                                    saisir.first.click(timeout=5000)
                                    page.wait_for_timeout(3000)
                    elif etape == "saisie":
                        if "work-efforts/create" not in page.url:
                            _bandeau(page, "Job-Room", ["Ouvre la page de saisie : « Recherches d'emploi » puis « Saisir ».",
                                                        "Le formulaire se remplira dès qu'il s'affiche."], "#9a6700")
                            try:
                                _cliquer(page, "a[href*='work-efforts/create']", attente=2000)
                            except Exception:
                                pass
                        elif not faits:
                            # Job-Room affiche parfois la liste sous l'adresse /create avant le formulaire :
                            # rien n'est touché tant que la question « Quand avez-vous postulé ? » n'est pas visible.
                            if not page.evaluate(_JS_CHAMPS_FORM)["trouve"]:
                                _bandeau(page, "Job-Room", ["En attente du formulaire de saisie…"], "#9a6700")
                                time.sleep(2)
                                continue
                            page.wait_for_timeout(1500)
                            faits, restants = _remplir_jobroom(page, v, journal)
                            journal("rempli : %s | à compléter : %s" % (", ".join(faits) or "-", ", ".join(restants) or "-"))
                            page.wait_for_timeout(1500)
                            erreurs = erreurs_formulaire(page)
                            journal("contrôle avant sauvegarde : %s" % ("; ".join(erreurs) or "aucune erreur"))
                            if erreurs or "date" not in faits or "entreprise" not in faits or "poste" not in faits:
                                etape = "incomplet"
                                restants = erreurs or restants
                            else:
                                _sauvegarder(page)
                                journal("%s | Sauvegarder cliqué" % dt.datetime.now().strftime("%H:%M:%S"))
                                page.wait_for_timeout(5000)
                                etape = "verification"
                            try:
                                etiquettes = page.evaluate("""() => [...document.querySelectorAll('label, mat-label, legend')]
                                    .map(e => e.innerText.trim()).filter(t => t && t.length < 80)""")
                                journal("étiquettes du formulaire : %s" % " | ".join(etiquettes)[:1500])
                            except Exception:
                                pass
                    elif etape == "verification":
                        erreurs = erreurs_formulaire(page) if "work-efforts/create" in page.url else []
                        if erreurs:
                            journal("refus du formulaire après sauvegarde : %s" % "; ".join(erreurs))
                            restants, etape = erreurs, "incomplet"
                            continue
                        onglet = ctx.new_page()
                        try:
                            onglet.goto(JOBROOM, wait_until="domcontentloaded", timeout=60000)
                            onglet.wait_for_timeout(3000)
                            lien = onglet.get_by_role("link", name=re.compile(r"recherches d.emploi|preuves de recherches|arbeitsbem|work effort", re.I))
                            _cliquer(onglet, lien) if lien.count() else onglet.goto(LISTE)
                            onglet.wait_for_timeout(5000)
                            constat = lignes_identiques(onglet, jetons_doublon, jetons_poste(v.get("poste", "")))
                        finally:
                            onglet.close()
                        journal("vérification après sauvegarde : %d ligne(s)" % constat["nombre"])
                        if constat["nombre"] == 1:
                            message = apres_sauvegarde() if apres_sauvegarde else "Preuve enregistrée."
                            _bandeau(page, "Preuve Job-Room enregistrée ✓", [
                                message, constat["texte"].replace("\n", " · ")[:160], "Tu peux fermer cette fenêtre."])
                            etape = "fini"
                        else:
                            _bandeau(page, "À vérifier dans Job-Room", [
                                "Après la sauvegarde, la liste montre %d ligne(s) pour ce poste au lieu d'une." % constat["nombre"],
                                "Ouvre « Recherches d'emploi » et vérifie la ligne. Rien n'est noté au registre."], "#b42318")
                            etape = "arret"
                    elif etape in ("incomplet", "fini", "arret"):
                        if etape == "incomplet":
                            _bandeau(page, "Saisie Job-Room incomplète : pas de sauvegarde", [
                                "Rempli : " + (", ".join(faits) or "rien"),
                                "Manque ou erreur : " + ("; ".join(restants) or "?"),
                                "Complète ces champs puis clique « Sauvegarder » toi-même, ou dis-le-moi pour que j'ajuste."],
                                "#9a6700")
                except Exception as e:
                    journal("%s | étape %s | incident : %s" % (dt.datetime.now().strftime("%H:%M:%S"), etape, str(e)[:200]))
                time.sleep(2)
            journal("Job-Room terminé : %s" % ", ".join(faits))
            try:
                ctx.close()
            except Exception:
                pass
    finally:
        verrou.close()


def valeurs_jobroom(f, envoi_md):
    """Champs de la preuve : date d'envoi réelle (registre), entreprise, adresse si l'annonce
    la donne (jamais inventée), poste exact, lien du formulaire."""
    from .coordonnees import reponses_envoi
    rep = reponses_envoi(envoi_md or "")
    v = {"date": dt.date.fromisoformat(f["postulee_le"][:10]).strftime("%d.%m.%Y"),
         "entreprise": rep.get("raison sociale") or f["entreprise"],
         "poste": rep.get("désignation exacte du poste") or f["titre"],
         "lien": f.get("url_postuler") or f.get("url") or ""}
    connue = (f.get("extra") or {}).get("adresse_employeur")
    if connue:                       # cherchée à la production du dossier (registre du commerce, annonce)
        v.update(rue=connue.get("rue"), numero=connue.get("numero"), npa=connue.get("npa"), ville=connue.get("ville"))
        return {k: x for k, x in v.items() if x}
    adresse = rep.get("lieu") or rep.get("adresse") or ""
    m = re.match(r"^(?P<rue>.+?)\s+(?P<no>\d+\w?),\s*(?P<npa>\d{4})\s+(?P<ville>.+)$", adresse)
    if m:
        v.update(rue=m.group("rue"), numero=m.group("no"), npa=m.group("npa"), ville=m.group("ville"))
    else:
        m = re.search(r"\b(\d{4})\s+([A-ZÀ-Ý][\w\- ]+)", adresse or f.get("lieu") or "")
        if m:
            v.update(npa=m.group(1), ville=m.group(2).strip())
    return {k: x for k, x in v.items() if x}
