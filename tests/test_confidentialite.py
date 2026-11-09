"""Les coordonnées ne partent jamais vers l'IA : balises à l'aller, vraies valeurs au retour."""

from pydantic import BaseModel

from conftest import annonce
from jobsearch import confidentialite, dossier, moteur
from jobsearch.moteur import FauxMoteur, Protege

OUVRIR = moteur.ouvrir          # la vraie fonction : le conftest la remplace pendant les tests

IDENTITE = {"prenom": "Claire", "nom": "Fontaine", "adresse": "Chemin des Lilas 4", "npa": "1205",
            "ville": "Genève", "telephone": "+41 79 123 45 67", "email": "claire.fontaine@example.ch",
            "site": "https://www.clairefontaine.example", "linkedin": "https://www.linkedin.com/in/claire-fontaine-ex/"}

CV_MAITRE = """# CV maître

## Identité et contact

| Champ | Valeur |
|---|---|
| Nom | Claire Fontaine |
| Adresse | Chemin des Lilas 4, 1205 Genève |
| Téléphone | 079 123 45 67 |
| E-mail | ancienne.adresse@example.org |
| Site | www.clairefontaine.example |
| LinkedIn | linkedin.com/in/claire-fontaine-ex/ |
| Naissance | 6 février 1989 |
| Nationalité | Suisse |

## Expérience

### Responsable UX — Exemple SA, Genève (2021 – 2025)
- Claire a formé 300 personnes ; contact de référence : Paul Martin, paul.martin@exemple-sa.ch, +41 22 555 66 77.
- Sources : `CV-Claire-Fontaine.pdf`, dossier « Fontaine, Claire ».
- Une fontaine à eau dans l'open space, sans rapport.
- Né le 06.02.1989, AVS 756.1234.5678.97.
"""

VALEURS = ["Claire", "Fontaine", "Lilas", "123 45 67", "claire.fontaine@example.ch", "ancienne.adresse",
           "clairefontaine.example", "claire-fontaine-ex", "1989", "756.1234"]


def masque():
    return confidentialite.construire(IDENTITE, CV_MAITRE)


def test_aucune_coordonnee_dans_le_contexte_masque():
    texte = masque().masquer(CV_MAITRE, contexte=True)
    for v in VALEURS:
        assert v not in texte, v
    assert "| Nom | {NOM_COMPLET} |" in texte and "| Adresse | {ADRESSE} |" in texte
    assert "| Téléphone | {TELEPHONE} |" in texte and "| Naissance | {DATE_NAISSANCE} |" in texte
    assert "| E-mail | {EMAIL} |" in texte and "| Site | {SITE} |" in texte and "| LinkedIn | {LINKEDIN} |" in texte
    assert "`CV-{PRENOM}-{NOM}.pdf`" in texte and "« {NOM}, {PRENOM} »" in texte
    assert "Une fontaine à eau" in texte                    # un mot courant n'est pas un nom propre
    assert "| Nationalité | Suisse |" in texte and "Genève (2021" in texte   # utiles à l'IA, gardés


def test_un_tiers_cite_a_sa_propre_balise_rendue_telle_quelle():
    m = masque()
    texte = m.masquer(CV_MAITRE, contexte=True)
    assert "paul.martin@exemple-sa.ch" not in texte and "555 66 77" not in texte
    assert m.demasquer("{EMAIL_2} · {TELEPHONE_2}") == "paul.martin@exemple-sa.ch · +41 22 555 66 77"


def test_le_retour_rend_les_valeurs_du_profil():
    m = masque()
    m.masquer(CV_MAITRE, contexte=True)
    entete = m.demasquer("# {NOM_COMPLET}\n{ADRESSE} | {TELEPHONE} | {email} | {SITE} | {{LINKEDIN}}\n{INCONNUE}")
    assert entete == ("# Claire Fontaine\nChemin des Lilas 4, 1205 Genève | +41 79 123 45 67 | "
                      "claire.fontaine@example.ch | www.clairefontaine.example | linkedin.com/in/claire-fontaine-ex/\n"
                      "{INCONNUE}")


def test_formats_courants_reconnus():
    m = masque()
    for t in ("+41 79 123 45 67", "+41791234567", "0041 79 123 45 67", "079 123 45 67", "079.123.45.67",
              "06.02.1989", "1989-02-06", "6 février 1989", "February 6, 1989", "CLAIRE FONTAINE",
              "Chemin des Lilas 4 · 1205 Genève", "https://clairefontaine.example/"):
        assert "{" in m.masquer("Contact : %s." % t) and not any(c.isdigit() for c in m.masquer(t)), t


def test_message_sans_detection_large_mais_avs_toujours_masque():
    m = masque()
    texte = m.masquer("Postuler : rh@employeur.ch, +41 21 000 11 22. AVS 756.9999.8888.77")
    assert "rh@employeur.ch" in texte and "+41 21 000 11 22" in texte      # l'annonce garde ses contacts
    assert "756.9999" not in texte


class Fiche(BaseModel):
    signature: str
    points: list[str]


def test_protege_ne_laisse_rien_passer_et_rend_les_vraies_valeurs():
    interne = FauxMoteur([Fiche(signature="{NOM_COMPLET}, {TELEPHONE}", points=["{PRENOM} a formé 300 personnes"])])
    t = moteur.Tache("dossier", "Tu écris pour Claire Fontaine.", contexte=CV_MAITRE, schema=Fiche)
    r = Protege(interne, masque()).executer(t, "Annonce. Écrire à claire.fontaine@example.ch.")
    tache, message = interne.appels[0]
    envoye = tache.consigne + tache.contexte + message
    for v in VALEURS:
        assert v not in envoye, v
    assert "Confidentialité : les coordonnées" in tache.consigne and "{NOM_COMPLET} : nom et prénom" in tache.consigne
    assert r.objet.signature == "Claire Fontaine, +41 79 123 45 67"
    assert r.objet.points == ["Claire a formé 300 personnes"]
    assert t.contexte == CV_MAITRE                                     # la tâche d'origine n'est pas touchée


def test_protege_lit_le_profil_et_laisse_passer_une_tache_non_masquee():
    interne = FauxMoteur(["Bonjour {PRENOM}.", "Bonjour Camille."])
    p = Protege(interne)                                              # profil de test : Camille Exemple
    assert p.executer(moteur.Tache("x", "Pour Camille Exemple."), "camille@example.ch").texte == "Bonjour Camille."
    assert "Camille" not in interne.appels[0][0].consigne and interne.appels[0][1] == "{EMAIL}"
    p.executer(moteur.Tache("y", "Pour Camille.", masquer=False), "m")
    assert interne.appels[1][0].consigne == "Pour Camille."


def test_ouvrir_rend_toujours_un_moteur_protege(monkeypatch):
    from jobsearch.travailleur import client as travailleur
    monkeypatch.setattr(moteur, "executable", lambda: "/opt/claude")
    monkeypatch.setattr(travailleur, "distant", lambda: False)
    assert isinstance(OUVRIR(verifier=lambda: True), Protege)
    monkeypatch.setattr(travailleur, "distant", lambda: True)
    monkeypatch.setattr(travailleur.Distant, "depuis_reglages", classmethod(lambda cls: cls("http://127.0.0.1:1", jeton_="x")))
    monkeypatch.setattr(travailleur.Distant, "verifie", lambda self: self)
    p = OUVRIR()
    assert isinstance(p, Protege) and isinstance(p.interne, travailleur.Distant)


def test_fiche_d_envoi_completee_sans_claude(reg):
    from jobsearch.navigateur import valeurs_jobroom
    e = annonce(reg, entreprise="Exemple SA", titre="Chef·fe de projet | IA", lieu="Pully")
    f = reg.get(e)
    claude = ("## 0. Avant d'envoyer\nRien.\n## 3. Réponses aux questions standard\n| Question | Réponse |\n|---|---|\n"
              "| Disponibilité | immédiate |\n## 4. Prétentions salariales\n100 000\n## 5. Job-Room\nà ignorer\n")
    envoi = dossier.completer_envoi(claude, f)
    assert "à ignorer" not in envoi and envoi.count("## 5. Job-Room") == 1
    assert envoi.index("Assignation par l'ORP : non") < envoi.index("## 4.")
    assert "| Lieu | adresse à compléter (lieu de l'annonce : Pully) |" in envoi
    f = dict(f, postulee_le="2026-11-09", extra={})
    v = valeurs_jobroom(f, envoi)
    assert v["entreprise"] == "Exemple SA" and v["poste"] == "Chef·fe de projet / IA"


def test_une_balise_restee_dans_le_cv_est_signalee(tmp_path):
    f = {"titre": "AI Product Manager", "langue": "en", "entreprise": "Exemple SA"}
    pieces = {"cv": "# Claire Fontaine\n## AI Product Manager\n{TELEPHONE}\n", "lettre": "Dear team,\n",
              "envoi": "", "analyse": ""}
    cq = {r["cq"][:5]: r for r in dossier.controler(f, tmp_path, pieces, None, "")}
    assert not cq["CQ-01"]["ok"] and "{TELEPHONE}" in cq["CQ-01"]["detail"]
