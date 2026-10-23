"""
Petits graphiques en SVG produits côté serveur : ils s'affichent sans
connexion Internet (aucune bibliothèque externe) et suivent le thème clair
ou sombre grâce aux variables CSS.
"""
import math

from markupsafe import Markup, escape

LARGEUR, HAUTEUR = 640, 250
MARGE_G, MARGE_D, MARGE_H, MARGE_B = 52, 14, 14, 30


def _pas_rond(etendue, nb=4):
    """Pas « rond » (1, 2, 2,5 ou 5 x 10^n) pour environ nb graduations."""
    brut = etendue / nb if etendue > 0 else 1
    puissance = 10 ** math.floor(math.log10(brut))
    for m in (1, 2, 2.5, 5, 10):
        if m * puissance >= brut:
            return m * puissance
    return 10 * puissance


def _fr(v, d):
    texte = f"{v:,.{d}f}".replace(",", " ").replace(".", ",")
    return texte


def courbes(series, etiquettes, unite="", decimales=0, depuis_zero=True, largeur=LARGEUR,
            hauteur=None, aire=False):
    """series : liste de dict {nom, couleur, valeurs, pointille (facultatif)}.
    etiquettes : libellés de l'axe horizontal (un par valeur).
    Renvoie un SVG prêt à insérer dans un gabarit."""
    toutes = [v for s in series for v in s["valeurs"] if v is not None]
    if not toutes or len(etiquettes) < 2:
        return Markup("")
    bas, haut = min(toutes), max(toutes)
    if depuis_zero:
        bas = min(0.0, bas)
    if haut == bas:
        haut, bas = haut + 1, bas - (0 if depuis_zero else 1)
    pas = _pas_rond(haut - bas)
    y0 = math.floor(bas / pas) * pas
    y1 = math.ceil(haut / pas) * pas
    dec_axe = max(0, -int(math.floor(math.log10(pas)))) if pas < 1 else 0

    HAUTEUR_G = hauteur or HAUTEUR
    largeur_utile = largeur - MARGE_G - MARGE_D
    hauteur_utile = HAUTEUR_G - MARGE_H - MARGE_B
    n = len(etiquettes)
    x = lambda i: MARGE_G + largeur_utile * i / (n - 1)
    y = lambda v: MARGE_H + hauteur_utile * (1 - (v - y0) / (y1 - y0))

    morceaux = [f'<svg class="graphique-svg" viewBox="0 0 {largeur} {HAUTEUR_G}" '
                f'role="img">']
    # Graduations horizontales
    v = y0
    while v <= y1 + pas / 1000:
        yy = y(v)
        morceaux.append(f'<line class="grille" x1="{MARGE_G}" x2="{largeur - MARGE_D}" '
                        f'y1="{yy:.1f}" y2="{yy:.1f}"/>')
        morceaux.append(f'<text class="axe" x="{MARGE_G - 8}" y="{yy + 4:.1f}" '
                        f'text-anchor="end">{_fr(v, dec_axe)}</text>')
        v += pas
    # Étiquettes horizontales (une sur deux si elles sont nombreuses)
    saut = 1 if n * 52 <= largeur_utile else 2
    for i, etiquette in enumerate(etiquettes):
        if i % saut == 0 or i == n - 1:
            morceaux.append(f'<text class="axe" x="{x(i):.1f}" y="{HAUTEUR_G - 8}" '
                            f'text-anchor="middle">{escape(etiquette)}</text>')
    # Courbes
    for s in series:
        points = [(x(i), y(val), val, i) for i, val in enumerate(s["valeurs"]) if val is not None]
        if not points:
            continue
        chemin = " ".join(f"{px:.1f},{py:.1f}" for px, py, _, _ in points)
        tirets = ' stroke-dasharray="6 4"' if s.get("pointille") else ""
        if aire and len(points) > 1:
            base = y(y0)
            morceaux.append(f'<polygon points="{points[0][0]:.1f},{base:.1f} {chemin} '
                            f'{points[-1][0]:.1f},{base:.1f}" fill="{s["couleur"]}" opacity="0.10"/>')
        morceaux.append(f'<polyline class="courbe" points="{chemin}" stroke="{s["couleur"]}"{tirets}/>')
        for px, py, val, i in points:
            morceaux.append(
                f'<circle class="point" cx="{px:.1f}" cy="{py:.1f}" r="3.4" fill="{s["couleur"]}">'
                f'<title>{escape(s["nom"])} · {escape(etiquettes[i])} : {_fr(val, decimales)}'
                f'{" " + unite if unite else ""}</title></circle>')
    morceaux.append("</svg>")
    return Markup("".join(morceaux))


def anneau(parts, texte, sous_texte="", taille=200):
    """Anneau de répartition. parts : liste de (libellé, valeur, couleur, détail).
    Un écart de 2 px sépare les secteurs ; chaque secteur a son info-bulle."""
    total = sum(v for _, v, _, _ in parts if v > 0)
    if total <= 0:
        return Markup("")
    c = taille / 2
    r_ext, r_int = c - 4, c * 0.58
    morceaux = [f'<svg class="anneau-svg" viewBox="0 0 {taille} {taille}" role="img">']
    angle = -math.pi / 2
    ecart = 0.012 if sum(1 for p in parts if p[1] > 0) > 1 else 0
    for libelle, valeur, couleur, detail in parts:
        if valeur <= 0:
            continue
        balayage = 2 * math.pi * valeur / total
        a0, a1 = angle + ecart, angle + balayage - ecart
        if a1 <= a0:
            angle += balayage
            continue
        grand = 1 if a1 - a0 > math.pi else 0
        if balayage >= 2 * math.pi - 1e-6:          # un seul secteur : cercle complet
            morceaux.append(f'<circle cx="{c}" cy="{c}" r="{(r_ext + r_int) / 2:.1f}" fill="none" '
                            f'stroke="{couleur}" stroke-width="{r_ext - r_int:.1f}">'
                            f'<title>{escape(libelle)} : {escape(detail)}</title></circle>')
        else:
            pts = [(c + r * math.cos(a), c + r * math.sin(a)) for r, a in
                   ((r_ext, a0), (r_ext, a1), (r_int, a1), (r_int, a0))]
            morceaux.append(
                f'<path class="secteur" fill="{couleur}" d="M{pts[0][0]:.2f},{pts[0][1]:.2f} '
                f'A{r_ext},{r_ext} 0 {grand} 1 {pts[1][0]:.2f},{pts[1][1]:.2f} '
                f'L{pts[2][0]:.2f},{pts[2][1]:.2f} '
                f'A{r_int:.2f},{r_int:.2f} 0 {grand} 0 {pts[3][0]:.2f},{pts[3][1]:.2f} Z">'
                f'<title>{escape(libelle)} : {escape(detail)}</title></path>')
        angle += balayage
    morceaux.append(f'<text class="anneau-valeur" x="{c}" y="{c + 2}" text-anchor="middle">{escape(texte)}</text>')
    if sous_texte:
        morceaux.append(f'<text class="anneau-sous" x="{c}" y="{c + 22}" text-anchor="middle">'
                        f'{escape(sous_texte)}</text>')
    morceaux.append("</svg>")
    return Markup("".join(morceaux))


def barres_empilees(categories, etiquettes, unite="", decimales=0, largeur=LARGEUR, hauteur=260):
    """Barres empilées. categories : liste de dict {nom, couleur, valeurs}
    (une valeur par étiquette), empilées dans l'ordre de la liste, du bas vers le haut."""
    n = len(etiquettes)
    totaux = [sum(cat["valeurs"][i] or 0 for cat in categories) for i in range(n)]
    if not n or max(totaux) <= 0:
        return Markup("")
    pas = _pas_rond(max(totaux))
    y1 = math.ceil(max(totaux) / pas) * pas
    largeur_utile = largeur - MARGE_G - MARGE_D
    hauteur_utile = hauteur - MARGE_H - MARGE_B
    y = lambda v: MARGE_H + hauteur_utile * (1 - v / y1)
    colonne = largeur_utile / n
    epaisseur = min(34, colonne * 0.58)
    morceaux = [f'<svg class="graphique-svg" viewBox="0 0 {largeur} {hauteur}" role="img">']
    v = 0.0
    while v <= y1 + pas / 1000:
        yy = y(v)
        morceaux.append(f'<line class="grille" x1="{MARGE_G}" x2="{largeur - MARGE_D}" '
                        f'y1="{yy:.1f}" y2="{yy:.1f}"/>')
        morceaux.append(f'<text class="axe" x="{MARGE_G - 8}" y="{yy + 4:.1f}" '
                        f'text-anchor="end">{_fr(v, 0)}</text>')
        v += pas
    for i, etiquette in enumerate(etiquettes):
        x = MARGE_G + colonne * i + (colonne - epaisseur) / 2
        cumul = 0.0
        for cat in categories:
            valeur = cat["valeurs"][i] or 0
            if valeur <= 0:
                continue
            haut, bas = y(cumul + valeur), y(cumul)
            h = max(bas - haut - 2, 0.6)               # écart de 2 px entre segments
            morceaux.append(
                f'<rect class="segment" x="{x:.1f}" y="{haut:.1f}" width="{epaisseur:.1f}" '
                f'height="{h:.1f}" rx="2" fill="{cat["couleur"]}"><title>{escape(cat["nom"])} · '
                f'{escape(etiquette)} : {_fr(valeur, decimales)}{" " + unite if unite else ""}'
                f'</title></rect>')
            cumul += valeur
        morceaux.append(f'<text class="axe" x="{x + epaisseur / 2:.1f}" y="{hauteur - 8}" '
                        f'text-anchor="middle">{escape(etiquette)}</text>')
    morceaux.append("</svg>")
    return Markup("".join(morceaux))
