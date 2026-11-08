"""V2.5.4: conservative, read-only cell structure validation.
Never certifies amounts or assumes a fiscal year from report year/column order.
"""
import re
from collections import defaultdict
from copy import deepcopy

NUM = re.compile(r'(?<!\d)\d{1,3}(?:[ \u00a0\u202f]+\d{3}){3,5}(?!\d)')
YEAR = re.compile(r'(?<!\d)(20(?:1\d|2\d|3\d))(?!\d)')
UNIT = re.compile(r'\b(?:en\s+)?(millions?|milliers?)\s+(?:de\s+)?(?:francs?\s+)?(?:f\.?\s*cfa|xof)\b', re.I)

def numbers_in_evidence(evidence):
    """Read complete amounts in an OCR row; do not join across column separators.
    Repair a 6-digit thousand-group glued to the previous 3-digit group only
    if doing so does not swallow the next column.
    """
    s=str(evidence or '').replace('\u202f',' ').replace('\xa0',' ')
    s=re.sub(r'(?<=\d)([\]\|])(?=\s*\d)', r' \1 ', s)
    s=re.sub(r'(?<!\d)(\d{1,3}(?:\s+\d{3}){1,3})\s+(\d{6})(?!\d)',
             lambda m: m.group(1)+' '+m.group(2)[:3]+' '+m.group(2)[3:], s)
    return [{'raw':' '.join(m.group().split()), 'value':int(re.sub(r'\D','',m.group())),'start':m.start()} for m in NUM.finditer(s)]

def _int(v):
    try:return int(float(v))
    except (TypeError,ValueError,OverflowError):return None

def _header_map(page_lines, row_y, page_width):
    """Use only explicit 2-year headers above the target row, with x coordinates."""
    candidates=[]
    for ln in page_lines:
        if ln['y'] >= row_y:continue
        ys=[]
        for w in ln['words']:
            match=YEAR.fullmatch(w['text'].strip('()[]:'))
            if match:ys.append((int(match.group(1)),(w['x']+w['right'])/2))
        if len({y for y,x in ys})>=2:candidates.append((ln['y'],ys))
    if not candidates:return []
    return max(candidates,key=lambda v:v[0])[1]

def _page_evidence(document,max_pages):
    """Independent OCR of headers and currency units; no implied years."""
    from pathlib import Path
    import hashlib
    from brvm_v252_reconcile import _ocr_page
    raw=Path(document['stored_path']).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=document['sha256']:raise ValueError('SHA-256 invalide')
    pages={}; errors=[]
    for i in range(max_pages):
        try:
            lines,width=_ocr_page(raw,i)
            unit_matches=[]
            for ln in lines:
                m=UNIT.search(ln['text'])
                if m:unit_matches.append((ln['y'], 'millions FCFA' if m.group(1).lower().startswith('million') else 'milliers FCFA'))
            pages[i+1]={'lines':lines,'width':width,'units':unit_matches}
        except IndexError:break
        except Exception as exc:errors.append(f'Page {i+1}: {type(exc).__name__}: {exc}')
    return pages,errors

def validate_candidates(rows,page_geometry=None):
    """Validate OCR candidates with evidence; never promote a guessed year/unit.
    page_geometry is optional, e.g. result of _page_evidence.
    """
    page_geometry=page_geometry or {}
    groups=defaultdict(list)
    for row in rows:groups[(row.get('document_id'),row.get('page_pdf'),row.get('indicator'))].append(deepcopy(row))
    out=[];rejected=[]
    for (doc,page,metric),group in groups.items():
        evidence_texts=list(dict.fromkeys(str(r.get('evidence') or '') for r in group))
        proof=[]
        for txt in evidence_texts:
            for n in numbers_in_evidence(txt):
                if n['value'] not in [v['value'] for v in proof]:proof.append(n)
        proof_values={n['value'] for n in proof}
        # If OCR line has exactly 2 amounts, keep only these, never a glued hybrid.
        selected={}
        for r in group:
            val=_int(r.get('numeric_candidate'))
            raw_digits=re.sub(r'\D','',str(r.get('raw_amount') or ''))
            raw_value=int(raw_digits) if raw_digits else None
            reasons=[]
            if val is None or raw_value!=val:reasons.append('Incohérence raw_amount / numeric_candidate')
            if len(str(abs(val or 0)))>15:reasons.append('Montant anormalement long : fusion probable de colonnes')
            if proof_values and val not in proof_values:reasons.append('Montant absent des cellules complètes de la ligne OCR')
            if reasons:
                r['validation_status']='REJETE_STRUCTURE';r['validation_reason']='; '.join(reasons)
                r['status']='NON_CERTIFIE';r['fiscal_year_proposed']=None;r['amount_millions_fcfa']=None
                rejected.append(r);continue
            if val not in selected or r.get('extraction_method')!='v253-ocr-inference':selected[val]=r
        # Recover complete evidence numbers only when exactly 2 and row's indicator is known.
        if len(proof)==2:
            for ix,p in enumerate(proof):
                if p['value'] not in selected:
                    template=deepcopy(group[0]);template.update(raw_amount=p['raw'],numeric_candidate=p['value'],
                        extraction_method='v254-evidence-cell',column_index=ix)
                    selected[p['value']]=template
        for idx,(val,r) in enumerate(sorted(selected.items(),key=lambda x: next((i for i,p in enumerate(proof) if p['value']==x[0]),999))):
            r['status']='NON_CERTIFIE';r['validation_status']='REVUE_HUMAINE'
            r['validation_reason']='Montant cohérent avec preuve OCR; vérification PDF requise' if val in proof_values else 'Preuve OCR insuffisante'
            r['fiscal_year_proposed']=None;r['unit_detected']='Unité non confirmée';r['amount_millions_fcfa']=None
            r['column_index']=next((i for i,p in enumerate(proof) if p['value']==val),r.get('column_index'))
            geo=page_geometry.get(page)
            if geo:
                # Match row by label/evidence, then use actual OCR x coordinates for amount cells.
                lines=geo['lines']; width=geo['width']
                target=None
                from brvm_v252_reconcile import _metric,_cells
                for ln in lines:
                    if _metric(ln['text'])!=metric:continue
                    cells=_cells([w for other in lines if abs(other['y']-ln['y'])<12 for w in other['words'] if w['x']>width*.43],width)
                    if any(c['value']==val for c in cells):target=(ln,cells);break
                if target:
                    ln,cells=target
                    cells=sorted({(c['value'],round(c['x']/8)):c for c in cells}.values(),key=lambda c:c['x'])
                    match=next((c for c in cells if c['value']==val),None)
                    if match:
                        r['column_index']=next((i for i,c in enumerate(cells) if c is match),None)
                        headers=_header_map(lines,ln['y'],width)
                        near=sorted(headers,key=lambda h:abs(h[1]-match['x']))
                        if len(near)>=2 and abs(near[0][1]-match['x'])<width*.12 and abs(near[0][1]-match['x'])+width*.04<abs(near[1][1]-match['x']):
                            r['fiscal_year_proposed']=near[0][0]
                    preceding=[u for y,u in geo['units'] if y<ln['y']]
                    if preceding and len(set(preceding))==1:
                        r['unit_detected']=preceding[-1]
                        mul=1000000 if preceding[-1]=='millions FCFA' else 1000
                        r['amount_millions_fcfa']=round(val*mul/1000000,6)
            if r['fiscal_year_proposed'] is None:r['validation_reason']+='; année non prouvée'
            if r['amount_millions_fcfa'] is None:r['validation_reason']+='; unité non prouvée'
            out.append(r)
    return out,rejected

def run_v254(document,max_pages=8):
    from brvm_v253_consolidate import run_v253
    prior=run_v253(document,max_pages)
    geo,errors=_page_evidence(document,min(max_pages,prior['pages_analyzed']))
    accepted,rejected=validate_candidates(prior['candidates'],geo)
    return {**prior,'candidates':accepted,'rejected_structural':rejected,
            'errors':prior.get('errors',[])+errors,
            'note':'Aucune certification automatique. Année et unité uniquement avec preuve OCR géométrique explicite.'}
