// Pile de tri (trier.html) : la carte du dessus se tire à droite (oui) ou à gauche (non), ou se tranche
// par les boutons et les flèches du clavier. La décision part sans recharger la page (/action en JSON) ;
// la carte s'envole tout de suite et revient si le serveur refuse. Après la dernière : droit au tableau de
// bord (« À faire aujourd'hui »), le bilan du tri en message.
(function () {
  "use strict";
  var salle = document.querySelector("[data-tri]");
  var pile = salle && salle.querySelector("[data-pile]");
  var form = salle && salle.querySelector("[data-form-tri]");
  if (!pile || !form) return;

  var total = parseInt(salle.dataset.total, 10) || 0;
  var faits = 0, occupe = false, tire = null;
  var bilan = {go: 0, pasgo: 0, attendre: 0};
  var message = salle.querySelector("[data-message-tri]");
  var rang = salle.querySelector("[data-rang]");
  var barre = salle.querySelector("[data-progression]");
  var boutonGo = form.querySelector("button[value=go]");
  var calme = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var DUREE = calme ? 0 : 220, SEUIL = 110;
  var SORTIES = {
    go: "translate(140%, -2rem) rotate(18deg)",
    pasgo: "translate(-140%, -2rem) rotate(-18deg)",
    attendre: "translateY(1rem) scale(.9)"
  };

  function dessus() { return pile.querySelector(".carte-tri:not(.sortante)"); }

  function dire(texte, ok) {
    message.textContent = texte || "";
    message.classList.toggle("ko", !ok);
    message.hidden = !texte;
  }

  function tampons(carte, go, non) {
    carte.querySelector(".tampon-tri.go").style.opacity = go;
    carte.querySelector(".tampon-tri.non").style.opacity = non;
  }

  function remettre(carte) {
    carte.style.transform = "";
    carte.style.opacity = "";
    tampons(carte, "", "");
  }

  function pluriel(n, mot) { return n + " " + mot + (n > 1 ? "s" : ""); }

  function finir() {
    var parts = [];
    if (bilan.go) parts.push(bilan.go + " oui");
    if (bilan.pasgo) parts.push(bilan.pasgo + " non");
    if (bilan.attendre) parts.push(bilan.attendre + " peut-être");
    var texte = pluriel(faits, "annonce") + " " + (faits > 1 ? "tranchées" : "tranchée") + " : " + parts.join(", ") + ".";
    if (bilan.go) texte += " Les dossiers des oui se préparent ; tu les retrouves ci-dessous.";
    window.location.replace("/?ensuite=1&ok=1&msg=" + encodeURIComponent(texte));
  }

  function avancer() {
    faits += 1;
    if (barre) {
      barre.querySelector("i").style.width = (100 * faits / total) + "%";
      barre.setAttribute("aria-valuenow", faits);
    }
    var suivante = dessus();
    if (!suivante) { finir(); return; }
    suivante.inert = false;
    form.elements.empreinte.value = suivante.dataset.empreinte;
    if (rang) rang.textContent = Math.min(faits + 1, total);
  }

  function trancher(type) {
    var carte = dessus();
    if (occupe || !carte || (type === "go" && boutonGo.disabled)) return;
    occupe = true;
    var donnees = new FormData(form);
    donnees.set("type", type);
    carte.classList.add("sortante");
    carte.style.transform = SORTIES[type];
    carte.style.opacity = "0";
    tampons(carte, type === "go" ? 1 : 0, type === "pasgo" ? 1 : 0);
    var envoi = fetch(form.action, {method: "POST", body: donnees, credentials: "same-origin",
                                    headers: {"Accept": "application/json"}})
      .then(function (r) { return r.ok ? r.json() : {ok: false, msg: "Le serveur a répondu " + r.status + " : rien n'est changé."}; })
      .catch(function () { return {ok: false, msg: "Le serveur ne répond pas : rien n'est changé."}; });
    var vol = new Promise(function (fini) { setTimeout(fini, DUREE); });
    Promise.all([envoi, vol]).then(function (x) {
      var rep = x[0];
      occupe = false;
      if (!rep.ok) { carte.classList.remove("sortante"); remettre(carte); dire(rep.msg, false); return; }
      bilan[type] += 1;
      dire(type === "go" ? rep.msg : "", true);
      carte.remove();
      avancer();
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    trancher(e.submitter ? e.submitter.value : "attendre");
  });

  document.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey || e.target.closest("input, textarea, select, [contenteditable]")) return;
    var type = {ArrowRight: "go", ArrowLeft: "pasgo", ArrowDown: "attendre"}[e.key];
    if (!type || !dessus()) return;
    e.preventDefault();
    trancher(type);
  });

  // Glisser : la carte suit le pointeur et penche ; le tampon Oui ou Non apparaît avec la distance.
  pile.addEventListener("pointerdown", function (e) {
    var carte = dessus();
    if (occupe || e.button !== 0 || !carte || !carte.contains(e.target)) return;
    if (e.target.closest("a, button, input, label, summary, .tags")) return;
    tire = {carte: carte, id: e.pointerId, x: e.clientX, y: e.clientY, dx: 0};
    carte.setPointerCapture(e.pointerId);
    carte.classList.add("tiree");
  });
  pile.addEventListener("pointermove", function (e) {
    if (!tire || e.pointerId !== tire.id) return;
    var dx = e.clientX - tire.x, dy = e.clientY - tire.y;
    tire.dx = dx;
    tire.carte.style.transform = "translate(" + dx + "px, " + dy * 0.2 + "px) rotate(" + dx / 20 + "deg)";
    var force = Math.min(Math.abs(dx) / SEUIL, 1);
    tampons(tire.carte, dx > 0 && !boutonGo.disabled ? force : 0, dx < 0 ? force : 0);
  });
  function lacher(e) {
    if (!tire || e.pointerId !== tire.id) return;
    var carte = tire.carte, dx = tire.dx;
    tire = null;
    carte.classList.remove("tiree");
    if (dx > SEUIL && !boutonGo.disabled) trancher("go");
    else if (dx < -SEUIL) trancher("pasgo");
    else remettre(carte);
  }
  pile.addEventListener("pointerup", lacher);
  pile.addEventListener("pointercancel", lacher);
})();
