// Carte de situation : zone d'entraînement du modèle et tuiles Sentinel-2.
// Emprises des tuiles : valeurs approchées, calculées depuis la grille UTM 33N.
const TUILES = {
  "33PVQ": [[12.574, 14.075], [13.569, 15.090]],
  "33PVR": [[13.478, 14.072], [14.473, 15.091]]
};

function carteZone(idElement, emprise, options) {
  options = options || {};
  const boite = document.getElementById(idElement);
  if (typeof L === "undefined") {
    boite.classList.add("carte-indisponible");
    boite.innerHTML = "<p>Carte indisponible : connexion Internet requise pour le fond satellite.</p>";
    return null;
  }
  const carte = L.map(idElement, {
    zoomControl: options.interactive !== false,
    dragging: options.interactive !== false,
    scrollWheelZoom: false,
    doubleClickZoom: options.interactive !== false,
    attributionControl: true
  });
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 17, attribution: "Imagerie © Esri" }).addTo(carte);

  // Tuiles Sentinel-2 couvertes
  (options.tuiles || Object.keys(TUILES)).forEach(function (nom) {
    if (!TUILES[nom]) { return; }
    const actif = options.tuileActive === nom;
    L.rectangle(TUILES[nom], {
      color: actif ? "#ffffff" : "#57c7d4", weight: actif ? 2 : 1,
      dashArray: "4 6", fill: false
    }).addTo(carte).bindTooltip("Tuile " + nom, { sticky: true });
  });

  // Zone d'entraînement : ouest, sud, est, nord
  const zone = L.rectangle(
    [[emprise[1], emprise[0]], [emprise[3], emprise[2]]],
    { color: "#f0a63a", weight: 2, fillColor: "#f0a63a", fillOpacity: 0.08 }
  ).addTo(carte).bindTooltip("Zone de validité du modèle", { sticky: true });

  carte.fitBounds(zone.getBounds().pad(options.marge === undefined ? 0.35 : options.marge));
  return carte;
}
