# Hades backend product backlog

Note di prodotto per le prossime implementazioni del backend e del suo
frontend. Questi punti descrivono problemi osservati e direzioni da progettare;
non sono ancora specifiche implementative approvate.

## Memoria di progetto: vista umana e dati grezzi

Esempio osservato:
`/projects/01KX8G47N6HK2AC4NSVS912ECJ/memory`.

La pagina mostra correttamente tutti i chunk di memoria caricati, ma il dump e'
poco leggibile e poco utile per una persona. Prima di modificare la UI occorre
stabilire:

- se i chunk grezzi debbano essere visibili nella vista principale;
- quali informazioni sintetiche servano a un umano per capire cosa il sistema
  sa del progetto;
- se i chunk debbano restare disponibili in una vista tecnica, con ricerca,
  filtri, provenienza, stato di revisione e possibilita' di ispezione puntuale;
- come evitare che una rappresentazione piu' leggibile nasconda provenienza,
  freshness, duplicati o contenuti non verificati.

Direzione da valutare: separare la memoria curata e leggibile dai dati grezzi
di ingestion, mantenendo i chunk come superficie tecnica secondaria anziche'
come rappresentazione primaria della memoria di progetto.

## Wiki: utilita' per macchina e per umano

Esempio osservato:
`/projects/01KX8G47N6HK2AC4NSVS912ECJ/wiki`.

Il popolamento avviene, ma il formato attuale e' piu' adatto a una macchina che
a una persona. La progettazione deve partire da due domande distinte:

1. quali contenuti strutturati servono agli agenti per orientarsi, cercare e
   ragionare sul progetto;
2. quali contenuti narrativi servono a sviluppatori, project manager e altri
   utenti per comprenderlo rapidamente.

Direzione da valutare: mantenere lo stesso URL e offrire due viste chiaramente
separate, per esempio `Human` e `Agent/Structured`, derivate dalle stesse fonti
ma con contratti diversi. La vista umana dovrebbe privilegiare sintesi,
navigazione, concetti, responsabilita' e collegamenti. La vista macchina
dovrebbe preservare struttura, identificatori, riferimenti, provenance,
freshness e formati facilmente interrogabili.

Prima dell'implementazione occorre definire una tassonomia dei contenuti e
stabilire cosa appartiene alla wiki, cosa alla memoria curata, cosa al grafo e
cosa deve restare un artefatto tecnico o chunk grezzo.

## Architettura multiutente e multi-azienda

Progettare l'evoluzione del backend verso un modello multi-tenant:

- registrazione e lifecycle di un'azienda/organizzazione;
- creazione, invito, sospensione e rimozione degli utenti dell'azienda;
- ruoli e permessi definiti nel perimetro dell'organizzazione;
- ownership e isolamento di progetti, memoria, wiki, agenti, token, job e code;
- eventuali utenti appartenenti a piu' organizzazioni;
- audit, amministrazione, recupero account e trasferimento di ownership;
- limiti, quote e configurazioni per organizzazione.

La progettazione deve esplicitare tenant boundary e authorization matrix prima
di modificare schema o API. Nessun identificatore fornito dal client deve
permettere accesso cross-tenant senza una verifica server-side dell'appartenenza
e del ruolo effettivo.

## Platon task clarifier

Il clarifier attuale formula domande non sempre coerenti con il task. Va
perfezionato come processo `memory-first`, non come generatore immediato di
domande generiche.

Comportamento desiderato:

1. comprendere e classificare il task ricevuto;
2. consultare memoria curata, wiki e contesto del progetto pertinenti;
3. distinguere fatti gia' noti, inferenze e informazioni realmente mancanti;
4. formulare solo domande che possono cambiare materialmente piano, scope,
   comportamento o criteri di accettazione;
5. motivare internamente ogni domanda con il gap che deve risolvere;
6. basare proposte e opzioni sui fatti trovati nella memoria e nella wiki,
   citandone la provenienza quando viene mostrata all'utente;
7. evitare domande gia' risolte dal contesto, irrilevanti per il task o troppo
   generiche per produrre una decisione operativa.

Aspetti da progettare e misurare:

- retrieval e ranking del contesto prima della generazione delle domande;
- soglia oltre la quale Platon procede con assunzioni esplicite invece di
  chiedere;
- numero massimo e ordinamento delle domande;
- gestione di memoria assente, contraddittoria, obsoleta o non verificata;
- tracciabilita' tra domanda, evidenza consultata e decisione conseguente;
- set di task realistici per valutare pertinenza, non ridondanza e utilita'
  delle domande prima del rilascio.

## Passo successivo comune

Prima di implementare uno di questi punti, produrre una breve discovery basata
su dati reali del backend: schema e volume dei chunk, esempi di wiki generata,
profili degli utenti, flusso attuale di Platon e vincoli di autorizzazione.
Separare poi le specifiche e i piani di esecuzione per evitare di accoppiare UX
di memoria/wiki, multi-tenancy e reasoning del clarifier in un'unica modifica.
