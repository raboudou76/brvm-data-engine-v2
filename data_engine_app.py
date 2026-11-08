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
tabs=st.tabs(['Historique vérifié','Synchronisation BRVM','Catalogue documentaire','Audit PDF V2.4.1','Importer les rapports','Validation','Contrôle qualité','Pipeline V2.5.0','Extraction hybride V2.5.1','Réconciliation V2.5.2','Consolidation V2.5.3','Validation structurelle V2.5.4','En-têtes et unités V2.5.5'])
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
                st.divider()
                st.markdown('#### V2.4.4 — Lecture multi-colonnes, multi-exercices (pilote)')
                st.caption('Méthode sans codes obligatoires, extraction en lecture seule, aucune certification ni synchronisation de la valorisation.')
                if st.button('Tester extraction multi-années',key=f'v244-{selected}'):
                    try:
                        from brvm_layout_engine import extract
                        with st.spinner('Lecture des lignes, dates et colonnes OCR…'):
                            raw4=download_pdf(source['URL'])
                            multi_rows,multi_diag=extract(raw4,source['Titre'],max_pages=ocr_pages)
                            st.session_state[f'v244-table-{selected}']=(source['URL'],multi_rows,multi_diag)
                    except Exception as exc:st.error(f'V2.4.4 : {type(exc).__name__}: {exc}')
                multi_result=st.session_state.get(f'v244-table-{selected}')
                if multi_result and multi_result[0]==source['URL']:
                    _,multi_rows,multi_diag=multi_result
                    a,b,c=st.columns(3)
                    a.metric('Valeurs candidates',len(multi_rows))
                    b.metric('Années proposées',multi_diag['assigned'])
                    c.metric('Non attribuées',multi_diag['unassigned'])
                    st.caption(f"Pages traitées : {multi_diag['pages_processed']} · Dates détectées : {multi_diag['headers']}")
                    for e in multi_diag['errors']:st.error(e)
                    if multi_rows:
                        st.dataframe(pd.DataFrame(multi_rows),hide_index=True,use_container_width=True)
                        st.download_button('Exporter V2.4.4 CSV',pd.DataFrame(multi_rows).to_csv(index=False).encode('utf-8-sig'),f'v244_{selected}.csv','text/csv')
                    st.warning('Candidats OCR non certifiés. Années et unités à confirmer dans le PDF avant toute utilisation.')
                st.divider()
                st.markdown('#### V2.4.5 — Regroupement des fragments OCR (pilote)')
                st.caption('Analyse géométrique en lecture seule. Aucune valeur automatiquement validée ou enregistrée.')
                if st.button('Tester regroupement OCR V2.4.5',key=f'v245-{selected}'):
                    try:
                        from brvm_cell_engine import extract as extract_cells
                        with st.spinner('Reconstitution des cellules OCR…'):
                            raw5=download_pdf(source['URL'])
                            cell_rows,cell_diag=extract_cells(raw5,source['Titre'],max_pages=ocr_pages)
                            st.session_state[f'v245-table-{selected}']=(source['URL'],cell_rows,cell_diag)
                    except Exception as exc:st.error(f'V2.4.5 : {type(exc).__name__}: {exc}')
                cell_result=st.session_state.get(f'v245-table-{selected}')
                if cell_result and cell_result[0]==source['URL']:
                    _,cell_rows,cell_diag=cell_result
                    a,b,c=st.columns(3)
                    a.metric('Montants candidats',len(cell_rows))
                    b.metric('Années proposées',cell_diag['assigned'])
                    c.metric('Sans attribution',cell_diag['unassigned'])
                    st.caption(f"Pages traitées : {cell_diag['pages_processed']} · En-têtes : {cell_diag['headers']}")
                    for e in cell_diag['errors']:st.error(e)
                    if cell_rows:
                        st.dataframe(pd.DataFrame(cell_rows),hide_index=True,use_container_width=True)
                        st.download_button('Exporter V2.4.5 CSV',pd.DataFrame(cell_rows).to_csv(index=False).encode('utf-8-sig'),f'v245_{selected}.csv','text/csv')
                    st.warning('Valeurs NON CERTIFIÉES. Ne pas alimenter Dashboard, T1/S1/T3 ou Valorisation.')
                st.divider()
                st.markdown('#### V2.4.6 — Contrôle qualité des extractions (lecture seule)')
                st.caption('Audit des montants V2.4.5, années, unités, doublons et preuves. Aucun enregistrement automatique.')
                if st.button('Contrôler la qualité V2.4.6',key=f'v246-{selected}'):
                    try:
                        from brvm_cell_engine import extract as extract_cells
                        from brvm_quality_engine import evaluate
                        with st.spinner('Extraction OCR et contrôle qualité…'):
                            raw6=download_pdf(source['URL'])
                            quality_source,quality_diag=extract_cells(raw6,source['Titre'],max_pages=ocr_pages)
                            quality_rows,quality_summary=evaluate(quality_source,quality_diag,source['URL'],source['Titre'],source.get('Année'))
                            st.session_state[f'v246-table-{selected}']=(source['URL'],quality_rows,quality_summary,quality_diag)
                    except Exception as exc:st.error(f'V2.4.6 : {type(exc).__name__}: {exc}')
                quality_result=st.session_state.get(f'v246-table-{selected}')
                if quality_result and quality_result[0]==source['URL']:
                    _,quality_rows,quality_summary,quality_diag=quality_result
                    a,b,c=st.columns(3)
                    a.metric('Valeurs candidates',quality_summary['candidats'])
                    b.metric('Bloquées',quality_summary['bloques'])
                    c.metric('À vérifier',quality_summary['a_verifier'])
                    st.caption(quality_summary['note'])
                    for err in quality_diag.get('errors',[]):st.error(err)
                    if quality_rows:
                        st.dataframe(pd.DataFrame(quality_rows),hide_index=True,use_container_width=True)
                        st.download_button('Exporter audit qualité V2.4.6 CSV',pd.DataFrame(quality_rows).to_csv(index=False).encode('utf-8-sig'),f'v246_qualite_{selected}.csv','text/csv')
                    else:st.warning('Aucune valeur candidate. Vérifier le PDF et le mode OCR.')
                    st.warning('Aucune valeur certifiée ou transférée vers la base financière. Vérifier le PDF original avant toute approbation.')
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

with tabs[7]:
    st.subheader('V2.5.0 — Veille et archivage incrémental des PDF officiels')
    st.caption('Collecte et archivage seulement : aucune extraction financière, certification ou synchronisation vers BRVM RADAR.')
    from brvm_pipeline import ingest, history, runs
    pgs=st.slider('Pages du catalogue à parcourir',1,8,3,key='v250_pages')
    max_dl=st.slider('Nombre maximum de nouveaux PDF à télécharger',1,30,8,key='v250_max')
    if st.button('Collecter et archiver les PDF officiels',type='primary',key='v250_run'):
        with st.spinner('Recherche et archivage incrémental des publications…'):
            try:
                st.session_state['v250_result']=ingest(selected,pages=pgs,max_downloads=max_dl)
            except Exception as exc:
                st.error(f'Pipeline : {type(exc).__name__}: {exc}')
    result=st.session_state.get('v250_result')
    if result:
        cols=st.columns(5)
        for col,label,key in zip(cols,['PDF identifiés','Nouvelles références','Téléchargés','Ignorés','Échecs'],['discovered','new_items','downloaded','skipped','failed']):
            col.metric(label,result.get(key,0))
        for error in result.get('errors',[]):st.warning(error)
    try:
        items=history(selected)
        if items:
            st.write('Archives documentaires')
            st.dataframe(pd.DataFrame(items),hide_index=True,use_container_width=True)
            st.download_button('Exporter inventaire CSV',pd.DataFrame(items).to_csv(index=False).encode('utf-8-sig'),f'archives_{selected}.csv','text/csv',key='v250_csv')
        logs=runs()
        if logs:
            with st.expander('Journal des exécutions'):
                st.dataframe(pd.DataFrame(logs),hide_index=True,use_container_width=True)
    except Exception as exc:
        st.error(f'Historique pipeline indisponible : {exc}')
    st.warning('Sur Streamlit Community Cloud, les fichiers PDF et SQLite locaux ne sont pas persistants. Configurer PostgreSQL et un stockage de fichiers persistant avant automatisation quotidienne.')

with tabs[8]:
    st.subheader('V2.5.1 — Extraction hybride des PDF archivés (lecture seule)')
    st.caption('PDF natif d’abord, OCR en secours. Aucun chiffre automatiquement validé, enregistré ni envoyé à BRVM RADAR.')
    from brvm_hybrid_extractor import archived_documents, extract_archived
    try:
        archive_docs=archived_documents(selected)
        archive_docs=[d for d in archive_docs if d.get('stored_path')]
        if not archive_docs:
            st.info('Aucun PDF archivé pour cette société : lancer d’abord le pipeline V2.5.0.')
        else:
            chosen=st.selectbox('PDF archivé à analyser',range(len(archive_docs)),
                format_func=lambda i: f"{archive_docs[i].get('fiscal_year')} · {archive_docs[i].get('period')} · {archive_docs[i]['title']}",key='v251_doc')
            max_p=st.slider('Nombre maximal de pages à analyser',1,30,8,key='v251_pages')
            if st.button('Extraire les tableaux du PDF archivé',key='v251_run'):
                with st.spinner('Lecture du PDF et extraction hybride…'):
                    try:st.session_state[f'v251_result_{selected}_{archive_docs[chosen]["id"]}']=extract_archived(archive_docs[chosen],max_p)
                    except Exception as exc:st.error(f'Extraction : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'v251_result_{selected}_{archive_docs[chosen]["id"]}')
            if result:
                st.write('Rapport analysé :',result['title'])
                c1,c2,c3=st.columns(3)
                c1.metric('Pages du PDF',result['pages_total'])
                c2.metric('Pages analysées',result['pages_analyzed'])
                c3.metric('Montants candidats',len(result['candidates']))
                st.dataframe(pd.DataFrame(result['page_diagnostics']),hide_index=True,use_container_width=True)
                if result['candidates']:
                    df=pd.DataFrame(result['candidates'])
                    st.dataframe(df,hide_index=True,use_container_width=True)
                    st.download_button('Exporter les preuves candidates CSV',df.to_csv(index=False).encode('utf-8-sig'),
                        f'extraction_v251_{selected}.csv','text/csv',key='v251_export')
                else:st.warning('Aucun candidat détecté : consulter les diagnostics des pages.')
                st.warning('Aucune valeur n’est certifiée. Vérifier visuellement les cellules, années, unités et comptes consolidés/individuels.')
    except Exception as exc:st.error(f'Archives V2.5.0 indisponibles : {type(exc).__name__}: {exc}')

with tabs[9]:
    st.subheader('V2.5.2 — Réconciliation OCR et colonnes (lecture seule)')
    st.caption('Conserve la V2.5.1 et ajoute les candidats géométriques. Aucune écriture financière ni certification.')
    from brvm_hybrid_extractor import archived_documents as archived_v252
    from brvm_v252_reconcile import reconcile
    try:
        docs_v252=archived_v252(selected)
        docs_v252=[d for d in docs_v252 if d.get('stored_path')]
        if not docs_v252:
            st.info('Aucun PDF archivé : exécuter le Pipeline V2.5.0.')
        else:
            ix_v252=st.selectbox('Rapport archivé',range(len(docs_v252)),format_func=lambda i: f"{docs_v252[i].get('fiscal_year')} · {docs_v252[i].get('period')} · {docs_v252[i]['title']}",key='v252_doc')
            pages_v252=st.slider('Pages maximales',1,30,8,key='v252_pages')
            if st.button('Réconcilier les cellules OCR V2.5.2',key='v252_run'):
                with st.spinner('Extraction et comparaison des candidats…'):
                    try:st.session_state[f'v252_{selected}_{docs_v252[ix_v252]["id"]}']=reconcile(docs_v252[ix_v252],pages_v252)
                    except Exception as exc:st.error(f'Réconciliation : {type(exc).__name__}: {exc}')
            result_v252=st.session_state.get(f'v252_{selected}_{docs_v252[ix_v252]["id"]}')
            if result_v252:
                a,b,c=st.columns(3)
                a.metric('Candidats V2.5.1',result_v252['original_count'])
                b.metric('Nouveaux candidats',result_v252['new_candidates'])
                c.metric('Total distinct',len(result_v252['candidates']))
                if result_v252['errors']:st.warning('Diagnostics OCR : '+' | '.join(result_v252['errors']))
                df_v252=pd.DataFrame(result_v252['candidates'])
                priority=['indicator','fiscal_year_proposed','raw_amount','numeric_candidate','unit_detected','amount_millions_fcfa','page_pdf','warning','status']
                st.dataframe(df_v252[[x for x in priority if x in df_v252.columns]],hide_index=True,use_container_width=True)
                st.download_button('Exporter audit V2.5.2 CSV',df_v252.to_csv(index=False).encode('utf-8-sig'),f'audit_v252_{selected}.csv','text/csv',key='v252_export')
                st.warning('Tous les montants sont NON CERTIFIÉS : vérifier les années, unités et colonnes sur le PDF officiel.')
    except Exception as exc:st.error(f'Archives V2.5.0 indisponibles : {type(exc).__name__}: {exc}')

with tabs[10]:
    st.subheader('V2.5.3 — Consolidation OCR et audit des fragments')
    st.caption('Lecture seule : les reconstitutions sont des hypothèses. Aucune écriture en base ni synchronisation BRVM RADAR.')
    from brvm_v253_consolidate import run_v253
    from brvm_hybrid_extractor import archived_documents as archived_v253
    try:
        docs_v253=[d for d in archived_v253(selected) if d.get('stored_path')]
        if not docs_v253:
            st.info('Aucun PDF archivé : lancer le Pipeline V2.5.0.')
        else:
            ix=st.selectbox('Rapport archivé à contrôler',range(len(docs_v253)),
                            format_func=lambda i: f"{docs_v253[i].get('fiscal_year')} · {docs_v253[i].get('period')} · {docs_v253[i]['title']}",key='v253_doc')
            pages=st.slider('Pages maximales à analyser',1,30,8,key='v253_pages')
            if st.button('Consolider les montants OCR V2.5.3',key='v253_run'):
                with st.spinner('Reconstruction et déduplication prudente…'):
                    try:st.session_state[f'v253_{selected}_{docs_v253[ix]["id"]}']=run_v253(docs_v253[ix],pages)
                    except Exception as exc:st.error(f'V2.5.3 : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'v253_{selected}_{docs_v253[ix]["id"]}')
            if result:
                a,b,c=st.columns(3)
                a.metric('Candidats retenus pour revue',len(result['candidates']))
                b.metric('Fragments écartés de la vue',len(result['fragments']))
                c.metric('Reconstitutions hypothétiques',result['inferred_count'])
                if result['errors']:st.warning('Diagnostics : '+' | '.join(result['errors']))
                fields=['indicator','fiscal_year_proposed','raw_amount','numeric_candidate','unit_detected','page_pdf','extraction_method','review_reason','warning']
                df=pd.DataFrame(result['candidates'])
                st.dataframe(df[[x for x in fields if x in df.columns]],hide_index=True,use_container_width=True)
                st.download_button('Exporter candidats V2.5.3 CSV',df.to_csv(index=False).encode('utf-8-sig'),f'audit_v253_{selected}.csv','text/csv',key='v253_export')
                with st.expander('Fragments écartés de la vue principale (conservés pour audit)'):
                    st.dataframe(pd.DataFrame(result['fragments']),hide_index=True,use_container_width=True)
                st.warning('Aucun montant ni exercice n’est certifié. Vérifier les valeurs reconstituées, les en-têtes N/N-1 et les unités sur le PDF.')
    except Exception as exc:st.error(f'Archives indisponibles : {type(exc).__name__}: {exc}')

with tabs[11]:
    st.subheader('V2.5.4 — Validation structurelle des cellules OCR')
    st.caption('Lecture seule : vérification des fusions de colonnes, des années et des unités. Aucune certification automatique.')
    from brvm_v254_validate import run_v254
    from brvm_hybrid_extractor import archived_documents as archived_v254
    try:
        docs=[d for d in archived_v254(selected) if d.get('stored_path')]
        if not docs:st.info('Aucun PDF archivé : lancer le Pipeline V2.5.0.')
        else:
            ix=st.selectbox('Rapport archivé V2.5.4',range(len(docs)),
                format_func=lambda i:f"{docs[i].get('fiscal_year')} · {docs[i].get('period')} · {docs[i]['title']}",key='v254_doc')
            pages=st.slider('Pages maximales V2.5.4',1,30,8,key='v254_pages')
            if st.button('Contrôler la structure OCR V2.5.4',key='v254_run'):
                with st.spinner('Contrôle des cellules, en-têtes et unités…'):
                    try:st.session_state[f'v254_{selected}_{docs[ix]["id"]}']=run_v254(docs[ix],pages)
                    except Exception as exc:st.error(f'V2.5.4 : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'v254_{selected}_{docs[ix]["id"]}')
            if result:
                a,b,c=st.columns(3)
                a.metric('Candidats pour revue',len(result['candidates']))
                b.metric('Rejets structurels',len(result['rejected_structural']))
                c.metric('Exercices attribués avec preuve',sum(r.get('fiscal_year_proposed') is not None for r in result['candidates']))
                if result['errors']:st.warning('Diagnostics : '+' | '.join(result['errors']))
                fields=['indicator','raw_amount','numeric_candidate','column_index','fiscal_year_proposed','unit_detected','page_pdf','validation_status','validation_reason']
                df=pd.DataFrame(result['candidates'])
                if not df.empty:st.dataframe(df[[x for x in fields if x in df.columns]],hide_index=True,use_container_width=True)
                st.download_button('Exporter audit V2.5.4 CSV',df.to_csv(index=False).encode('utf-8-sig'),f'audit_v254_{selected}.csv','text/csv',key='v254_export')
                with st.expander('Valeurs rejetées (audit conservé)'):
                    st.dataframe(pd.DataFrame(result['rejected_structural']),hide_index=True,use_container_width=True)
                st.warning('Candidats NON CERTIFIÉS. Comparer les exercices, unités et chiffres au PDF officiel avant toute utilisation.')
    except Exception as exc:st.error(f'Archives indisponibles : {type(exc).__name__}: {exc}')

with tabs[12]:
    st.subheader('V2.5.5 — Attribution des exercices et des unités')
    st.caption('En-têtes géométriques et unités documentées ; aucun résultat certifié automatiquement.')
    from brvm_v255_headers import run_v255
    from brvm_hybrid_extractor import archived_documents as archived_v255
    try:
        docs=[d for d in archived_v255(selected) if d.get('stored_path')]
        if not docs:st.info('Aucun PDF archivé : lancer le Pipeline V2.5.0.')
        else:
            ix=st.selectbox('Rapport archivé V2.5.5',range(len(docs)),
                format_func=lambda i:f"{docs[i].get('fiscal_year')} · {docs[i].get('period')} · {docs[i]['title']}",key='v255_doc')
            pages=st.slider('Pages maximales V2.5.5',1,30,8,key='v255_pages')
            if st.button('Analyser en-têtes et unités V2.5.5',key='v255_run'):
                with st.spinner('Analyse OCR des en-têtes et des unités…'):
                    try:st.session_state[f'v255_{selected}_{docs[ix]["id"]}']=run_v255(docs[ix],pages)
                    except Exception as exc:st.error(f'V2.5.5 : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'v255_{selected}_{docs[ix]["id"]}')
            if result:
                rows=result['candidates'];a,b,c=st.columns(3)
                a.metric('Candidats non certifiés',len(rows))
                b.metric('Années prouvées',sum(r.get('fiscal_year_proposed') is not None for r in rows))
                c.metric('Unités prouvées',sum(r.get('unit_confidence')=='MENTION_EXPLICITE' for r in rows))
                if result['errors']:st.warning('Diagnostics : '+' | '.join(result['errors']))
                df=pd.DataFrame(rows)
                fields=['indicator','numeric_candidate','column_index','fiscal_year_proposed','year_confidence','unit_detected','amount_millions_fcfa','unit_confidence','page_pdf','v255_diagnostic']
                if not df.empty:st.dataframe(df[[x for x in fields if x in df.columns]],hide_index=True,use_container_width=True)
                st.download_button('Exporter audit V2.5.5 CSV',df.to_csv(index=False).encode('utf-8-sig'),f'audit_v255_{selected}.csv','text/csv',key='v255_export')
                with st.expander('Preuves et audit détaillés'):
                    if not df.empty:st.dataframe(df,hide_index=True,use_container_width=True)
                st.warning('NON CERTIFIÉ : vérifier visuellement les années, les unités et les montants sur le PDF officiel.')
    except Exception as exc:st.error(f'Archives indisponibles : {type(exc).__name__}: {exc}')
