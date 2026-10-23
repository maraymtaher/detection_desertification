// Choix du thème clair ou sombre.
// Sans choix enregistré, le système suit le réglage de l'ordinateur.
(function () {
  let choix = null;
  try { choix = localStorage.getItem("theme"); } catch (e) {}
  if (choix !== "clair" && choix !== "sombre") {
    choix = (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches)
      ? "sombre" : "clair";
  }
  if (choix === "sombre") { document.documentElement.setAttribute("data-theme", "sombre"); }
})();

function themeActuel() {
  return document.documentElement.getAttribute("data-theme") === "sombre" ? "sombre" : "clair";
}

function marquerTheme() {
  const actuel = themeActuel();
  document.querySelectorAll("[data-choix-theme]").forEach(function (bouton) {
    const actif = bouton.getAttribute("data-choix-theme") === actuel;
    bouton.classList.toggle("actif", actif);
    bouton.setAttribute("aria-pressed", actif ? "true" : "false");
  });
}

function choisirTheme(theme) {
  if (theme === "sombre") { document.documentElement.setAttribute("data-theme", "sombre"); }
  else { document.documentElement.removeAttribute("data-theme"); }
  try { localStorage.setItem("theme", theme); } catch (e) {}
  marquerTheme();
}

document.addEventListener("DOMContentLoaded", marquerTheme);

// Menu latéral réduit (icônes seules) : choix gardé d'une page à l'autre.
(function () {
  let replie = null;
  try { replie = localStorage.getItem("menu"); } catch (e) {}
  if (replie === "replie" && window.innerWidth > 860) { document.documentElement.setAttribute("data-menu", "replie"); }
})();

function basculerMenu() {
  const racine = document.documentElement;
  if (window.matchMedia("(max-width: 860px)").matches) {
    const ouvert = racine.getAttribute("data-menu-mobile") === "ouvert";
    if (ouvert) { racine.removeAttribute("data-menu-mobile"); } else { racine.setAttribute("data-menu-mobile", "ouvert"); }
    return;
  }
  const replie = racine.getAttribute("data-menu") !== "replie";
  if (replie) { racine.setAttribute("data-menu", "replie"); } else { racine.removeAttribute("data-menu"); }
  try { localStorage.setItem("menu", replie ? "replie" : "ouvert"); } catch (e) {}
  // Les cartes Leaflet recalculent leur taille sur l'événement « resize »
  setTimeout(function () { window.dispatchEvent(new Event("resize")); }, 50);
}

document.addEventListener("DOMContentLoaded", function () {
  // Info-bulle avec le nom de la page, utile quand seules les icônes restent
  document.querySelectorAll(".menu a").forEach(function (lien) {
    const texte = lien.querySelector(".texte-menu");
    if (texte && !lien.title) { lien.title = texte.textContent.trim(); }
  });
});
