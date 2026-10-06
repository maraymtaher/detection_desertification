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
