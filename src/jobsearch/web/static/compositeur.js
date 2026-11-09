// Compositeur des entretiens avec l'IA (Nexus AIComposer / AIStatus) et dictée sur l'ordinateur.
//
// La dictée ne passe par aucun service en ligne : elle n'apparaît que si la reconnaissance se fait
// sur cette machine.
//  - Dans l'application Mac : le pont « dictee » (PageWeb.swift), micro de l'ordinateur et
//    reconnaissance vocale d'Apple en mode « sur l'appareil » uniquement.
//  - Dans un navigateur : la reconnaissance vocale du navigateur seulement s'il sait la faire en
//    local (processLocally, Chrome récent) ; sinon le bouton reste caché et la dictée du Mac
//    (deux fois Fn) est proposée.
(function () {
  "use strict";

  var LANGUE = { fr: "fr-FR", de: "de-CH", it: "it-IT", en: "en-US" }[(document.documentElement.lang || "fr").slice(0, 2)] || "fr-FR";
  var pont = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.dictee;
  var SR = window.SpeechRecognition || window.webkitSpeechRecognition;

  // --- Moteurs de dictée : même interface, demarrer(rappels) / arreter() ---------------------
  function moteurNatif() {
    var r = null;
    window.dicteeRetour = function (m) { if (r) r(m); };
    return {
      nom: "natif",
      demarrer: function (rappel) { r = rappel; pont.postMessage({ action: "demarrer", langue: LANGUE }); },
      arreter: function () { pont.postMessage({ action: "arreter" }); },
    };
  }

  function moteurNavigateur() {
    var rec = null;
    return {
      nom: "navigateur",
      demarrer: function (rappel) {
        var lancer = function () {
          rec = new SR();
          rec.lang = LANGUE; rec.continuous = true; rec.interimResults = true;
          rec.processLocally = true;
          var acquis = "";
          rec.onstart = function () { rappel({ type: "pret" }); };
          rec.onresult = function (ev) {
            var provisoire = "";
            for (var i = ev.resultIndex; i < ev.results.length; i++) {
              var t = ev.results[i][0].transcript;
              if (ev.results[i].isFinal) acquis += t; else provisoire += t;
            }
            rappel({ type: "partiel", texte: (acquis + provisoire).trim() });
          };
          rec.onerror = function (ev) {
            var m = { "not-allowed": "l'accès au micro est refusé", "no-speech": "aucune parole entendue",
                      "audio-capture": "aucun micro trouvé", "language-not-supported": "la langue n'est pas installée" }[ev.error];
            rappel({ type: "erreur", message: m || ev.error });
          };
          rec.onend = function () { rappel({ type: "fin" }); rec = null; };
          rec.start();
        };
        // Langue pas encore sur l'ordinateur : le navigateur la télécharge une fois, puis tout reste local.
        SR.available({ langs: [LANGUE], processLocally: true }).then(function (etat) {
          if (etat === "available") return lancer();
          rappel({ type: "installation" });
          return SR.install({ langs: [LANGUE], processLocally: true }).then(function (ok) {
            if (ok) lancer(); else rappel({ type: "erreur", message: "la langue n'a pas pu être installée" });
          });
        }).catch(function (e) { rappel({ type: "erreur", message: e.message || String(e) }); });
      },
      arreter: function () { if (rec) rec.stop(); },
    };
  }

  function detecter() {
    if (pont) return Promise.resolve(moteurNatif());
    if (SR && typeof SR.available === "function") {
      return SR.available({ langs: [LANGUE], processLocally: true })
        .then(function (etat) { return etat === "unavailable" ? null : moteurNavigateur(); })
        .catch(function () { return null; });
    }
    return Promise.resolve(null);
  }

  // --- Un compositeur ----------------------------------------------------------------------
  function brancher(boite, moteur) {
    var form = boite.closest("form");
    var zone = boite.querySelector("textarea");
    var bouton = boite.querySelector("[data-dictee]");
    var libelle = boite.querySelector("[data-dictee-libelle]");
    var icone = boite.querySelector("[data-dictee-icone]");
    var etat = boite.querySelector("[data-dictee-etat]");
    var erreurDictee = boite.querySelector("[data-dictee-erreur]");
    var iconeMicro = icone.innerHTML;
    var iconeArret = '<svg class="icone" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor"/></svg>';

    // Entrée va à la ligne ; ⌘ (ou Ctrl) Entrée envoie.
    zone.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) { ev.preventDefault(); form.requestSubmit(); }
    });
    // La zone grandit avec le texte, jusqu'à la hauteur fixée par la feuille de style.
    function ajuster() { zone.style.height = "auto"; zone.style.height = zone.scrollHeight + 2 + "px"; }
    zone.addEventListener("input", ajuster);

    // Attente de l'IA (Nexus AIStatus) : le temps écoulé apparaît après 10 s.
    var attente = form.querySelector("[data-attente]");
    if (attente) {
      var ecoule = attente.querySelector("[data-ecoule]"), debut = 0, minuterie = null;
      new MutationObserver(function () {
        var actif = !attente.hidden;
        boite.classList.toggle("occupe", actif);
        boite.setAttribute("aria-busy", actif ? "true" : "false");
        clearInterval(minuterie); ecoule.hidden = true;
        if (!actif) return;
        if (dicte) moteur && moteur.arreter();
        debut = Date.now();
        minuterie = setInterval(function () {
          var s = Math.floor((Date.now() - debut) / 1000);
          if (s < 10) return;
          ecoule.hidden = false;
          ecoule.textContent = "Temps écoulé " + String(Math.floor(s / 60)).padStart(2, "0") + ":" + String(s % 60).padStart(2, "0");
        }, 1000);
      }).observe(attente, { attributes: true, attributeFilter: ["hidden"] });
    }

    if (!moteur) {
      etat.textContent = "Dictée du Mac : Fn deux fois";
      etat.classList.add("indice");
      return;
    }
    bouton.hidden = false;

    // Le texte dicté s'insère à la place du curseur ; ce qui était avant et après est gardé.
    var dicte = false, avant = "", apres = "";
    function afficher(actif, message) {
      dicte = actif;
      boite.classList.toggle("dictee", actif);
      bouton.setAttribute("aria-pressed", actif ? "true" : "false");
      libelle.textContent = actif ? "Arrêter" : "Dicter";
      icone.innerHTML = actif ? iconeArret : iconeMicro;
      var err = !!(message && message.erreur);
      etat.textContent = message && !err ? message.texte : "";
      erreurDictee.hidden = !err;
      erreurDictee.textContent = err ? message.texte : "";
    }
    function inserer(texte) {
      if (!texte) return;
      var sep = avant && !/\s$/.test(avant) ? " " : "";
      var fin = apres && !/^\s/.test(apres) ? " " : "";
      zone.value = avant + sep + texte + fin + apres;
      var pos = (avant + sep + texte).length;
      zone.setSelectionRange(pos, pos);
      ajuster();
    }
    function retour(m) {
      if (m.type === "pret") afficher(true, { texte: "J'écoute…" });
      else if (m.type === "installation") afficher(true, { texte: "Installation de la langue…" });
      else if (m.type === "partiel" || m.type === "final") inserer(m.texte);
      else if (m.type === "erreur") afficher(false, { texte: "Dictée impossible : " + m.message + ".", erreur: true });
      else if (m.type === "fin") { if (dicte) afficher(false); zone.focus(); }
    }
    bouton.addEventListener("click", function () {
      if (dicte) { moteur.arreter(); return; }
      var d = zone.selectionStart, f = zone.selectionEnd;
      avant = zone.value.slice(0, d); apres = zone.value.slice(f);
      afficher(true, { texte: "Micro…" });
      moteur.demarrer(retour);
    });
    // Échap arrête la dictée sans rien perdre.
    zone.addEventListener("keydown", function (ev) { if (ev.key === "Escape" && dicte) { ev.preventDefault(); moteur.arreter(); } });
  }

  function demarrer() {
    var boites = document.querySelectorAll("[data-compositeur]");
    if (!boites.length) return;
    // Le fil s'ouvre sur la dernière question.
    document.querySelectorAll(".fil").forEach(function (fil) { fil.scrollTop = fil.scrollHeight; });
    detecter().then(function (moteur) { boites.forEach(function (b) { brancher(b, moteur); }); });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", demarrer); else demarrer();
})();
