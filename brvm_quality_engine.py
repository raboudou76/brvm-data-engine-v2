"""V2.4.6: auditable read-only quality assessment for OCR candidates.
No issuer-specific constants, no database writes, no automatic approval.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation
import re

FIELDS = ('Indicateur','Année proposée','Montant brut','Montant numérique','Unité','Millions FCFA','Page PDF','Preuve')

def _numeric(row):
    try:
        v=Decimal(str(row.get('Montant numérique')))
        return v if v.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):return None

def _fragment(row):
    raw=str(row.get('Montant brut') or '').strip()
    # A value of 3 groups can be perfectly valid; do not silently correct it.
    return bool(re.fullmatch(r'\d{1,3}(?:[ \u00a0\u202f]\d{3}){1,2}',raw))

def evaluate(rows, diagnostics=None, document_url='', document_title='', expected_year=None):
    """Return enriched rows, summary. Scores are heuristic review priorities, not probabilities."""
    diagnostics=diagnostics or {}
    indexed=defaultdict(list)
    for i,r in enumerate(rows):
        year=r.get('Année proposée')
        if year is not None:indexed[(r.get('Indicateur'),str(year))].append(i)
    out=[]
    for i,r in enumerate(rows):
        r=dict(r);issues=[];v=_numeric(r)
        year=r.get('Année proposée');unit=str(r.get('Unité') or '')
        proof=str(r.get('Preuve') or '')
        score=0
        if v is not None and len(re.sub(r'\D','',str(r.get('Montant brut') or '')))>=6:
            score+=20
        else:issues.append('MONTANT_INVALIDE_OU_COURT')
        if _fragment(r):issues.append('FRAGMENT_POSSIBLE');score=max(0,score-10)
        if year is not None and str(year).isdigit() and 2000<=int(year)<=2100:
            score+=20
            if expected_year is not None and int(year) not in (int(expected_year),int(expected_year)-1):issues.append('ANNEE_HORS_EXERCICE')
        else:issues.append('ANNEE_NON_ATTRIBUEE')
        if unit in ('FCFA','milliers FCFA','millions FCFA'):
            score+=15
        else:issues.append('UNITE_NON_CONFIRMEE')
        if r.get('Indicateur') and proof and len(proof)>=12:score+=15
        else:issues.append('LIBELLE_OU_PREUVE_INSUFFISANT')
        if document_url.startswith('https://') and r.get('Page PDF'):score+=5
        else:issues.append('SOURCE_INCOMPLETE')
        if year is not None and len(indexed[(r.get('Indicateur'),str(year))])>1:
            issues.append('DOUBLON_INDICATEUR_ANNEE')
        # Financial reconciliation: only exact comparisons for unambiguous values.
        # Do not award consistency points without supporting independently extracted fields.
        issues.append('COHERENCE_NON_VERIFIEE')
        if diagnostics.get('errors'):issues.append('ERREUR_EXTRACTION_DANS_RAPPORT')
        if issues and any(x in issues for x in ('ANNEE_NON_ATTRIBUEE','UNITE_NON_CONFIRMEE','MONTANT_INVALIDE_OU_COURT','DOUBLON_INDICATEUR_ANNEE','ANNEE_HORS_EXERCICE')):
            status='BLOQUE'
        else:status='A_VERIFIER'
        r.update({'Score technique /100':min(score,85),'Statut qualité':status,'Anomalies':'; '.join(issues),'Source URL':document_url,'Document':document_title})
        out.append(r)
    summary={'candidats':len(out),'annees_attribuees':sum(r.get('Année proposée') is not None for r in out),
             'bloques':sum(r['Statut qualité']=='BLOQUE' for r in out),
             'a_verifier':sum(r['Statut qualité']=='A_VERIFIER' for r in out),
             'valides':0,'erreurs_extraction':len(diagnostics.get('errors',[])),
             'note':'Score de triage, pas une certification; aucun contrôle de cohérence comptable validé.'}
    return out,summary
