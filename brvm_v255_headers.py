"""V2.5.5 — conservative OCR context recognition for year/units.
No certification, no assumption based on report year or left/right order.
"""
import re
from collections import defaultdict
from copy import deepcopy

YEAR = re.compile(r'(?<!\d)(20(?:1\d|2\d|3\d))(?!\d)')
YEAR_TOKEN = re.compile(r'(?<!\d)(?:31[./-]12[./-]|(?:exercice|au|annee|année)\s*)(20(?:1\d|2\d|3\d))(?!\d)',re.I)
UNIT = re.compile(r'\b(?:en\s+)?(?:(millions?|milliers?)\s+(?:de\s+)?)?(?:francs?\s+)?(?:f\s*\.?\s*cfa|xof)\b',re.I)


def unit_from_text(text):
    """Require explicit currency. Do not infer FCFA merely from country."""
    m=UNIT.search(str(text or ''))
    if not m:return None
    scale=(m.group(1) or '').lower()
    if scale.startswith('million'):return ('millions FCFA',1000000)
    if scale.startswith('millier'):return ('milliers FCFA',1000)
    return ('FCFA',1)


def _year_words(lines):
    out=[]
    for ln in lines:
        for w in ln.get('words',[]):
            t=str(w.get('text','')).strip('()[]:;,')
            if YEAR.fullmatch(t):
                out.append({'year':int(t),'x':(w['x']+w['right'])/2,'y':ln['y'],'evidence':ln['text']})
        # Handle date headers broken into tokens or embedded 31/12/2025.
        for m in YEAR_TOKEN.finditer(ln.get('text','')):
            yr=int(m.group(1))
            if not any(a['year']==yr and abs(a['y']-ln['y'])<3 for a in out):
                ws=[w for w in ln.get('words',[]) if str(yr) in w['text']]
                if ws:
                    w=ws[0];out.append({'year':yr,'x':(w['x']+w['right'])/2,'y':ln['y'],'evidence':ln['text']})
    return out


def header_candidates(lines,row_y,width):
    """Find 2 distinct years near row, respecting actual x positions.
    A header must have at least 2 year labels in the same horizontal band.
    """
    points=[p for p in _year_words(lines) if 0 < row_y-p['y'] < 900 and p['x']>width*.35]
    groups=defaultdict(list)
    for p in points:groups[round(p['y']/22)].append(p)
    candidates=[]
    for group in groups.values():
        unique={p['year'] for p in group}
        if len(unique)<2:continue
        candidates.append((max(p['y'] for p in group),sorted(group,key=lambda p:p['x'])))
    return max(candidates,key=lambda item:item[0])[1] if candidates else []


def detect_unit(lines,row_y):
    """Only explicit unit evidence on the same page above row; reject conflicts."""
    found=[]
    for ln in lines:
        if ln['y']>=row_y:continue
        unit=unit_from_text(ln.get('text'))
        if unit:found.append((ln['y'],unit,ln['text']))
    if not found:return None,'Aucune mention monétaire explicite au-dessus de la ligne'
    nearest=sorted(found,key=lambda x:x[0],reverse=True)
    if len({u[1] for u in nearest})>1:return None,'Mentions d’unités contradictoires sur la page'
    return nearest[0][1],nearest[0][2]


def resolve_context(rows,geometry):
    """Enrich V2.5.4 candidates with explicit, auditable context.
    Never overwrite prior year/unit with a guess; no certified output.
    """
    results=[]
    for original in rows:
        r=deepcopy(original);r['status']='NON_CERTIFIE'
        r['fiscal_year_proposed']=None;r['amount_millions_fcfa']=None
        r['unit_detected']='Unité non confirmée';r['header_evidence']='';r['unit_evidence']=''
        r['year_confidence']='NON_PROUVE';r['unit_confidence']='NON_PROUVE'
        r['validation_status']='REVUE_HUMAINE'
        geo=geometry.get(int(r.get('page_pdf') or 0))
        if not geo:
            r['v255_diagnostic']='Géométrie OCR indisponible';results.append(r);continue
        from brvm_v252_reconcile import _metric,_cells
        lines=geo['lines'];width=geo['width'];val=int(r['numeric_candidate'])
        matches=[]
        for ln in lines:
            if _metric(ln['text'])!=r.get('indicator'):continue
            words=sorted((w for other in lines if abs(other['y']-ln['y'])<12 for w in other['words'] if w['x']>width*.43),key=lambda w:w['x'])
            cells=_cells(words,width)
            for c in cells:
                if c['value']==val:matches.append((ln,c,cells))
        if not matches:
            r['v255_diagnostic']='Montant non localisé dans les cellules géométriques OCR';results.append(r);continue
        ln,cell,cells=min(matches,key=lambda t:abs(t[1]['x']-width*.7))
        ordered=sorted(cells,key=lambda c:c['x'])
        r['column_index']=next((i for i,c in enumerate(ordered) if c is cell),None)
        headers=header_candidates(lines,ln['y'],width)
        if headers:
            closest=sorted(headers,key=lambda h:abs(h['x']-cell['x']))
            d0=abs(closest[0]['x']-cell['x'])
            d1=abs(closest[1]['x']-cell['x'])
            if d0<width*.12 and d1-d0>width*.035:
                r['fiscal_year_proposed']=closest[0]['year'];r['header_evidence']=closest[0]['evidence']
                r['year_confidence']='EN_TETE_GEOMETRIQUE'
        unit,evidence=detect_unit(lines,ln['y'])
        if unit:
            r['unit_detected']=unit[0];r['unit_evidence']=evidence
            r['unit_confidence']='MENTION_EXPLICITE'
            r['amount_millions_fcfa']=round(val*unit[1]/1_000_000,6)
        issues=[]
        if r['fiscal_year_proposed'] is None:issues.append('année non prouvée')
        if r['amount_millions_fcfa'] is None:issues.append('unité non prouvée')
        r['v255_diagnostic']='; '.join(issues) if issues else 'Contexte identifié; revue PDF toujours obligatoire'
        results.append(r)
    return results


def run_v255(document,max_pages=8):
    from brvm_v254_validate import run_v254,_page_evidence
    prior=run_v254(document,max_pages)
    geometry,errors=_page_evidence(document,min(max_pages,prior['pages_analyzed']))
    rows=resolve_context(prior['candidates'],geometry)
    return {**prior,'candidates':rows,'errors':prior.get('errors',[])+errors,
            'note':'V2.5.5 : exercices et unités seulement sur preuve explicite. Aucun montant certifié.'}
