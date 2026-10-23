// Carte d'une détection : pertes et gains (règle du mémoire), zones à surveiller
// et image en couleurs réelles, avec un affichage de secours hors connexion.
function activerCarteDetection(cfg) {
  const boite = document.getElementById("carte");
  const e = cfg.emprise;                                     // ouest, sud, est, nord
  const opacite = document.getElementById("opacite");
  const caseDeg = document.getElementById("voir-degradation");
  const caseZones = document.getElementById("voir-zones");
  const caseRvb = document.getElementById("voir-rvb");
  const lienZone = function (n) { return cfg.urlZone.replace(/\/0$/, "/" + n); };

  if (typeof L === "undefined") {
    // Sans Internet : les images seules, superposées, et les zones dessinées en SVG
    boite.classList.add("carte-hors-ligne", "exacte");
    const largeur = e[2] - e[0], hauteur = e[3] - e[1];
    // Même proportion que la zone (en km), pour que les zones tombent au bon endroit
    const ratio = largeur * Math.cos((e[1] + e[3]) / 2 * Math.PI / 180) / hauteur;
    boite.style.height = "auto";
    boite.style.aspectRatio = ratio.toFixed(4);
    let svg = "<svg class='calque-zones' viewBox='0 0 1000 1000' preserveAspectRatio='none'>";
    cfg.zones.forEach(function (contour, i) {
      const pts = contour.map(function (p) {
        return (1000 * (p[0] - e[0]) / largeur).toFixed(1) + "," + (1000 * (e[3] - p[1]) / hauteur).toFixed(1);
      }).join(" ");
      svg += "<a href='" + lienZone(i + 1) + "'><polygon points='" + pts + "'/></a>";
    });
    svg += "</svg>";
    boite.innerHTML = (cfg.rvb ? "<img class='fond' src='" + cfg.rvb + "' alt=''>" : "")
      + "<img class='calque' src='" + cfg.synthese + "' alt=''>" + svg
      + "<span class='etiquette-hors-ligne'>Sans connexion : fond de carte indisponible</span>";
    const fond = boite.querySelector(".fond"), calque = boite.querySelector(".calque"),
          zones = boite.querySelector(".calque-zones");
    opacite.addEventListener("input", function () { calque.style.opacity = opacite.value / 100; });
    caseDeg.addEventListener("change", function () { calque.style.display = caseDeg.checked ? "" : "none"; });
    caseZones.addEventListener("change", function () { zones.style.display = caseZones.checked ? "" : "none"; });
    caseRvb.addEventListener("change", function () { if (fond) { fond.style.display = caseRvb.checked ? "" : "none"; } });
    return;
  }

  const bornes = [[e[1], e[0]], [e[3], e[2]]];
  const carte = L.map("carte", { scrollWheelZoom: true });
  L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18, attribution: "Fond © Esri · Sentinel-2 © Copernicus" }).addTo(carte);
  L.control.scale({ imperial: false }).addTo(carte);
  const rvb = cfg.rvb ? L.imageOverlay(cfg.rvb, bornes).addTo(carte) : null;
  const synthese = L.imageOverlay(cfg.synthese, bornes, { opacity: 0.9 }).addTo(carte);
  const zones = L.layerGroup(cfg.zones.map(function (contour, i) {
    return L.polygon(contour.map(function (p) { return [p[1], p[0]]; }),
      { color: "#e0403a", weight: 2, fillOpacity: 0.05 })
      .bindTooltip("Zone " + (i + 1) + " · cliquer pour vérifier", { sticky: true })
      .on("click", function () { window.location.href = lienZone(i + 1); });
  })).addTo(carte);
  const v = cfg.validite;
  L.rectangle([[v[1], v[0]], [v[3], v[2]]], { color: "#f0a63a", weight: 2, fill: false, interactive: false }).addTo(carte);
  L.rectangle(bornes, { color: "#ffffff", weight: 1, fill: false, interactive: false }).addTo(carte);
  carte.fitBounds(bornes);

  opacite.addEventListener("input", function () { synthese.setOpacity(opacite.value / 100); });
  caseDeg.addEventListener("change", function () { if (caseDeg.checked) { synthese.addTo(carte); } else { carte.removeLayer(synthese); } });
  caseZones.addEventListener("change", function () { if (caseZones.checked) { zones.addTo(carte); } else { carte.removeLayer(zones); } });
  caseRvb.addEventListener("change", function () {
    if (!rvb) { return; }
    if (caseRvb.checked) { rvb.addTo(carte); synthese.bringToFront(); } else { carte.removeLayer(rvb); }
  });
  // Survol d'une zone dans la liste : la zone est mise en évidence sur la carte
  document.querySelectorAll(".ligne-zone").forEach(function (lien) {
    const n = Number(lien.getAttribute("data-numero")) - 1;
    const couche = zones.getLayers()[n];
    if (!couche) { return; }
    lien.addEventListener("mouseenter", function () { couche.setStyle({ weight: 4, fillOpacity: 0.25 }); });
    lien.addEventListener("mouseleave", function () { couche.setStyle({ weight: 2, fillOpacity: 0.05 }); });
  });
}
