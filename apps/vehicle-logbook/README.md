> Táto aplikácia je súčasťou spoločného KBM portálu. Spustenie a prihlasovanie sa riadia hlavným `../../README.md`. Nižšie je pôvodná dokumentácia funkcií samostatnej aplikácie; pôvodné samostatné prihlasovanie a Compose sa v tomto balíku nepoužívajú.

# Kniha jázd

Interná webová aplikácia na evidenciu jázd, tankovaní a spotreby PHM. Používa React, TypeScript a Material UI na fronte, FastAPI a SQLAlchemy na backende a PostgreSQL na trvalé uloženie údajov. Schému spravuje Alembic.

## Prvé spustenie na Ubuntu alebo Debiane

Predpokladom je Docker Engine s doplnkom Docker Compose v2. Po rozbalení archívu:

```bash
cd vehicle-logbook
docker compose version
umask 077
cp .env.example .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 32)/" .env
docker compose up -d --build
docker compose ps
```

Pri prvom štarte backend automaticky spustí migrácie. Počkajte, kým služby v `docker compose ps` zobrazia stav `healthy`. Prvého správcu vytvára posledný príkaz a heslo sa zadáva bez zobrazenia. Nové účty a ich prístup k vozidlám spravuje správca v aplikácii.

Aplikácia predvolene počúva len na adrese `127.0.0.1:8081`. Pri skúške priamo na serveri alebo cez SSH tunel nastavte `COOKIE_SECURE=0` v `.env` a spustite `docker compose up -d`. Tunel z pracovného počítača:

```bash
ssh -L 8081:127.0.0.1:8081 POUZIVATEL@SERVER
```

Následne otvorte [http://localhost:8081](http://localhost:8081). Pre firemný prístup nastavte `COOKIE_SECURE=1` a smerujte HTTPS reverzný proxy server na `127.0.0.1:8081`. Ak je proxy na inom hostiteľovi, nastavte `BIND_IP` na privátnu adresu rozhrania a povoľte prístup iba z proxy servera. Login má v pribalenom Nginx obmedzenie počtu požiadaviek podľa zdrojovej IP.

## Mesačný sprievodca

V menu otvorte **Mesačný sprievodca**, vyberte vozidlo a skončený mesiac. Zadajte počiatočný a konečný tachometer a pridajte približné trasy s počtom opakovaní. Pri cieli z katalógu sa navrhne dvojnásobok jednosmernej vzdialenosti. Pri novom cieli doplňte približné kilometre za jednu celú jazdu. Príklad podkladov môže byť Košice 6×, Prešov 3×, Poprad 4× a mesto 3×.

Po stlačení **Navrhnúť mesačné jazdy** systém rozdelí presný rozdiel tachometrov medzi zadané trasy a pracovné dni. Zadané tachometre na dokladoch tankovaní rozdelia mesiac na úseky, na ktoré musia jazdy nadväzovať. Väčšia odchýlka od približnej vzdialenosti vyvolá upozornenie. Systém používa deterministický výpočet bez externého AI API.

V druhom kroku upravte dátumy, ciele, kilometre, kategórie a podľa potreby účel a poznámky. Po úpravách musí sedieť mesačný rozdiel, chronologické poradie aj tankovania. Potvrďte kontrolu a stlačte **Potvrdiť a uložiť jazdy**. Záznamy sa uložia naraz, označia ako **Mesačný odhad** a zaznamenajú do auditu. Následne možno stiahnuť mesačný PDF alebo XLSX. Pôvod odhadu ostáva označený aj v exportoch.

Sprievodca je určený pre mesiace bez existujúcich jázd. Kontroluje nadväznosť na predchádzajúcu aj nasledujúcu evidenciu a uzávierky. Zmenené údaje medzi náhľadom a schválením si vyžiadajú nový návrh. Opakované potvrdenie toho istého návrhu nevytvorí ďalšie jazdy.

## Demo na testovanie

Ako správca otvorte **Nastavenia → Pridať / otvoriť demo dáta**, prípadne tlačidlo na prázdnom prehľade. Vytvorí sa vozidlo **DEMO-KJ-01**, štyri ukážkové jazdy v predchádzajúcom období, štyri destinácie a dva fiktívne doklady tankovaní v poslednom skončenom mesiaci. Aplikácia vyberie demo vozidlo a otvorí sprievodcu s tachometrami **12 240 → 13 280 km** a predvyplnenými trasami. Návrh obsahuje 16 jázd a presne 1 040 km. Demo údaje sú označené v názve vozidla aj poznámkach. Ďalšie stlačenie tlačidla otvorí rovnaké demo.

## Práca s evidenciou

1. V časti **Nastavenia** vyberte pôvodný `vzor_cesr.xlsm`, vytvorte náhľad a skontrolujte upozornenia. Po potvrdení sa prenesie vozidlo, platné záznamy, destinácie, sviatky a počiatočné stavy. Pôvodný zošit sa neupravuje a je uložený samostatne.
2. Ďalšie vozidlo založte cez **Vozidlá a destinácie**. Prístupy prideľujte v časti **Nastavenia → Používatelia**.
3. Pri jazde stačí dátum, kategória a konečný tachometer. Pri výbere známeho cieľa sa navrhne dvojnásobok jednosmernej vzdialenosti. Počiatočný tachometer možno nechať prázdny a backend ho doplní. Pri úprave historickej jazdy si skontrolujte nadväzujúce záznamy a upozornenia pri tankovaní.
4. Pri tankovaní zadajte litre, sumu alebo jednotkovú cenu, sadzbu DPH a tachometer z evidencie. Voliteľný meraný stav nádrže sa premietne do korekcie. Normovaná spotreba, výpočtová spotreba a bilančná spotreba sa zobrazujú osobitne.
5. V časti **Výkazy** vyberte vozidlo, rok a pri mesačnom výkaze aj mesiac. PDF a XLSX sa generujú z rovnakých údajov. Uzávierka uloží výkazy po vybraný mesiac vrátane a chráni ich zdrojové záznamy. Správca môže otvoriť poslednú uzávierku, pričom jej pôvodný výkaz ostáva v auditnom zázname.
6. Opätovný import toho istého súboru aj import do existujúceho EČV sa zablokuje. Následné opravy robte v aplikácii. Rozdiely oproti Excelu sú v [EXCEL_ANALYSIS.md](EXCEL_ANALYSIS.md).

## Zálohovanie a obnova

Záloha pozostáva z databázového súboru a súkromnej kópie `.env`. Databázové zálohy obsahujú firemné údaje a uložené relácie. Uchovávajte ich s obmedzeným prístupom.

```bash
umask 077
docker compose exec -T db pg_dump -U logbook -d logbook -Fc > "logbook-$(date +%F).dump"
```

Obnova prepíše databázové objekty v aktuálnej inštalácii. Použite správny záložný súbor a nechajte frontend aj backend vypnuté až do úspešného obnovenia.

```bash
docker compose stop frontend backend
docker compose up -d db
docker compose exec -T db pg_restore -U logbook -d logbook --clean --if-exists --no-owner --exit-on-error --single-transaction < logbook-YYYY-MM-DD.dump
docker compose up -d
```

## Aktualizácia

Pre túto verziu rozbaľte nový archív a skopírujte obsah priečinka `vehicle-logbook` do existujúceho `/home/krokujeme/vehicle-logbook`. Nová migrácia pridá mesačné návrhy a ich pôvod k jazdám pri štarte backendu. Vlastný `.env` sa z archívu nedodáva. V existujúcom `.env` použite `APP_PORT=8081`, aby aplikácia používala požadovaný port.


Vytvorte zálohu, vymeňte zdrojové súbory za nové vydanie a ponechajte existujúce `.env`, názov Compose projektu a databázový zväzok.

```bash
docker compose up -d --build
docker compose ps
docker compose logs --tail=80 backend
```

## Testy

Backendové testy štartujú z prázdnej testovacej databázy, aplikujú migráciu a kontrolujú API, kontinuitu tachometra a nádrže, oprávnenia, uzávierky aj formáty exportov. Ďalšie scenáre preverujú mesačné návrhy, doklady tankovaní, kontrolu pred schválením, opakované uloženie, zmenené podklady a demo. Pri nastavení `ORIGINAL_XLSM` porovnajú aj viaceré obdobia s pôvodným zošitom. Voliteľný `LOGBOOK_TEST_DATABASE_URL` musí odkazovať na prázdnu databázu určenú výhradne pre testy.

```bash
cd backend
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
ORIGINAL_XLSM=/absolutna/cesta/vzor_cesr.xlsm python -m unittest discover -s tests -v
cd ../frontend
npm ci
npm run build
```
