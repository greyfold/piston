// Champs du design system Nexus, sans React : PhoneField, DateField (mode date de naissance) et lignes répétables.
// Même comportement que src/components/PhoneField.jsx et DateField.jsx du dépôt design-system.
(function () {
  "use strict";
  var LOCALE = "fr-CH";

  /* ---------------------------------------------------------------- PhoneField */

  var MASQUES = {
    CH: "## ### ## ##", FR: "# ## ## ## ##", DE: "### ########", GB: "#### ######", BE: "### ## ## ##",
    NL: "# ########", IT: "### ### ####", ES: "### ## ## ##", PT: "### ### ###", AT: "### #######",
    LU: "### ### ###", IE: "## ### ####", DK: "## ## ## ##", NO: "### ## ###", PL: "### ### ###",
    AU: "### ### ###", JP: "## #### ####", CN: "### #### ####", IN: "##### #####", BR: "## #####-####",
    MX: "## #### ####"
  };
  var ZERO_NATIONAL = ["CH", "FR", "DE", "GB", "GG", "JE", "IM", "BE", "NL", "AT", "IE", "AU", "NZ", "JP", "IN", "ZA"];
  var chiffres = function (s) { return String(s || "").replace(/\D/g, ""); };

  function masque(pays, indicatif) { return MASQUES[pays] || (indicatif === "1" ? "(###) ###-####" : null); }
  function maxChiffres(pays, indicatif) {
    var m = masque(pays, indicatif);
    return m ? (m.match(/#/g) || []).length : 15 - indicatif.length;
  }
  function appliquer(ch, m) {
    if (!m) return ch;
    var out = "", i = 0;
    for (var k = 0; k < m.length && i < ch.length; k++) out += m[k] === "#" ? ch[i++] : m[k];
    return out;
  }
  function national(brut, pays, indicatif) {
    var t = String(brut || "").trim();
    if (indicatif && t.indexOf("+" + indicatif) === 0) t = t.slice(indicatif.length + 1);
    else if (indicatif && t.indexOf("00" + indicatif) === 0) t = t.slice(indicatif.length + 2);
    var ch = chiffres(t);
    if (ZERO_NATIONAL.indexOf(pays) >= 0 && ch[0] === "0") ch = ch.slice(1);
    return ch.slice(0, maxChiffres(pays, indicatif));
  }
  function apresChiffres(texte, n) {
    if (n <= 0) return 0;
    for (var i = 0, vus = 0; i < texte.length; i++) if (/\d/.test(texte[i]) && ++vus === n) return i + 1;
    return texte.length;
  }

  function telephone(bloc) {
    var select = bloc.querySelector("select"), saisie = bloc.querySelector("input[type=tel]");
    var cache = bloc.querySelector("input[type=hidden]");
    var iso = bloc.querySelector(".iso"), code = bloc.querySelector(".code");

    // Noms des pays dans la langue de la page, triés : « Suisse (+41) ».
    try {
      var noms = new Intl.DisplayNames([LOCALE], { type: "region" });
      var options = Array.prototype.slice.call(select.options);
      options.forEach(function (o) { o.textContent = (noms.of(o.value) || o.value) + " (+" + o.dataset.indicatif + ")"; });
      options.sort(function (a, b) { return a.textContent.localeCompare(b.textContent, LOCALE); });
      var choisi = select.value;
      options.forEach(function (o) { select.appendChild(o); });
      select.value = choisi;
    } catch (e) { /* noms laissés en codes ISO */ }

    var ancien = saisie.value;
    function etat() {
      var o = select.options[select.selectedIndex];
      return { pays: select.value, indicatif: o ? o.dataset.indicatif : "" };
    }
    function rendre(ch, curseur) {
      var e = etat(), m = masque(e.pays, e.indicatif);
      ch = ch.slice(0, maxChiffres(e.pays, e.indicatif));
      saisie.value = appliquer(ch, m);
      saisie.placeholder = m ? m.replace(/#/g, "0") : "";
      cache.value = ch ? "+" + e.indicatif + " " + saisie.value : "";
      iso.textContent = e.pays;
      code.textContent = "+" + e.indicatif;
      ancien = saisie.value;
      if (curseur !== undefined && document.activeElement === saisie) {
        var p = apresChiffres(saisie.value, curseur);
        saisie.setSelectionRange(p, p);
      }
    }
    saisie.addEventListener("input", function () {
      var e = etat(), brut = saisie.value;
      var ch = national(brut, e.pays, e.indicatif), avant = chiffres(ancien);
      var curseur = chiffres(brut.slice(0, saisie.selectionStart == null ? brut.length : saisie.selectionStart)).length;
      // Effacer un séparateur ne retire aucun chiffre : on retire celui d'avant.
      if (ch === avant && brut.length < ancien.length && curseur > 0) {
        ch = avant.slice(0, curseur - 1) + avant.slice(curseur);
        curseur -= 1;
      }
      rendre(ch, curseur);
    });
    select.addEventListener("change", function () { rendre(chiffres(saisie.value)); });
    select.addEventListener("focus", function () { bloc.classList.add("indicatif-focus"); });
    select.addEventListener("blur", function () { bloc.classList.remove("indicatif-focus"); });
    rendre(chiffres(saisie.value));
  }

  /* ---------------------------------------------------------------- DateField */

  var pad = function (n, l) { return String(n).padStart(l || 2, "0"); };
  var reel = function (y, m, d) { var t = new Date(y, m, d); return t.getFullYear() === y && t.getMonth() === m && t.getDate() === d; };
  var versIso = function (p) { return pad(p.y, 4) + "-" + pad(p.m + 1) + "-" + pad(p.d); };
  var deDate = function (t) { return { y: t.getFullYear(), m: t.getMonth(), d: t.getDate() }; };
  var comparer = function (a, b) { return versIso(a).localeCompare(versIso(b)); };
  function lireIso(s) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s || "");
    if (!m) return null;
    var y = +m[1], mo = +m[2] - 1, d = +m[3];
    return reel(y, mo, d) ? { y: y, m: mo, d: d } : null;
  }
  function lireTexte(t) {
    var g = String(t || "").trim().split(/\D+/).filter(Boolean);   // jour, mois, année (ordre fr-CH)
    if (g.length !== 3 || g[2].length !== 4) return null;
    var y = +g[2], mo = +g[1] - 1, d = +g[0];
    return reel(y, mo, d) ? { y: y, m: mo, d: d } : null;
  }
  var texte = function (p) { return p ? pad(p.d) + "." + pad(p.m + 1) + "." + pad(p.y, 4) : ""; };
  var plusJours = function (p, n) { return deDate(new Date(p.y, p.m, p.d + n)); };
  function plusMois(p, n) {
    var c = new Date(p.y, p.m + n, 1), dernier = new Date(c.getFullYear(), c.getMonth() + 1, 0).getDate();
    return { y: c.getFullYear(), m: c.getMonth(), d: Math.min(p.d, dernier) };
  }

  function date(bloc) {
    var saisie = bloc.querySelector("input[type=text]"), cache = bloc.querySelector("input[type=hidden]");
    var bouton = bloc.querySelector(".ouvrir-calendrier"), erreur = bloc.querySelector(".erreur-champ");
    var naissance = bloc.hasAttribute("data-naissance");
    var auj = deDate(new Date());
    var min = naissance ? plusMois(auj, -12 * 120) : null, max = naissance ? auj : null;
    var vue0 = naissance ? plusMois(auj, -12 * 30) : auj;
    var fmtLong = new Intl.DateTimeFormat(LOCALE, { dateStyle: "long" });
    var fmtComplet = new Intl.DateTimeFormat(LOCALE, { dateStyle: "full" });
    var fmtMois = new Intl.DateTimeFormat(LOCALE, { month: "long" });
    var fmtJour = new Intl.DateTimeFormat(LOCALE, { weekday: "short" });
    var premierJour = 1;                                   // lundi
    var panneau = null, actif = null;

    var borne = function (p) { if (min && comparer(p, min) < 0) return min; if (max && comparer(p, max) > 0) return max; return p; };
    var dansBornes = function (p) { return !(min && comparer(p, min) < 0) && !(max && comparer(p, max) > 0); };

    function poser(p) {
      cache.value = p ? versIso(p) : "";
      saisie.value = texte(p);
      montrerErreur("");
    }
    function montrerErreur(msg) {
      erreur.textContent = msg; erreur.hidden = !msg;
      bloc.classList.toggle("invalide", !!msg);
      if (msg) saisie.setAttribute("aria-invalid", "true"); else saisie.removeAttribute("aria-invalid");
    }

    saisie.addEventListener("input", function () {
      var p = lireTexte(saisie.value);
      cache.value = p && dansBornes(p) ? versIso(p) : "";
      montrerErreur("");
      if (p && panneau) { actif = borne(p); dessiner(false); }
    });
    saisie.addEventListener("blur", function (e) {
      if (bloc.contains(e.relatedTarget) || (panneau && panneau.contains(e.relatedTarget))) return;
      if (!saisie.value.trim()) { cache.value = ""; montrerErreur(""); return; }
      var p = lireTexte(saisie.value);
      if (!p) montrerErreur("Saisis une date au format JJ.MM.AAAA.");
      else if (!dansBornes(p)) montrerErreur("Choisis une date entre le " + fmtLong.format(new Date(min.y, min.m, min.d)) + " et le " + fmtLong.format(new Date(max.y, max.m, max.d)) + ".");
      else poser(p);
    });
    saisie.addEventListener("click", function () { if (!panneau) ouvrir(false); });
    saisie.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown") { e.preventDefault(); panneau ? focusJour() : ouvrir(true); }
      if (e.key === "Escape" && panneau) { e.preventDefault(); fermer(true); }
    });
    bouton.addEventListener("click", function () { panneau ? fermer(false) : ouvrir(true); });

    function ouvrir(focus) {
      var choisi = lireIso(cache.value);
      actif = borne(choisi || vue0);
      panneau = document.createElement("div");
      panneau.className = "panneau-date";
      panneau.setAttribute("role", "dialog");
      panneau.setAttribute("aria-label", "Calendrier");
      document.body.appendChild(panneau);
      bouton.setAttribute("aria-expanded", "true");
      dessiner(false);
      placer();
      void panneau.offsetWidth;                         // état de départ posé, puis l'entrée (160 ms)
      panneau.classList.add("ouvert");
      if (focus) {
        var annee = panneau.querySelector("select.annee");
        if (naissance && !choisi && annee) annee.focus({ preventScroll: true }); else focusJour();
      }
      document.addEventListener("mousedown", dehors, true);
      document.addEventListener("focusin", dehors, true);
      window.addEventListener("resize", placer);
      window.addEventListener("scroll", placer, true);
    }
    function fermer(rendreFocus) {
      if (!panneau) return;
      panneau.remove(); panneau = null;
      bouton.setAttribute("aria-expanded", "false");
      document.removeEventListener("mousedown", dehors, true);
      document.removeEventListener("focusin", dehors, true);
      window.removeEventListener("resize", placer);
      window.removeEventListener("scroll", placer, true);
      if (rendreFocus) saisie.focus({ preventScroll: true });
    }
    function dehors(e) { if (panneau && !panneau.contains(e.target) && !bloc.contains(e.target)) fermer(false); }
    function placer() {
      if (!panneau) return;
      var r = saisie.getBoundingClientRect(), h = panneau.offsetHeight, l = panneau.offsetWidth, ecart = 8;
      var haut = r.bottom + ecart;
      if (haut + h > window.innerHeight - ecart && r.top - ecart - h >= ecart) haut = r.top - ecart - h;
      panneau.style.top = haut + "px";
      panneau.style.left = Math.max(ecart, Math.min(r.left, window.innerWidth - l - ecart)) + "px";
    }
    function focusJour() {
      var b = panneau && panneau.querySelector("button[tabindex='0']");
      if (b) b.focus({ preventScroll: true });
    }
    function choisir(p) { poser(p); fermer(true); }

    function dessiner(focus) {
      var choisi = lireIso(cache.value);
      panneau.innerHTML = "";
      // En-tête : flèches et listes mois + année (mode date de naissance : on tape « 1990 » pour y sauter).
      var tete = el("div", "tete");
      var prec = fleche("Mois précédent", "m15 6-6 6 6 6", -1), suiv = fleche("Mois suivant", "m9 6 6 6-6 6", 1);
      var mois = el("select", "mois"); mois.setAttribute("aria-label", "Mois");
      for (var m = 0; m < 12; m++) mois.appendChild(option(m, fmtMois.format(new Date(2000, m, 1)), m === actif.m));
      var annee = el("select", "annee"); annee.setAttribute("aria-label", "Année");
      for (var y = max ? max.y : auj.y + 10; y >= (min ? min.y : auj.y - 120); y--) annee.appendChild(option(y, y, y === actif.y));
      mois.addEventListener("change", function () { aller(actif.y, +mois.value); });
      annee.addEventListener("change", function () { aller(+annee.value, actif.m); });
      var listes = el("div", "listes"); listes.appendChild(mois); listes.appendChild(annee);
      tete.appendChild(prec); tete.appendChild(listes); tete.appendChild(suiv);
      panneau.appendChild(tete);

      var grille = el("div", "grille"); grille.setAttribute("role", "grid");
      var entete = el("div", "semaine jours-semaine"); entete.setAttribute("role", "row");
      for (var j = 0; j < 7; j++) {
        var lundi = new Date(2024, 0, 1 + ((premierJour + j) % 7) - 1);   // le 1er janvier 2024 est un lundi
        var th = el("span", ""); th.setAttribute("role", "columnheader");
        th.textContent = fmtJour.format(lundi).replace(".", "");
        entete.appendChild(th);
      }
      grille.appendChild(entete);
      var debut = new Date(actif.y, actif.m, 1), decalage = (debut.getDay() - premierJour + 7) % 7;
      var jour = { y: actif.y, m: actif.m, d: 1 - decalage };
      for (var s = 0; s < 6; s++) {
        var ligne = el("div", "semaine"); ligne.setAttribute("role", "row");
        for (var k = 0; k < 7; k++) {
          var p = deDate(new Date(jour.y, jour.m, jour.d + s * 7 + k));
          var b = el("button", "jour");
          b.type = "button";
          b.textContent = p.d;
          b.setAttribute("role", "gridcell");
          b.setAttribute("aria-label", fmtComplet.format(new Date(p.y, p.m, p.d)));
          b.tabIndex = comparer(p, actif) === 0 ? 0 : -1;
          if (p.m !== actif.m) b.classList.add("hors-mois");
          if (choisi && comparer(p, choisi) === 0) { b.classList.add("choisi"); b.setAttribute("aria-selected", "true"); }
          if (comparer(p, auj) === 0) { b.classList.add("aujourdhui"); b.setAttribute("aria-current", "date"); }
          if (!dansBornes(p)) b.disabled = true;
          (function (q) { b.addEventListener("click", function () { choisir(q); }); })(p);
          ligne.appendChild(b);
        }
        grille.appendChild(ligne);
      }
      grille.addEventListener("keydown", clavier);
      panneau.appendChild(grille);
      var annonce = el("p", "annonce"); annonce.setAttribute("aria-live", "polite");
      annonce.textContent = fmtMois.format(new Date(actif.y, actif.m, 1)) + " " + actif.y;
      panneau.appendChild(annonce);
      panneau.addEventListener("keydown", function (e) {
        if (e.key === "Escape") { e.preventDefault(); fermer(true); }
        if (e.key === "Tab") {
          var arrets = panneau.querySelectorAll("button:not([disabled]):not([tabindex='-1']), select");
          var bord = e.shiftKey ? arrets[0] : arrets[arrets.length - 1];
          if (document.activeElement === bord) { e.preventDefault(); fermer(true); }
        }
      });
      if (focus) focusJour();

      function fleche(nom, trace, sens) {
        var f = el("button", "fleche"); f.type = "button"; f.setAttribute("aria-label", nom);
        f.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="' + trace + '"/></svg>';
        var cible = plusMois(actif, sens);
        if ((sens < 0 && min && comparer({ y: cible.y, m: cible.m, d: new Date(cible.y, cible.m + 1, 0).getDate() }, min) < 0) ||
            (sens > 0 && max && comparer({ y: cible.y, m: cible.m, d: 1 }, max) > 0)) f.disabled = true;
        f.addEventListener("click", function () { aller(cible.y, cible.m); });
        return f;
      }
    }
    function aller(y, m) {
      var dernier = new Date(y, m + 1, 0).getDate();
      actif = borne({ y: y, m: m, d: Math.min(actif.d, dernier) });
      var liste = document.activeElement && document.activeElement.tagName === "SELECT" ? document.activeElement.className : "";
      dessiner(false);
      var memeListe = liste && panneau.querySelector("select." + liste);
      if (memeListe) memeListe.focus({ preventScroll: true });
    }
    function clavier(e) {
      var touches = {
        ArrowLeft: function () { return plusJours(actif, -1); }, ArrowRight: function () { return plusJours(actif, 1); },
        ArrowUp: function () { return plusJours(actif, -7); }, ArrowDown: function () { return plusJours(actif, 7); },
        Home: function () { return plusJours(actif, -((new Date(actif.y, actif.m, actif.d).getDay() - premierJour + 7) % 7)); },
        End: function () { return plusJours(actif, 6 - ((new Date(actif.y, actif.m, actif.d).getDay() - premierJour + 7) % 7)); },
        PageUp: function () { return plusMois(actif, e.shiftKey ? -12 : -1); },
        PageDown: function () { return plusMois(actif, e.shiftKey ? 12 : 1); }
      };
      if (touches[e.key]) { e.preventDefault(); actif = borne(touches[e.key]()); dessiner(true); }
      else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (dansBornes(actif)) choisir(actif); }
    }
  }

  /* ------------------------------------------------------- Lignes répétables */

  function viderLigne(ligne) { ligne.querySelectorAll("input, select").forEach(function (i) { i.value = ""; }); }

  // La ligne vide du bas, ou une nouvelle, copiée de la dernière puis vidée.
  function ligneLibre(liste) {
    var toutes = liste.querySelectorAll("[data-ligne]"), derniere = toutes[toutes.length - 1];
    var vide = Array.prototype.every.call(derniere.querySelectorAll("input, select"), function (i) { return !i.value.trim(); });
    if (vide) return derniere;
    var cible = derniere.cloneNode(true);
    viderLigne(cible);
    liste.appendChild(cible);
    return cible;
  }

  function lignes(bloc) {
    var liste = bloc.querySelector("[data-lignes]"), ajouter = bloc.querySelector("[data-ajouter-ligne]");
    if (!liste || !ajouter) return;
    var toutes = function () { return liste.querySelectorAll("[data-ligne]"); };
    var vider = viderLigne;
    ajouter.hidden = false;
    ajouter.addEventListener("click", function () { ligneLibre(liste).querySelector("input, select").focus(); });
    liste.addEventListener("click", function (e) {
      var bouton = e.target.closest("[data-retirer-ligne]");
      if (!bouton) return;
      var ligne = bouton.closest("[data-ligne]");
      if (toutes().length === 1) { vider(ligne); ligne.querySelector("input, select").focus(); return; }
      var voisine = ligne.nextElementSibling || ligne.previousElementSibling;
      ligne.remove();
      (voisine && voisine.matches("[data-ligne]") ? voisine.querySelector("input") : ajouter).focus();
    });
  }

  /* --------------------------------------------- Liste déroulante avec recherche */

  var numeroListe = 0;
  function plier(t) { return String(t).normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim(); }
  function libelleChoisi(select) { return select.value ? select.options[select.selectedIndex].textContent : ""; }

  // Le <select> du serveur reste le champ envoyé : il passe, caché, derrière une saisie qui filtre ses options.
  function listeRecherche(select) {
    var boite = el("div", "liste-recherche"), saisie = el("input"), panneau = el("ul", "panneau-liste");
    saisie.type = "text"; saisie.autocomplete = "off"; saisie.spellcheck = false;
    saisie.placeholder = select.options[0].textContent;
    saisie.setAttribute("role", "combobox"); saisie.setAttribute("aria-autocomplete", "list");
    saisie.setAttribute("aria-expanded", "false"); saisie.setAttribute("aria-label", select.getAttribute("aria-label") || "");
    saisie.value = libelleChoisi(select);
    panneau.setAttribute("role", "listbox"); panneau.hidden = true;
    select.parentNode.insertBefore(boite, select);
    boite.appendChild(saisie); boite.appendChild(panneau); boite.appendChild(select);
    select.hidden = true;
  }

  // Les valeurs déjà choisies dans les autres lignes : elles ne sont plus proposées ici.
  function prisesAilleurs(boite) {
    var liste = boite.closest("[data-lignes]"), prises = {};
    if (liste) liste.querySelectorAll(".liste-recherche select").forEach(function (s) {
      if (s.value && !boite.contains(s)) prises[plier(s.value)] = true;
    });
    return prises;
  }

  function activer(boite, item) {
    var saisie = boite.querySelector("input"), ancien = boite.querySelector("li.actif");
    if (ancien) ancien.classList.remove("actif");
    if (!item) { saisie.removeAttribute("aria-activedescendant"); return; }
    item.classList.add("actif");
    saisie.setAttribute("aria-activedescendant", item.id);
    item.scrollIntoView({block: "nearest"});
  }

  // Ouvre le panneau sur les options qui contiennent le filtre, celles qui commencent par lui d'abord.
  function ouvrirListe(boite, filtre) {
    var saisie = boite.querySelector("input"), panneau = boite.querySelector("ul"), select = boite.querySelector("select");
    var prises = prisesAilleurs(boite), cle = plier(filtre || ""), debut = [], suite = [];
    Array.prototype.forEach.call(select.options, function (o) {
      if (!o.value || prises[plier(o.value)]) return;
      var position = plier(o.textContent).indexOf(cle);
      if (position === 0) debut.push(o); else if (position > 0) suite.push(o);
    });
    panneau.id = "liste-recherche-" + (++numeroListe);          // une ligne ajoutée est une copie : identifiant refait
    panneau.textContent = "";
    debut.concat(suite).forEach(function (o, i) {
      var item = el("li");
      item.id = panneau.id + "-" + i; item.textContent = o.textContent;
      item.setAttribute("role", "option"); item.setAttribute("data-valeur", o.value);
      item.setAttribute("aria-selected", o.value === select.value ? "true" : "false");
      panneau.appendChild(item);
    });
    if (!panneau.firstChild) {
      var vide = el("li", "vide");
      vide.textContent = "Aucun résultat"; vide.setAttribute("role", "presentation");
      panneau.appendChild(vide);
    }
    panneau.hidden = false;
    saisie.setAttribute("aria-expanded", "true"); saisie.setAttribute("aria-controls", panneau.id);
    activer(boite, (!cle && panneau.querySelector("[aria-selected=true]")) || panneau.querySelector("[role=option]"));
  }

  function fermerListe(boite) {
    var saisie = boite.querySelector("input"), panneau = boite.querySelector("ul");
    panneau.hidden = true; panneau.textContent = "";
    saisie.setAttribute("aria-expanded", "false"); saisie.removeAttribute("aria-activedescendant");
  }

  function choisirDansListe(boite, valeur) {
    var saisie = boite.querySelector("input"), select = boite.querySelector("select");
    select.value = valeur;
    saisie.value = libelleChoisi(select);
    fermerListe(boite);
    select.dispatchEvent(new Event("change", {bubbles: true}));
  }

  // En quittant la saisie : vidée, plus rien n'est choisi ; un nom écrit en entier est choisi ; sinon le choix d'avant revient.
  function quitterListe(boite) {
    var saisie = boite.querySelector("input"), select = boite.querySelector("select");
    var cle = plier(saisie.value), prises = prisesAilleurs(boite);
    var exacte = cle && Array.prototype.filter.call(select.options, function (o) {
      return o.value && !prises[plier(o.value)] && plier(o.textContent) === cle;
    })[0];
    if (!cle) choisirDansListe(boite, "");
    else if (exacte) choisirDansListe(boite, exacte.value);
    else { saisie.value = libelleChoisi(select); fermerListe(boite); }
  }

  function boiteDe(cible) { return cible.closest ? cible.closest(".liste-recherche") : null; }
  function saisieDe(e) { return e.target.matches && e.target.matches(".liste-recherche input") ? boiteDe(e.target) : null; }

  // À l'écoute du document : une ligne ajoutée (copie de la dernière) marche sans rien rebrancher.
  document.addEventListener("input", function (e) { var b = saisieDe(e); if (b) ouvrirListe(b, e.target.value); });
  document.addEventListener("focusout", function (e) { var b = saisieDe(e); if (b) quitterListe(b); });
  document.addEventListener("mousedown", function (e) {
    var boite = boiteDe(e.target);
    if (!boite) return;
    var item = e.target.closest("li");
    if (item) {                                       // le clic ne prend pas le focus à la saisie
      e.preventDefault();
      if (item.hasAttribute("data-valeur")) choisirDansListe(boite, item.getAttribute("data-valeur"));
    } else if (e.target.matches("input")) {
      if (boite.querySelector("ul").hidden) { ouvrirListe(boite, ""); e.target.select(); e.preventDefault(); e.target.focus(); }
      else fermerListe(boite);
    }
  });
  document.addEventListener("keydown", function (e) {
    var boite = saisieDe(e);
    if (!boite) return;
    var panneau = boite.querySelector("ul"), items = panneau.querySelectorAll("[role=option]");
    var rang = Array.prototype.indexOf.call(items, panneau.querySelector("li.actif"));
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (panneau.hidden) { ouvrirListe(boite, ""); return; }
      if (items.length) activer(boite, items[(rang + (e.key === "ArrowDown" ? 1 : items.length - 1)) % items.length]);
    } else if (e.key === "Enter" && !panneau.hidden) {
      e.preventDefault();                             // Entrée choisit, sans envoyer le formulaire
      if (rang >= 0) choisirDansListe(boite, items[rang].getAttribute("data-valeur"));
    } else if (e.key === "Escape" && !panneau.hidden) {
      e.preventDefault();
      e.target.value = libelleChoisi(boite.querySelector("select"));
      fermerListe(boite);
    }
  });

  /* ------------------------------------------------ Postes proposés par l'IA */

  // « Ajouter » : le poste proposé passe dans les lignes du champ « Postes recherchés », juste au-dessus.
  function reprendrePoste(bouton) {
    var bloc = bouton.closest("[data-postes-proposes]"), champ = bloc.previousElementSibling;
    var liste = champ && champ.matches("[data-champ-lignes]") ? champ.querySelector("[data-lignes]") : null;
    if (!liste) return;
    var ligne = ligneLibre(liste), champs = ligne.querySelectorAll("input");
    champs[0].value = bouton.getAttribute("data-intitule");
    if (champs[1]) champs[1].value = bouton.getAttribute("data-variantes");
    var item = bouton.closest("li"), ul = item.parentNode;
    item.remove();
    if (!ul.querySelector("li")) {                    // plus rien à reprendre : la liste et son titre s'en vont
      var intro = ul.previousElementSibling;
      if (intro && intro.matches(".champ-intro")) intro.remove();
      ul.remove();
    }
    champs[0].focus();
  }

  // « Proposer des postes » : l'appel à l'IA, puis le bloc rendu par le serveur remplace l'ancien.
  function proposerPostes(bouton) {
    var bloc = bouton.closest("[data-postes-proposes]");
    var attente = bloc.querySelector("[data-attente]"), erreur = bloc.querySelector("[data-erreur]");
    bouton.disabled = true; bouton.setAttribute("aria-busy", "true");
    attente.hidden = false; erreur.hidden = true;
    fetch("/profil/postes/proposer", {method: "POST", credentials: "same-origin"})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok) throw new Error(d.erreur || "échec");
        var tmp = document.createElement("div");
        tmp.innerHTML = d.html;
        var neuf = tmp.querySelector("[data-postes-proposes]");
        bloc.replaceWith(neuf);
        var premier = neuf.querySelector("[data-ajouter-poste], [data-proposer-postes]");
        if (premier) premier.focus();
      })
      .catch(function (e) {
        attente.hidden = true;
        bouton.disabled = false; bouton.removeAttribute("aria-busy");
        erreur.hidden = false;
        erreur.textContent = "L'IA n'a pas pu proposer de postes (" + e.message + ").";
      });
  }

  document.addEventListener("click", function (e) {
    var b = e.target.closest("[data-ajouter-poste]");
    if (b) { reprendrePoste(b); return; }
    b = e.target.closest("[data-proposer-postes]");
    if (b && !b.disabled) proposerPostes(b);
  });

  function el(tag, classe) { var e = document.createElement(tag); if (classe) e.className = classe; return e; }
  function option(v, t, choisi) { var o = document.createElement("option"); o.value = v; o.textContent = t; o.selected = choisi; return o; }

  document.querySelectorAll("[data-telephone]").forEach(telephone);
  document.querySelectorAll("[data-date]").forEach(date);
  document.querySelectorAll("select[data-liste-recherche]").forEach(listeRecherche);
  document.querySelectorAll("[data-champ-lignes]").forEach(lignes);
})();
