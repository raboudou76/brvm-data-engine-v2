"""Conservative PDF text extraction with traceable evidence; never certifies numbers."""
import io, re, math
from dataclasses import dataclass
import fitz
from brvm_registry import norm

PATTERNS = {
 'PNB': [r'\bproduit\s+net\s+bancaire\b'],
 'CA': [r'\bchiffre\s+d.affaires\b',r'\bchiffre\s+affaires\b'],
 'RN': [r'\bresultat\s+net\b',r'\bbenefice\s+net\b'],
 'EQUITY': [r'\bcapitaux\s+propres\b'],
 'ASSETS': [r'\btotal\s+(?:du\s+)?bilan\b',r'\btotal\s+actif\b'],
}
NUM = re.compile(r'(?<![\w])[-−]?(?:\d{1,3}(?:[\s\u00a0\u202f.]\d{3})+|\d{4,15}|\d{1,3})(?:,\d{1,3})?(?![\w])')
YEAR = re.compile(r'(?<!\d)20\d{2}(?!\d)')

def _num(s):
    s=s.replace('\u202f','').replace('\u00a0','').replace(' ','').replace('−','-')
    if ',' in s: s=s.replace('.','').replace(',','.')
    elif '.' in s and len(s.split('.')[-1])==3: s=s.replace('.','')
    return float(s)

def _unit(lines,idx):
    scope=' '.join(lines[max(0,idx-18):idx+1]).lower()
    scope=norm(scope)
    # Reject if no explicit units in nearby text; unit conversion is not safe.
    m=list(re.finditer(r'(millions?\s+(?:de\s+)?(?:fcfa|f\s*cfa)|milliers?\s+(?:de\s+)?(?:fcfa|f\s*cfa)|en\s+(?:millions?|milliers?)\s+(?:de\s+)?(?:fcfa|f\s*cfa))',scope))
    if not m:return None
    return 1 if 'million' in m[-1].group() else 0.001

def extract_candidates(raw,title,period='ANNUEL',max_pages=160):
    """Returns (candidates, diagnostics). Evidence includes PDF page and exact source line.
    For multi-column financial statements values are intentionally NOT staged automatically.
    """
    doc=fitz.open(stream=raw,filetype='pdf')
    diagnostics={'pages':len(doc),'pages_with_text':0,'candidates':0,'review_required':0,'notes':[]}
    candidates=[]
    years=[int(y) for y in YEAR.findall(title)]
    year=years[-1] if years else None
    for page_no in range(min(len(doc),max_pages)):
        text=doc[page_no].get_text('text',sort=True)
        if len(text.strip())>50:diagnostics['pages_with_text']+=1
        lines=[re.sub(r'\s+',' ',x).strip() for x in text.splitlines() if x.strip()]
        for i,line in enumerate(lines):
            clean=norm(line)
            for metric,patterns in PATTERNS.items():
                if not any(re.search(p,clean) for p in patterns):continue
                # Only consider numeric values on same line; no adjacent-line guessing.
                tail=re.sub(r'^.*?'+r'(?:'+ '|'.join(patterns)+r')', '',clean,count=1)
                matches=list(NUM.finditer(tail))
                nums=[]
                for m in matches:
                    try:
                        v=_num(m.group())
                        if math.isfinite(v):nums.append(v)
                    except ValueError:pass
                unit=_unit(lines,i)
                # Single-value candidates with explicit unit can be manually staged; multiple columns require manual column/year check.
                status='review_required'
                reason=('unite_non_identifiee' if unit is None else 'colonnes_a_verifier' if len(nums)!=1 or len(matches)>0 and len(matches[0].group().replace(' ',''))>10 else 'verification_source_obligatoire')
                candidates.append({'Année présumée':year,'Période':period,'Indicateur':metric,
                  'Valeurs brutes':tail.strip()[:130],'Facteur vers millions FCFA':unit,
                  'Page PDF':page_no+1,'Ligne source':line[:280],'Statut':status,'Motif':reason})
    doc.close()
    diagnostics['candidates']=len(candidates)
    diagnostics['review_required']=len(candidates)
    if not diagnostics['pages_with_text']:diagnostics['notes'].append('PDF image/scanné : extraction texte insuffisante; OCR ou lecture manuelle nécessaire')
    if not year:diagnostics['notes'].append('Exercice non identifié dans le titre')
    return candidates,diagnostics
