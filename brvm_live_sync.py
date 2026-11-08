"""Live BRVM discovery, cautious extraction, and staging. Never auto-approve."""
from brvm_financial_sync import find_reports,parse_report
from brvm_catalog import annual_pdf_reports
from auto_financials import download_pdf
from brvm_sync import fetch_dividends
from brvm_registry import identities,norm
from data_engine import stage
import math

MAX_PDF_BYTES=25*1024*1024

def synchronize(db,symbol,max_reports=6,financial_pages=5,dividend_pages=12):
    diag={'reports_found':0,'pdf_read':0,'pdf_failed':0,'financial_rows':0,'dividend_rows':0,'staged':0,'errors':[],'report_details':[],'catalog_diagnostic':{}}
    staged=[]
    try:
        reports, catalog_diag=annual_pdf_reports(symbol,pages=financial_pages)
        diag['catalog_diagnostic']=catalog_diag
        if not reports:
            diag['errors'].append('Catalogue officiel : aucun PDF annuel direct trouvé; recherche de secours activée.')
            reports=find_reports(symbol,pages=financial_pages)
        diag['reports_found']=len(reports)
        for r in reports[:max_reports]:
            try:
                raw=download_pdf(r['URL'])
                if len(raw)>MAX_PDF_BYTES:raise ValueError('PDF trop volumineux')
                diag['pdf_read']+=1
                extracted=parse_report(raw,r['URL'],r['Titre'])
                diag['report_details'].append({'Rapport':r['Titre'][:110],'Valeurs':len(extracted),'Source':r['URL']})
                for item in extracted:
                    metric={'Chiffre d\'affaires':'CA','Résultat net':'RN'}.get(item['Indicateur'])
                    # Banking PNB is exposed as Chiffre d'affaires by legacy parser; identify from original PDF label.
                    if metric=='CA' and 'produit net bancaire' in norm(item.get('Libellé source','')):metric='PNB'
                    if metric is None:continue
                    staged.append({'symbol':symbol,'year':item['Année'],'metric':metric,'value':item['Montant'],
                        'unit':'millions FCFA','source_url':item['Source'],'source_page':str(item.get('Page PDF','')),
                        'source_title':item.get('Publication','')})
                    diag['financial_rows']+=1
            except Exception as exc:
                diag['pdf_failed']+=1
                diag['errors'].append(f"PDF {r.get('Titre','')[:55]} : {type(exc).__name__}: {str(exc)[:140]}")
    except Exception as exc:diag['errors'].append(f"Recherche financière : {type(exc).__name__}: {str(exc)[:180]}")
    try:
        data,_=fetch_dividends(max_pages=dividend_pages)
        aliases={norm(x) for x in identities(symbol)['aliases']}
        matched=data[data['Société'].astype(str).map(lambda x:norm(x) in aliases)]
        for _,r in matched.iterrows():
            value=float(r['Dividende net BRVM'])
            if not math.isfinite(value):continue
            staged.append({'symbol':symbol,'year':int(r['Année']),'metric':'DIV_NET','value':value,
                'unit':'FCFA/action','source_url':r['Source'],'source_page':'','source_title':'Annonce dividende BRVM — montant net publié'})
            diag['dividend_rows']+=1
    except Exception as exc:diag['errors'].append(f"Dividendes : {type(exc).__name__}: {str(exc)[:180]}")
    # Avoid ambiguous amounts for the same metric/year within a batch.
    grouped={}
    for r in staged:grouped.setdefault((r['symbol'],r['year'],r['metric']),[]).append(r)
    safe=[]
    for key,rows in grouped.items():
        amounts={round(r['value'],5) for r in rows}
        if len(amounts)!=1:
            diag['errors'].append(f'Conflit {key[0]} {key[1]} {key[2]} : {len(amounts)} valeurs différentes; aucune importation')
        else:safe.append(rows[0])
    diag['staged'],errors=stage(db,safe)
    diag['errors'].extend(errors)
    return diag
