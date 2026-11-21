import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import pandas as pd
import streamlit as st
from pypdf import PdfReader
from supabase import create_client

st.set_page_config(page_title="Energiaszámla Ellenőrző", page_icon="⚡", layout="wide")

SUPABASE_URL = st.secrets["SUPABASE_URL"].strip()
SUPABASE_KEY = st.secrets["SUPABASE_KEY"].strip()

@st.cache_resource
def get_supabase():
    return create_client(SUPABASE_URL, SUPABASE_KEY)

supabase = get_supabase()

if "form_ver" not in st.session_state:
    st.session_state["form_ver"] = 0

def urit_urlap():
    st.session_state["form_ver"] += 1

def szam_tisztit(szoveg):
    if not szoveg:
        return 0.0
    tiszta = re.sub(r"[^\d,.-]", "", str(szoveg)).replace(" ", "")
    if "," in tiszta and "." in tiszta:
        tiszta = tiszta.replace(".", "").replace(",", ".")
    elif "," in tiszta:
        tiszta = tiszta.replace(",", ".")
    elif tiszta.count(".") > 1:
        tiszta = tiszta.replace(".", "")
    try:
        return float(tiszta)
    except ValueError:
        return 0.0

def feldolgoz_xml(fajl):
    adat = {"szoveg": "XML", "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": "", "tetelek": list()}
    try:
        tree = ET.parse(fajl)
        root = tree.getroot()
        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()
            val = (elem.text or "").strip()
            if not val:
                continue
            if tag in ("invoicenumber", "szamlaszam") and not adat["szamlaszam"]:
                adat["szamlaszam"] = val
            elif tag in ("invoiceissuedate", "szamlakelte"):
                try: adat["kelte"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
                except ValueError: pass
            elif tag in ("paymentdate", "fizetesihatarido"):
                try: adat["hatarido"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
                except ValueError: pass
            elif tag in ("invoicedeliveryperiodstart", "idoszaktol"):
                try: adat["tol"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
                except ValueError: pass
            elif tag in ("invoicedeliveryperiodend", "idoszakig"):
                try: adat["ig"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
                except ValueError: pass
            elif tag in ("invoicegrossamount", "bruttoosszeg") and adat["brutto"] == 0.0:
                adat["brutto"] = szam_tisztit(val)
            elif "HU" in val and len(val) >= 16 and not adat["pod"]:
                m_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", val)
                if m_pod: adat["pod"] = m_pod.group(1)

        for elem in root.iter():
            tag = elem.tag.split("}")[-1].lower()
            if tag in ("line", "tetel"):
                megn, me = "Díjtétel", "kWh"
                menny, ar, afa, br = 0.0, 0.0, 27.0, 0.0
                for child in elem.iter():
                    ctag = child.tag.split("}")[-1].lower()
                    cval = (child.text or "").strip()
                    if not cval: continue
                    if ctag in ("linedescription", "megnevezes"): megn = cval
                    elif ctag in ("quantity", "mennyiseg"): menny = szam_tisztit(cval)
                    elif ctag in ("unitofmeasure", "mertekegyseg"): me = cval
                    elif ctag in ("unitprice", "egysegar"): ar = szam_tisztit(cval)
                    elif ctag in ("vatpercentage", "afakulcs"): afa = szam_tisztit(cval) * (100 if szam_tisztit(cval) < 1 else 1)
                    elif ctag in ("linegrossamount", "tetelbrutto"): br = szam_tisztit(cval)
                if menny > 0 or br > 0:
                    adat["tetelek"].append({"Állapot": "", "Megnevezés": megn, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa, "Számlán bruttó (Ft)": br})
    except Exception:
        pass
    return adat

def feldolgoz_pdf(fajl):
    reader = PdfReader(fajl)
    oldalak = list()
    for p in reader.pages:
        try: oldalak.append(p.extract_text(extraction_mode="layout") or "")
        except Exception: oldalak.append(p.extract_text() or "")
    szoveg = "\n".join(oldalak)
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": "", "tetelek": list()}

    m = re.search(r"(?:Számla\s*sorszáma|Számlaszám|Bizonylat\s*sorszáma)[:\s]+([A-Za-z0-9/-]{5,})", szoveg, re.I)
    if m: adat["szamlaszam"] = m.group(1).strip()
    alap_nev = next(iter(fajl.name.rsplit(".", 1)))
    mf = re.search(r"(\d{6,})", alap_nev)
    if not adat["szamlaszam"] and mf: adat["szamlaszam"] = mf.group(1)
    if not adat["szamlaszam"] and len(alap_nev) >= 5: adat["szamlaszam"] = alap_nev

    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk: adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh: adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mi = re.search(r"Elszámolt\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mi:
        adat["tol"] = datetime.strptime(mi.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(mi.group(2), "%Y.%m.%d").date()

    mb = re.search(r"(?:Fizetendő\s*összeg|Számla\s*végösszege|Bruttó\s*összesen)[:\s]+([\d\.\s,]+)\s*Ft", szoveg, re.I)
    if mb: adat["brutto"] = szam_tisztit(mb.group(1))
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()

    # Digitális PDF táblázatsorok felismerése (mennyiség, egység, egységár, nettó, áfa%, bruttó)
    sor_minta = re.compile(r"([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű\"'\(\)\-\s]{4,}?)\s+(?:\d{4}\.\d{2}\.\d{2}\s*[-–]\s*\d{4}\.\d{2}\.\d{2}\s+)?([\d\s\.,]+)\s+(kWh|hó|m3|MJ|db|kW)\s+([\d\s\.,]+)\s*(?:Ft/\w+)?\s+([\d\s\.,]+)\s+(\d{1,2})%?\s+([\d\s\.,]+)")
    for sor in szoveg.splitlines():
        tal = sor_minta.search(sor)
        if tal:
            megn = re.sub(r"\s+", " ", tal.group(1)).strip()
            if "összesen" in megn.lower() or len(megn) < 4:
                continue
            menny = szam_tisztit(tal.group(2))
            me = tal.group(3).strip()
            ar = szam_tisztit(tal.group(4))
            afa = szam_tisztit(tal.group(6))
            brutto_sor = szam_tisztit(tal.group(7))
            if menny > 0 and ar > 0:
                adat["tetelek"].append({"Állapot": "", "Megnevezés": megn, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa, "Számlán bruttó (Ft)": brutto_sor})

    return adat

def get_helyek(uid):
    ma = date.today().isoformat()
    helyek = supabase.table("felhasznalasi_helyek").select("*").eq("aktiv", True).execute().data or list()
    helyett = supabase.table("helyettesitesek").select("*").eq("helyettesito_energetikus_id", uid).eq("aktiv", True).lte("kezdet_datum", ma).gte("vege_datum", ma).execute().data or list()
    lista = list()
    for h in helyek:
        if h.get("felelos_energetikus_id") == uid or h.get("allando_helyettes_id") == uid:
            lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Saját"})
    for hel in helyett:
        for h in helyek:
            if h.get("felelos_energetikus_id") == hel.get("helyettesitett_energetikus_id"):
                if hel.get("felhasznalasi_hely_id") is None or hel.get("felhasznalasi_hely_id") == h["id"]:
                    lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Helyettesítés"})
    return lista

def szamol_tetelek(df):
    ossz_netto = Decimal("0")
    ossz_brutto = Decimal("0")
    tetelek = list()
    van_tetel_hiba = False
    statuszok = list()

    for _, row in df.iterrows():
        m_val = row.get("Mennyiség")
        a_val = row.get("Nettó egységár")
        if m_val is None or a_val is None or pd.isna(m_val) or pd.isna(a_val):
            statuszok.append("")
            continue
        menny = Decimal(str(m_val))
        ar = Decimal(str(a_val))
        szamla_tetel_br = Decimal(str(row.get("Számlán bruttó (Ft)") or 0))
        if menny == 0 and ar == 0 and szamla_tetel_br == 0:
            statuszok.append("")
            continue
        afa_val = row.get("ÁFA (%)")
        afa = Decimal(str(afa_val)) if (afa_val is not None and not pd.isna(afa_val)) else Decimal("27.0")
        netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ossz_netto += netto
        ossz_brutto += brutto

        tetel_elt = abs(szamla_tetel_br - brutto) if szamla_tetel_br > 0 else Decimal("0")
        hibas_sor = (szamla_tetel_br > 0) and (tetel_elt > Decimal("2.00"))
        if hibas_sor:
            van_tetel_hiba = True
            statuszok.append(f"🔴 ELTÉRÉS ({tetel_elt:,.0f} Ft)")
        elif szamla_tetel_br > 0:
            statuszok.append("✅ OK")
        else:
            statuszok.append("")

        megn = str(row.get("Megnevezés") or "Díjtétel")
        me = str(row.get("Mértékegység") or "kWh")
        tetelek.append({"megnevezes_eredeti": megn, "mennyiseg": float(menny), "mertekegyseg": me, "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})

    return ossz_netto, ossz_brutto, tetelek, van_tetel_hiba, statuszok

def mentes_adatbazisba(uj_szamla, tetelek, fajl):
    try:
        res = supabase.table("szamlak").insert(uj_szamla).execute()
        sid = next(iter(res.data))["id"]
        for t in tetelek:
            t["szamla_id"] = sid
        supabase.table("szamla_tetelek").insert(tetelek).execute()
        if fajl is not None and fajl.name.lower().endswith(".pdf"):
            fajl.seek(0)
            fnev = f"{date.today().year}_{uj_szamla['szamlaszam'].replace('/', '_')}.pdf"
            supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
            supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
        st.session_state["utolso_mentes"] = f"✅ Sikeresen megtörtént a számla rögzítése! (Számlaszám: {uj_szamla['szamlaszam']}, ID: #{sid})"
        urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")
