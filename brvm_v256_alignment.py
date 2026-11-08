"""V2.5.6: cautious table-column alignment and explicit unit provenance.
Read-only. All values remain NON_CERTIFIE. Requires previous V2.5.5 modules.
"""
from collections import defaultdict
from copy import deepcopy
import re
from brvm_v255_headers import run_v255, _year_words, unit_from_text


def _amount_spans(line, value):
    """Find an amount in OCR text by digit sequence, tolerating OCR separators.
    Returns text offsets only; text offsets are NOT geometric coordinates.
    """
    digits=str(value)
    found=[]
    for m in re.finditer(r'\d[\d\s\u00a0\u202f\]\|]*\d',str(line)):
        t=m.group(); compact=re.sub(r'\D','',t)
        at=compact.find(digits)
        if at>=0:found.append((m.start(),m.end(),at,len(compact)))
    return found


def _unit_scope(geometry,page,row_y):
    """Return explicit unambiguous unit, preferring same-page header above table.
    Previous-page unit is diagnostic only, never used for conversion.
    """
    geo=geometry.get(page)
    if not geo:return None,'','Géométrie absente'
    candidates=[]
    for ln in geo['lines']:
        if ln['y'] >= row_y:continue
        unit=unit_from_text(ln['text'])
        if unit:candidates.append((ln['y'],unit,ln['text']))
    if candidates:
        scales={c[1][1] for c in candidates}
        if len(scales)==1:
            chosen=max(candidates,key=lambda c:c[0]);return chosen[1],chosen[2],'PAGE_EXPLICITE'
        return None,' | '.join(c[2] for c in candidates),'UNITES_CONTRADICTOIRES'
    return None,'','UNITE_NON_PROUVEE'


def _anchors(geometry,page,at_y):
    geo=geometry.get(page)
    if not geo:return []
    groups=defaultdict(list)
    for p in _year_words(geo['lines']):
        if 0 < at_y-p['y'] < 900 and p['x'] > geo['width']*.35:
            groups[round(p['y']/22)].append(p)
    candidates=[(max(x['y'] for x in g),sorted(g,key=lambda x:x['x'])) for g in groups.values() if len({x['year'] for x in g})>=2]
    if not candidates:return []
    return max(candidates,key=lambda t:t[0])[1]


def _locate(geometry,page,indicator,value):
    geo=geometry.get(page)
    if not geo:return None
    from brvm_v252_reconcile import _metric,_cells
    width=geo['width'];matches=[]
    for ln in geo['lines']:
        if _metric(ln['text'])!=indicator:continue
        words=sorted([w for other in geo['lines'] if abs(other['y']-ln['y'])<12 for w in other['words'] if w['x']>width*.43],key=lambda w:w['x'])
        for c in _cells(words,width):
            if c['value']==value:matches.append({'x':c['x'],'y':ln['y'],'text':ln['text'],'method':'CELLULE_OCR'})
        # Match OCR word sequences independently when the legacy cell builder missed a value.
        for i in range(len(words)):
            for j in range(i+1,min(i+8,len(words))+1):
                seq=words[i:j]
                if any(seq[k+1]['x']-seq[k]['right']>max(28,width*.020) for k in range(len(seq)-1)):break
                raw=''.join(re.sub(r'\D','',w['text']) for w in seq)
                if raw==str(value):
                    matches.append({'x':(seq[0]['x']+seq[-1]['right'])/2,'y':ln['y'],'text':ln['text'],'method':'MOTS_OCR'})
    if not matches:return None
    return sorted(matches,key=lambda m:(m['method']!='CELLULE_OCR',m['x']))[0]


def align_candidates(rows,geometry):
    """Enrich candidates without inferring a year solely from their order.
    A table-level header may be reused only with measured x alignment.
    """
    result=[]
    for original in rows:
        r=deepcopy(original)
        r.update(status='NON_CERTIFIE',validation_status='REVUE_HUMAINE',
                 v256_x=None,v256_y=None,v256_header_x=None,v256_alignment_method='',
                 v256_header_proof='',v256_unit_scope='',v256_diagnostic='')
        page=int(r.get('page_pdf') or 0)
        try:value=int(r['numeric_candidate'])
        except (TypeError,ValueError):
            r['v256_diagnostic']='Montant invalide';result.append(r);continue
        loc=_locate(geometry,page,r.get('indicator'),value)
        if not loc:
            r['v256_diagnostic']='Coordonnées OCR du montant non retrouvées; année et unité non modifiées'
            result.append(r);continue
        r['v256_x']=round(loc['x'],1);r['v256_y']=round(loc['y'],1)
        r['v256_alignment_method']=loc['method']
        geo=geometry[page];width=geo['width']
        anchors=_anchors(geometry,page,loc['y'])
        if len(anchors)>=2:
            near=sorted(anchors,key=lambda a:abs(a['x']-loc['x']))
            d0=abs(near[0]['x']-loc['x']);d1=abs(near[1]['x']-loc['x'])
            r['v256_header_x']=round(near[0]['x'],1)
            r['v256_header_proof']=near[0]['evidence']
            if d0 < width*.12 and d1-d0 > width*.035:
                existing=r.get('fiscal_year_proposed')
                if existing is not None and str(existing).strip() not in ('','nan') and int(float(existing))!=near[0]['year']:
                    r['fiscal_year_proposed']=None;r['year_confidence']='CONFLIT_EN_TETES'
                else:
                    r['fiscal_year_proposed']=near[0]['year'];r['year_confidence']='ALIGNEMENT_GEOMETRIQUE_V256'
                    r['header_evidence']=near[0]['evidence']
        unit,evidence,scope=_unit_scope(geometry,page,loc['y'])
        r['v256_unit_scope']=scope
        if unit:
            r['unit_detected']=unit[0];r['unit_evidence']=evidence
            r['unit_confidence']='MENTION_EXPLICITE_PAGE_V256'
            r['amount_millions_fcfa']=round(value*unit[1]/1_000_000,6)
        issues=[]
        if r.get('fiscal_year_proposed') is None:issues.append('Exercice non démontré')
        if r.get('amount_millions_fcfa') is None:issues.append('Unité non démontrée')
        r['v256_diagnostic']='; '.join(issues) or 'Contexte candidat documenté; contrôle humain requis'
        result.append(r)
    # Detect duplicate year assignments within a single indicator, without inventing alternatives.
    by_key=defaultdict(list)
    for r in result:
        if r.get('fiscal_year_proposed') is not None:
            by_key[(r.get('document_id'),r.get('page_pdf'),r.get('indicator'),r['fiscal_year_proposed'])].append(r)
    for group in by_key.values():
        if len({str(r.get('numeric_candidate')) for r in group})>1:
            for r in group:
                r['fiscal_year_proposed']=None;r['year_confidence']='CONFLIT_COLONNE'
                r['v256_diagnostic']='Conflit: plusieurs montants pour la même année et le même indicateur'
    return result


def run_v256(document,max_pages=8):
    from brvm_v254_validate import _page_evidence
    prior=run_v255(document,max_pages)
    geometry,errors=_page_evidence(document,min(max_pages,prior['pages_analyzed']))
    rows=align_candidates(prior['candidates'],geometry)
    return {**prior,'candidates':rows,'errors':prior.get('errors',[])+errors,
            'note':'V2.5.6 : alignement géométrique conservateur, sans certification ni écriture en base.'}
