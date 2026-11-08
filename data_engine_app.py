"""Launch: streamlit run data_engine_app.py"""
import os
import pandas as pd
import streamlit as st
from data_engine import connect,records,stage,approve,parse_csv,coverage
from structured_financials import extract_structured

st.set_page_config(page_title='BRVM Data Engine V2.4.1',layout='wide')
st.title('BRVM RADAR — Data Engine V2.4.1')
st.caption('Pilote indépendant : stockage, provenance et validation humaine des données financières')
try: db=connect()
except Exception as exc: st.error(f'Base indisponible : {exc}');st.stop()

symbols=['SMBC','SGBC','BOAB','CBIBF']
for r in db.execute('SELECT DISTINCT symbol FROM financials'):
    if r['symbol'] not in symbols:symbols.append(r['symbol'])
selected=st.selectbox('Société',symbols)
tabs=st.tabs(['Historique vérifié','Synchronisation BRVM','Catalogue documentaire','Audit PDF V2.4.1','Importer les rapports','Validation','Contrôle qualité'])
with tabs[0]:
    rs=records(db,selected,True)
    if not rs: st.info('Aucune donnée encore vérifiée pour cette société.')
    else:
        df=pd.DataFrame(rs)
        for metric,title in [('CA','Chiffre d’affaires'),('PNB','Produit net bancaire'),('RN','Résultat net'),('DIV_NET','Dividende net par action')]:
            x=df[df.metric==metric].sort_values('year')
            if not x.empty:
                st.subheader(title)
                st.bar_chart(x.set_index('year')['value'])
                st.dataframe(x[['year','value','unit','source_url','source_page']],hide_index=True,use_container_width=True)
        annual=df[df.metric.isin(['CA','PNB','RN'])].pivot(index='year',columns='metric',values='value').sort_index()
        base='PNB' if 'PNB' in annual else 'CA'
        if base in annual:
            growth=annual[base].pct_change(fill_method=None)*100
            growth=growth.where(pd.Series(annual.index,index=annual.index).diff()==1)
            st.subheader(f'Croissance {base} (%)')
            st.dataframe(growth.dropna().rename('Croissance %').reset_index(),hide_index=True)
with tabs[1]:
    st.subheader('Synchroniser les publications officielles BRVM')
    st.caption('Les données sont téléchargées et placées en attente de validation. Aucune valeur n’est automatiquement certifiée.')
    max_reports=st.slider('Nombre maximum de rapports PDF à analyser',1,12,6)
    financial_pages=st.slider('Pages de publications financières à explorer',1,8,5)
    dividend_pages=st.slider('Pages d’annonces de dividendes à explorer',1,30,12)
    if st.button('Synchroniser les données BRVM',type='primary'):
        from brvm_live_sync import synchronize
        with st.spinner('Recherche et analyse des publications BRVM en cours…'):
            try:
                result=synchronize(db,selected,max_reports,financial_pages,dividend_pages)
                st.session_state[f'sync-{selected}']=result
            except Exception as exc:st.error(f'Synchronisation impossible : {type(exc).__name__}: {exc}')
    result=st.session_state.get(f'sync-{selected}')
    if result:
        cols=st.columns(4)
        for c,label,key in zip(cols,['Rapports repérés','PDF lus','Valeurs financières extraites','Valeurs mises en attente'],['reports_found','pdf_read','financial_rows','staged']):c.metric(label,result[key])
        st.caption(f"Dividendes extraits : {result['dividend_rows']} · PDF en échec : {result['pdf_failed']}")
        if result['report_details']:st.dataframe(pd.DataFrame(result['report_details']),hide_index=True,use_container_width=True)
        cd=result.get('catalog_diagnostic') or {}
        if cd:
            st.caption(f"Catalogue : {cd.get('documents_found',0)} documents, {cd.get('pdf_links',0)} liens PDF")
            if cd.get('pages'):st.dataframe(pd.DataFrame(cd['pages']),hide_index=True,use_container_width=True)
        for error in result['errors']:st.warning(error)
        st.info('Consulte l’onglet Validation pour vérifier les valeurs, puis Historique vérifié pour afficher les histogrammes.')
with tabs[2]:
    st.subheader('Catalogue des publications BRVM')
    st.caption('Recherche indépendante de l’extraction : rapports annuels et T1/S1/T3, sans certification automatique.')
    cat_pages=st.slider('Pages du catalogue par société',1,8,3)
    if st.button('Rechercher les publications officielles',type='primary'):
        from brvm_catalog import catalog
        with st.spinner('Exploration des pages officielles BRVM…'):
            try:st.session_state[f'catalog-{selected}']=catalog(selected,cat_pages)
            except Exception as exc:st.error(f'Catalogue : {type(exc).__name__}: {exc}')
    data=st.session_state.get(f'catalog-{selected}')
    if data:
        docs,diag=data
        st.metric('Documents identifiés',len(docs))
        if docs:
            st.dataframe(pd.DataFrame(docs),hide_index=True,use_container_width=True)
            st.download_button('Exporter le catalogue CSV',pd.DataFrame(docs).to_csv(index=False).encode('utf-8-sig'),f'catalogue_{selected}.csv','text/csv')
        if diag['pages']:st.dataframe(pd.DataFrame(diag['pages']),hide_index=True,use_container_width=True)
        for error in diag['errors']:st.warning(error)
        if not docs:st.warning('Aucune publication détectée : consulter les URL et statuts ci-dessus.')
with tabs[3]:
    st.subheader('V2.4.2 — Audit PDF et colonnes financières')
    st.caption('Lecture page par page et affichage des libellés, montants candidats et preuves. Aucun chiffre n’est automatiquement certifié ni enregistré.')
    from brvm_catalog import catalog
    from auto_financials import download_pdf
    from brvm_pdf_audit import extract_candidates
    use_ocr=st.checkbox('Activer OCR pour les pages scannées',value=True)
    ocr_pages=st.slider('Nombre maximal de pages OCR par rapport',1,20,8)
    documents=st.session_state.get(f'catalog-{selected}')
    if not documents:
        st.info('Lancer d’abord la recherche dans Catalogue documentaire pour cette société.')
    else:
        pdfs=[d for d in documents[0] if d.get('PDF') and d.get('Période') in ('ANNUEL','T1','S1','T3')]
        if not pdfs: st.warning('Aucun lien PDF à analyser dans le catalogue.')
        else:
            options=list(range(len(pdfs)))
            choice=st.selectbox('Rapport à examiner',options,format_func=lambda i:f"{pdfs[i]['Année']} · {pdfs[i]['Période']} · {pdfs[i]['Titre'][:110]}")
            chosen=pdfs[choice]
            st.write('Source :',chosen['URL'])
            if st.button('Lire le PDF et contrôler les chiffres',type='primary'):
                try:
                    with st.spinner('Lecture et audit du PDF…'):
                        raw=download_pdf(chosen['URL'])
                        if len(raw)>25*1024*1024:raise ValueError('PDF supérieur à 25 Mo')
                        found,diagnostic=extract_candidates(raw,chosen['Titre'],chosen['Période'],use_ocr=use_ocr,ocr_max_pages=ocr_pages)
                        st.session_state[f'audit-{selected}']=(chosen,found,diagnostic)
                except Exception as exc:st.error(f'Audit PDF : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'audit-{selected}')
            if result:
                source,found,diagnostic=result
                st.write('Rapport audité :',source['Titre'])
                c1,c2,c3,c4=st.columns(4)
                c1.metric('Pages',diagnostic['pages'])
                c2.metric('Pages avec texte',diagnostic['pages_with_text'])
                c3.metric('Pages OCR',diagnostic.get('pages_ocr',0))
                c4.metric('Libellés candidats',diagnostic['candidates'])
                for err in diagnostic.get('ocr_errors',[]):st.error(err)
                for note in diagnostic['notes']:st.warning(note)
                st.warning('Les valeurs OCR sont des candidats non certifiés : comparer visuellement avec le PDF officiel avant toute validation.')
                if found:
                    st.dataframe(pd.DataFrame(found),hide_index=True,use_container_width=True)
                    st.download_button('Exporter les preuves CSV',pd.DataFrame(found).to_csv(index=False).encode('utf-8-sig'),f'audit_pdf_{selected}.csv','text/csv')
                else:st.warning('Aucun libellé reconnu. Vérifier le document et sa qualité texte.')
                st.info('La présence de chiffres candidats ne démontre pas leur exactitude. Vérifier unités, colonnes N/N-1, périodes et états consolidés avant validation.')
                st.divider()
                st.markdown('#### V2.4.2 — Analyse des colonnes et des unités')
                st.caption('Analyse OCR positionnelle supplémentaire. Les attributions d’année et conversions restent provisoires. Aucun enregistrement en base.')
                if st.button('Analyser les colonnes N/N-1 et les unités',key=f'v242-{selected}'):
                    try:
                        from brvm_table_audit import extract_table_evidence
                        with st.spinner('Analyse positionnelle OCR des tableaux…'):
                            raw2=download_pdf(source['URL'])
                            table_rows,table_diag=extract_table_evidence(raw2,source['Titre'],max_pages=ocr_pages)
                            st.session_state[f'v242-table-{selected}']=(source['URL'],table_rows,table_diag)
                    except Exception as exc:st.error(f'Analyse V2.4.2 : {type(exc).__name__}: {exc}')
                table_result=st.session_state.get(f'v242-table-{selected}')
                if table_result and table_result[0]==source['URL']:
                    _,table_rows,table_diag=table_result
                    a,b,c=st.columns(3)
                    a.metric('Lignes de montants candidates',len(table_rows))
                    b.metric('Années attribuées provisoirement',table_diag['assigned'])
                    c.metric('Montants sans attribution',table_diag['unassigned'])
                    for error in table_diag['ocr_errors']:st.error(error)
                    if table_rows:
                        st.dataframe(pd.DataFrame(table_rows),hide_index=True,use_container_width=True)
                        st.download_button('Exporter audit des colonnes CSV',pd.DataFrame(table_rows).to_csv(index=False).encode('utf-8-sig'),f'audit_colonnes_{selected}.csv','text/csv')
                    else:st.warning('Aucun montant exploitable détecté. Vérification manuelle requise.')
                    st.warning('Ne pas utiliser les montants dans le Dashboard, T1/S1/T3 ou Valorisation sans vérification des colonnes, unités et comptes individuels/consolidés.')
                st.divider()
                st.markdown('#### V2.4.3 — Reconstruction universelle des tableaux (expérimentale)')
                st.caption('Reconnaissance des libellés et géométrie des colonnes, sans obligation de codes comptables. Aucun enregistrement ni certification.')
                if st.button('Reconstruire les tableaux par OCR',key=f'v243-{selected}'):
                    try:
                        from brvm_universal_tables import reconstruct
                        with st.spinner('Reconstruction des tableaux financiers…'):
                            raw3=download_pdf(source['URL'])
                            universal_rows,universal_diag=reconstruct(raw3,source['Titre'],max_pages=ocr_pages)
                            st.session_state[f'v243-table-{selected}']=(source['URL'],universal_rows,universal_diag)
                    except Exception as exc:st.error(f'Analyse V2.4.3 : {type(exc).__name__}: {exc}')
                universal_result=st.session_state.get(f'v243-table-{selected}')
                if universal_result and universal_result[0]==source['URL']:
                    _,universal_rows,universal_diag=universal_result
                    u1,u2,u3=st.columns(3)
                    u1.metric('Montants candidats',universal_diag['rows'])
                    u2.metric('Années proposées',universal_diag['assigned'])
                    u3.metric('Sans attribution',universal_diag['unassigned'])
                    for err in universal_diag['errors']:st.error(err)
                    st.caption(f"Pages OCR : {universal_diag['pages_ocr']} · En-têtes détectés : {universal_diag['headers_found']}")
                    if universal_rows:
                        st.dataframe(pd.DataFrame(universal_rows),hide_index=True,use_container_width=True)
                        st.download_button('Exporter audit universel CSV',pd.DataFrame(universal_rows).to_csv(index=False).encode('utf-8-sig'),f'audit_universel_{selected}.csv','text/csv')
                    else:st.warning('Aucun montant candidat reconstitué. Consulter le PDF original.')
                    st.warning('Toutes les attributions sont provisoires et non certifiées. Aucune valeur ne doit alimenter les calculs financiers avant validation.')
with tabs[4]:
    st.write('Import CSV de valeurs vérifiées dans les publications, ou pré-extraction d’un PDF natif. Toute valeur importée reste en attente de validation.')
    st.download_button('Télécharger le modèle CSV',open(os.path.join(os.path.dirname(__file__),'modele_import.csv'),'rb').read(),'modele_import.csv')
    csvfile=st.file_uploader('Importer un CSV',type='csv',key='csv')
    if csvfile and st.button('Mettre les valeurs en attente de contrôle'):
        try:
            n,errors=stage(db,parse_csv(csvfile.getvalue()))
            st.success(f'{n} valeurs importées, non encore validées')
            for error in errors:st.warning(error)
        except Exception as exc:st.error(str(exc))
    st.divider()
    pdf=st.file_uploader('PDF financier officiel',type='pdf')
    source=st.text_input('URL HTTPS de la publication officielle (obligatoire pour enregistrer)')
    if pdf and st.button('Pré-extraire le PDF'):
        try:
            _,raw,_=extract_structured(pdf.getvalue())
            rows=[]
            mapping={'chiffre_affaires':'CA','pnb':'PNB','resultat_net':'RN'}
            for _,r in raw.iterrows():
                for ycol,vcol in [('Exercice N','2025 ou N'),('Exercice N-1','2024 ou N-1')]:
                    rows.append(dict(symbol=selected,year=int(r[ycol]),metric=mapping[r['Indicateur']],value=float(r[vcol]),unit='millions FCFA',source_url=source,source_page=str(r['Page']),source_title=pdf.name))
            st.session_state['pdf_rows']=rows
            st.dataframe(pd.DataFrame(rows),hide_index=True)
            st.info('Extraction automatique : montants non encore vérifiés sur le document original.')
        except Exception as exc:st.error(f'Extraction impossible : {exc}')
    if st.session_state.get('pdf_rows') and st.button('Enregistrer les valeurs extraites en attente'):
        n,errors=stage(db,st.session_state['pdf_rows'])
        st.success(f'{n} lignes en attente de validation')
        for err in errors:st.warning(err)
with tabs[5]:
    pending=[r for r in records(db,selected) if r['status']=='a_verifier']
    if not pending:st.info('Aucune valeur en attente pour ce titre.')
    for r in pending:
        with st.expander(f"{r['year']} · {r['metric']} · {r['value']:,.2f} {r['unit']}"):
            st.write('Source :',r['source_url'],'— Page :',r['source_page'])
            st.warning('Comparer avec le PDF officiel avant de valider.')
            if st.button('J’ai vérifié cette valeur dans la source',key=f"ok-{r['symbol']}-{r['year']}-{r['metric']}"):
                approve(db,r['symbol'],r['year'],r['metric']);st.rerun()
with tabs[6]:
    st.dataframe(pd.DataFrame(coverage(db,symbols)),hide_index=True,use_container_width=True)
    st.caption('La couverture mesure uniquement les valeurs validées. Une valeur absente n’est jamais assimilée à zéro.')
    st.download_button('Exporter les données vérifiées',pd.DataFrame(records(db,verified_only=True)).to_csv(index=False).encode('utf-8-sig'),'brvm_financials_verified.csv','text/csv')
st.caption('Attention : sur Streamlit Community Cloud, le fichier SQLite local peut disparaître au redéploiement. Utiliser un volume persistant ou PostgreSQL avant production.')
