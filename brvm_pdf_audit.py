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

def _scan_lines(lines, page_no, year, period, method):
    found=[]
    for i,line in enumerate(lines):
        clean=norm(line)
        for metric,patterns in PATTERNS.items():
            if not any(re.search(p,clean) for p in patterns):continue
            tail=re.sub(r'^.*?(?:'+ '|'.join(patterns)+r')', '',clean,count=1)
            nums=[]
            for m in NUM.finditer(tail):
                try:
                    v=_num(m.group())
                    if math.isfinite(v):nums.append(v)
                except ValueError:pass
            unit=_unit(lines,i)
            found.append({'Année présumée':year,'Période':period,'Indicateur':metric,
                'Valeurs brutes':tail.strip()[:130], 'Facteur vers millions FCFA':unit,
                'Page PDF':page_no+1,'Ligne source':line[:280],
                'Méthode':method,'Statut':'review_required',
                'Motif':'OCR_a_verifier' if method=='OCR' else 'colonnes_et_unites_a_verifier',
                'Nombres candidats (non attribués)':str(nums[:8])})
    return found


def extract_candidates(raw,title,period='ANNUEL',max_pages=160,use_ocr=True,ocr_max_pages=8,dpi=190):
    """Direct text then OCR fallback for image-only pages. Never auto-certifies numbers.

    OCR is capped to avoid long runs on Streamlit Cloud. All candidates remain unverified.
    """
    doc=fitz.open(stream=raw,filetype='pdf')
    diag={'pages':len(doc),'pages_with_text':0,'pages_ocr':0,'ocr_attempted':0,
          'ocr_errors':[],'candidates':0,'review_required':0,'notes':[]}
    year_matches=YEAR.findall(title)
    year=int(year_matches[-1]) if year_matches else None
    candidates=[]
    try:
        for page_no in range(min(len(doc),max_pages)):
            page=doc[page_no]
            direct=page.get_text('text',sort=True)
            method='PDF texte'
            text=direct
            if len(direct.strip())>=50:
                diag['pages_with_text']+=1
            elif use_ocr and diag['ocr_attempted']<ocr_max_pages:
                diag['ocr_attempted']+=1
                try:
                    import pytesseract
                    from PIL import Image
                    import io
                    pix=page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
                    im=Image.open(io.BytesIO(pix.tobytes('png')))
                    # Use French OCR when installed; Streamlit packages.txt provisions it.
                    languages=pytesseract.get_languages(config='')
                    lang='fra+eng' if 'fra' in languages else 'eng'
                    text=pytesseract.image_to_string(im,lang=lang,config='--psm 6',timeout=45)
                    method='OCR'
                    diag['pages_ocr']+=1
                except Exception as exc:
                    diag['ocr_errors'].append(f'Page {page_no+1}: {type(exc).__name__}: {str(exc)[:140]}')
                    text=direct
            lines=[re.sub(r'\s+',' ',x).strip() for x in text.splitlines() if x.strip()]
            candidates.extend(_scan_lines(lines,page_no,year,period,method))
        diag['candidates']=len(candidates)
        diag['review_required']=len(candidates)
        if diag['ocr_attempted'] and diag['pages_ocr']==0:
            diag['notes'].append('OCR indisponible : vérifier packages.txt, requirements.txt et journaux Streamlit.')
        if len(doc)>ocr_max_pages and use_ocr:
            diag['notes'].append(f'OCR limité aux {ocr_max_pages} premières pages sans texte. Augmenter le plafond si nécessaire.')
        if not diag['pages_with_text'] and not diag['pages_ocr']:
            diag['notes'].append('Aucun texte exploitable; examen manuel nécessaire.')
        if not candidates:
            diag['notes'].append('Aucun libellé reconnu : vérifier les pages OCR et les libellés utilisés dans le document.')
        if not year:diag['notes'].append('Exercice non identifié dans le titre.')
        diag['notes'].append('Toute valeur OCR et tout montant candidat restent NON CERTIFIÉS.')
        return candidates,diag
    finally:
        doc.close()
