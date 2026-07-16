# qSign stability audit - Phase 2

Data audit: 2026-07-16

## 1. Giudizio sintetico

**Utilizzabile con limitazioni.**

Lo stato attuale mostra basi buone per uso controllato: apertura PDF con PyMuPDF protetta da errori, salvataggio firmato su copia, filtri ERP lato server e lato client, SOAP con `connect -> getDocument -> disconnect`, credenziali persistite via DPAPI e test unitari verdi.

Non lo considero ancora pienamente solido per uso quotidiano senza presidio, per quattro rischi alti: possibile sovrascrittura silenziosa di copie firmate con lo stesso nome, cancellazione/incrocio dei temporanei ERP tra istanze, callback di download attive dopo chiusura finestra, e chiamate ERP sincrone sulla UI con freeze fino al timeout. Non sono emersi problemi bloccanti che impongano di fermare ogni uso.

## 2. Aspetti verificati come corretti

- Test automatici: `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` ha eseguito 229 test, tutti OK.
- Compilazione: `.\.venv\Scripts\python.exe -m compileall app ui` completato senza errori.
- Strumenti statici: `ruff`, `mypy` e `pyflakes` non risultano installati nel virtualenv; non sono state installate nuove dipendenze.
- Stato Git verificato: presenti modifiche non committate e file non tracciati, inclusi `app/services/infinity_dms_client.py` e `tests/test_infinity_dms_client.py`; l'audit li considera parte dello stato attuale.
- Il download SOAP usa `ElementTree` per costruire XML, quindi i valori con caratteri speciali non vengono concatenati a stringa: `app/services/infinity_dms_client.py`, `InfinityDmsClient._connect`, `_get_document`, `_disconnect`, `_soap_envelope`, righe 82-118 e 160-167.
- La sequenza SOAP tenta `disconnect` nel `finally` quando `connect` ha restituito un contesto: `app/services/infinity_dms_client.py`, `InfinityDmsClient.download_document`, righe 51-75; coperto da `tests/test_infinity_dms_client.py`, righe 89-109.
- Base64 vuoto, non valido e contenuto non PDF sono rifiutati almeno tramite magic header `%PDF-`: `app/services/infinity_dms_client.py`, `_decode_pdf_base64`, righe 202-214; coperto da `tests/test_infinity_dms_client.py`, righe 126-142.
- Le chiamate HTTP ERP e SOAP hanno timeout di 8 secondi: documenti ERP in `app/services/general_preferences_service.py`, `fetch_erp_documents`, righe 374-375; SOAP in `app/services/infinity_dms_client.py`, `_post`, riga 132.
- L'elenco documenti ERP aggiunge `pVFCHECKOUTBY` alla query e applica anche filtro locale su `vfcheckoutby`: `app/services/general_preferences_service.py`, `fetch_erp_documents`, righe 362-367, e `_erp_document_from_row`, righe 345-402 e funzioni successive; coperto da `tests/test_general_preferences_service.py`, casi intorno alle righe 559-613.
- Le colonne della griglia ERP non espongono `vfauthcode`: coperto da `tests/test_main_view.py`, `test_erp_documents_grid_shows_safe_columns_only`.
- Il doppio click su un documento ERP viene serializzato con lock: `ui/main_view.py`, `MainView._open_erp_document`, righe 992-1037; coperto da `tests/test_main_view.py`, righe 284-329.
- I nomi file temporanei ERP sono neutralizzati e prefissati con UUID: `ui/main_view.py`, `_safe_erp_document_filename`, righe 57-63, e `_save_erp_temp_pdf`, righe 1062-1069.
- Le password ERP, SOAP URL completi e token Base64 non vengono loggati nei punti principali ispezionati. Il logging ERP registra solo booleani di configurazione/cambiamento: `app/services/general_preferences_service.py`, `_log_erp_user_settings_saved`, righe 627-677; coperto da `tests/test_general_preferences_service.py`, righe 179-190.
- L'apertura PDF locale rifiuta file mancanti, PDF cifrati e PDF senza pagine: `services/pdf/providers/pymupdf_renderer.py`, `PyMuPDFRenderer.open_document`, righe 46-91.
- Il salvataggio firmato del flusso normale produce una copia e poi chiude il documento aperto: `app/pdf_viewer_controller.py`, `PDFViewerController.save_signed_pdf`, righe 839-899.

## 3. Problemi ordinati per gravita

### ALTO - Le copie firmate possono sovrascriversi silenziosamente

- File, classe, funzione: `services/pdf/pdf_service.py`, `PDFService.save_signed_preview`, `save_signed_previews`, `_default_signed_preview_path`; `services/pdf/providers/pyhanko_digital_signature_writer.py`, `PyHankoDigitalSignatureWriter.sign_pdf` e `_sign_pdf_with_pyhanko`.
- Scenario concreto: due documenti locali o ERP hanno lo stesso stem, per esempio `VISITA.pdf`, oppure lo stesso documento viene firmato due volte. Il percorso predefinito e' sempre `dist/signed/<stem>_signed.pdf`.
- Conseguenza per l'utente: la seconda firma puo' sostituire la prima copia firmata nello storico, con perdita di un documento firmato gia' prodotto.
- Evidenza nel codice o nei test: `PDFService` calcola sempre lo stesso nome quando `destination` e' assente, righe 116-120 e 146-150; `_default_signed_preview_path` restituisce `f"{source.stem}_signed.pdf"`. Il writer digitale apre la destinazione con `destination.open("wb")`, quindi tronca un file esistente, `services/pdf/providers/pyhanko_digital_signature_writer.py`, righe 181-193. I test usano sempre un solo salvataggio atteso su `sample_signed.pdf` e non coprono collisioni.
- Correzione minima consigliata: rendere il nome di output univoco, per esempio timestamp/UUID o suffisso incrementale, e rifiutare sovrascritture non confermate.
- Test necessario: firmare due PDF con stesso stem o due volte lo stesso PDF e verificare che entrambi i file restino presenti e diversi, senza apertura `wb` su destinazione esistente non confermata.

### ALTO - I temporanei ERP sono condivisi tra istanze e il cleanup puo' eliminare file di un'altra sessione

- File, classe, funzione: `ui/main_view.py`, `MainView.__init__`, `_cleanup_orphaned_erp_temp_files`, `_cleanup_erp_temp_files`, `_erp_temp_root`.
- Scenario concreto: una seconda istanza di qSign parte mentre la prima ha appena scaricato un PDF ERP o lo sta per aprire. Entrambe usano `%TEMP%\qsign\erp_documents`; all'avvio la seconda elimina ogni `*.pdf` trovato.
- Conseguenza per l'utente: il documento scaricato dalla prima istanza puo' sparire prima dell'apertura o mentre l'utente sta ancora lavorando, causando errore di apertura/salvataggio o perdita della copia temporanea necessaria per firmare.
- Evidenza nel codice o nei test: `_erp_temp_root` e' globale, righe 1071-1073; `_cleanup_orphaned_erp_temp_files` elimina tutti i PDF della cartella, righe 328-338; `_cleanup_erp_temp_files` elimina i file registrati dalla vista corrente, righe 319-326. Il test `test_erp_orphaned_temp_pdfs_are_removed_on_startup` valida proprio la cancellazione globale, ma non simula due istanze.
- Correzione minima consigliata: usare una sottocartella per istanza/sessione, con marker/lock owner, e cancellare all'avvio solo file chiaramente orfani e vecchi oltre una soglia.
- Test necessario: creare due istanze simulate con root separabile/owner diverso e verificare che il cleanup della seconda non cancelli i file attivi della prima.

### ALTO - Un download ERP puo' completare dopo la chiusura della finestra e aggiornare una UI non piu' valida

- File, classe, funzione: `ui/main_view.py`, `MainView._open_erp_document`, `run_background_task`, `run_ui_task`, `stop_background_tasks`; `app/qsign_application.py`, `QSignApplication._bind_shutdown`.
- Scenario concreto: l'utente clicca "Apri" su un documento ERP e chiude qSign durante il download SOAP. `stop_background_tasks` ferma solo l'auto-refresh; il thread di download resta daemon e alla fine chiama `run_ui_task` per aggiornare stato e aprire il PDF.
- Conseguenza per l'utente: possibile eccezione Flet/session closed, blocco o apertura di un documento dopo che l'app ha iniziato lo shutdown; inoltre il file temporaneo creato dopo il cleanup non viene rimosso.
- Evidenza nel codice o nei test: `run_background_task` crea thread daemon senza handle/cancel, righe 624-625; `_open_erp_document` schedula `_finish_erp_document_download`, righe 1011-1037; `_finish_erp_document_download` aggiorna UI e invoca `_on_open_document`, righe 1046-1060; `stop_background_tasks` non attende download, righe 312-317. Non ci sono test per risposta tardiva dopo chiusura.
- Correzione minima consigliata: aggiungere flag `_closing` o generation token e cancellazione cooperativa; nello shutdown marcare la vista chiusa, impedire callback UI/open document, rilasciare lock e pulire eventuale file creato.
- Test necessario: avviare download finto sospeso, chiamare `stop_background_tasks`, completare il fake client e verificare che non vengano chiamati ne' update UI ne' `on_open_document`, e che il lock venga rilasciato.

### ALTO - Le chiamate ERP manuali bloccano il thread UI fino al timeout

- File, classe, funzione: `ui/main_view.py`, `MainView.refresh_erp_documents`, `show_user_preferences.load_users`, `show_user_preferences.test_users`; `app/services/general_preferences_service.py`, `fetch_erp_documents`, `fetch_erp_users`.
- Scenario concreto: ERP o rete non disponibili; l'utente preme refresh documenti, carica utenti, testa utenti o seleziona utente. La chiamata HTTP avviene direttamente nel callback UI.
- Conseguenza per l'utente: interfaccia congelata fino a 8 secondi per chiamata, ripetibile; con piu' azioni ravvicinate sembra un blocco applicativo.
- Evidenza nel codice o nei test: `refresh_erp_documents` chiama direttamente `fetch_erp_documents`, righe 645-658; `load_users` e `test_users` chiamano direttamente `fetch_erp_users`, righe 1799-1819. I timeout sono nel servizio, ma non c'e' thread di lavoro per questi percorsi. I test usano fake immediati.
- Correzione minima consigliata: eseguire fetch documenti/utenti in background con stato "caricamento", disabilitare i pulsanti interessati e rientrare sulla UI solo con token valido.
- Test necessario: fake opener che resta bloccato; verificare che il callback UI ritorni subito, che i pulsanti vengano disabilitati e poi ripristinati su successo/errore.

### ALTO - L'URL utenti ERP puo' essere HTTP e inviare Basic Auth in chiaro

- File, classe, funzione: `app/services/general_preferences_service.py`, `GeneralPreferencesService.save_erp_user_settings`, `fetch_erp_users`.
- Scenario concreto: in preferenze amministratore viene salvato `http://...` come URL utenti ERP; il caricamento utenti usa Basic Auth su HTTP.
- Conseguenza per l'utente: esposizione di username/password Basic Auth sulla rete.
- Evidenza nel codice o nei test: `save_erp_user_settings` impone HTTPS solo a `documents_url` e `document_service_url`, righe 217-227; `fetch_erp_users` accetta sia `https://` sia `http://`, righe 304-318. I test validano HTTPS per documenti, non per utenti.
- Correzione minima consigliata: richiedere HTTPS anche per `users_url`, salvo override esplicito solo per ambienti di test locali.
- Test necessario: salvare/fetchare `http://erp.../users` e verificare errore prima della costruzione dell'header Authorization.

### MEDIO - Il PDF scaricato via SOAP non viene validato come PDF apribile prima di scriverlo

- File, classe, funzione: `app/services/infinity_dms_client.py`, `_decode_pdf_base64`; `ui/main_view.py`, `_save_erp_temp_pdf`, `_finish_erp_document_download`.
- Scenario concreto: il SOAP restituisce Base64 valido che inizia con `%PDF-` ma e' troncato, cifrato, enorme o corrotto. Il client lo accetta, lo scrive su disco e solo dopo il viewer fallisce in apertura.
- Conseguenza per l'utente: errore tardivo, file temporaneo inutile lasciato in tracking finche' l'app resta aperta, possibile consumo disco con Base64 molto grande.
- Evidenza nel codice o nei test: `_decode_pdf_base64` controlla solo vuoto/Base64/magic header, righe 202-214; `_save_erp_temp_pdf` scrive direttamente i bytes, righe 1062-1069. I test usano `b"%PDF-demo"`, che non e' un PDF reale, quindi non dimostrano validita' strutturale.
- Correzione minima consigliata: dopo il decode aprire il contenuto con PyMuPDF da stream o validatore equivalente, imporre limite dimensione ragionevole e rifiutare PDF cifrati/vuoti.
- Test necessario: Base64 di `%PDF-` troncato e PDF cifrato devono fallire prima della scrittura temporanea; PDF reale valido deve passare.

### MEDIO - La risposta documenti ERP malformata/incompleta viene trattata come lista vuota

- File, classe, funzione: `app/services/general_preferences_service.py`, `_parse_erp_documents`, `fetch_erp_documents`.
- Scenario concreto: l'ERP cambia formato o restituisce `{ "error": ... }`, oppure `data` manca/non e' lista.
- Conseguenza per l'utente: qSign mostra "Nessun documento da firmare" invece di un errore di integrazione; un medico puo' credere che non ci siano documenti da firmare.
- Evidenza nel codice o nei test: `_parse_erp_documents` ritorna `[]` se payload non e' dict o `data` non e' lista; `fetch_erp_documents` converte comunque in `success=True`, righe 389-402 e funzioni successive. Il test `test_erp_documents_require_https_and_valid_json_data` verifica JSON invalido, ma il caso JSON valido con schema sbagliato risulta "nessun documento".
- Correzione minima consigliata: distinguere risposta vuota valida (`data: []`) da schema inatteso; per schema inatteso restituire `success=False`.
- Test necessario: payload `{}` o `{"data": {}}` deve produrre errore controllato, non empty state.

### MEDIO - Cambio utente durante download puo' aprire un documento della sessione precedente

- File, classe, funzione: `ui/main_view.py`, `MainView._open_erp_document`, `show_user_preferences.select_user`.
- Scenario concreto: l'utente avvia il download di un documento per l'utente ERP A, poi cambia utente ERP a B prima che il SOAP risponda. Il callback apre comunque il documento scaricato con le impostazioni catturate al click.
- Conseguenza per l'utente: rischio operativo di firmare un documento non coerente con l'utente ERP visibile nella barra di stato.
- Evidenza nel codice o nei test: `_open_erp_document` cattura `settings` prima del thread, righe 1001-1022, e `_finish_erp_document_download` apre il file senza ricontrollare l'utente corrente, righe 1046-1059; `select_user` aggiorna `_erp_session_user_confirmed` e salva l'utente, righe 1760-1784. Non c'e' token di sessione utente associato al download.
- Correzione minima consigliata: associare al download `selected_user_id` e verificare al completamento che sia ancora quello corrente; in caso contrario scartare il file e mostrare messaggio.
- Test necessario: avviare download finto per utente A, cambiare fake settings a B, completare download e verificare che `on_open_document` non sia chiamato.

### MEDIO - Preferenze corrotte o DPAPI non decifrabile vengono azzerate in silenzio

- File, classe, funzione: `app/services/general_preferences_service.py`, `_read_preferences`, `_read_encrypted_value`.
- Scenario concreto: `config/preferences.json` viene troncato, contiene JSON corrotto, oppure le preferenze DPAPI sono lette da un diverso utente Windows.
- Conseguenza per l'utente: impostazioni ERP/certificati/admin sembrano sparite senza spiegazione; l'operatore puo' trovarsi senza utente ERP o senza endpoint configurati.
- Evidenza nel codice o nei test: `_read_preferences` ritorna `{}` su `OSError`/`JSONDecodeError`, righe 728-735; `_read_encrypted_value` ritorna stringa vuota su errore DPAPI, righe 705-720. Non viene loggato warning ne' mostrato messaggio all'utente.
- Correzione minima consigliata: loggare warning non sensibile e mostrare stato "preferenze non leggibili"; mantenere backup automatico prima della scrittura.
- Test necessario: file JSON corrotto e valore DPAPI non decifrabile devono produrre warning controllato senza segreti e messaggio diagnostico.

### MEDIO - Scrittura preferenze non atomica

- File, classe, funzione: `app/services/general_preferences_service.py`, `_write_preferences`.
- Scenario concreto: crash, spegnimento o errore disco mentre `preferences.json` viene scritto.
- Conseguenza per l'utente: file preferenze corrotto e, al successivo avvio, reset silenzioso come sopra.
- Evidenza nel codice o nei test: `_write_preferences` usa direttamente `write_text` sul file finale, righe 737-742; non c'e' write su temp file + replace atomico.
- Correzione minima consigliata: scrivere in un file temporaneo nella stessa directory, flush/fsync se opportuno, poi `replace`; tenere `.bak` dell'ultima versione valida.
- Test necessario: simulare errore durante scrittura e verificare che il file precedente resti valido.

### BASSO - Packaging e ambiente installato non sono verificati in modo completo

- File, classe, funzione: `requirements.txt`; `pyproject.toml`; `QSign.spec`.
- Scenario concreto: una macchina nuova installa dipendenze da `requirements.txt` come da README, ma il runtime usa `pyHanko` per la firma digitale.
- Conseguenza per l'utente: l'app installata/dev puo' fallire all'avvio o al salvataggio firmato se `pyHanko` non e' presente, nonostante il test locale passi perche' il venv attuale lo contiene.
- Evidenza nel codice o nei test: `pyproject.toml` include `pyHanko==0.35.2`, ma `requirements.txt` contiene solo Flet e PyMuPDF. `QSign.spec` ha `hiddenimports=[]`; il pacchetto non e' stato validato con build/install pulita durante questo audit.
- Correzione minima consigliata: allineare `requirements.txt` alle dipendenze runtime o usare un solo lock/source, e aggiungere test smoke su ambiente installato/build.
- Test necessario: creare ambiente pulito da `requirements.txt`, eseguire `python -c "import app.main"` e smoke di salvataggio firmato; build PyInstaller e avvio minimale.

## 4. Lacune dei test

- Mancano test per collisione di output firmato e sovrascrittura dello storico.
- Mancano test multi-istanza per cleanup temporanei ERP.
- Mancano test per download SOAP che termina dopo shutdown/finestra chiusa.
- Mancano test per UI non bloccante con opener lento o timeout reale simulato.
- Mancano test per `users_url` HTTP con Basic Auth.
- I fake SOAP accettano `b"%PDF-demo"`: utile per protocollo, ma non valida PDF reale, PDF troncato, cifrato o molto grande.
- Mancano test per schema ERP documenti JSON valido ma inatteso.
- Mancano test per cambio utente durante download in corso.
- Mancano test per preferenze JSON corrotte, DPAPI di altro utente e scrittura preferenze interrotta.
- Mancano test di packaging/installazione pulita rispetto a `requirements.txt`, `pyproject.toml` e `QSign.spec`.

## 5. Checklist prove manuali su Windows

1. Aprire un PDF locale valido, uno su percorso di rete, uno inesistente, uno bloccato senza permessi, uno cifrato, uno vuoto e uno corrotto.
2. Firmare due documenti con lo stesso nome file e verificare che lo storico conservi entrambe le copie senza sovrascrittura.
3. Firmare lo stesso documento due volte nello stesso giorno e verificare nomi output e contenuto.
4. Configurare ERP con rete assente e premere refresh documenti: verificare che la finestra resti responsiva.
5. Configurare URL utenti ERP HTTP e verificare che non sia possibile inviare Basic Auth in chiaro.
6. Avviare download ERP, chiudere subito qSign, poi verificare assenza di errori e temporanei residui.
7. Avviare due istanze qSign: nella prima scaricare/aprire un PDF ERP; nella seconda avviare qSign e verificare che non cancelli il temporaneo della prima.
8. Avviare download per utente ERP A, cambiare utente a B prima della risposta, verificare che il documento di A non venga aperto.
9. Simulare SOAP Fault su `getDocument` e verificare che `disconnect` sia chiamata.
10. Simulare errore su `disconnect` dopo download riuscito e verificare messaggio/log senza perdere il PDF.
11. Simulare Base64 valido ma PDF troncato, PDF cifrato e documento molto grande.
12. Corrompere `config/preferences.json` e verificare messaggio utente/log e recupero da backup.
13. Aprire qSign con un diverso utente Windows rispetto a quello che ha salvato DPAPI e verificare diagnosi chiara.
14. Eseguire build/installazione pulita senza cartella di sviluppo e verificare avvio, apertura PDF, firma e storico.

## 6. Conclusione

Non ci sono bloccanti assoluti. Possiamo attendere la futura API di upload solo se prima vengono risolti almeno i problemi ALTO: output firmati univoci, temporanei ERP isolati per istanza, download cancellabile/ignorato dopo shutdown, fetch ERP non bloccante sulla UI e HTTPS obbligatorio anche per l'endpoint utenti.

I problemi MEDIO possono essere pianificati subito dopo, ma alcuni sono molto vicini all'uso quotidiano: validazione reale del PDF SOAP, schema ERP inatteso e preferenze corrotte meritano copertura prima di estendere il flusso di upload.
