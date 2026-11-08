"""V2.5.7 — Page-level accounting grid with traceable, conservative propagation.
Read-only; uses the V2.5.6 output and does not certify figures.
"""
from collections import defaultdict
from copy import deepcopy
import math


def _number(v):
    try:
        n=float(v)
        return n if math.isfinite(n) else None
    except (ValueError, TypeError):
        return None


def _year(v):
    n=_number(v)
    return int(n) if n is not None and n.is_integer() and 2000 <= n <= 2040 else None


def _anchors(rows):
    """Only pre-existing year assignments backed by explicit header evidence."""
    by_page=defaultdict(list)
    for r in rows:
        x=_number(r.get('v256_x'));year=_year(r.get('fiscal_year_proposed'))
        proof=str(r.get('v256_header_proof') or r.get('header_evidence') or '').strip()
        confidence=str(r.get('year_confidence') or '')
        if x is not None and year and proof and ('GEOMETRIQUE' in confidence or 'EN_TETE' in confidence):
            by_page[int(r['page_pdf'])].append({'x':x,'year':year,'proof':proof,'indicator':r.get('indicator')})
    grids={}
    for page,points in by_page.items():
        years=sorted({p['year'] for p in points})
        if len(years)!=2:continue
        per_year={y:[p for p in points if p['year']==y] for y in years}
        if not all(per_year.values()):continue
        # A single trusted observation per year is acceptable only when both
        # observations reference the same literal two-year header.
        common={p['proof'] for p in per_year[years[0]]} & {p['proof'] for p in per_year[years[1]]}
        if not common:continue
        proof=sorted(common)[0]
        selected={y:[p['x'] for p in per_year[y] if p['proof']==proof] for y in years}
        if not all(selected.values()):continue
        centers={y:sum(selected[y])/len(selected[y]) for y in years}
        if abs(centers[years[0]]-centers[years[1]])<35:continue
        grids[page]={'centers':centers,'proof':proof,'source':'EN_TETE_EXPLICITE_MEME_PAGE'}
    return grids


def propagate_grid(rows, geometry=None):
    """Assign years only for x-aligned amounts in a documented page grid.

    No year assignment is made when coordinates are absent, the two columns
    are too close, or the closest column is ambiguous. Unit fields are not
    modified; explicit unit evidence must be established independently.
    """
    result=[deepcopy(r) for r in rows]
    for r in result:
        if _year(r.get('fiscal_year_proposed')) is None:
            r['fiscal_year_proposed']=None
        if _number(r.get('amount_millions_fcfa')) is None:
            r['amount_millions_fcfa']=None
    grids=_anchors(result)
    for r in result:
        page=int(r.get('page_pdf') or 0)
        grid=grids.get(page)
        r.update(v257_grid_source='',v257_grid_proof='',v257_grid_x=None,
                 v257_grid_distance=None,v257_grid_status='SANS_GRILLE',
                 v257_diagnostic='')
        if not grid:
            r['v257_diagnostic']='Aucune grille à deux exercices documentée sur cette page'
            continue
        x=_number(r.get('v256_x'))
        if x is None:
            r['v257_grid_status']='COORDONNEE_ABSENTE'
            r['v257_diagnostic']='Montant sans coordonnée x : exercice non attribué'
            continue
        centers=grid['centers'];ordered=sorted(centers,key=lambda y:abs(x-centers[y]))
        best,second=ordered[:2];d0=abs(x-centers[best]);d1=abs(x-centers[second]);separation=abs(centers[best]-centers[second])
        r['v257_grid_x']=round(centers[best],1)
        r['v257_grid_distance']=round(d0,1)
        r['v257_grid_proof']=grid['proof']
        r['v257_grid_source']=grid['source']
        # Distances are in OCR pixels. Conservative 25% of separation,
        # capped at 65 px; clear separation margin also required.
        if d0>min(65,separation*.25) or d1-d0<separation*.45:
            r['v257_grid_status']='ALIGNEMENT_AMBIGU'
            r['v257_diagnostic']='Alignement hors tolérance ou ambigu'
            continue
        existing=_year(r.get('fiscal_year_proposed'))
        if existing is not None and existing!=best:
            r['fiscal_year_proposed']=None
            r['year_confidence']='CONFLIT_GRILLE_V257'
            r['v257_grid_status']='CONFLIT'
            r['v257_diagnostic']='Année antérieure contradictoire avec la grille'
            continue
        r['fiscal_year_proposed']=best
        r['year_confidence']='GRILLE_PAGE_V257' if existing is None else r.get('year_confidence')
        r['header_evidence']=grid['proof']
        r['v257_grid_status']='ALIGNE'
        r['v257_diagnostic']='Exercice proposé par alignement sur une grille prouvée ; revue humaine requise'
    # Reject duplicate assignments for distinct values in same indicator and page.
    groups=defaultdict(list)
    for r in result:
        year=_year(r.get('fiscal_year_proposed'))
        if year is not None:
            groups[(r.get('document_id'),r.get('page_pdf'),r.get('indicator'),year)].append(r)
    for group in groups.values():
        if len({str(r.get('numeric_candidate')) for r in group})>1:
            for r in group:
                r['fiscal_year_proposed']=None
                r['year_confidence']='CONFLIT_DOUBLON_V257'
                r['v257_grid_status']='CONFLIT'
                r['v257_diagnostic']='Deux valeurs différentes attribuées au même exercice'
    for r in result:
        r['status']='NON_CERTIFIE';r['validation_status']='REVUE_HUMAINE'
    return result,grids


def run_v257(document,max_pages=8):
    from brvm_v256_alignment import run_v256
    prior=run_v256(document,max_pages)
    rows,grids=propagate_grid(prior['candidates'])
    return {**prior,'candidates':rows,'v257_grids':grids,
            'note':'V2.5.7 : grille par page fondée sur en-têtes explicites. Aucune certification ou écriture en base.'}
