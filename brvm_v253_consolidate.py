"""V2.5.3: conservative consolidation of V2.5.2 candidates; read-only.
No automatic approval or publication to valuation. Keeps raw candidates for audit.
"""
import re
from collections import defaultdict
from copy import deepcopy
from brvm_v252_reconcile import reconcile

DIGITS = re.compile(r'\d+')

def normalize_ocr_amounts(evidence):
    """Recover OCR glued thousand groups only in financial row numeric suffix.
    A 6-digit token is split into 3+3 solely when preceded by a valid
    1-3 digit thousand group; a trailing OCR bracket is allowed.
    Returns inferred values requiring human verification.
    """
    if not evidence:
        return []
    # Do not treat accounting codes as monetary figures.
    label = re.search(r'(?:CAPITAUX PROPRES|PRODUIT NET BANCAIRE|RESULTAT NET|RÉSULTAT NET|RESULTAT D.EXPLOITATION|RÉSULTAT D.EXPLOITATION)', evidence, re.I)
    if not label:
        return []
    tail = evidence[label.end():]
    tail = re.sub(r'(?<=\d)[\]\|](?=\s*\d)', ' ', tail)
    tail = re.sub(r'(?<=\d)[\]\|](?=\s*$)', ' ', tail)
    # OCR sometimes joins adjacent groups: 19 647 175711 -> 19 647 175 711.
    tail = re.sub(r'(?<!\d)(\d{1,3}(?:\s+\d{3})+)\s+(\d{6})(?!\d)',
                  lambda m: m.group(1)+' '+m.group(2)[:3]+' '+m.group(2)[3:], tail)
    # Capture full 3-group+ amounts, with no partial tokens.
    pat = re.compile(r'(?<!\d)(\d{1,3}(?:[ \u00a0\u202f]+\d{3}){2,5})(?!\d)')
    out=[]
    for match in pat.finditer(tail):
        raw=' '.join(match.group(1).split())
        value=int(raw.replace(' ',''))
        if value>=1000000 and value not in [x['value'] for x in out]:
            out.append({'raw':raw,'value':value,'inferred':True})
    return out

def _contains_full_value(fragment, whole):
    a=re.sub(r'\D','',str(fragment));b=re.sub(r'\D','',str(whole))
    return len(a)>=4 and len(b)>len(a) and (a in b or b.startswith(a))

def consolidate_candidates(rows):
    """Preserve raw audit; present distinct full-length candidate values separately."""
    groups=defaultdict(list)
    for r in rows:
        groups[(r.get('document_id'),r.get('page_pdf'),r.get('indicator'))].append(deepcopy(r))
    accepted=[];suppressed=[];inferred=[]
    for key,items in groups.items():
        seen={(r.get('numeric_candidate')) for r in items}
        for row in items:
            for cand in normalize_ocr_amounts(row.get('evidence','')):
                if cand['value'] in seen:continue
                new=deepcopy(row)
                new.update(raw_amount=cand['raw'],numeric_candidate=cand['value'],
                           fiscal_year_proposed=None,amount_millions_fcfa=None,
                           extraction_method='v253-ocr-inference',
                           warning='Reconstruction OCR hypothétique : contrôle visuel obligatoire',
                           status='NON_CERTIFIE')
                items.append(new);inferred.append(new);seen.add(cand['value'])
        unique={}
        for row in items:
            v=row.get('numeric_candidate')
            if v not in unique or (unique[v].get('extraction_method')=='v253-ocr-inference' and row.get('extraction_method')!='v253-ocr-inference'):
                unique[v]=row
        items=list(unique.values())
        for row in items:
            larger=[r for r in items if r is not row and _contains_full_value(row.get('raw_amount',''),r.get('raw_amount',''))]
            if larger:
                row['review_reason']='Fragment probable; valeur plus complète présente dans la même ligne financière'
                suppressed.append(row)
            else:
                row['review_reason']='Candidat complet à vérifier sur le PDF'
                row['status']='NON_CERTIFIE'
                accepted.append(row)
    return accepted,suppressed,inferred

def run_v253(document,max_pages=8):
    result=reconcile(document,max_pages)
    original=deepcopy(result['candidates'])
    selected,fragments,inferred=consolidate_candidates(original)
    result.update(candidates=selected,raw_candidates=original,fragments=fragments,
                  inferred_count=len(inferred),
                  note='Aucune donnée certifiée. Année et unité seulement si documentées; montants reconstruits = hypothèses.')
    return result
