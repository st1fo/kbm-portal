> Táto aplikácia je súčasťou spoločného KBM portálu. Spustenie a prihlasovanie sa riadia hlavným `../../README.md`. Nižšie je pôvodná dokumentácia funkcií samostatnej aplikácie; pôvodné samostatné prihlasovanie a Compose sa v tomto balíku nepoužívajú.

# KBM Workplan

Slovenská aplikácia na tímovú dochádzku. Python 3.12 / FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL 16, React 19 / TypeScript / Vite, Tailwind 4, Radix Dialog (lokálny shadcn/ui-style komponent), Lucide a TanStack Query. Bez platených služieb a bez simulovaných API odpovedí vo výslednej aplikácii.

## Rýchle spustenie na Linuxe

Predpoklady: Docker Engine, Docker Compose v2, Python 3 na vytvorenie konfigurácie. Všetky ďalšie závislosti sa nainštalujú do kontajnerov.

```bash
unzip KBM_Workplan.zip
cd kbm-workplan
python3 scripts/configure.py
docker compose up --build -d
docker compose ps
```

Konfiguračný skript vytvorí `.env` s náhodnými heslami a JWT kľúčom. Existujúci `.env` nikdy neprepíše. Alternatívne skopírujte `.env.example` do `.env` a nahraďte všetky zástupné hodnoty. Heslo PostgreSQL musí byť URL-safe (napr. hex), pretože vstupuje do DATABASE_URL.

- Aplikácia: http://localhost:8080
- OpenAPI JSON: http://localhost:8080/openapi.json
- Swagger dokumentácia: http://localhost:8080/docs (štandardné Swagger CDN vyžaduje internet; samotná aplikácia funguje bez CDN).
- PostgreSQL ani backend sa samostatne nezverejňujú. Frontend sprostredkuje `/api` cez Nginx.
- Predvolené bindovanie je `127.0.0.1`. Pre prístup v LAN nastavte `BIND_ADDRESS=0.0.0.0` a reštartujte Compose. Pred prenosom reálnych prihlasovacích údajov zabezpečte HTTPS na reverznom proxy.

Pri prvom štarte prebehne migrácia, inicializácia kategórií/sviatkov a pri `SEED_DEMO=true` aj vytvorenie demo používateľov. Počkajte, kým majú backend a databáza stav healthy. Kalendár sa otvára na novembri 2026, aby boli demo dáta ihneď viditeľné. Tlačidlo Dnes prepne na aktuálny mesiac.

## Demo účty

| Meno | Prihlasovací e-mail | Rola |
|---|---|---|
| Maroš Sokolák | maros@demo.kbm.local | Administrátor |
| Zdenko Sokolovský | zdenko@demo.kbm.local | Zamestnanec |
| Florián Žifčák | florian@demo.kbm.local | Zamestnanec |
| Martin Zelenák | martin@demo.kbm.local | Zamestnanec |

Heslo všetkých štyroch účtov je hodnota `DEMO_PASSWORD` vo vašom `.env`. Nie je zabudované v kóde. Doména demo.kbm.local označuje fiktívne interné účty; e-maily sa neposielajú. Všetky záznamy dochádzky sú vymyslené. November obsahuje 84 denných plánov a jednu čakajúcu žiadosť Floriána.

Inicializácia je opakovateľná bez duplikátov. Ak databáza už obsahuje používateľov, demo seed sa preskočí. Zmena DEMO_PASSWORD nemení heslá existujúcich účtov — zmeňte ich v administrácii. Pri deaktivácii účtu prestanú jeho tokeny fungovať.

## Čo aplikácia obsahuje

- Dominantný mesačný kalendár zamestnanec × deň; navigácia a výber mesiaca, zvýraznenie dneška, víkendov a dní pracovného pokoja, pevný stĺpec mien a hlavička, posúvanie, vyhľadávanie, filter, ikony, legenda a natívne tooltips s detailmi.
- Editor dňa alebo rozsahu, pracovné časy a poznámky, výslovné nahradenie existujúcich plánov, mazanie s potvrdením, okamžitá aktualizácia dát cez TanStack Query.
- Zamestnanec vidí tímový kalendár; upravuje iba svoje plánované dni v kancelárii/doma a vlastné pracovné časy. Ostatné kategórie rieši žiadosťou. Pravidlá sa kontrolujú aj v API.
- Žiadosti čakajúce / schválené / zamietnuté, poznámka a dôvod, meno schvaľujúceho, notifikácie a audit. Schválenie vloží absenciu do pracovných dní a nahradí príslušný plán. Schválenú absenciu ruší administrátor cez žiadosť; tým sa vymažú jej dni. Pôvodný nahradený plán sa automaticky neobnovuje.
- Administrátor nesmie schváliť ani zamietnuť vlastnú žiadosť. Na vlastnú absenciu potrebuje druhého administrátora; vytvorte ho alebo prideľte rolu v administrácii.
- Dashboard s dnešným stavom a budúcimi schválenými absenciami.
- Správa používateľov, rolí, úväzkov, kategórií, ich farieb a ikon, aktívneho stavu, víkendov, štandardného pracovného dňa a dní pracovného pokoja.
- Export CSV a XLSX s filtrom dátumov/zamestnanca. XLSX obsahuje aj hárok Súhrn s počtom plánovaných dní podľa kategórií. Textové bunky sú chránené pred vyhodnotením ako vzorce.
- JWT HS256 s 8-hodinovou platnosťou, Argon2 heslá, základný limit pokusov o prihlásenie, kontrola aktívneho účtu pri každom volaní, audit administratívnych zmien.

## Produkcia bez demo údajov

V čistej databáze nastavte `SEED_DEMO=false`, odstráňte DEMO_PASSWORD a zvoľte vlastné secrets. Potom:

```bash
docker compose up --build -d
```

Sprievodca vyžiada meno, e-mail a heslo bezpečne v termináli. Referenčné kategórie a sviatky sa inicializujú aj bez demo ľudí. Existujúce používateľské úpravy referenčných údajov sa pri štarte neprepisujú. Nezamieňajte demo databázu s produkčnou; pre produkciu použite samostatný projekt/volume. V administrácii môžete zmeniť heslá alebo deaktivovať demo účty, ak pokračujete v tej istej databáze.

JWT je uložené v sessionStorage a odhlásenie ho odstráni v danom prehliadači. Už vydané tokeny sa individuálne nerevokujú; zablokuje ich deaktivácia používateľa alebo rotácia JWT_SECRET. Aplikácia nemá obnovu hesla e-mailom ani MFA; heslo obnovuje správca. Limit prihlásení je v pamäti jedného procesu, preto konfigurácia používa jeden worker. Pre viac replík doplňte spoločný rate limiter na proxy/Redis.

## Databáza a čas

Tabuľky: users (rola s CHECK obmedzením), attendance_types, attendance_entries, leave_requests, public_holidays, notifications, audit_logs, application_settings. Väzby sú cez cudzie kľúče. Denné plány majú jedinečnosť `(user_id, date)`. Zápisy sú transakčné; v PostgreSQL sa používateľ pri plánovaní a žiadosti zamyká, aby sa serializovali súbežné zmeny. Migrácia 0001 obsahuje zmrazené DDL, nie dynamické vytváranie tabuliek z aktuálnych modelov; migrácia 0002 odstraňuje tabuľku `work_time_entries` (záznamy pracovného času boli vyňaté).

Denné plány používajú dátum a miestny čas Europe/Bratislava. Audit a schválenia používajú timezone-aware timestampy (PostgreSQL TIMESTAMP WITH TIME ZONE). Denný režim je jednoduchý cez denný úväzok zamestnanca — bez individuálnych smenových rozpisov. Denné plány nepredstavujú mzdový systém ani výpočet nároku na dovolenku.

Dni pracovného pokoja sú predvyplnené pre 2025–2030. Pre rok 2026 sú z voľných dní vylúčené 8. máj a 15. september; 1. september, 28. október a 17. november sa nezapočítavajú ako dni pracovného pokoja. Správca môže dátumy upraviť. Pre ďalšie roky alebo zmeny pravidiel doplňte údaje v administrácii.

Zdroje kontroly kalendára (3.11.2026):
- https://www.vlada.gov.sk/slovensko/statne-sviatky/
- https://www.ip.gov.sk/pracovnopravne-vztahy/pracovny-cas-doba-odpocinku/sviatky-8-maj-15-september-2026-z-pohladu-pracovneho-prava.html

## Údržba a záloha

```bash
# Stav a logy
docker compose ps
docker compose logs --tail=100 backend
# Migrácie (vykonávajú sa aj pri štarte)
docker compose exec backend python -m alembic upgrade head
# Ručné doplnenie referenčných údajov do prázdnych sekcií
docker compose exec backend python -m app.init_data
# Demo seed do databázy bez používateľov
docker compose exec backend python -m app.seed
# Záloha
docker compose exec -T db pg_dump -U workplan -d workplan > workplan-backup.sql
# Obnova do prázdnej databázy
docker compose exec -T db psql -U workplan -d workplan < workplan-backup.sql
# Aktualizácia aplikácie
docker compose up --build -d
```

Údaje prežijú reštart a `docker compose down`. Príkaz `docker compose down -v` odstráni databázový volume; nie je súčasťou bežného postupu.

## Lokálny vývoj a testy

Frontend:

```bash
cd frontend
npm ci
npm run dev
# v inom termináli / pre kontrolu zostavenia
npm run build
```

API, pri vývoji s PostgreSQL:

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL='postgresql+psycopg://workplan:YOUR_PASSWORD@localhost:5432/workplan'
export JWT_SECRET='YOUR_RANDOM_SECRET_AT_LEAST_32_CHARACTERS'
export DEMO_PASSWORD='YOUR_DEVELOPMENT_PASSWORD'
python -m alembic upgrade head
python -m app.seed
python -m uvicorn app.main:app --reload --port 8000
```

Vite proxy smeruje `/api` na localhost:8000. Pre lokálny PostgreSQL ho spustite samostatne alebo dočasne publikujte jeho port iba na loopback.

```bash
cd backend
python -m pytest -q
```

Predvolené testy používajú izolovanú SQLite databázu. Pre rovnaké API testy na PostgreSQL nastavte `TEST_DATABASE_URL` na **samostatnú testovaciu databázu**; test fixture v nej vytvára a odstraňuje tabuľky. Nikdy tam nesmerujte produkciu. Testy sa dajú spustiť v backend kontajneri po dočasnom pripojení priečinka tests (produkčný image testy zámerne neobsahuje).

## Overenie tejto dodávky a otvorené limity

- 15 automatických testov: prihlásenie, oprávnenia, administrácia, seed/idempotencia, rozsah a atómové kolízie, žiadosti a vlastné schvaľovanie, schválenie/zamietnutie/rušenie, notifikácie, audit, mesačné filtre, sviatky, CSV/XLSX a ochrana vzorcov.
- Produkčné zostavenie React/TypeScript/Vite bolo úspešné.
- Samostatne bolo spustené reálne FastAPI so SQLite a cez HTTP overené prihlásenie, štyri účty, novembrové dáta, uloženie/načítanie/vymazanie rozsahu a XLSX export.
- Alembic migrácia bola úspešne vygenerovaná offline pre PostgreSQL dialect; Python zdroje prešli kompiláciou.
- **Docker ani skutočný PostgreSQL server nebolo možné v tvorivom prostredí spustiť. Docker Compose build/start, samotná PostgreSQL migrácia a súbežné transakcie teda neboli integračne overené.** Pred firemným používaním ich overte na cieľovom Linuxe.
- V prehliadači bola overená prihlasovacia obrazovka a získané oficiálne logo. Automatizovaná kontrola prihláseného rozhrania bola zablokovaná pravidlami prehliadača, preto netvrdíme, že kompletný frontend end-to-end alebo mobilné zobrazenie prešlo vizuálnym testom. Screenshot výsledného kalendára nie je súčasťou dodávky.
- V aktuálnej prílohe bol iba text zadania, nie referenčný screenshot WORKPLAN. Rozloženie vychádza z jeho slovného opisu, nie z priameho pixelového porovnania.
- Manuálny editor je jeden časový interval na deň; nie sú tu GPS, terminálové čipy, integrácia miezd, dovolenkové zostatky, prílohy ani automatické e-maily.

## Logo

Oficiálny bitmapový súbor je uložený lokálne: `frontend/public/assets/kbm-logo.png` (60 × 75 px). Zdroj: https://www.kbm.sk/theme/logo.png, načítané 3.11.2026. Je použitý bez prekreslenia; značka zostáva majetkom KBM. Farebnosť aplikácie kombinuje žltý/čierny firemný základ a jemné farby kategórií dochádzky. Fonty a všetky runtime assety rozhrania sú lokálne/systémové.
