# KBM Automatizácie – testovací portál

Kompletný balík pre Linux server: spoločná hlavná stránka s fotografiou prevádzky KBM, spoločné prihlásenie, Workplan a Kniha jázd. Ide o testovací prototyp (MVP). Na server sa nič automaticky nepublikuje.

## Spustenie

Vyžaduje Docker Engine a Docker Compose v2. ZIP rozbaľte do nového priečinka vedľa existujúcich aplikácií.

```bash
unzip KBM_Automatizacie.zip
cd kbm-portal
sh init.sh
docker compose up -d --build
docker compose ps
```

Otvorte **http://127.0.0.1:8090** na serveri. Pre prístup z iných počítačov v dôveryhodnej sieti nastavte v `.env` `BIND_ADDRESS=0.0.0.0` (potom **http://IP_SERVERA:8090**), pre internet použite tunel nižšie. Pri prvom zostavení sa sťahujú obrazy a závislosti; môže trvať niekoľko minút.

`init.sh` vytvorí `.env` s náhodnými heslami a vypíše počiatočné prihlasovacie heslo. Opakované spustenie existujúci `.env` neprepíše. Na spustenie potrebujete iba Docker, Node ani Python na server zvlášť neinštalujete.

| Meno | Prihlasovacie meno | Rola |
| --- | --- | --- |
| Maroš Sokolák | `maros` | Správca |
| Zdenko Sokolovský | `zdenko` | Zamestnanec |
| Florián Žifčák | `florian` | Zamestnanec |
| Martin Zelenák | `martin` | Zamestnanec |

Možno použiť aj e-mail, napr. `maros@demo.kbm.local`. Všetky štyri účty majú pri prvom vytvorení heslo vypísané skriptom. Každý používateľ si ho môže zmeniť tlačidlom **Heslo** na hlavnej stránke. Po zmene hesla sa zrušia všetky relácie daného účtu.

Portál otvára:

- `/` – hlavná stránka a prihlásenie;
- `/workplan/` – dochádzka, žiadosti a exporty;
- `/vehicle-logbook/` – jazdy, vozidlá, tankovanie, mesačný sprievodca a exporty.

## Ako funguje spoločné prihlásenie

Portál vytvorí jednu 12-hodinovú reláciu v HttpOnly cookie na rovnakej adrese. Obe API pri každej chránenej požiadavke overia túto reláciu u portálu cez interný servisný kľúč. Heslo sa neposiela do menších aplikácií ani sa neukladá v prehliadači. Ich pôvodné prihlasovacie API sú vypnuté. Odhlásenie z ktorejkoľvek aplikácie zneplatní spoločnú reláciu aj v druhej aplikácii. Už zobrazená stránka v inej karte môže zostať viditeľná, ale ďalšie požiadavky na jej údaje budú zamietnuté.

Na začiatku existujú štyri účty (Maroš je správca). Ďalších ľudí pridáva administrátor v portáli tlačidlom **Používatelia** (vpravo hore). Workplan dovolí upraviť úväzok a aktivitu používateľa; Kniha jázd dovolí aktivitu a priradenie vozidiel. Účty sa párujú e-mailom vo Workplane a prihlasovacím menom v Knihe jázd, preto sa e-mail ani prihlasovacie meno po vytvorení nemenia. Bežní používatelia majú v Knihe jázd prístup len k vozidlám, ktoré im správca priradí v Nastaveniach.

## Správa používateľov, prístupov a skupín

Administrátor v portáli (`/?admin=1`):

- **pridá používateľa**: meno, prihlasovacie meno, e-mail, aplikácie, skupiny a voliteľne rolu administrátora. Heslo nikto nevymýšľa: portál vytvorí jednorazový **pozývací odkaz** platný 72 hodín, cez ktorý si človek nastaví vlastné heslo. Ak je nastavené SMTP, odkaz príde e-mailom, inak ho admin skopíruje a pošle sám. Rovnako funguje **Nový odkaz na heslo** pri zabudnutom hesle;
- **pridelí prístup k aplikáciám** (Workplan, Kniha jázd). Bez prístupu sa karta aplikácie na hlavnej stránke nezobrazí, nginx nepustí rozhranie (presmeruje na portál s upozornením) a API aplikácie vráti 403. Zmena platí okamžite;
- **zaradí do skupín**. Úvodné skupiny sú Vedenie, Vývojári, Predaj, Technici, Administratíva a Brigádnici; ďalšie sa dajú pridať, premenovať alebo zmazať. Človek môže byť vo viacerých skupinách. **Vo Workplane zamestnanec vidí v kalendári, prehľade a zozname ľudí iba seba a členov svojich skupín**, administrátor vidí všetkých. Kalendár má filter podľa skupiny;
- **deaktivuje účet**: okamžite zruší všetky relácie a prihlásenie.
- **zmaže účet natrvalo** (Upraviť → Zmazať natrvalo, potvrdenie napísaním prihlasovacieho mena): portál najprv požiada Workplan aj Knihu jázd o zmazanie všetkých údajov človeka (plány, žiadosti, notifikácie, Denník, priradenie vozidiel, rozpracované návrhy) a až potom zmaže účet. Firemné záznamy (jazdy a tankovania vozidiel, zdieľané projekty a úlohy) ostanú; v histórii zmien a pri schváleniach, ktoré urobil, je „Zmazaný používateľ“. Ak niektorá aplikácia neodpovie, účet sa nezmaže a dá sa to zopakovať. Vlastný účet zmazať nemožno.

Zmeny sa zapisujú do histórie v správe používateľov. Nový používateľ sa vo Workplane aj v Knihe jázd objaví hneď (aplikácie si zoznam ľudí berú z portálu cez interné `/internal/directory`), takže mu admin môže naplánovať dochádzku alebo priradiť vozidlo ešte pred prvým prihlásením.

E-maily (voliteľné) v `.env`, napr. bezplatný Gmail s [heslom aplikácie](https://myaccount.google.com/apppasswords):

```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=vas.ucet@gmail.com
SMTP_PASSWORD=heslo-aplikacie-16-znakov
SMTP_FROM=KBM portál <vas.ucet@gmail.com>
PUBLIC_URL=https://kbm.<váš-tailnet>.ts.net
```

Po úprave `.env` spustite `docker compose up -d --build`.

Workplan začína bez dochádzky a dovoleniek. Kniha jázd začína bez vozidiel; môžete ich vytvoriť, importovať Excel alebo použiť pôvodné tlačidlo na pridanie demo dát. Doterajšie funkcie aplikácií sú súčasťou balíka.

## Existujúce aplikácie a údaje

Nový Compose projekt sa volá `kbm-portal-demo`, predvolený port je 8090 a má vlastné dátové volumes. Pôvodné samostatné aplikácie ani ich databázy sa automaticky nemenia alebo nekopírujú. Tento ZIP obsahuje ich zdrojový kód, nie údaje zo servera. Staré nasadenia môžu ďalej bežať vedľa portálu.

Pri tejto verzii nepoužívajte pôvodné samostatné Compose súbory. Všetko spúšťajte z hlavného priečinka `kbm-portal`.

## Mesačný sprievodca s asistentom Gemini (Kniha jázd)

Sprievodca v Knihe jázd (`/vehicle-logbook/mesacny-navrh`) má vľavo živý náhľad rozpracovaného mesiaca a vpravo chat. Na mobile sú to dve záložky.

1. Používateľ nahrá bločky (JPG, PNG, WebP, HEIC, PDF, max. 20 súborov po 8 MB) a opíše mesiac, napríklad „začiatok 12 240 km, koniec 13 280 km, trikrát Košice, dvakrát Poprad“.
2. Gemini z bločkov prečíta dátum, stanicu, litre, sumu, menu a tachometer. Nečitateľné polia a možné duplicity sa zvýraznia. Harmonogram sa vytvorí sám, len čo je známy začiatok, koniec a ciele; pred schválením treba potvrdiť, že bločky sú skontrolované.
3. Gemini pochopí opis jázd a backend vypočíta kilometre a tachometre. Musia sedieť mesačný rozdiel aj tankovania so známym tachometrom. Opravy v chate („V Poprade som bol 14. októbra“, „To druhé tankovanie bolo 42 litrov“) sa uplatnia ako povolené operácie nad konkrétnym riadkom.
4. Koncept sa priebežne ukladá na serveri a prežije obnovenie stránky. Tlačidlo **Schváliť a uložiť výkaz** zapíše jazdy aj tankovania v jednej transakcii. Potom je k dispozícii export PDF a XLSX.

Odhadované jazdy sú vo výkaze označené ako mesačný odhad. Tankovanie bez tachometra na bločku ostane bez tachometra.

Konfigurácia v `.env`, ktorú dostanú služby `logbook-api` a `workplan-api`:

```
GEMINI_API_KEY=...                    # bez kľúča sprievodca funguje s ručným zadávaním
GEMINI_MODEL=gemini-3.6-flash         # voliteľné; predvolený model Flash (lacnejší a rýchlejší: gemini-3.1-flash-lite)
```

Jeden bloček stojí približne 1 500 vstupných tokenov. Koncept má limit 150 volaní Gemini.

## Verejný prístup cez Tailscale Funnel (bez domény a verejnej IP)

Tailscale Funnel zadarmo sprístupní portál na stálej HTTPS adrese `https://kbm.<váš-tailnet>.ts.net`. Návštevníci nič neinštalujú. Počítač s Dockerom musí byť zapnutý.

```bash
curl -fsSL https://tailscale.com/install.sh | sh   # inštalácia (oficiálny skript)
sudo tailscale up                                   # otvorte vypísaný odkaz a prihláste sa (bezplatný účet)
sudo tailscale set --hostname=kbm
sudo tailscale funnel --bg 8090                     # pri prvom spustení vypíše odkaz na povolenie HTTPS a Funnel
tailscale funnel status                             # ukáže verejnú adresu
```

Potom v `.env` nastavte `COOKIE_SECURE=1` a `BIND_ADDRESS=127.0.0.1` a spustite `docker compose up -d --build`. Portál bude dostupný iba cez HTTPS odkaz, priama adresa `http://IP_SERVERA:8090` prestane fungovať. Nginx berie skutočnú IP návštevníka z `X-Forwarded-For`, preto musí port ostať viazaný na `127.0.0.1`.

Pred zdieľaním odkazu si každý zmení počiatočné heslo (tlačidlo **Heslo** na hlavnej stránke). Všetky účty majú na začiatku rovnaké `DEMO_PASSWORD`. Ide stále o testovací prototyp; databázy pravidelne zálohujte.

Vypnutie verejného prístupu: `sudo tailscale funnel --https=443 off`. Pre prístup cez LAN potom nastavte `COOKIE_SECURE=0` a `BIND_ADDRESS=0.0.0.0`.

## Bežné príkazy

```bash
# Stav a posledné hlásenia
docker compose ps
docker compose logs --tail=100 portal workplan-api logbook-api gateway

# Po úprave zdrojového kódu
docker compose up -d --build

# Zastavenie so zachovaním údajov
docker compose down
```

Databázy prežijú reštart aj bežné `docker compose down`. Nepoužívajte `down -v`, ak chcete údaje ponechať.

Port upravíte v `.env` cez `PORT=8090`. Predvolené `BIND_ADDRESS=127.0.0.1` sprístupní portál iba na tomto počítači (a cez tunel); pre testovanie cez IP servera v dôveryhodnej sieti nastavte `BIND_ADDRESS=0.0.0.0`. Pre HTTP testovanie je `COOKIE_SECURE=0`. Pri neskoršom použití HTTPS nastavte `COOKIE_SECURE=1` a zachovajte jednu spoločnú adresu pre portál aj obe aplikácie. Tento balík nie je pripravený ako produkčné nasadenie.

Zmena `DEMO_PASSWORD` v `.env` už vytvorené účty neprehesluje. Pri zabudnutom hesle:

```bash
docker compose exec portal python reset_password.py maros
```

Skript sa bezpečne opýta na nové heslo a zruší relácie účtu. Existujúcu hodnotu `POSTGRES_PASSWORD` po inicializácii databáz nemeňte bez zodpovedajúcej zmeny hesla v PostgreSQL.

## Overenie balíka

Overené boli zostavenia oboch React aplikácií, trinásť integračných testov centrálneho prihlásenia a API vrátane exportov a prechod v prehliadači: login → Workplan → portál → Kniha jázd → portál → odhlásenie. Skontrolované bolo aj mobilné rozloženie.

Integračné testy používajú izolované SQLite databázy. Workplan má pôvodnú migráciu určenú pre PostgreSQL. Kompletné spustenie Docker Compose a PostgreSQL migrácie v kontajneroch nebolo v prostredí prípravy možné overiť, pretože tu nebol Docker daemon. Lokálny test prehliadača používal pomocný proxy server s rovnakými cestami; finálny balík používa Nginx z `gateway/nginx.conf`.

Testy pripojenia môžete spustiť mimo kontajnerov v Python 3.12:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-test.txt
python -m pytest tests -q
```

Testy vytvoria vlastné dočasné databázy a krátkodobé API procesy na lokálnych portoch 19100 až 19102. Nepoužívajú serverové údaje.
