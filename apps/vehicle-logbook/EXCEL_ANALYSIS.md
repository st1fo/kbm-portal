# Analýza vzor_cesr.xlsm

Zošit bol analyzovaný cez openpyxl (vzorce aj uložené hodnoty) a oletools (zdroj VBA). Makrá sa nespúšťali.

| Hárok | Rozsah a vzorce | Prenos |
|---|---|---|
| mesačný výkaz | 42 × 14, 104 vzorcov | Výber mesiaca, tachometer, km podľa typu, stav PHM a detail nákupov s cenou a DPH |
| cesty | 2923 × 32, 46 603 vzorcov | Kalendárne riadky sa rozdelia na jazdy a tankovania, nulové dni sa neprenášajú |
| ročný výkaz | 34 × 14, 33 vzorcov | Mesačné súhrny, ročný súčet a normovaná spotreba |
| nastavenia | 13 × 4 | EČV, zodpovedná osoba, typ, počiatočný tachometer, objem a hodnota PHM, normy, objem nádrže, začiatok, hranica uzávierky |
| sviatky | 247 dátumov, 2014–2030 | Historický kalendár zo zošita, informačné označenie dní |
| miesta | 80 riadkov vrátane hlavičky | Ciele a jednosmerné vzdialenosti, výber s návrhom spiatočnej vzdialenosti |

## Výpočty a závislosti

V hárku cesty A je dátum zo začiatku evidencie, ďalej každý deň +1. B–D sú cieľ, poznámka a kategória. E preberá predošlé F alebo počiatočný stav. F a G vypĺňa VBA. H spája poradie tankovania Z s mesiacom V. I–M sú litre, hrubá suma, sadzba DPH, čas a miesto. N = J/I. O je sadzba zapísaná makrom. P = O × G / 100, Q = P × T/R. R preberá predchádzajúci S, T predchádzajúci U. U = T − Q + J/(1 + K). Pri chýbajúcej sadzbe je historická vetva 20 % pred 1.1.2025 a 23 % od tohto dátumu. V–AA sú kalendárne a poradové kľúče, AB označuje víkend alebo dátum zo sviatkov. AC–AF sú príznaky fixácie tachometra, vzdialenosti, nádrže a cieľa. Overenie v Exceli používa zoznam kategórií a pomenovaný zoznam cieľov.

Výkazy používajú SUMIFS pre km a nákupy, MINIFS/MAXIFS pre stavy a prvý/posledný poradový riadok, VLOOKUP/XLOOKUP pre tankovania. Spotreba za obdobie = počiatočný stav + nákup − koncový stav. To platí aj pre hodnotu PHM bez DPH. Ročná norma sa počíta zvlášť pre mesto a mimo mesta. V aplikácii je súčet nákupov bez DPH založený na sadzbe každej položky a detail nemá limit desať tankovaní.

## Makrá

- Otvorenie zošita nastavuje ochranu. Zmeny riadku volajú prepočty vzdialeností a paliva, upravujú príznaky a dopĺňajú DPH. Zmena cieľa môže použiť alebo aktualizovať katalóg vzdialeností.
- FillCounter rozdeľuje zvyšné kilometre medzi nefixované pracovné dni. Používa limit z B11, odchýlky z Rnd a náhodný výber dlhšej cesty. Ak chýba cieľový tachometer, odhaduje ho. Aplikácia eviduje explicitne zadané jazdy. Import zachováva uložené kladné vzdialenosti a označuje riadky bez ručne fixovanej vzdialenosti.
- FillFuelTank medzi fixovanými stavmi rozdelí úbytok podľa vážených km. Pomer sadzieb je B8/B9. Bez koncového merania použije normy. Pri migrácii sa zachovávajú platné uložené sadzby O a stav S pri tankovaní. Pri novej jazde sa použije kategóriová norma alebo ručne zadaná sadzba. Pri tankovaní možno zadať meraný stav a rozdiel sa zobrazí vo výkaze ako korekcia nádrže. Pri výpočte bez dostupného objemu má čerpanie z otváracieho stavu nulovú účtovnú cenu a vzniká upozornenie.
- LockMonth uzatvára evidenciu po zvolený mesiac vrátane a zamyká nastavenia. UnlockMonth odomyká riadky po hodnote B13. Web má auditované uzávierky, uložené výkazy a pri opätovnom otvorení vyžaduje otváranie od najnovšej uzávierky. Tlač už sama neuzatvára obdobie.
- ClearAllData maže evidenciu, CloneAllData prepisuje dáta z iného zošita. Web robí náhľad a samostatný bezpečný import bez prepisu existujúceho vozidla. RefreshOrder triedi dáta podľa dátumu a tachometra. Tlačidlo ročného výkazu uzamkne rok a otvorí tlač.

## Nezrovnalosti

- V katalógu miest je 20 záporných vzdialeností. Import ich vynechá a uvedie v náhľade.
- Riadok 269 má koncový tachometer 88 270 pri začiatku 89 556 a vzdialenosti 1 km. Aplikácia ho vynechá. Ročný súčet preto klesne z 17 367 na 17 366 km.
- Riadok 2 má vypočítanú zápornú spotrebu −10,47 l a Q/U vracajú #DIV/0! pri prázdnej nádrži. Import nahrádza neplatnú sadzbu normou a vykazuje korekciu pri prenose uloženého stavu nádrže. Finančné výsledky po tomto bode sa môžu líšiť od zošita. Aplikácia neprenáša chybnú nulovú obstarávaciu hodnotu prvého nákupu.
- Mesačné F24 odkazuje na prázdne I21. Ročné ceny sú paušálne prepočítavané cez 1,23 a dátum podpisu je pevne z roku 2021. Tieto chyby sa neprenášajú.
- September 2026 má kontrolné hodnoty 2 756 km, 402 km v meste, 370,57 l nákupov, 671,92 € s DPH a 329,95 l bilančnej spotreby. Historická sadzba DPH zo zošita je predvyplnená pomôcka, sadzbu na doklade treba zadať podľa nákupu.


## Rozšírenie o mesačnú rekonštrukciu

Nový mesačný sprievodca nahrádza potrebu denného vypĺňania pri spätnej rekonštrukcii. Človek zadáva tachometre, skutočne navštívené ciele, počty jázd a orientačné vzdialenosti. Návrh používa predvídateľné vážené rozdelenie celých kilometrov a pracovných dátumov, pričom dodržiava tachometre tankovaní. Nezískané podrobnosti majú charakter odhadu. Schválenie používateľa, pôvodné podklady, návrh a upravené záznamy ostávajú v audite. Pri úprave existujúcej databázy sa doplní väzba `trips.draft_id`; staršie záznamy ostávajú s prázdnou väzbou. Výpočet spotreby, cien a uzávierok využíva rovnaké existujúce služby.
