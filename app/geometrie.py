"""Petits calculs sur des rectangles (ouest, sud, est, nord) en degrés."""
import math


def surface_km2(o, s, e, n):
    """Surface approchée d'un rectangle géographique."""
    lat = math.radians((s + n) / 2)
    return max(0.0, (e - o) * 111.320 * math.cos(lat)) * max(0.0, (n - s) * 110.574)


def intersection(a, b):
    """Partie commune de deux rectangles, ou None."""
    o, s, e, n = max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])
    return (o, s, e, n) if e > o and n > s else None


def lire_zone(texte):
    """Convertit "ouest,sud,est,nord" en quatre nombres valides."""
    try:
        o, s, e, n = [float(v) for v in texte.split(",")]
    except (ValueError, AttributeError):
        return None
    if not (-180 <= o < e <= 180 and -90 <= s < n <= 90):
        return None
    return (o, s, e, n)
