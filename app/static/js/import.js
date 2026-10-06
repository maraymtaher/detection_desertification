// Envoi d'une archive avec glisser-déposer et barre de progression.
// Utilisé par le tableau de bord et par la page « Nouvelle analyse ».
function activerImport(urlEnvoi) {
  const formulaire = document.getElementById("formulaire");
  const champ = document.getElementById("archive");
  const depot = document.getElementById("depot");
  const bouton = document.getElementById("bouton");
  const suivi = document.getElementById("suivi");
  const barre = document.getElementById("barre");
  const etat = document.getElementById("etat");
  const part = document.getElementById("part");
  const erreur = document.getElementById("erreur");

  function afficherChoix() {
    const f = champ.files[0];
    document.getElementById("choisi").textContent =
      f ? f.name + "  ·  " + (f.size / 1048576).toFixed(0) + " Mo" : "";
    bouton.disabled = !f;
  }
  champ.addEventListener("change", afficherChoix);
  ["dragenter", "dragover"].forEach(function (t) {
    depot.addEventListener(t, function (e) { e.preventDefault(); depot.classList.add("survol"); });
  });
  ["dragleave", "drop"].forEach(function (t) {
    depot.addEventListener(t, function (e) { e.preventDefault(); depot.classList.remove("survol"); });
  });
  depot.addEventListener("drop", function (e) {
    if (e.dataTransfer.files.length) { champ.files = e.dataTransfer.files; afficherChoix(); }
  });

  function echec(message) {
    suivi.classList.add("d-none");
    erreur.textContent = message;
    erreur.classList.remove("d-none");
    bouton.disabled = false;
  }
  formulaire.addEventListener("submit", function (evenement) {
    evenement.preventDefault();
    erreur.classList.add("d-none");
    suivi.classList.remove("d-none");
    bouton.disabled = true;
    const requete = new XMLHttpRequest();
    requete.open("POST", urlEnvoi);
    requete.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    requete.upload.addEventListener("progress", function (e) {
      if (e.lengthComputable) {
        const p = Math.round(100 * e.loaded / e.total);
        barre.style.width = p + "%";
        part.textContent = p + " %";
        etat.textContent = p < 100 ? "Envoi en cours…" : "Vérification du produit…";
      }
    });
    requete.addEventListener("load", function () {
      let reponse = null;
      try { reponse = JSON.parse(requete.responseText); } catch (e) {}
      if (reponse && reponse.ok && reponse.url) { window.location.href = reponse.url; }
      else if (requete.status === 413) { echec("Fichier trop volumineux (maximum 1,5 Go)."); }
      else { echec(reponse ? reponse.message : "Erreur du serveur (code " + requete.status + ")."); }
    });
    requete.addEventListener("error", function () { echec("Connexion au serveur interrompue."); });
    requete.send(new FormData(formulaire));
  });
}
