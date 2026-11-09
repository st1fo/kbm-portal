"""PDF and XLSX reports laid out like the original workbook (sheets "mesačný výkaz" / "ročný výkaz").

Portrait A4, one summary page with the place, date, author and a signature line, followed by the trip
log. XLSX output mirrors the same layout with print settings; strings are never written as formulas.
"""
import os
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import xlsxwriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# Column catalogue of the monthly report rows (also used for annual totals in main.annual).
REPORT = {
    "month":"Mesiac", "opening_odo":"Počiatočný tachometer", "closing_odo":"Koncový tachometer",
    "distance":"Vzdialenosť (km)", "city_km":"Mesto (km)", "outside_km":"Mimo mesta (km)",
    "trip_count":"Počet jázd", "opening_liters":"Počiatočný stav (l)", "closing_liters":"Koncový stav (l)",
    "purchased":"Nákup (l)", "normative":"Normovaná spotreba (l)", "calculated":"Výpočtová spotreba (l)",
    "consumption":"Bilančná spotreba (l)", "average":"Priemer (l/100 km)",
    "correction":"Korekcia nádrže (l)", "gross":"Nákup s DPH (€)", "purchase_net":"Nákup bez DPH (€)",
    "opening_net":"Počiatočná hodnota (€)", "closing_net":"Koncová hodnota (€)",
    "consumption_net":"Hodnota spotreby bez DPH (€)", "gross_per_liter":"Priemerná cena s DPH (€/l)"
}
TRIPS = {"day":"Dátum","origin":"Odkiaľ","destination":"Cieľ","category":"Kategória",
    "start_odo":"Tachometer od","end_odo":"Tachometer do","distance":"Km","consumed":"Spotreba (l)",
    "purpose":"Účel","note":"Poznámka","source":"Pôvod záznamu"}
FUELS = {"day":"Dátum","clock":"Čas","station":"Stanica","odo":"Tachometer","purchased":"Litre",
    "unit_price":"€/l s DPH","gross":"Suma s DPH (€)","vat":"DPH (%)","vat_amount":"DPH (€)",
    "net":"Bez DPH (€)","measured":"Meraný stav (l)","note":"Poznámka"}

MONTHS = ["január", "február", "marec", "apríl", "máj", "jún", "júl", "august", "september", "október",
          "november", "december"]
PLACES = {"Žilina": "V Žiline", "Bratislava": "V Bratislave", "Košice": "V Košiciach", "Martin": "V Martine",
          "Trenčín": "V Trenčíne", "Poprad": "V Poprade", "Prešov": "V Prešove", "Nitra": "V Nitre"}
ESTIMATE = "* jazda doplnená z mesačného odhadu"

D = Decimal


# --- Shared formatting ----------------------------------------------------------------------------

def dec(value):
    return D(str(value)) if value not in (None, "") else D(0)


def number(value, places=2):
    """Slovak number format: 1 234,56 (non-breaking space as thousands separator)."""
    if value in (None, ""):
        return ""
    text = f"{dec(value):,.{places}f}"
    return text.replace(",", " ").replace(".", ",")


def sk_date(value):
    if not value:
        return ""
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    return f"{day.day}. {day.month}. {day.year}"


def period_name(context):
    if context["month"]:
        return f"{MONTHS[context['month'] - 1]} {context['year']}"
    return str(context["year"])


def place_phrase(place):
    return PLACES.get(place, f"{place},") if place else "Dňa"


def per_liter(total, liters):
    return dec(total) / dec(liters) if dec(liters) else None


def settlement(report):
    """The four rows of "Vyúčtovanie PHM": liters, price per liter and value, all without VAT."""
    first = "minulého mesiaca" if report.get("_monthly", True) else "minulého roka"
    rows = [("Zostatok z " + first, report["opening_liters"], report["opening_net"]),
            ("Zostatok súčasný", report["closing_liters"], report["closing_net"]),
            ("Nákup", report["purchased"], report["purchase_net"])]
    result = [(label, liters, per_liter(value, liters), value) for label, liters, value in rows]
    result.append(("Skutočná spotreba v " + ("mesiaci" if report.get("_monthly", True) else "roku"),
                   report["consumption"], None, report["consumption_net"]))
    return result


def norm_rows(context):
    vehicle, report = context["vehicle"], context["report"]
    city = dec(report["city_km"]) * vehicle.city_rate / 100
    outside = dec(report["outside_km"]) * vehicle.outside_rate / 100
    actual = dec(report["consumption"])
    return [("mesto:", vehicle.city_rate, city), ("ostatné:", vehicle.outside_rate, outside),
            ("spolu:", None, city + outside)], actual, actual - (city + outside)


def fuel_values(row):
    liters = dec(row["purchased"])
    return liters, per_liter(row["net"], liters), per_liter(row["gross"], liters), dec(row["net"]), dec(row["gross"])


# --- PDF ------------------------------------------------------------------------------------------

INK = colors.HexColor("#1F2933")
LINE = colors.HexColor("#4A5560")
HEAD = colors.HexColor("#DDE4EE")
VALUE = colors.HexColor("#FFFBD1")
WIDTH = A4[0] - 30 * mm


def fonts():
    regular = Path(os.getenv("PDF_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
    bold = regular.with_name("DejaVuSans-Bold.ttf")
    pdfmetrics.registerFont(TTFont("Report", str(regular)))
    pdfmetrics.registerFont(TTFont("Report-Bold", str(bold if bold.exists() else regular)))


def styles():
    return dict(
        cell=ParagraphStyle("cell", fontName="Report", fontSize=8, leading=10, textColor=INK),
        bold=ParagraphStyle("bold", fontName="Report-Bold", fontSize=8, leading=10, textColor=INK),
        section=ParagraphStyle("section", fontName="Report-Bold", fontSize=10, leading=13, textColor=INK, spaceAfter=4),
        small=ParagraphStyle("small", fontName="Report", fontSize=7, leading=9, textColor=LINE),
        sign=ParagraphStyle("sign", fontName="Report", fontSize=9, leading=11, textColor=INK, alignment=1))


def frame(*extra, header_rows=1):
    """Workbook-like grid: thin inner lines, medium outline, shaded header."""
    commands = [("FONTNAME", (0, 0), (-1, -1), "Report"), ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("TEXTCOLOR", (0, 0), (-1, -1), INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE), ("BOX", (0, 0), (-1, -1), 1, INK),
                ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
    if header_rows:
        commands += [("BACKGROUND", (0, 0), (-1, header_rows - 1), HEAD),
                     ("FONTNAME", (0, 0), (-1, header_rows - 1), "Report-Bold"),
                     ("ALIGN", (0, 0), (-1, header_rows - 1), "CENTER"),
                     ("LINEBELOW", (0, header_rows - 1), (-1, header_rows - 1), 1, INK)]
    return TableStyle(commands + list(extra))


HEADING = ParagraphStyle("heading", fontName="Report-Bold", fontSize=8, leading=9.5, alignment=1, textColor=INK)


def table(data, widths, *extra, header_rows=1, repeat=0, padding=3):
    data = [[Paragraph(escape(cell), HEADING) if index < header_rows and isinstance(cell, str) and cell else cell
             for cell in row] for index, row in enumerate(data)]
    result = Table(data, colWidths=[w * mm for w in widths], repeatRows=repeat, hAlign="LEFT")
    extra = (*extra, ("TOPPADDING", (0, header_rows), (-1, -1), padding),
             ("BOTTOMPADDING", (0, header_rows), (-1, -1), padding))
    result.setStyle(frame(*extra, header_rows=header_rows))
    return result


def numbered_canvas(title):
    class Numbered(canvas.Canvas):
        """Draws the workbook header box with "Strana x z y" once the page count is known."""
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.pages = []

        def showPage(self):
            self.pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self.pages)
            for page in self.pages:
                self.__dict__.update(page)
                self.header(total)
                super().showPage()
            super().save()

        def header(self, total):
            left, top, height = 15 * mm, A4[1] - 15 * mm, 13 * mm
            pager = 30 * mm
            self.setStrokeColor(INK)
            self.setLineWidth(1)
            self.rect(left, top - height, WIDTH, height)
            self.line(left + WIDTH - pager, top, left + WIDTH - pager, top - height)
            self.setFillColor(INK)
            self.setFont("Report-Bold", 12.5)
            self.drawCentredString(left + (WIDTH - pager) / 2, top - height / 2 - 4, title)
            self.setFont("Report", 8.5)
            self.drawCentredString(left + WIDTH - pager / 2, top - height / 2 - 3, f"Strana {self._pageNumber} z {total}")
    return Numbered


def info_block(context, style):
    vehicle = context["vehicle"]
    label = "MESIAC:" if context["month"] else "ROK:"
    data = [[label, Paragraph(f"<b>{escape(period_name(context))}</b>", style["cell"]), "", "", "", ""],
            ["EČV:", Paragraph(f"<b>{escape(vehicle.registration)}</b>", style["cell"]), "TYP:",
             Paragraph(escape(vehicle.kind or "–"), style["cell"]), "OZPMV:",
             Paragraph(escape(vehicle.responsible or "–"), style["cell"])]]
    return table(data, [20, 42, 16, 38, 20, 44], ("SPAN", (1, 0), (-1, 0)),
                 ("BACKGROUND", (1, 0), (1, 1), VALUE), ("BACKGROUND", (3, 1), (3, 1), VALUE),
                 ("BACKGROUND", (5, 1), (5, 1), VALUE), header_rows=0)


def odometer_block(context):
    report = context["report"]
    driven = "Najazdené km v mesiaci /km/:" if context["month"] else "Najazdené km v roku /km/:"
    data = [["Počiatočný stav tachometra /km/:", number(report["opening_odo"], 0), "Mesto:", number(report["city_km"], 0) + " km"],
            ["Konečný stav tachometra /km/:", number(report["closing_odo"], 0), "Kombinovaná:", number(report["outside_km"], 0) + " km"],
            [driven, number(report["distance"], 0), "Počet jázd:", number(report["trip_count"], 0)]]
    return table(data, [70, 30, 40, 40], ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("ALIGN", (3, 0), (3, -1), "RIGHT"),
                 ("BACKGROUND", (1, 0), (1, -1), VALUE), ("BACKGROUND", (3, 0), (3, -1), VALUE),
                 ("FONTNAME", (1, 2), (1, 2), "Report-Bold"), header_rows=0)


def settlement_block(context):
    report = dict(context["report"], _monthly=bool(context["month"]))
    data = [["P.č.", "Vyúčtovanie PHM", "Pohonné hmoty", "", ""],
            ["", "", "Litre", "cena za l v EUR", "Spolu v EUR"],
            ["", "", "l", "bez DPH", "bez DPH"]]
    for index, (label, liters, price, value) in enumerate(settlement(report), 1):
        data.append([f"{index}.", label, number(liters), number(price, 4) if price is not None else "–", number(value)])
    return table(data, [12, 74, 28, 33, 33], ("SPAN", (0, 0), (0, 2)), ("SPAN", (1, 0), (1, 2)), ("SPAN", (2, 0), (4, 0)),
                 ("ALIGN", (2, 3), (-1, -1), "RIGHT"), ("ALIGN", (0, 3), (0, -1), "CENTER"),
                 ("BACKGROUND", (2, 3), (-1, -1), VALUE), ("FONTNAME", (0, -1), (-1, -1), "Report-Bold"),
                 ("LINEABOVE", (0, -1), (-1, -1), 1, INK), header_rows=3)


def average_block(context):
    data = [["Priemerná cena za liter /s DPH v EUR/:", number(context["report"]["gross_per_liter"], 3)]]
    return table(data, [140, 40], ("ALIGN", (1, 0), (1, 0), "RIGHT"), ("BACKGROUND", (1, 0), (1, 0), VALUE),
                 ("FONTNAME", (1, 0), (1, 0), "Report-Bold"), header_rows=0)


def norm_block(context):
    rows, actual, difference = norm_rows(context)
    data = [["Normovaná spotreba:", label, f"{number(rate)} l/100 km" if rate is not None else "–", number(liters) + " l"]
            for label, rate, liters in rows]
    data += [["Skutočná spotreba:", "", "", number(actual) + " l"], ["Rozdiely spotreby:", "", "", number(difference) + " l"]]
    return table(data, [60, 30, 50, 40], ("SPAN", (0, 0), (0, 2)), ("VALIGN", (0, 0), (0, 2), "TOP"),
                 ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("BACKGROUND", (3, 0), (3, -1), VALUE),
                 ("FONTNAME", (0, -1), (-1, -1), "Report-Bold"), ("LINEABOVE", (0, 3), (-1, 3), 1, INK), header_rows=0)


def fuel_block(context, style):
    data = [["P.č.", "Dátum", "Čas", "Miesto", "Litre", "cena za l (EUR)", "", "Spolu (EUR)", ""],
            ["", "", "", "", "(l)", "bez DPH", "s DPH", "bez DPH", "s DPH"]]
    totals = [D(0), D(0), D(0)]
    for index, row in enumerate(context["fuels"], 1):
        liters, net_price, gross_price, net, gross = fuel_values(row)
        totals = [totals[0] + liters, totals[1] + net, totals[2] + gross]
        data.append([f"{index}.", sk_date(row["day"]), (row.get("clock") or "")[:5],
                     Paragraph(escape(row.get("station") or "–"), style["cell"]), number(liters),
                     number(net_price, 3), number(gross_price, 3), number(net), number(gross)])
    if not context["fuels"]:
        data.append(["", "", "", "V období nebolo tankovanie.", "", "", "", "", ""])
    data.append(["SPOLU", "", "", "", number(totals[0]), "", "", number(totals[1]), number(totals[2])])
    return table(data, [10, 21, 11, 46, 16, 19, 19, 19, 19],
                 ("SPAN", (0, 0), (0, 1)), ("SPAN", (1, 0), (1, 1)), ("SPAN", (2, 0), (2, 1)), ("SPAN", (3, 0), (3, 1)),
                 ("SPAN", (5, 0), (6, 0)), ("SPAN", (7, 0), (8, 0)), ("SPAN", (0, -1), (3, -1)),
                 ("ALIGN", (4, 2), (-1, -1), "RIGHT"), ("ALIGN", (0, 2), (2, -2), "CENTER"),
                 ("BACKGROUND", (0, -1), (-1, -1), HEAD), ("FONTNAME", (0, -1), (-1, -1), "Report-Bold"),
                 ("LINEABOVE", (0, -1), (-1, -1), 1, INK), header_rows=2, repeat=2)


def months_block(context):
    data = [["Mesiac", "Km", "Mesto km", "Mimo km", "Nákup l", "Spotreba l", "Nákup € s DPH", "Spotreba € bez DPH"]]
    for index, row in enumerate(context["months"]):
        data.append([MONTHS[index], number(row["distance"], 0), number(row["city_km"], 0), number(row["outside_km"], 0),
                     number(row["purchased"]), number(row["consumption"]), number(row["gross"]), number(row["consumption_net"])])
    report = context["report"]
    data.append(["Spolu", number(report["distance"], 0), number(report["city_km"], 0), number(report["outside_km"], 0),
                 number(report["purchased"]), number(report["consumption"]), number(report["gross"]), number(report["consumption_net"])])
    return table(data, [24, 18, 20, 20, 22, 24, 26, 26], ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                 ("BACKGROUND", (0, -1), (-1, -1), HEAD), ("FONTNAME", (0, -1), (-1, -1), "Report-Bold"),
                 ("LINEABOVE", (0, -1), (-1, -1), 1, INK), repeat=1, padding=1.8)


def trips_block(context, style):
    data = [["P.č.", "Dátum", "Odkiaľ", "Cieľ", "Typ", "Tach. od", "Tach. do", "Km", "Spotr. (l)"]]
    distance, consumed, estimated = 0, D(0), False
    for index, row in enumerate(context["trips"], 1):
        mark = " *" if row.get("estimated") else ""
        estimated |= bool(mark)
        distance += row["distance"]
        consumed += dec(row["consumed"])
        data.append([f"{index}.", sk_date(row["day"]) + mark, Paragraph(escape(row.get("origin") or ""), style["cell"]),
                     Paragraph(escape(row.get("destination") or ""), style["cell"]),
                     "mesto" if row.get("category") == "mesto" else "mimo mesta",
                     number(row["start_odo"], 0), number(row["end_odo"], 0), number(row["distance"], 0), number(row["consumed"])])
    data.append(["SPOLU", "", "", "", "", "", "", number(distance, 0), number(consumed)])
    result = table(data, [9, 21, 27, 37, 21, 17, 17, 13, 18], ("SPAN", (0, -1), (6, -1)),
                   ("ALIGN", (5, 1), (-1, -1), "RIGHT"), ("ALIGN", (0, 1), (1, -2), "CENTER"),
                   ("BACKGROUND", (0, -1), (-1, -1), HEAD), ("FONTNAME", (0, -1), (-1, -1), "Report-Bold"),
                   ("LINEABOVE", (0, -1), (-1, -1), 1, INK), repeat=1)
    return result, estimated


def signature_block(context, style):
    data = [[f"{place_phrase(context['place'])} dňa:", sk_date(context["day"]), "Vypracoval:",
             Paragraph(f"<b>{escape(context['author'])}</b>", style["sign"]), "Podpis:", ""]]
    signature = Table(data, colWidths=[w * mm for w in [26, 24, 20, 48, 15, 47]], rowHeights=[16 * mm], hAlign="LEFT")
    signature.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Report"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"), ("LINEBELOW", (1, 0), (1, 0), 0.6, INK),
        ("LINEBELOW", (3, 0), (3, 0), 0.6, INK), ("LINEBELOW", (5, 0), (5, 0), 0.6, INK),
        ("ALIGN", (1, 0), (1, 0), "CENTER"), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return KeepTogether([Spacer(1, 4 * mm), signature])


def pdf(context):
    fonts()
    style = styles()
    monthly = bool(context["month"])
    title = ("Mesačný" if monthly else "Ročný") + " výkaz spotreby PHM motorového vozidla"
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm - 6, rightMargin=15 * mm - 6,
                                 topMargin=32 * mm, bottomMargin=12 * mm, title=f"{title} · {context['vehicle'].registration} · {period_name(context)}",
                                 author=context["author"])
    gap = Spacer(1, (4 if monthly else 3) * mm)
    content = [info_block(context, style), gap, odometer_block(context), gap, settlement_block(context), gap,
               average_block(context)]
    if monthly:
        content += [gap, Paragraph("Prehľad tankovania", style["section"]), fuel_block(context, style)]
    else:
        content += [gap, norm_block(context), gap, Paragraph("Prehľad po mesiacoch", style["section"]), months_block(context)]
    content.append(signature_block(context, style))
    if monthly and context["trips"]:
        trips, estimated = trips_block(context, style)
        content += [PageBreak(), Paragraph(f"Kniha jázd · {period_name(context)} · {escape(context['vehicle'].registration)}",
                                           style["section"]), trips]
        if estimated:
            content.append(Paragraph(ESTIMATE, style["small"]))
        content.append(signature_block(context, style))
    document.build(content, canvasmaker=numbered_canvas(title))
    return buffer.getvalue()


# --- XLSX -----------------------------------------------------------------------------------------

class Book:
    """Workbook formats that mirror the original sheet: Arial 9, thin grid, medium outline, yellow values."""
    def __init__(self, buffer):
        self.book = xlsxwriter.Workbook(buffer, {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False})
        self.cache = {}

    def fmt(self, **options):
        key = tuple(sorted(options.items()))
        if key not in self.cache:
            base = dict(font_name="Arial", font_size=9, valign="vcenter", border=1, border_color="#4A5560")
            base.update(options)
            self.cache[key] = self.book.add_format(base)
        return self.cache[key]

    def label(self, **extra):
        return self.fmt(**extra)

    def head(self, **extra):
        return self.fmt(**{"bold": True, "bg_color": "#DDE4EE", "align": "center", "text_wrap": True, **extra})

    def value(self, num_format="General", **extra):
        return self.fmt(**{"bg_color": "#FFFBD1", "align": "right", "num_format": num_format, **extra})


def as_date(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def write_day(book, sheet, row, col, value, **extra):
    sheet.write_datetime(row, col, as_date(value), book.value(num_format="d. m. yyyy", **{"align": "center", **extra}))


def page_setup(sheet, last_row, last_col, repeat=None):
    sheet.set_paper(9)
    sheet.set_portrait()
    sheet.fit_to_pages(1, 0)
    sheet.center_horizontally()
    sheet.set_margins(left=0.5, right=0.5, top=0.6, bottom=0.6)
    sheet.print_area(0, 0, last_row, last_col)
    sheet.hide_gridlines(2)
    sheet.set_footer("&C&\"Arial\"&8Strana &P z &N")
    if repeat is not None:
        sheet.repeat_rows(repeat)


def sheet_title(book, sheet, row, text, last_col):
    sheet.set_row(row, 30)
    sheet.merge_range(row, 0, row, last_col, text, book.fmt(bold=True, font_size=13, align="center", border=2))


def signature_rows(book, sheet, row, context, last_col):
    plain = book.fmt(border=0, font_size=10)
    line = book.fmt(border=0, bottom=1, font_size=10, align="center")
    sheet.set_row(row, 28)
    sheet.write_string(row, 0, f"{place_phrase(context['place'])} dňa:", plain)
    sheet.merge_range(row, 2, row, 3, "", line)
    if context["day"]:   # bez dátumu ostane prázdna linka na ručné vypísanie
        sheet.write_datetime(row, 2, as_date(context["day"]), book.fmt(border=0, bottom=1, font_size=10, align="center",
                                                                        num_format="d. m. yyyy"))
    sheet.write_string(row, 4, "Vypracoval:", plain)
    sheet.merge_range(row, 5, row, last_col, context["author"], book.fmt(border=0, bottom=1, font_size=10, bold=True, align="center"))
    sheet.set_row(row + 1, 36)
    sheet.write_string(row + 1, 4, "Podpis:", book.fmt(border=0, font_size=10, valign="bottom"))
    sheet.merge_range(row + 1, 5, row + 1, last_col, "", line)
    return row + 2


def report_sheet(book, context):
    """Columns A–I follow the fuel overview table; the other blocks merge across them."""
    monthly = bool(context["month"])
    sheet = book.book.add_worksheet("Mesačný výkaz" if monthly else "Ročný výkaz")
    for col, width in enumerate([5, 11, 7, 28, 9, 10, 10, 11, 11]):
        sheet.set_column(col, col, width)
    vehicle, report = context["vehicle"], dict(context["report"], _monthly=monthly)
    money, price, liters, km = "#,##0.00", "#,##0.000", "#,##0.00", "#,##0"
    sheet_title(book, sheet, 0, ("Mesačný" if monthly else "Ročný") + " výkaz spotreby PHM motorového vozidla", 8)
    sheet.set_row(2, 20)
    sheet.merge_range(2, 0, 2, 1, "MESIAC:" if monthly else "ROK:", book.label())
    sheet.merge_range(2, 2, 2, 3, period_name(context), book.value(bold=True, align="center", font_size=11))
    sheet.set_row(3, 20)
    sheet.merge_range(3, 0, 3, 1, "EČV:", book.label())
    sheet.merge_range(3, 2, 3, 3, vehicle.registration, book.value(bold=True, align="center"))
    sheet.write_string(3, 4, "TYP:", book.label())
    sheet.merge_range(3, 5, 3, 6, vehicle.kind or "", book.value(align="center"))
    sheet.write_string(3, 7, "OZPMV:", book.label())
    sheet.write_string(3, 8, vehicle.responsible or "", book.value(align="center"))

    rows = [("Počiatočný stav tachometra /km/:", report["opening_odo"], "Mesto:", report["city_km"]),
            ("Konečný stav tachometra /km/:", report["closing_odo"], "Kombinovaná:", report["outside_km"]),
            ("Najazdené km v " + ("mesiaci" if monthly else "roku") + " /km/:", report["distance"], "Počet jázd:", report["trip_count"])]
    for offset, (label, value, side, side_value) in enumerate(rows):
        row = 5 + offset
        sheet.set_row(row, 17)
        sheet.merge_range(row, 0, row, 3, label, book.label())
        sheet.merge_range(row, 4, row, 5, float(dec(value)), book.value(km, bold=offset == 2))
        sheet.merge_range(row, 6, row, 7, side, book.label())
        sheet.write_number(row, 8, float(dec(side_value)), book.value(km))

    top = 9
    sheet.merge_range(top, 0, top + 2, 0, "P.č.", book.head())
    sheet.merge_range(top, 1, top + 2, 3, "Vyúčtovanie PHM", book.head(align="left"))
    sheet.merge_range(top, 4, top, 8, "Pohonné hmoty", book.head())
    sheet.write_string(top + 1, 4, "Litre", book.head())
    sheet.merge_range(top + 1, 5, top + 1, 6, "cena za l v EUR", book.head())
    sheet.merge_range(top + 1, 7, top + 1, 8, "Spolu v EUR", book.head())
    sheet.write_string(top + 2, 4, "l", book.head())
    sheet.merge_range(top + 2, 5, top + 2, 6, "bez DPH", book.head())
    sheet.merge_range(top + 2, 7, top + 2, 8, "bez DPH", book.head())
    for index, (label, amount, unit, value) in enumerate(settlement(report), 1):
        row, last = top + 2 + index, index == 4
        sheet.set_row(row, 17)
        sheet.write_string(row, 0, f"{index}.", book.label(align="center", bold=last))
        sheet.merge_range(row, 1, row, 3, label, book.label(bold=last))
        sheet.write_number(row, 4, float(dec(amount)), book.value(liters, bold=last))
        if unit is None:
            sheet.merge_range(row, 5, row, 6, "–", book.value(align="center"))
        else:
            sheet.merge_range(row, 5, row, 6, float(unit), book.value("#,##0.0000"))
        sheet.merge_range(row, 7, row, 8, float(dec(value)), book.value(money, bold=last))
    row = top + 8
    sheet.set_row(row, 17)
    sheet.merge_range(row, 0, row, 6, "Priemerná cena za liter /s DPH v EUR/:", book.label())
    sheet.merge_range(row, 7, row, 8, float(dec(report["gross_per_liter"])), book.value(price, bold=True))
    row += 2

    if monthly:
        sheet.merge_range(row, 0, row, 8, "Prehľad tankovania", book.head(border=2))
        heads = ["P.č.", "Dátum", "Čas", "Miesto", "Litre"]
        for col, text in enumerate(heads):
            sheet.merge_range(row + 1, col, row + 2, col, text if col != 4 else "Litre\n(l)", book.head())
        sheet.merge_range(row + 1, 5, row + 1, 6, "cena za l (EUR)", book.head())
        sheet.merge_range(row + 1, 7, row + 1, 8, "Spolu (EUR)", book.head())
        for col, text in zip(range(5, 9), ["bez DPH", "s DPH", "bez DPH", "s DPH"]):
            sheet.write_string(row + 2, col, text, book.head())
        row += 3
        first = row
        for index, fuel in enumerate(context["fuels"], 1):
            amount, net_price, gross_price, net, gross = fuel_values(fuel)
            sheet.set_row(row, 17)
            sheet.write_string(row, 0, f"{index}.", book.label(align="center"))
            write_day(book, sheet, row, 1, fuel["day"])
            sheet.write_string(row, 2, (fuel.get("clock") or "")[:5], book.value(align="center"))
            sheet.write_string(row, 3, fuel.get("station") or "", book.value(align="left"))
            sheet.write_number(row, 4, float(amount), book.value(liters))
            for col, value, number_format in ((5, net_price, price), (6, gross_price, price), (7, net, money), (8, gross, money)):
                sheet.write_number(row, col, float(value or 0), book.value(number_format))
            row += 1
        if not context["fuels"]:
            sheet.merge_range(row, 0, row, 8, "V období nebolo tankovanie.", book.label(align="center"))
            row += 1
        total = book.head(align="right")
        sheet.merge_range(row, 0, row, 3, "SPOLU", book.head(align="left"))
        if context["fuels"]:
            sheet.write_formula(row, 4, f"=SUM(E{first + 1}:E{row})", book.head(align="right", num_format=liters))
            sheet.write_blank(row, 5, None, total)
            sheet.write_blank(row, 6, None, total)
            sheet.write_formula(row, 7, f"=SUM(H{first + 1}:H{row})", book.head(align="right", num_format=money))
            sheet.write_formula(row, 8, f"=SUM(I{first + 1}:I{row})", book.head(align="right", num_format=money))
        else:
            for col in range(4, 9):
                sheet.write_number(row, col, 0, book.head(align="right", num_format=money))
        row += 1
    else:
        norm, actual, difference = norm_rows(context)
        sheet.merge_range(row, 0, row + 2, 2, "Normovaná spotreba:", book.label(valign="top"))
        for offset, (label, rate, amount) in enumerate(norm):
            sheet.write_string(row + offset, 3, label, book.label())
            if rate is None:
                sheet.merge_range(row + offset, 4, row + offset, 6, "–", book.label(align="center"))
            else:
                sheet.merge_range(row + offset, 4, row + offset, 6, f"{number(rate)} l/100 km", book.label(align="right"))
            sheet.merge_range(row + offset, 7, row + offset, 8, float(amount), book.value(liters + ' "l"', bold=offset == 2))
        for offset, (label, amount) in enumerate((("Skutočná spotreba:", actual), ("Rozdiely spotreby:", difference)), 3):
            sheet.merge_range(row + offset, 0, row + offset, 6, label, book.label(bold=offset == 4))
            sheet.merge_range(row + offset, 7, row + offset, 8, float(amount), book.value(liters + ' "l"', bold=offset == 4))
        row += 6
        sheet.merge_range(row, 0, row, 8, "Prehľad po mesiacoch", book.head(border=2))
        row += 1
        columns = [("distance", "Km", km), ("city_km", "Mesto km", km), ("outside_km", "Mimo km", km),
                   ("purchased", "Nákup l", liters), ("consumption", "Spotreba l", liters),
                   ("gross", "Nákup € s DPH", money), ("consumption_net", "Spotreba € bez DPH", money)]
        sheet.set_row(row, 28)
        sheet.merge_range(row, 0, row, 1, "Mesiac", book.head())
        for col, (_, text, _) in enumerate(columns, 2):
            sheet.write_string(row, col, text, book.head())
        first = row + 1
        for index, month in enumerate(context["months"]):
            row += 1
            sheet.merge_range(row, 0, row, 1, MONTHS[index], book.label())
            for col, (key, _, number_format) in enumerate(columns, 2):
                sheet.write_number(row, col, float(dec(month[key])), book.value(number_format))
        row += 1
        sheet.merge_range(row, 0, row, 1, "Spolu", book.head(align="left"))
        for col, (_, _, number_format) in enumerate(columns, 2):
            letter = "ABCDEFGHI"[col]
            sheet.write_formula(row, col, f"=SUM({letter}{first + 1}:{letter}{row})",
                                book.head(align="right", num_format=number_format))
        row += 1
    last = signature_rows(book, sheet, row + 1, context, 8)
    page_setup(sheet, last, 8)


def list_header(book, sheet, title, context, columns):
    last = len(columns) - 1
    sheet.set_row(0, 26)
    sheet.merge_range(0, 0, 0, last, title, book.fmt(bold=True, font_size=12, align="center", border=2))
    sheet.merge_range(1, 0, 1, last, f"EČV {context['vehicle'].registration} · {context['vehicle'].name} · {period_name(context)}",
                      book.fmt(border=0, align="center", font_color="#4A5560"))
    for col, (label, width) in enumerate(columns):
        sheet.set_column(col, col, width)
        sheet.write_string(2, col, label, book.head())
    sheet.set_row(2, 28)
    sheet.freeze_panes(3, 0)


def trips_sheet(book, context):
    sheet = book.book.add_worksheet("Jazdy")
    columns = [("P.č.", 5), ("Dátum", 11), ("Cieľ", 22), ("Odkiaľ", 16), ("Typ", 10), ("Tach. od", 10), ("Tach. do", 10),
               ("Km", 7), ("Spotreba (l)", 10), ("Účel", 18), ("Pôvod záznamu", 16)]
    list_header(book, sheet, "Kniha jázd", context, columns)
    row, estimated = 3, False
    for index, trip in enumerate(context["trips"], 1):
        estimated |= bool(trip.get("estimated"))
        values = [f"{index}.", None, trip.get("destination") or "", trip.get("origin") or "",
                  "mesto" if trip.get("category") == "mesto" else "mimo mesta"]
        for col, value in enumerate(values):
            if value is not None:
                sheet.write_string(row, col, value, book.label(align="center" if col == 0 else "left"))
        write_day(book, sheet, row, 1, trip["day"])
        for col, key, number_format in ((5, "start_odo", "#,##0"), (6, "end_odo", "#,##0"), (7, "distance", "#,##0"), (8, "consumed", "#,##0.00")):
            sheet.write_number(row, col, float(dec(trip[key])), book.value(number_format))
        sheet.write_string(row, 9, trip.get("purpose") or "", book.label())
        sheet.write_string(row, 10, (trip.get("source") or "") + (" *" if trip.get("estimated") else ""), book.label())
        row += 1
    sheet.merge_range(row, 0, row, 6, "SPOLU", book.head(align="left"))
    if context["trips"]:
        sheet.write_formula(row, 7, f"=SUM(H4:H{row})", book.head(align="right", num_format="#,##0"))
        sheet.write_formula(row, 8, f"=SUM(I4:I{row})", book.head(align="right", num_format="#,##0.00"))
        sheet.autofilter(2, 0, row - 1, len(columns) - 1)
    else:
        sheet.write_number(row, 7, 0, book.head(align="right"))
        sheet.write_number(row, 8, 0, book.head(align="right"))
    sheet.write_blank(row, 9, None, book.head())
    sheet.write_blank(row, 10, None, book.head())
    if estimated:
        row += 1
        sheet.write_string(row, 0, ESTIMATE, book.fmt(border=0, font_size=8, font_color="#4A5560"))
    last = signature_rows(book, sheet, row + 2, context, len(columns) - 1)
    page_setup(sheet, last, len(columns) - 1, repeat=2)


def fuel_sheet(book, context):
    sheet = book.book.add_worksheet("Tankovanie")
    columns = [("P.č.", 5), ("Dátum", 11), ("Čas", 7), ("Čerpacia stanica", 26), ("Tachometer", 10), ("Litre", 8),
               ("€/l s DPH", 9), ("Suma s DPH (€)", 11), ("DPH (%)", 7), ("DPH (€)", 9), ("Bez DPH (€)", 11)]
    list_header(book, sheet, "Prehľad tankovania", context, columns)
    row = 3
    for index, fuel in enumerate(context["fuels"], 1):
        for col, value in ((0, f"{index}."), (2, (fuel.get("clock") or "")[:5]), (3, fuel.get("station") or "")):
            sheet.write_string(row, col, value, book.label(align="left" if col == 3 else "center"))
        write_day(book, sheet, row, 1, fuel["day"])
        if fuel.get("odo") is None:
            sheet.write_string(row, 4, "neuvedený", book.value(align="center", font_color="#4A5560"))
        else:
            sheet.write_number(row, 4, float(fuel["odo"]), book.value("#,##0"))
        for col, key, number_format in ((5, "purchased", "#,##0.00"), (6, "unit_price", "#,##0.000"), (7, "gross", "#,##0.00"),
                                        (8, "vat", "0"), (9, "vat_amount", "#,##0.00"), (10, "net", "#,##0.00")):
            sheet.write_number(row, col, float(dec(fuel[key])), book.value(number_format))
        row += 1
    sheet.merge_range(row, 0, row, 4, "SPOLU", book.head(align="left"))
    for col in range(5, 11):
        letter = "ABCDEFGHIJK"[col]
        if col in (6, 8) or not context["fuels"]:
            sheet.write_blank(row, col, None, book.head())
        else:
            sheet.write_formula(row, col, f"=SUM({letter}4:{letter}{row})", book.head(align="right", num_format="#,##0.00"))
    if context["fuels"]:
        sheet.autofilter(2, 0, row - 1, len(columns) - 1)
    last = signature_rows(book, sheet, row + 2, context, len(columns) - 1)
    page_setup(sheet, last, len(columns) - 1, repeat=2)


def xlsx(context, kind="report"):
    buffer = BytesIO()
    book = Book(buffer)
    if kind == "report":
        report_sheet(book, context)
    if kind in ("report", "trips"):
        trips_sheet(book, context)
    if kind in ("report", "fuel"):
        fuel_sheet(book, context)
    book.book.close()
    return buffer.getvalue()
