"""Approximate one-way road distances (km) from the company base in Žilina.

Own reference table for the monthly assistant; values are rounded driving distances, not exact routes.
The vehicle's own destination catalogue always wins over this table. The destination list follows
the places used in the legacy workbook (`miesta` sheet), whose distances were not reliable.
"""
import os
import unicodedata

HOME = os.getenv("LOGBOOK_HOME", "Žilina").strip() or "Žilina"

FROM_ZILINA = {
    # Žilina region and nearby
    "Žilina": 8, "Bytča": 15, "Varín": 10, "Strečno": 12, "Teplička nad Váhom": 6, "Lietavská Lúčka": 8,
    "Rajec": 22, "Rajecké Teplice": 15, "Rajecká Lesná": 27, "Kysucké Nové Mesto": 12,
    "Krásno nad Kysucou": 20, "Čadca": 30, "Turzovka": 40, "Martin": 32, "Vrútky": 25, "Sučany": 22,
    "Turčianske Teplice": 55, "Ružomberok": 65, "Dolný Kubín": 55, "Námestovo": 80, "Tvrdošín": 85,
    "Trstená": 95, "Nižná": 80, "Liptovský Mikuláš": 90, "Liptovský Hrádok": 100,
    "Považská Bystrica": 33, "Púchov": 45, "Beluša": 40, "Ilava": 60, "Dubnica nad Váhom": 65,
    "Nová Dubnica": 68, "Nemšová": 75, "Horné Srnie": 75,
    # Western Slovakia
    "Trenčín": 80, "Trenčianske Stankovce": 90, "Nové Mesto nad Váhom": 105, "Bánovce nad Bebravou": 90,
    "Prievidza": 70, "Bojnice": 72, "Handlová": 75, "Partizánske": 95, "Topoľčany": 115, "Piešťany": 125,
    "Hlohovec": 140, "Trnava": 155, "Sereď": 170, "Dolná Streda": 170, "Galanta": 190, "Šaľa": 180,
    "Nitra": 150, "Zlaté Moravce": 145, "Vráble": 165, "Nové Zámky": 185, "Šurany": 175, "Komárno": 215,
    "Levice": 180, "Myjava": 145, "Senica": 180, "Skalica": 175, "Malacky": 230, "Bratislava": 200,
    "Dunajská Streda": 240, "Pezinok": 185,
    # Central and eastern Slovakia
    "Banská Bystrica": 88, "Zvolen": 105, "Detva": 120, "Brezno": 120, "Podbrezová": 110, "Kremnica": 95,
    "Žiar nad Hronom": 110, "Banská Štiavnica": 135, "Hodruša-Hámre": 125, "Lučenec": 160,
    "Rimavská Sobota": 190, "Rožňava": 210, "Poprad": 140, "Kežmarok": 155, "Levoča": 165,
    "Spišská Nová Ves": 165, "Prešov": 210, "Sabinov": 225, "Bardejov": 250, "Košice": 245,
    "Humenné": 290, "Michalovce": 300, "Orlov": 240,
    # Abroad
    "Ostrava": 105, "Frýdek-Místek": 85, "Zlín": 120, "Olomouc": 160, "Brno": 165, "Lednice": 210,
    "Kurdějov": 185, "Praha": 370, "Viedeň": 255, "Budapešť": 280, "Bielsko-Biała": 100,
    "Katowice": 140, "Krakov": 190, "Mníchov": 600,
}
ALIASES = {"ba": "Bratislava", "ke": "Košice", "za": "Žilina", "bb": "Banská Bystrica", "po": "Prešov",
           "tt": "Trnava", "nr": "Nitra", "tn": "Trenčín", "mt": "Martin", "zv": "Zvolen", "pp": "Poprad",
           "snv": "Spišská Nová Ves", "dk": "Dolný Kubín", "lm": "Liptovský Mikuláš", "ds": "Dunajská Streda",
           "pb": "Považská Bystrica", "vieden": "Viedeň", "wien": "Viedeň", "vienna": "Viedeň",
           "budapest": "Budapešť", "krakow": "Krakov", "kraków": "Krakov", "prague": "Praha",
           "mnichov": "Mníchov", "munchen": "Mníchov", "hodrusa hamre": "Hodruša-Hámre"}
CITY_KM = 15  # one round trip within the town


def normalized(value):
    return "".join(char for char in unicodedata.normalize("NFKD", (value or "").casefold())
                   if not unicodedata.combining(char)).replace("-", " ").strip()


INDEX = {normalized(name): km for name, km in FROM_ZILINA.items()}
INDEX.update({normalized(alias): FROM_ZILINA[name] for alias, name in ALIASES.items()})


def is_home(origin):
    return not origin or normalized(origin) == normalized(HOME)


def is_city(destination):
    return normalized(destination) in {"mesto", "po meste", "v meste", normalized(HOME)}


def round_trip(destination, origin=""):
    """Approximate km of one trip there and back from the base, or None when unknown."""
    if not is_home(origin):
        return None
    if is_city(destination):
        return CITY_KM
    name = normalized(destination)
    if name in INDEX:
        return INDEX[name] * 2
    # "BA / Kaellina", "Trenčín/Konštrukta": use the town before the slash or company name.
    for part in name.replace("/", " / ").split(" / "):
        if part.strip() in INDEX:
            return INDEX[part.strip()] * 2
    return None
