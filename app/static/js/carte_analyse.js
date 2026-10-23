// Carte d'une analyse : classes du modèle, image du jour en couleurs réelles,
// rideau de comparaison et lecture d'un pixel au clic.
function activerCarteAnalyse(cfg) {
  const boite = document.getElementById("carte");
  if (typeof L === "undefined") {
    boite.classList.add("carte-indisponible");
    boite.innerHTML = "<p>Carte indisponible : connexion Internet requise pour le fond satellite.</p>";
    return;
  }
  const e = cfg.emprise;                                    // ouest, sud, est, nord
  const bornes = [[e[1], e[0]], [e[3], e[2]]];
  const carte = L.map("carte");
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18, attribution: "Fond © Esri · image du jour © Copernicus Sentinel-2" }).addTo(carte);

  // Image Sentinel-2 du jour (sous les classes)
  const rvb = L.imageOverlay(cfg.rvb, bornes, { opacity: 1 }).addTo(carte);
  rvb.on("error", function () {
    document.getElementById("voir-rvb").disabled = true;
    document.getElementById("voir-rvb").parentNode.title = "Image indisponible (archive introuvable)";
  });
  const classes = L.imageOverlay(cfg.carte, bornes, { opacity: 0.8 }).addTo(carte);
  classes.bringToFront();

  L.rectangle(bornes, { color: "#ffffff", weight: 1, fill: false, interactive: false }).addTo(carte);
  const v = cfg.validite;
  L.rectangle([[v[1], v[0]], [v[3], v[2]]],
    { color: "#f0a63a", weight: 2, fill: false, interactive: false }).addTo(carte)
    .bindTooltip("Zone de validité du modèle", { sticky: true });
  carte.fitBounds(bornes);

  // --- Couche couleurs réelles et opacité ---
  document.getElementById("voir-rvb").addEventListener("change", function () {
    if (this.checked) { rvb.addTo(carte); classes.bringToFront(); } else { carte.removeLayer(rvb); }
  });
  document.getElementById("opacite").addEventListener("input", function () {
    classes.setOpacity(this.value / 100);
  });

  // --- Rideau : les classes à gauche du trait, l'image en dessous à droite ---
  const curseur = document.getElementById("rideau");
  const trait = document.createElement("div");
  trait.className = "rideau-trait";
  carte.getContainer().appendChild(trait);
  function rideau() {
    const img = classes.getElement();
    if (!img) { return; }
    const largeur = carte.getSize().x;
    const x = largeur * curseur.value / 100;
    const gauche = carte.latLngToContainerPoint([e[3], e[0]]).x;
    const droite = carte.latLngToContainerPoint([e[1], e[2]]).x;
    const visible = Math.min(Math.max(x - gauche, 0), droite - gauche);
    const coupe = (droite - gauche) - visible;
    img.style.clipPath = coupe > 0 ? "inset(0 " + (100 * coupe / (droite - gauche)) + "% 0 0)" : "";
    trait.style.display = curseur.value < 100 ? "block" : "none";
    trait.style.left = x + "px";
  }
  curseur.addEventListener("input", rideau);
  carte.on("move zoom zoomend moveend resize", rideau);
  rideau();

  // --- Lecture d'un pixel ---
  const nb = function (v, d) { return v === null || v === undefined ? "—" : v.toFixed(d).replace(".", ","); };
  const echapper = function (t) {
    return String(t).replace(/[&<>"]/g, function (c) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; });
  };
  carte.on("click", function (ev) {
    const popup = L.popup({ maxWidth: 320 }).setLatLng(ev.latlng)
      .setContent("<div class='fiche-pixel'>Lecture du pixel dans l'archive…</div>").openOn(carte);
    const url = cfg.pixel + "?lat=" + ev.latlng.lat.toFixed(6) + "&lon=" + ev.latlng.lng.toFixed(6);
    fetch(url, { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) { popup.setContent("<div class='fiche-pixel'>" + echapper(d.message) + "</div>"); return; }
      let h = "<div class='fiche-pixel'><div class='classe'><i class='pastille' style='background:"
        + d.couleur + "'></i>" + echapper(d.classe) + "</div>";
      h += "<div style='color:#5f7067'>" + nb(d.lat, 5) + " N, " + nb(d.lon, 5) + " E · pixel (" + d.ligne + ", "
        + d.colonne + ") · " + cfg.date + "</div>";
      if (!d.dans_validite) { h += "<div style='color:#bf4337'>Hors zone de validité du modèle.</div>"; }
      if (d.valide === false) {
        h += "<div>Pixel masqué (nuage, ombre ou donnée manquante) : il n'a pas été classé.</div>";
      } else if (d.indices) {
        const i = d.indices;
        h += "<table><tr><td>NDVI (végétation)</td><td>" + nb(i.NDVI, 3) + "</td></tr>"
          + "<tr><td>NDWI (eau)</td><td>" + nb(i.NDWI, 3) + "</td></tr>"
          + "<tr><td>BSI (sol nu)</td><td>" + nb(i.BSI, 3) + "</td></tr>"
          + "<tr><td>NDRE (bord rouge)</td><td>" + nb(i.NDRE, 3) + "</td></tr></table>";
        h += "<details><summary>Réflectances des 10 bandes</summary><table>";
        Object.keys(d.bandes).forEach(function (b) {
          h += "<tr><td>" + b + "</td><td>" + nb(d.bandes[b], 4) + "</td></tr>";
        });
        h += "</table></details>";
        h += "<div style='color:#5f7067; margin-top:4px'>Valeurs recalculées avec la chaîne de "
          + "prétraitement du modèle (14 canaux d'entrée).</div>";
      } else {
        h += "<div>Archive introuvable : seule la classe est disponible.</div>";
      }
      popup.setContent(h + "</div>");
    }).catch(function () {
      popup.setContent("<div class='fiche-pixel'>Lecture impossible (serveur injoignable).</div>");
    });
  });
}
