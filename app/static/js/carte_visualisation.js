// Page « Cartes & Visualisation » : couches, transparence, lecture d'un pixel,
// mesure de distance et comparaison de deux années par un rideau.
function activerVisualisation(cfg) {
  const boite = document.getElementById("carte");
  const aide = document.getElementById("aide-outil");
  const e = cfg.emprise, bornes = [[e[1], e[0]], [e[3], e[2]]];
  const transparence = document.getElementById("transparence");
  const opacite = function () { return 1 - transparence.value / 100; };

  if (typeof L === "undefined") {
    boite.classList.add("carte-hors-ligne");
    boite.innerHTML = "<img class='fond' src='" + cfg.rvb + "' alt=''><img class='calque' src='" + cfg.classes + "' alt=''>"
      + "<span class='etiquette-hors-ligne'>Sans connexion : carte interactive indisponible</span>";
    const calque = boite.querySelector(".calque"), fond = boite.querySelector(".fond");
    transparence.addEventListener("input", function () { calque.style.opacity = opacite(); });
    document.getElementById("c-classes").addEventListener("change", function () { calque.style.display = this.checked ? "" : "none"; });
    document.getElementById("c-rvb").addEventListener("change", function () { fond.style.display = this.checked ? "" : "none"; });
    calque.style.opacity = opacite();
    return;
  }
  const carte = L.map("carte");
  const fond = L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { maxZoom: 18, attribution: "Fond © Esri · Sentinel-2 © Copernicus" }).addTo(carte);
  L.control.scale({ imperial: false }).addTo(carte);
  const rvb = L.imageOverlay(cfg.rvb, bornes).addTo(carte);
  const ndvi = cfg.ndvi ? L.imageOverlay(cfg.ndvi, bornes, { opacity: opacite() }) : null;
  const autre = cfg.autre ? L.imageOverlay(cfg.autre, bornes, { opacity: opacite() }) : null;
  const classes = L.imageOverlay(cfg.classes, bornes, { opacity: opacite() }).addTo(carte);
  const v = cfg.validite;
  const limites = L.layerGroup([
    L.rectangle([[v[1], v[0]], [v[3], v[2]]], { color: "#f0a63a", weight: 2, fill: false, interactive: false }),
    L.rectangle(bornes, { color: "#ffffff", weight: 1, fill: false, interactive: false })]).addTo(carte);
  carte.fitBounds(bornes);

  function basculer(id, couche) {
    document.getElementById(id).addEventListener("change", function () {
      if (!couche) { return; }
      if (this.checked) { couche.addTo(carte); } else { carte.removeLayer(couche); }
      classes.bringToFront();
      if (id === "c-ndvi") { document.getElementById("legende-ndvi").classList.toggle("d-none", !this.checked); }
    });
  }
  basculer("c-rvb", rvb); basculer("c-classes", classes); basculer("c-ndvi", ndvi);
  basculer("c-limites", limites); basculer("c-fond", fond);
  transparence.addEventListener("input", function () {
    [classes, ndvi, autre].forEach(function (c) { if (c) { c.setOpacity(opacite()); } });
  });

  // --- Outils ---
  let outil = "info";
  const boutons = { info: "outil-info", mesure: "outil-mesure", comparer: "outil-comparer" };
  const aides = {
    info: "Cliquez sur la carte pour lire la classe et les indices d'un pixel.",
    mesure: "Cliquez des points successifs pour mesurer une distance ; double-cliquez pour terminer.",
    comparer: "Gauche du trait : " + cfg.date + " · droite : " + cfg.dateAutre + ". Déplacez la souris sur la carte."
  };
  let points = [], trace = null;
  const trait = document.createElement("div");
  trait.className = "rideau-trait"; trait.style.display = "none";
  carte.getContainer().appendChild(trait);

  function choisir(nom) {
    outil = nom;
    Object.keys(boutons).forEach(function (k) { document.getElementById(boutons[k]).classList.toggle("actif", k === nom); });
    aide.textContent = aides[nom];
    carte.doubleClickZoom[nom === "mesure" ? "disable" : "enable"]();
    if (nom !== "mesure" && trace) { carte.removeLayer(trace); trace = null; points = []; }
    document.getElementById("ligne-mesure").classList.toggle("d-none", nom !== "mesure");
    if (autre) {
      if (nom === "comparer") { autre.addTo(carte); autre.bringToFront(); classes.bringToFront(); rideau(carte.getSize().x / 2); }
      else { carte.removeLayer(autre); classes.getElement().style.clipPath = ""; trait.style.display = "none"; }
    }
  }
  Object.keys(boutons).forEach(function (k) {
    document.getElementById(boutons[k]).addEventListener("click", function () { choisir(k); });
  });

  // Rideau : la carte affichée à gauche du trait, l'autre année à droite
  function rideau(x) {
    const img = classes.getElement();
    const g = carte.latLngToContainerPoint([e[3], e[0]]).x, d = carte.latLngToContainerPoint([e[1], e[2]]).x;
    const coupe = Math.min(Math.max(d - x, 0), d - g);
    img.style.clipPath = "inset(0 " + (100 * coupe / (d - g)) + "% 0 0)";
    trait.style.display = "block"; trait.style.left = x + "px";
  }
  carte.on("mousemove", function (ev) { if (outil === "comparer" && autre) { rideau(ev.containerPoint.x); } });

  carte.on("click", function (ev) {
    if (outil === "mesure") {
      points.push(ev.latlng);
      if (trace) { carte.removeLayer(trace); }
      trace = L.polyline(points, { color: "#ffd400", weight: 3, dashArray: "6 4" }).addTo(carte);
      let total = 0;
      for (let i = 1; i < points.length; i++) { total += carte.distance(points[i - 1], points[i]); }
      document.getElementById("val-mesure").textContent = total >= 1000
        ? (total / 1000).toFixed(2).replace(".", ",") + " km" : Math.round(total) + " m";
      return;
    }
    if (outil !== "info") { return; }
    const popup = L.popup().setLatLng(ev.latlng).setContent("Lecture du pixel…").openOn(carte);
    fetch(cfg.pixel + "?lat=" + ev.latlng.lat.toFixed(6) + "&lon=" + ev.latlng.lng.toFixed(6), { credentials: "same-origin" })
      .then(function (r) { return r.json(); }).then(function (d) {
        if (!d.ok) { popup.setContent(d.message); return; }
        let h = "<b>" + d.classe + "</b><br>" + cfg.date;
        if (d.indices) {
          h += "<br>NDVI " + d.indices.NDVI.toFixed(3) + " · NDWI " + d.indices.NDWI.toFixed(3) + " · BSI " + d.indices.BSI.toFixed(3);
        } else if (d.valide === false) { h += "<br>Pixel masqué (nuage)"; }
        popup.setContent(h.replace(/(\d)\.(\d)/g, "$1,$2"));
      }).catch(function () { popup.setContent("Lecture impossible."); });
  });
  carte.on("dblclick", function () { if (outil === "mesure") { points = []; } });
}
