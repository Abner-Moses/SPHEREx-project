"""Build the compact sky catalogue the Sky view loads (frontend/public/data/hyg.json, constellations.json).

Sources (from the teammate's Stellarium-style explorer, spherex-code/spherex/public):
  stars.json                 HYG Database v4.1 (David Nash / Astronexus), CC BY-SA 4.0, magnitude <= 8
  constellations.json        d3-celestial constellation lines (Olaf Frohn), BSD-3-Clause
  constellation-names.json   d3-celestial constellation names and label positions, BSD-3-Clause

Usage: python tools/build_sky_catalogue.py <path to spherex-code/spherex/public>
"""
import json
import math
import re
import sys
from pathlib import Path

GREEK = {'Alp': 'alf', 'Bet': 'bet', 'Gam': 'gam', 'Del': 'del', 'Eps': 'eps', 'Zet': 'zet', 'Eta': 'eta', 'The': 'tet', 'Iot': 'iot',
         'Kap': 'kap', 'Lam': 'lam', 'Mu': 'mu.', 'Nu': 'nu.', 'Xi': 'ksi', 'Omi': 'omi', 'Pi': 'pi.', 'Rho': 'rho', 'Sig': 'sig',
         'Tau': 'tau', 'Ups': 'ups', 'Phi': 'phi', 'Chi': 'chi', 'Psi': 'psi', 'Ome': 'ome'}
BAYER = re.compile(r'^([A-Z][a-z]{1,2})\s*(\d?)\s*([A-Z][a-z]{2})$')


def simbad_style(name):
    """HYG 'Kap1Scl' -> '* kap1 Scl' (the form the rest of the app and SIMBAD understand); proper names stay."""
    m = BAYER.match(name.strip())
    if m and m.group(1) in GREEK:
        return f"* {GREEK[m.group(1)]}{m.group(2)} {m.group(3)}"
    return name.strip()


def main(src):
    src = Path(src)
    out = Path(__file__).resolve().parents[1] / 'frontend' / 'public' / 'data'
    stars = json.loads((src / 'stars.json').read_text(encoding='utf-8'))
    stars.sort(key=lambda s: s['mag'])  # brightest first: the engine can stop at the magnitude limit
    cols = {k: [] for k in ('ra', 'dec', 'mag', 'bv', 'pmra', 'pmdec', 'dist', 'hip')}
    names = {}
    for i, s in enumerate(stars):
        cols['ra'].append(round(math.degrees(s['ra']) % 360, 5))
        cols['dec'].append(round(math.degrees(s['dec']), 5))
        cols['mag'].append(round(s['mag'], 2))
        cols['bv'].append(round(s['bv'], 2) if s.get('bv') is not None else 0.65)
        cols['pmra'].append(round(s.get('pmra') or 0, 2))
        cols['pmdec'].append(round(s.get('pmdec') or 0, 2))
        cols['dist'].append(round(s['dist'], 2) if s.get('dist') and s['dist'] < 100000 else 0)
        cols['hip'].append(int(s['hip']) if str(s.get('hip', '')).isdigit() else 0)
        n = s['name']
        if not n.startswith(('HIP', 'HD', 'Gl', 'GJ')) and not n[:1].isdigit():
            names[i] = simbad_style(n)
    hyg = {
        'source': 'HYG Database v4.1, David Nash / Astronexus, CC BY-SA 4.0 (https://github.com/astronexus/HYG-Database). '
                  'Filtered to V <= 8, J2000 positions in degrees, proper motion mas/yr, distance parsecs (0 = unknown).',
        'count': len(stars), **cols, 'names': names,
    }
    (out / 'hyg.json').write_text(json.dumps(hyg, separators=(',', ':')), encoding='utf-8')

    lines = json.loads((src / 'constellations.json').read_text(encoding='utf-8'))
    labels = json.loads((src / 'constellation-names.json').read_text(encoding='utf-8'))
    cons = {
        'source': 'd3-celestial, Olaf Frohn, BSD-3-Clause (https://github.com/ofrohn/d3-celestial)',
        'lines': [[[round(p[0] % 360, 4), round(p[1], 4)] for p in line] for f in lines['features'] for line in f['geometry']['coordinates']],
        'names': [{'id': f['id'], 'name': f['properties'].get('name') or f['id'], 'ra': round(f['geometry']['coordinates'][0] % 360, 3),
                   'dec': round(f['geometry']['coordinates'][1], 3), 'rank': int(f['properties'].get('rank') or 3)} for f in labels['features']],
    }
    (out / 'constellations.json').write_text(json.dumps(cons, separators=(',', ':')), encoding='utf-8')
    print(f"hyg.json: {len(stars)} stars, {len(names)} named; constellations: {len(cons['lines'])} lines, {len(cons['names'])} names")


if __name__ == '__main__':
    main(sys.argv[1])
