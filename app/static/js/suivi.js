// Suivi en direct d'une analyse : interroge le serveur toutes les 2 secondes.
function suivreAnalyse(urlEtat) {
  const barre = document.getElementById("barre");
  const part = document.getElementById("part");
  const message = document.getElementById("message");
  const etapes = document.querySelectorAll("[data-etape]");

  function marquer(statut, progression) {
    // Étapes : 1 import, 2 vérification (faites), 3 et 4 pendant le calcul, 5 résultats
    let courante = 2;
    if (statut === "en_cours") { courante = progression >= 96 ? 5 : (progression >= 2 ? 4 : 3); }
    etapes.forEach(function (e) {
      const n = Number(e.getAttribute("data-etape"));
      e.classList.toggle("faite", n < courante || statut === "terminee");
      e.classList.toggle("courante", n === courante && statut === "en_cours");
    });
  }
  function interroger() {
    fetch(urlEtat, { headers: { "Accept": "application/json" } })
      .then(function (r) { return r.json(); })
      .then(function (e) {
        if (e.statut === "terminee" || e.statut === "echouee") { window.location.reload(); return; }
        barre.style.width = e.progression + "%";
        part.textContent = e.progression + " %";
        message.textContent = e.statut === "en_attente"
          ? (e.devant > 0 ? "En file d'attente : " + e.devant + " analyse(s) avant celle-ci."
                          : "Démarrage imminent…")
          : e.message;
        marquer(e.statut, e.progression);
        setTimeout(interroger, 2000);
      })
      .catch(function () { message.textContent = "Serveur injoignable, nouvel essai…";
                           setTimeout(interroger, 5000); });
  }
  interroger();
}
