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
tabs=st.tabs(['Historique vérifié','Synchronisation BRVM','Catalogue documentaire','Audit PDF V2.4.1','Importer les rapports','Validation','Contrôle qualité','Pipeline V2.5.0','Extraction hybride V2.5.1','Réconciliation V2.5.2','Consolidation V2.5.3','Validation structurelle V2.5.4','En-têtes et unités V2.5.5','Alignement V2.5.6','Grille comptable V2.5.7','Finance intégrée V2.6.0'])
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

with tabs[13]:
    st.subheader('V2.5.6 — Alignement géométrique et preuves des unités')
    st.caption('Analyse en lecture seule. Aucun chiffre certifié ou synchronisé.')
    from brvm_v256_alignment import run_v256
    from brvm_hybrid_extractor import archived_documents as archived_v256
    try:
        docs=[d for d in archived_v256(selected) if d.get('stored_path')]
        if not docs:st.info('Aucun PDF archivé : lancer le Pipeline V2.5.0.')
        else:
            ix=st.selectbox('Rapport archivé V2.5.6',range(len(docs)),
                format_func=lambda i:f"{docs[i].get('fiscal_year')} · {docs[i].get('period')} · {docs[i]['title']}",key='v256_doc')
            pages=st.slider('Pages maximales V2.5.6',1,30,8,key='v256_pages')
            if st.button('Analyser alignement V2.5.6',key='v256_run'):
                with st.spinner('Alignement géométrique OCR et audit des unités…'):
                    try:st.session_state[f'v256_{selected}_{docs[ix]["id"]}']=run_v256(docs[ix],pages)
                    except Exception as exc:st.error(f'V2.5.6 : {type(exc).__name__}: {exc}')
            result=st.session_state.get(f'v256_{selected}_{docs[ix]["id"]}')
            if result:
                rows=result['candidates'];a,b,c=st.columns(3)
                a.metric('Candidats non certifiés',len(rows))
                b.metric('Années proposées',sum(r.get('fiscal_year_proposed') is not None for r in rows))
                c.metric('Unités avec preuve',sum(str(r.get('unit_confidence','')).startswith('MENTION_EXPLICITE') for r in rows))
                if result.get('errors'):st.warning('Diagnostics : '+' | '.join(result['errors']))
                df=pd.DataFrame(rows)
                fields=['indicator','numeric_candidate','page_pdf','v256_x','v256_y','v256_header_x','column_index','fiscal_year_proposed','year_confidence','unit_detected','unit_confidence','v256_unit_scope','v256_diagnostic']
                if not df.empty:st.dataframe(df[[x for x in fields if x in df.columns]],hide_index=True,use_container_width=True)
                st.download_button('Exporter audit V2.5.6 CSV',df.to_csv(index=False).encode('utf-8-sig'),f'audit_v256_{selected}.csv','text/csv',key='v256_export')
                with st.expander('Preuves et coordonnées détaillées'):
                    if not df.empty:st.dataframe(df,hide_index=True,use_container_width=True)
                st.warning('NON CERTIFIÉ : vérifier les en-têtes, unités et montants sur le PDF officiel.')
    except Exception as exc:st.error(f'Archives indisponibles : {type(exc).__name__}: {exc}')

with tabs[14]:
    st.subheader('V2.5.7 — Grille comptable par page')
    st.caption('Années proposées uniquement avec en-tête explicite et alignement géométrique ; montants non certifiés.')
    from brvm_v257_grid import run_v257
    from brvm_hybrid_extractor import archived_documents as archived_v257
    if selected:
        docs=[d for d in archived_v257(selected) if d.get('stored_path')]
        if docs:
            ix=st.selectbox('Document archivé V2.5.7',range(len(docs)),format_func=lambda i:f"{docs[i].get('fiscal_year')} · {docs[i].get('period')} · {docs[i]['title']}",key='v257_doc')
            pages=st.slider('Pages maximales V2.5.7',1,30,8,key='v257_pages')
            key=f'v257_{selected}_{docs[ix]["id"]}'
            if st.button('Reconstruire la grille V2.5.7',key='v257_run'):
                with st.spinner('Analyse géométrique des grilles...'):
                    try:st.session_state[key]=run_v257(docs[ix],pages)
                    except Exception as exc:st.error(f'Analyse impossible : {exc}')
            result=st.session_state.get(key)
            if result:
                import pandas as pd
                df=pd.DataFrame(result['candidates'])
                years=int(df['fiscal_year_proposed'].notna().sum()) if len(df) else 0
                units=int(df['amount_millions_fcfa'].notna().sum()) if len(df) else 0
                c1,c2,c3=st.columns(3)
                c1.metric('Candidats',len(df));c2.metric('Exercices proposés',years);c3.metric('Unités documentées',units)
                st.write('Grilles identifiées (par page) :',result.get('v257_grids',{}))
                fields=['indicator','numeric_candidate','page_pdf','v256_x','v257_grid_x','v257_grid_distance','fiscal_year_proposed','year_confidence','unit_detected','unit_confidence','v257_grid_status','v257_grid_proof','v257_diagnostic']
                st.dataframe(df[[c for c in fields if c in df.columns]],use_container_width=True)
                st.download_button('Exporter audit V2.5.7 CSV',df.to_csv(index=False).encode('utf-8-sig'),f'audit_v257_{selected}.csv','text/csv',key='v257_export')
                st.warning('Les exercices sont des propositions vérifiables ; aucun montant n’est certifié ni synchronisé avec BRVM RADAR.')
        else:st.info('Aucun document archivé pour cette société.')

with tabs[15]:
    st.subheader('V2.6.0 — Base financière et analyses provisoires')
    st.caption('Import des audits V2.5.x, stockage local SQLite, historique et croissance. Aucune certification ni synchronisation automatique.')
    from brvm_v260_finance import connect_db, import_csv, observations, indicators, ALLOWED
    if selected not in ALLOWED:
        st.info('Pilote limité à BOAB, SMBC, SNTS, SGBC et CBIBF.')
    else:
        st.warning('Sur Streamlit Community Cloud, le stockage SQLite local peut être effacé au redémarrage. Exporter les données ; PostgreSQL sera nécessaire pour la production.')
        db260=connect_db()
        csv260=st.file_uploader('Importer un audit CSV V2.5.x',type=['csv'],key='v260_csv')
        xof260=st.checkbox('Devise XOF déduite du contexte UEMOA',value=True,key='v260_xof')
        scale260=st.checkbox('Montants entiers présumés (facteur ×1 si aucune unité explicite)',value=True,key='v260_scale')
        if st.button('Importer dans la base provisoire',key='v260_import',disabled=csv260 is None):
            try:st.session_state['v260_stats']=import_csv(db260,csv260.getvalue(),selected,xof260,scale260)
            except Exception as exc:st.error(f'Import impossible : {exc}')
        if 'v260_stats' in st.session_state:st.json(st.session_state['v260_stats'])
        rows260=observations(db260,selected)
        a260,b260,c260=st.columns(3)
        a260.metric('Observations',len(rows260))
        b260.metric('Provisoires',sum(r['review_status']=='PROVISOIRE' for r in rows260))
        c260.metric('Bloquées',sum(r['review_status']=='BLOQUE' for r in rows260))
        if rows260:
            df260=pd.DataFrame(rows260)
            st.dataframe(df260[['indicator','fiscal_year','raw_amount','amount_xof','currency_basis','scale_basis','review_status','issue','source_page']],use_container_width=True,hide_index=True)
            st.download_button('Exporter observations V2.6.0 CSV',df260.to_csv(index=False).encode('utf-8-sig'),f'observations_v260_{selected}.csv','text/csv',key='v260_export')
        analytics260=indicators(db260,selected)
        if analytics260:
            st.subheader('Indicateurs annuels — provisoires')
            st.dataframe(pd.DataFrame(analytics260),hide_index=True,use_container_width=True)
        st.info('Les valeurs sans exercice attribué sont bloquées. Les ratios nécessitant des données absentes (BPA, PER, dividendes, valorisation) ne sont pas calculés.')


# V2.7.2 — Tableau de bord PostgreSQL multi-sociétés (lecture seule)
st.divider()
st.header("V2.7.2 — Tableau de bord financier PostgreSQL")
st.caption("Source : Supabase PostgreSQL. Les données PROVISIONAL ne sont pas certifiées ; BLOCKED est exclu des graphiques et des calculs.")

if st.button("Charger les sociétés et les données PostgreSQL", key="v272_load"):
    try:
        import psycopg
        from psycopg.rows import dict_row
        database_url = st.secrets.get("DATABASE_URL")
        if not database_url:
            st.error("DATABASE_URL absent des Secrets Streamlit.")
        else:
            with psycopg.connect(database_url, connect_timeout=12, row_factory=dict_row) as pg_conn:
                with pg_conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
                    cur.execute("""
                        SELECT c.ticker, c.company_name,
                               COUNT(f.fact_id) AS observations
                        FROM public.companies c
                        LEFT JOIN public.financial_facts f ON f.company_id = c.company_id
                        GROUP BY c.company_id, c.ticker, c.company_name
                        ORDER BY c.ticker
                    """)
                    companies272 = cur.fetchall()
                    cur.execute("""
                        SELECT c.ticker, c.company_name, f.fact_id, f.fiscal_year,
                               f.fiscal_period, f.period_basis, f.standardized_account,
                               f.amount, f.raw_amount, f.currency, f.quality_status,
                               f.source_page, f.issue, r.report_url
                        FROM public.financial_facts f
                        JOIN public.companies c ON c.company_id = f.company_id
                        LEFT JOIN public.financial_reports r ON r.report_id = f.report_id
                        ORDER BY c.ticker, f.fiscal_year NULLS LAST, f.fact_id
                    """)
                    facts272 = cur.fetchall()
            st.session_state["v272_companies"] = companies272
            st.session_state["v272_facts"] = facts272
            st.success(f"Lecture PostgreSQL réussie : {len(companies272)} sociétés et {len(facts272)} observations.")
    except Exception as exc:
        st.error(f"Lecture PostgreSQL impossible ({type(exc).__name__}). Vérifier la connexion et le schéma.")

companies272 = st.session_state.get("v272_companies")
if companies272 is not None:
    if not companies272:
        st.info("Aucune société enregistrée dans PostgreSQL.")
    else:
        catalog272 = pd.DataFrame(companies272)
        st.subheader("Couverture des sociétés")
        st.dataframe(catalog272, hide_index=True, use_container_width=True)
        st.caption("Une société sans observations est affichée dans le catalogue, mais aucun chiffre n'est inventé.")
        names272 = {str(x["ticker"]): str(x["company_name"] or x["ticker"]) for x in companies272}
        tickers272 = list(names272)
        index272 = tickers272.index(selected) if selected in tickers272 else 0
        chosen272 = st.selectbox("Société PostgreSQL", tickers272, index=index272,
                                  format_func=lambda t: f"{t} — {names272[t]}", key="v272_ticker")
        all272 = pd.DataFrame(st.session_state.get("v272_facts", []))
        df272 = all272[all272["ticker"] == chosen272].copy() if not all272.empty else pd.DataFrame()
        if df272.empty:
            st.info("Aucune observation financière enregistrée pour cette société.")
        else:
            counts272 = df272["quality_status"].value_counts()
            ca, cb, cc, cd = st.columns(4)
            ca.metric("Observations", len(df272))
            cb.metric("Provisoires", int(counts272.get("PROVISIONAL", 0)))
            cc.metric("Bloquées", int(counts272.get("BLOCKED", 0)))
            cd.metric("Validées", int(counts272.get("VALIDATED", 0)))
            st.subheader("Historique financier — valeurs non certifiées identifiées")
            annual272 = df272[
                df272["quality_status"].isin(["PROVISIONAL", "VALIDATED"])
                & df272["fiscal_year"].notna()
                & df272["amount"].notna()
                & df272["fiscal_period"].eq("ANNUEL")
                & df272["period_basis"].eq("REPORTED")
                & df272["currency"].eq("XOF")
            ].copy()
            if annual272.empty:
                st.info("Aucune donnée annuelle XOF admissible à la visualisation.")
            else:
                annual272["fiscal_year"] = annual272["fiscal_year"].astype(int)
                annual272["amount_milliards"] = pd.to_numeric(annual272["amount"], errors="coerce") / 1_000_000_000
                # Écarter les couples année/indicateur ambigus plutôt que choisir arbitrairement une valeur.
                dup272 = annual272.duplicated(["fiscal_year", "standardized_account"], keep=False)
                if dup272.any():
                    st.warning("Certaines années/indicateurs ont plusieurs observations : exclus des graphiques et taux de croissance jusqu'à réconciliation.")
                plot272 = annual272.loc[~dup272].copy()
                labels272 = {"PNB": "Produit net bancaire", "NET_INCOME": "Résultat net",
                             "OPERATING_INCOME": "Résultat d'exploitation", "REVENUE": "Chiffre d'affaires",
                             "EQUITY": "Capitaux propres"}
                metrics272 = sorted(plot272["standardized_account"].dropna().unique())
                for metric272 in metrics272:
                    data272 = plot272[plot272["standardized_account"] == metric272].sort_values("fiscal_year")
                    if data272.empty:
                        continue
                    st.markdown(f"#### {labels272.get(metric272, metric272)}")
                    st.bar_chart(data272.set_index("fiscal_year")["amount_milliards"],
                                 y_label="Milliards FCFA", x_label="Exercice")
                    display272 = data272[["fiscal_year", "amount_milliards", "quality_status"]].rename(
                        columns={"fiscal_year": "Exercice", "amount_milliards": "Milliards FCFA", "quality_status": "Statut"})
                    st.dataframe(display272, hide_index=True, use_container_width=True)
                    if len(data272) > 1:
                        data272 = data272.copy()
                        data272["annee_precedente"] = data272["fiscal_year"].shift(1)
                        data272["croissance"] = data272["amount_milliards"].pct_change(fill_method=None) * 100
                        valid272 = data272[
                            (data272["fiscal_year"] - data272["annee_precedente"] == 1)
                            & (data272["amount_milliards"].shift(1) != 0)
                            & data272["croissance"].notna()
                        ]
                        if not valid272.empty:
                            st.caption("Variation annuelle indicative (valeurs non certifiées) : " +
                                       " ; ".join(f"{int(r.fiscal_year)} : {r.croissance:+.2f} %" for r in valid272.itertuples()))
            st.subheader("Audit et traçabilité des observations")
            st.dataframe(df272, hide_index=True, use_container_width=True)
            st.download_button("Exporter les observations PostgreSQL (CSV)",
                               df272.to_csv(index=False).encode("utf-8-sig"),
                               f"postgresql_{chosen272}_v272.csv", "text/csv", key="v272_export")
            st.warning("Les données PROVISIONAL restent à confronter aux rapports officiels. Les données BLOCKED et les observations ambiguës ne servent jamais aux graphiques ni à la croissance. Aucun transfert automatique vers les scores ou valorisations.")

# V2.7.3 — Revue documentaire et dossier de validation (sans écriture en base)
st.header("V2.7.3 — Revue documentaire des observations")
st.caption("Comparer chaque observation au PDF officiel. Les conclusions sont exportées pour audit ; aucun statut PostgreSQL n'est modifié automatiquement.")
review_facts273 = pd.DataFrame(st.session_state.get("v272_facts", []))
if review_facts273.empty:
    st.info("Charger d'abord les sociétés et observations avec le bouton V2.7.2 ci-dessus.")
else:
    review_tickers273 = sorted(review_facts273["ticker"].dropna().astype(str).unique().tolist())
    review_ticker273 = st.selectbox("Société à contrôler", review_tickers273, key="v273_ticker")
    review_df273 = review_facts273[
        (review_facts273["ticker"] == review_ticker273)
        & (review_facts273["quality_status"] == "PROVISIONAL")
    ].copy()
    st.metric("Observations provisoires à examiner", len(review_df273))
    if review_df273.empty:
        st.info("Aucune observation provisoire à contrôler pour cette société.")
    else:
        review_df273 = review_df273.sort_values("fact_id")
        review_ids273 = review_df273["fact_id"].astype(int).tolist()
        review_id273 = st.selectbox("Observation à examiner", review_ids273,
            format_func=lambda fid: (
                f"#{fid} · " + str(review_df273.loc[review_df273['fact_id'] == fid, 'standardized_account'].iloc[0])
                + " · " + str(review_df273.loc[review_df273['fact_id'] == fid, 'fiscal_year'].iloc[0])
            ), key="v273_fact")
        fact273 = review_df273[review_df273["fact_id"] == review_id273].iloc[0]
        st.write("**Indicateur :**", fact273["standardized_account"])
        st.write("**Exercice proposé :**", fact273["fiscal_year"])
        st.write("**Montant proposé (FCFA) :**", str(fact273["amount"]))
        st.write("**Page indiquée :**", fact273.get("source_page"))
        source273 = fact273.get("report_url")
        if pd.notna(source273) and str(source273).startswith("https://"):
            st.link_button("Ouvrir le rapport officiel pour vérification", str(source273))
        else:
            st.error("URL officielle manquante : la revue ne peut pas être conclue.")
        st.warning("La présence d'un lien ne prouve pas l'exactitude des chiffres. Contrôler directement les tableaux, les en-têtes N/N-1 et l'unité affichée dans le PDF.")
        with st.form("v273_review_form", clear_on_submit=True):
            reviewer273 = st.text_input("Nom du vérificateur")
            page273 = st.text_input("Page PDF réellement vérifiée")
            year273 = st.checkbox("Exercice et colonne N/N-1 confirmés dans le PDF")
            value273 = st.checkbox("Montant et signe comparés visuellement au PDF")
            unit273 = st.checkbox("Devise et facteur d'échelle explicitement vérifiés")
            scope273 = st.checkbox("Périmètre et période (annuel, individuel/consolidé) confirmés")
            conclusion273 = st.selectbox("Conclusion de la revue", ["À revoir", "Concordance documentaire", "Écart constaté"])
            notes273 = st.text_area("Preuve / commentaire / anomalie")
            submitted273 = st.form_submit_button("Enregistrer cette revue dans la session")
        if submitted273:
            if not reviewer273.strip() or not page273.strip() or not notes273.strip():
                st.error("Renseigner le vérificateur, la page et une preuve/commentaire.")
            elif conclusion273 == "Concordance documentaire" and not all([year273, value273, unit273, scope273]):
                st.error("Une concordance exige la confirmation de tous les contrôles.")
            elif conclusion273 == "Concordance documentaire" and not (pd.notna(source273) and str(source273).startswith("https://")):
                st.error("La source officielle doit être disponible.")
            else:
                from datetime import datetime, timezone
                row273 = {
                    "fact_id": int(review_id273), "ticker": review_ticker273,
                    "standardized_account": str(fact273["standardized_account"]),
                    "fiscal_year": str(fact273["fiscal_year"]),
                    "amount": str(fact273["amount"]), "report_url": str(source273),
                    "source_page_verified": page273.strip(), "reviewer": reviewer273.strip(),
                    "year_checked": year273, "amount_checked": value273,
                    "unit_checked": unit273, "scope_checked": scope273,
                    "conclusion": conclusion273, "evidence_notes": notes273.strip(),
                    "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
                    "database_status_unchanged": "PROVISIONAL"
                }
                st.session_state.setdefault("v273_reviews", {})[int(review_id273)] = row273
                st.success("Revue conservée dans cette session. Exporter le dossier CSV avant de fermer l'application.")
    reviewed273 = list(st.session_state.get("v273_reviews", {}).values())
    if reviewed273:
        st.subheader("Journal des revues documentaires")
        audit273 = pd.DataFrame(reviewed273)
        st.dataframe(audit273, hide_index=True, use_container_width=True)
        st.download_button("Télécharger le dossier de revue V2.7.3 (CSV)",
            audit273.to_csv(index=False).encode("utf-8-sig"),
            "revue_documentaire_v273.csv", "text/csv", key="v273_export")
    st.info("Étape suivante : après vérification humaine du dossier et mise en place d'un journal d'audit persistant, une procédure séparée pourra autoriser le statut VALIDATED. Cette version ne modifie aucune donnée financière dans PostgreSQL.")

# V2.7.5 — Résolution documentée des observations bloquées (lecture seule)
st.divider()
st.header("V2.7.5 — Diagnostic et résolution des anomalies")
st.caption("BOAB : correspondances identifiées dans le rapport officiel 2025, pages PDF 2 et 4. L'interface ne modifie pas PostgreSQL.")
source275 = "https://www.brvm.org/sites/default/files/20260415_-_etats_financiers_-_exercice_2025_-_boa_bn.pdf"
st.link_button("Consulter le rapport officiel BOAB 2025", source275)
expected275 = pd.DataFrame([
    {"Compte":"EQUITY", "Montant FCFA":117396355692, "Exercice":2024, "Page PDF":2, "Code source":"RBP_0090"},
    {"Compte":"EQUITY", "Montant FCFA":117506988771, "Exercice":2025, "Page PDF":2, "Code source":"RBP_0090"},
    {"Compte":"OPERATING_INCOME", "Montant FCFA":21455499312, "Exercice":2024, "Page PDF":4, "Code source":"RCR_0160"},
])
st.subheader("Correspondances documentaires identifiées")
st.dataframe(expected275, hide_index=True, use_container_width=True)
all275 = pd.DataFrame(st.session_state.get("v272_facts", []))
if all275.empty:
    st.info("Clique d'abord sur « Charger les sociétés et les données PostgreSQL » dans la section V2.7.2.")
else:
    boab275 = all275[all275["ticker"] == "BOAB"].copy()
    blocked275 = boab275[boab275["quality_status"] == "BLOCKED"].copy()
    st.metric("Observations BOAB encore bloquées (dernière lecture)", len(blocked275))
    if not blocked275.empty:
        st.dataframe(blocked275[["fact_id", "standardized_account", "amount", "fiscal_year", "source_page", "quality_status", "issue"]],
                     hide_index=True, use_container_width=True)
    expected_pairs275 = {(r["Compte"], int(r["Montant FCFA"])) for _,r in expected275.iterrows()}
    current_pairs275 = set()
    for _, r in blocked275.iterrows():
        try:
            current_pairs275.add((str(r["standardized_account"]), int(r["amount"])))
        except (TypeError, ValueError):
            pass
    if len(blocked275) == 3 and current_pairs275 == expected_pairs275:
        st.success("Les trois montants BLOCKED correspondent au dossier de résolution. Exécuter le script SQL séparé après sauvegarde.")
    elif len(blocked275) == 0:
        validated275 = boab275[boab275["quality_status"] == "VALIDATED"]
        found275 = {(str(r["standardized_account"]), int(r["amount"]))
                    for _,r in validated275.iterrows() if pd.notna(r["amount"])}
        if expected_pairs275.issubset(found275):
            st.success("Les trois observations corrigées sont désormais VALIDATED dans les données rechargées.")
        else:
            st.warning("Aucune observation BLOCKED, mais les trois montants attendus ne sont pas tous VALIDATED.")
    else:
        st.error("État inattendu : ne pas exécuter le script SQL. Examiner les observations et doublons.")
    st.warning("Après exécution dans Supabase, recharger les données PostgreSQL pour actualiser cet écran. Vérifier aussi les événements d'audit.")
st.download_button("Exporter le référentiel de correction V2.7.5 (CSV)",
                   expected275.to_csv(index=False).encode("utf-8-sig"),
                   "BOAB_referentiel_anomalies_V275.csv", "text/csv", key="v275_reference_csv")


# V2.7.6 — Extension multi-sociétés : inventaire, qualité et préparation d'import
# Lecture seule PostgreSQL : aucun ajout ni validation automatique.
st.divider()
st.header("V2.7.6 — Extension multi-sociétés BRVM")
st.caption("Inventaire PostgreSQL, couverture des données VALIDATED et préparation sécurisée des imports. Aucune écriture en base.")
companies276 = pd.DataFrame(st.session_state.get("v272_companies", []))
facts276 = pd.DataFrame(st.session_state.get("v272_facts", []))
if companies276.empty:
    st.info("Clique d'abord sur « Charger les sociétés et les données PostgreSQL » dans V2.7.2, puis reviens ici.")
else:
    summary276 = companies276[["ticker", "company_name"]].drop_duplicates().copy()
    if facts276.empty:
        summary276["Observations"] = 0
        summary276["VALIDATED"] = 0
        summary276["PROVISIONAL"] = 0
        summary276["BLOCKED"] = 0
        summary276["REJECTED"] = 0
    else:
        for stat276 in ("VALIDATED", "PROVISIONAL", "BLOCKED", "REJECTED"):
            counts276 = facts276.loc[facts276["quality_status"].eq(stat276)].groupby("ticker").size()
            summary276[stat276] = summary276["ticker"].map(counts276).fillna(0).astype(int)
        summary276["Observations"] = summary276[["VALIDATED", "PROVISIONAL", "BLOCKED", "REJECTED"]].sum(axis=1)
    summary276["Couverture validée (%)"] = (100 * summary276["VALIDATED"] / summary276["Observations"].replace(0, float("nan"))).round(1)
    summary276["Couverture validée (%)"] = summary276["Couverture validée (%)"].fillna(0)
    c1_276, c2_276, c3_276, c4_276 = st.columns(4)
    c1_276.metric("Sociétés enregistrées", len(summary276))
    c2_276.metric("Sociétés avec données", int((summary276["Observations"] > 0).sum()))
    c3_276.metric("Observations validées", int(summary276["VALIDATED"].sum()))
    c4_276.metric("Observations à revoir", int(summary276["PROVISIONAL"].sum() + summary276["BLOCKED"].sum()))
    st.subheader("Tableau de couverture multi-sociétés")
    st.dataframe(summary276.sort_values(["VALIDATED", "ticker"], ascending=[False, True]), hide_index=True, use_container_width=True)
    st.download_button("Exporter la couverture BRVM (CSV)", summary276.to_csv(index=False).encode("utf-8-sig"), "brvm_couverture_V276.csv", "text/csv", key="v276_cov_export")
    st.subheader("Sociétés à compléter")
    missing276 = summary276[summary276["Observations"] == 0]
    if missing276.empty:
        st.success("Toutes les sociétés du catalogue possèdent au moins une observation.")
    else:
        st.dataframe(missing276[["ticker", "company_name"]], hide_index=True, use_container_width=True)
    st.subheader("Jeu de données éligible pour BRVM Radar")
    st.caption("Uniquement les montants annuels, déclarés, en XOF et VALIDATED. Les couples société/exercice/compte dupliqués sont écartés.")
    if facts276.empty:
        st.info("Aucune observation à exporter.")
    else:
        eligible276 = facts276.loc[
            facts276["quality_status"].eq("VALIDATED")
            & facts276["fiscal_year"].notna()
            & facts276["amount"].notna()
            & facts276["fiscal_period"].eq("ANNUEL")
            & facts276["period_basis"].eq("REPORTED")
            & facts276["currency"].eq("XOF")
        ].copy()
        if not eligible276.empty:
            keys276 = ["ticker", "fiscal_year", "standardized_account"]
            ambiguous276 = eligible276.duplicated(keys276, keep=False)
            excluded276 = int(ambiguous276.sum())
            eligible276 = eligible276.loc[~ambiguous276].copy()
            if excluded276:
                st.warning(f"{excluded276} lignes écartées pour doublons/ambiguïtés (société, exercice, compte).")
        st.metric("Observations éligibles", len(eligible276))
        if not eligible276.empty:
            st.dataframe(eligible276[["ticker", "fiscal_year", "standardized_account", "amount", "currency", "quality_status", "source_page", "report_url"]], hide_index=True, use_container_width=True)
            st.download_button("Exporter les données VALIDATED pour Radar (CSV)", eligible276.to_csv(index=False).encode("utf-8-sig"), "brvm_radar_validated_V276.csv", "text/csv", key="v276_radar_export")
        else:
            st.info("Aucune donnée ne satisfait l'ensemble des conditions de publication.")

st.subheader("Préparer l'intégration d'une nouvelle société")
st.caption("Le fichier CSV est examiné localement dans Streamlit et n'est pas inséré dans Supabase. Toute observation nouvelle reste PROVISIONAL ou BLOCKED jusqu'à revue documentaire.")
example276 = pd.DataFrame([{
    "ticker": "EXEMPLE", "company_name": "Société exemple", "fiscal_year": 2025,
    "fiscal_period": "ANNUEL", "period_basis": "REPORTED", "standardized_account": "NET_INCOME",
    "amount": "", "currency": "XOF", "source_page": "", "report_url": "",
    "quality_status": "PROVISIONAL", "evidence": ""
}])
st.download_button("Télécharger le modèle d'import (CSV)", example276.to_csv(index=False).encode("utf-8-sig"), "modele_import_BRVM_V276.csv", "text/csv", key="v276_template")
up276 = st.file_uploader("Contrôler un fichier CSV avant intégration", type=["csv"], key="v276_csv")
if up276 is not None:
    try:
        import io
        candidate276 = pd.read_csv(io.BytesIO(up276.getvalue()), dtype=str, encoding="utf-8-sig", sep=None, engine="python").fillna("")
        required276 = list(example276.columns)
        absent276 = [col for col in required276 if col not in candidate276.columns]
        if absent276:
            st.error("Colonnes manquantes : " + ", ".join(absent276))
        elif candidate276.empty:
            st.warning("Fichier vide.")
        else:
            issues276 = []
            valid_accounts276 = {"PNB", "NET_INCOME", "OPERATING_INCOME", "EQUITY", "REVENUE"}
            existing276 = set()
            if not facts276.empty:
                for r276 in facts276.itertuples(index=False):
                    try:
                        existing276.add((str(r276.ticker).strip().upper(), int(r276.fiscal_year), str(r276.standardized_account).strip().upper()))
                    except (ValueError, TypeError):
                        pass
            seen276 = set()
            for idx276, r276 in candidate276.iterrows():
                problems276 = []
                tick276 = str(r276["ticker"]).strip().upper()
                account276 = str(r276["standardized_account"]).strip().upper()
                try:
                    yr276 = int(r276["fiscal_year"])
                    if not 1990 <= yr276 <= 2100:
                        raise ValueError()
                except ValueError:
                    yr276 = None
                    problems276.append("exercice invalide")
                try:
                    from decimal import Decimal
                    val276 = Decimal(str(r276["amount"]).strip())
                    if not val276.is_finite():
                        raise ValueError()
                except (ValueError, ArithmeticError):
                    problems276.append("montant absent/invalide")
                if not tick276 or not str(r276["company_name"]).strip():
                    problems276.append("société non renseignée")
                if account276 not in valid_accounts276:
                    problems276.append("compte non reconnu (mapping requis)")
                if r276["fiscal_period"] != "ANNUEL" or r276["period_basis"] != "REPORTED":
                    problems276.append("période/base à examiner")
                if r276["currency"] != "XOF":
                    problems276.append("devise/unité à examiner")
                if not str(r276["report_url"]).startswith("https://"):
                    problems276.append("URL source HTTPS absente")
                if not str(r276["source_page"]).strip():
                    problems276.append("page source absente")
                if r276["quality_status"] not in ("PROVISIONAL", "BLOCKED"):
                    problems276.append("statut d'import interdit : utiliser PROVISIONAL ou BLOCKED")
                key276 = (tick276, yr276, account276)
                if key276 in existing276:
                    problems276.append("observation déjà présente en base (contrôle manuel)")
                if key276 in seen276:
                    problems276.append("doublon dans le CSV")
                seen276.add(key276)
                issues276.append(" ; ".join(problems276))
            reviewed276 = candidate276.copy()
            reviewed276.insert(0, "ligne_csv", range(2, len(reviewed276) + 2))
            reviewed276["anomalies_import"] = issues276
            st.metric("Lignes à corriger", sum(bool(x) for x in issues276))
            st.dataframe(reviewed276, hide_index=True, use_container_width=True)
            st.download_button("Exporter le rapport de précontrôle (CSV)", reviewed276.to_csv(index=False).encode("utf-8-sig"), "controle_import_V276.csv", "text/csv", key="v276_preflight")
            st.info("Précontrôle uniquement : aucune société, aucun rapport et aucune observation n'ont été créés dans PostgreSQL.")
    except Exception as exc:
        st.error(f"Impossible de lire le CSV ({type(exc).__name__}). Vérifier l'encodage et le séparateur.")
