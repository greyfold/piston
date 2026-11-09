// Entretiens avec l'IA (Ton parcours, Ce qui compte pour toi) : le fil de discussion, l'envoi d'une réponse,
// la page qui s'ouvre en bas comme une conversation, et le volet latéral. Chargé sans « defer » par les pages
// d'entretien, avant leur propre script.
(function () {
  // À l'envoi, la réponse part tout de suite dans le fil, suivie de l'attente ; la zone de saisie se vide et
  // garde sa hauteur. En cas d'échec, l'erreur s'affiche dans le fil, sous la réponse, avec « Réessayer »
  // (renvoie le même texte) et « Modifier ma réponse » (la remet dans la zone de saisie).
  function versLeFil(form, fil, attente) {
    var champ = form.querySelector("textarea"), place = attente.parentNode, suivant = attente.nextSibling;
    fil.querySelectorAll(".envoi-echoue").forEach(function (li) { li.remove(); });   // un nouvel envoi remplace l'échec
    var courante = fil.querySelector(".courante");
    var texte = champ.value;
    var moi = document.createElement("li"), p = document.createElement("p"), cache = document.createElement("span");
    moi.className = "message moi"; cache.className = "cache"; cache.textContent = "Ta réponse : ";
    p.textContent = texte; moi.append(cache, p);
    var attendre = document.createElement("li");
    attendre.className = "message attente-fil";
    attendre.appendChild(attente);
    fil.append(moi, attendre);
    if (courante) courante.classList.remove("courante");
    champ.value = ""; champ.style.height = "";              // la zone revient à une ligne
    return function (message) {                            // l'envoi a échoué : l'erreur à la suite du fil
      place.insertBefore(attente, suivant); attendre.remove();
      if (courante) courante.classList.add("courante");
      moi.classList.add("envoi-echoue");
      var li = document.createElement("li"), msg = document.createElement("p"), actions = document.createElement("div");
      li.className = "message erreur-fil envoi-echoue"; li.setAttribute("role", "alert");
      msg.textContent = message;
      actions.className = "actions-erreur";
      var reessayer = document.createElement("button"), modifier = document.createElement("button");
      reessayer.type = modifier.type = "button";
      reessayer.className = "go petit"; reessayer.textContent = "Réessayer";
      modifier.className = "lien-neutre"; modifier.textContent = "Modifier ma réponse";
      reessayer.addEventListener("click", function () { champ.value = texte; form.requestSubmit(); });
      modifier.addEventListener("click", function () {
        moi.remove(); li.remove(); champ.value = texte;
        champ.dispatchEvent(new Event("input")); champ.focus();   // la zone reprend la hauteur du texte
      });
      actions.append(reessayer, modifier); li.append(msg, actions);
      fil.appendChild(li);
      window.scrollTo(0, document.documentElement.scrollHeight);
    };
  }

  // Envoie le formulaire à `url` ; si tout va bien, va à l'adresse que rend `quand(réponse)`.
  // Avec `fil`, la réponse s'affiche dans le fil (sinon l'erreur s'affiche sous le formulaire, suivie de `suite`).
  function envoyer(form, url, quand, suite, fil) {
    var attente = form.querySelector("[data-attente]"), erreur = form.querySelector("[data-erreur]");
    var boutons = form.querySelectorAll("button, textarea, input");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var corps = new FormData(form);
      var echec = fil ? versLeFil(form, fil, attente) : null;
      erreur.hidden = true; attente.hidden = false;
      if (fil) window.scrollTo(0, document.documentElement.scrollHeight);   // la réponse et l'attente au-dessus de la saisie
      boutons.forEach(function (b) { b.disabled = true; });
      fetch(url, {method: "POST", credentials: "same-origin", body: corps})
        .then(function (r) {
          return r.json().catch(function () { return {ok: false, erreur: "le serveur a répondu " + r.status}; });
        })
        .then(function (d) {
          if (!d.ok) throw new Error(d.erreur || "échec");
          // Même adresse à l'ancre près : le navigateur ne ferait que défiler jusqu'à l'ancre,
          // et la page resterait sur l'attente.
          var cible = new URL(quand(d), location.href);
          if (cible.pathname + cible.search === location.pathname + location.search) {
            history.replaceState(null, "", cible.href);
            location.reload();
          } else {
            location.href = cible.href;
          }
        })
        .catch(function (e) {
          attente.hidden = true;
          boutons.forEach(function (b) { b.disabled = false; });
          if (echec) { echec("Ça n'a pas abouti : " + e.message + "."); return; }
          erreur.hidden = false;
          erreur.textContent = "Ça n'a pas abouti : " + e.message + ". " + suite;
        });
    });
  }

  // Comme une conversation : la page s'ouvre tout en bas, sur le dernier échange au-dessus de la saisie.
  // Encore une fois une fois la police et la page chargées, qui changent la hauteur du fil.
  // Au rechargement (après une réponse), le navigateur remettrait l'ancienne position : c'est nous qui la fixons.
  function salle() {
    if (!document.querySelector(".salle-entretien")) return;
    if ("scrollRestoration" in history) history.scrollRestoration = "manual";
    var enBas = function () { window.scrollTo(0, document.documentElement.scrollHeight); };
    enBas();
    window.addEventListener("load", enBas);
    if (document.fonts) document.fonts.ready.then(enBas);
  }

  // Volet latéral (<dialog> modal) : s'ouvre par tout bouton [data-ouvrir-volet="<id>"] et rend le focus à celui
  // qui l'a ouvert ; Échap, la croix et un clic sur le fond le ferment. Avec `ancre` (« #base »), l'adresse
  // qui la porte rouvre le volet (retour d'une correction). Rend {ouvrir} ou null.
  function volet(id, ancre) {
    var v = document.getElementById(id);
    if (!v) return null;
    var boutons = document.querySelectorAll('[data-ouvrir-volet="' + id + '"]'), origine = boutons[0];
    var ouvrir = function (depuis) { if (depuis) origine = depuis; if (!v.open) v.showModal(); };
    var fermer = function () { v.classList.add("ferme"); setTimeout(function () { v.close(); v.classList.remove("ferme"); }, 180); };
    boutons.forEach(function (b) { b.addEventListener("click", function () { ouvrir(b); }); });
    v.querySelector("[data-fermer-volet]").addEventListener("click", fermer);
    v.addEventListener("cancel", function (ev) { ev.preventDefault(); fermer(); });
    v.addEventListener("click", function (ev) { if (ev.target === v) fermer(); });   // clic sur le fond
    v.addEventListener("close", function () {
      if (ancre && location.hash === ancre) history.replaceState(null, "", location.pathname + location.search);
      if (origine) origine.focus({preventScroll: true});
    });
    if (ancre && location.hash === ancre) ouvrir();
    return {ouvrir: ouvrir, element: v};
  }

  function confirmer() {
    document.querySelectorAll("[data-confirmer]").forEach(function (b) {
      b.addEventListener("click", function (ev) { if (!confirm(b.dataset.confirmer)) ev.preventDefault(); });
    });
  }

  window.Entretien = {envoyer: envoyer, salle: salle, volet: volet, confirmer: confirmer};
})();
