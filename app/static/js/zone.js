// Choix de la zone d'analyse : dessin d'un rectangle sur la carte.
function activerZone(cfg) {
  // cfg : { emprise, validite, surfaceMax } — rectangles [ouest, sud, est, nord]
  const boite = document.getElementById("carte");
  const champ = document.getElementById("zone");
  const lancer = document.getElementById("lancer");
  const dessiner = document.getElementById("dessiner");
  const valSurface = document.getElementById("val-surface");
  const valDuree = document.getElementById("val-duree");
  const valHors = document.getElementById("val-hors");
  const alerte = document.getElementById("alerte-zone");

  if (typeof L === "undefined") {
    boite.classList.add("carte-indisponible");
    boite.innerHTML = "<p>Carte indisponible : connexion Internet requise.</p>";
    return;
  }
  const versBornes = function (r) { return [[r[1], r[0]], [r[3], r[2]]]; };
  const carte = L.map("carte", { scrollWheelZoom: true });
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 17, attribution: "Imagerie © Esri" }).addTo(carte);
  const cadreImage = L.rectangle(versBornes(cfg.emprise),
    { color: "#ffffff", weight: 2, dashArray: "6 6", fill: false, interactive: false }).addTo(carte);
  L.rectangle(versBornes(cfg.validite),
    { color: "#f0a63a", weight: 2, fillColor: "#f0a63a", fillOpacity: 0.06, interactive: false }).addTo(carte);
  carte.fitBounds(cadreImage.getBounds().pad(0.05));

  let selection = null;      // rectangle Leaflet de la zone choisie
  let enDessin = false;
  let depart = null;

  function surfaceKm2(r) {
    const lat = (r[1] + r[3]) / 2 * Math.PI / 180;
    return Math.max(0, (r[2] - r[0]) * 111.320 * Math.cos(lat)) * Math.max(0, (r[3] - r[1]) * 110.574);
  }
  function croiser(a, b) {
    const r = [Math.max(a[0], b[0]), Math.max(a[1], b[1]), Math.min(a[2], b[2]), Math.min(a[3], b[3])];
    return (r[2] > r[0] && r[3] > r[1]) ? r : null;
  }
  const infoRecadrage = document.getElementById("info-recadrage");
  function fixer(r) {
    // La zone est toujours limitée à l'image
    const demande = r;
    r = r ? croiser(r, cfg.emprise) : null;
    const recadre = !!(demande && r && demande.some(function (v, i) { return Math.abs(v - r[i]) > 1e-9; }));
    infoRecadrage.classList.toggle("d-none", !recadre);
    if (selection) { carte.removeLayer(selection); selection = null; }
    if (!r) {
      champ.value = ""; lancer.disabled = true;
      valSurface.textContent = "—"; valDuree.textContent = "—"; valHors.textContent = "—";
      alerte.classList.add("d-none");
      return;
    }
    selection = L.rectangle(versBornes(r),
      { color: "#35b67d", weight: 3, fillColor: "#35b67d", fillOpacity: 0.18 }).addTo(carte);
    const s = surfaceKm2(r);
    const dedans = croiser(r, cfg.validite);
    const hors = 100 * (1 - (dedans ? surfaceKm2(dedans) : 0) / s);
    valSurface.textContent = s.toFixed(1).replace(".", ",") + " km²";
    const secondes = s * 0.14;      // vitesse mesurée sur la machine de développement
    valDuree.textContent = secondes < 60 ? "moins d'une minute"
      : "environ " + Math.round(secondes / 60) + " min";
    valHors.textContent = hors.toFixed(0) + " %";
    champ.value = r.map(function (v) { return v.toFixed(6); }).join(",");

    const messages = [];
    let bloque = false;
    if (s > cfg.surfaceMax) {
      bloque = true;
      messages.push("Zone trop grande : le maximum est de " + cfg.surfaceMax + " km².");
    }
    if (hors >= 1) {
      messages.push(hors.toFixed(0) + " % de la zone se trouve hors de la zone de validité du modèle. "
        + "La carte sera produite partout, mais elle n'est fiable que dans le cadre orange ; "
        + "les surfaces seront aussi calculées séparément pour cette partie.");
    }
    alerte.textContent = messages.join(" ");
    alerte.classList.toggle("d-none", messages.length === 0);
    alerte.classList.toggle("danger", bloque);
    alerte.classList.toggle("warning", !bloque);
    lancer.disabled = bloque;
  }

  function modeDessin(actif) {
    enDessin = actif;
    dessiner.classList.toggle("actif", actif);
    dessiner.textContent = actif ? "Cliquez et faites glisser sur la carte…" : "Dessiner une zone";
    boite.style.cursor = actif ? "crosshair" : "";
    if (actif) { carte.dragging.disable(); } else { carte.dragging.enable(); }
  }
  dessiner.addEventListener("click", function () { modeDessin(!enDessin); });

  let apercu = null;
  carte.on("mousedown", function (e) {
    if (!enDessin) { return; }
    depart = e.latlng;
  });
  carte.on("mousemove", function (e) {
    if (!enDessin || !depart) { return; }
    if (apercu) { carte.removeLayer(apercu); }
    apercu = L.rectangle(L.latLngBounds(depart, e.latlng),
      { color: "#35b67d", weight: 2, dashArray: "4 4", fill: false, interactive: false }).addTo(carte);
  });
  carte.on("mouseup", function (e) {
    if (!enDessin || !depart) { return; }
    const b = L.latLngBounds(depart, e.latlng);
    depart = null;
    if (apercu) { carte.removeLayer(apercu); apercu = null; }
    modeDessin(false);
    fixer([b.getWest(), b.getSouth(), b.getEast(), b.getNorth()]);
  });

  document.getElementById("zone-validite").addEventListener("click", function () {
    const r = croiser(cfg.validite, cfg.emprise);
    fixer(r);
    if (selection) { carte.fitBounds(selection.getBounds().pad(0.15)); }
  });
  document.getElementById("toute-image").addEventListener("click", function () {
    fixer(cfg.emprise);
    if (selection) { carte.fitBounds(selection.getBounds().pad(0.05)); }
  });
  document.getElementById("effacer").addEventListener("click", function () { fixer(null); });
  fixer(null);
}
