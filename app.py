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
    if not szoveg: return 0.0
    tiszta = re.sub(r"[^\d,.-]", "", str(szoveg)).replace(" ", "")
    if "," in tiszta and "." in tiszta: tiszta = tiszta.replace(".", "").replace(",", ".")
    elif "," in tiszta: tiszta = tiszta.replace(",", ".")
    elif tiszta.count(".") > 1: tiszta = tiszta.replace(".", "")
    return float(tiszta) if re.match(r"^-?\d+(\.\d+)?$", tiszta) else 0.0

def feldolgoz_xml(fajl):
    adat = {"szoveg": "XML", "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": "", "tetelek": list()}
    tree = ET.parse(fajl)
    root = tree.getroot()
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        val = (elem.text or "").strip()
        if not val: continue
        if tag in ("invoicenumber", "szamlaszam") and not adat["szamlaszam"]: adat["szamlaszam"] = val
        elif tag in ("invoiceissuedate", "szamlakelte") and len(val) >= 10: adat["kelte"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
        elif tag in ("paymentdate", "fizetesihatarido") and len(val) >= 10: adat["hatarido"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
        elif tag in ("invoicedeliveryperiodstart", "idoszaktol") and len(val) >= 10: adat["tol"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
        elif tag in ("invoicedeliveryperiodend", "idoszakig") and len(val) >= 10: adat["ig"] = datetime.strptime(val[:10], "%Y-%m-%d").date()
        elif tag in ("invoicegrossamount", "bruttoosszeg") and adat["brutto"] == 0.0: adat["brutto"] = szam_tisztit(val)
        elif "HU" in val and len(val) >= 16 and not adat["pod"]:
            m_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", val)
            if m_pod: adat["pod"] = m_pod.group(1)
    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag in ("line", "tetel"):
            megn, me, menny, ar, afa, br = "Díjtétel", "kWh", 0.0, 0.0, 27.0, 0.0
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
                adat["tetelek"].append({"Megnevezés": megn, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa, "Számlán bruttó (Ft)": br})
    return adat

def feldolgoz_pdf(fajl):
    reader = PdfReader(fajl)
    szoveg = "\n".join(list(p.extract_text() or "" for p in reader.pages))
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
    sor_minta = re.compile(r"([A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű\"'\(\)\-\s]{4,}?)\s+(?:\d{4}\.\d{2}\.\d{2}\s*[-–]\s*\d{4}\.\d{2}\.\d{2}\s+)?([\d\s\.,]+)\s+(kWh|hó|m3|MJ|db|kW)\s+([\d\s\.,]+)\s*(?:Ft/\w+)?\s+([\d\s\.,]+)\s+(\d{1,2})%?\s+([\d\s\.,]+)")
    for sor in szoveg.splitlines():
        tal = sor_minta.search(sor)
        if tal:
            megn = re.sub(r"\s+", " ", tal.group(1)).strip()
            if "összesen" in megn.lower() or len(megn) < 4: continue
            menny, ar = szam_tisztit(tal.group(2)), szam_tisztit(tal.group(4))
            if menny > 0 and ar > 0:
                adat["tetelek"].append({"Megnevezés": megn, "Mennyiség": menny, "Mértékegység": tal.group(3).strip(), "Nettó egységár": ar, "ÁFA (%)": szam_tisztit(tal.group(6)), "Számlán bruttó (Ft)": szam_tisztit(tal.group(7))})
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
            if h.get("felelos_energetikus_id") == hel.get("helyettesitett_energetikus_id") and (hel.get("felhasznalasi_hely_id") is None or hel.get("felhasznalasi_hely_id") == h["id"]):
                lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Helyettesítés"})
    return lista
def szamol_tetelek(df):
    ossz_netto, ossz_brutto = Decimal("0"), Decimal("0")
    tetelek, hibas_sorok = list(), list()
    for idx, row in df.iterrows():
        m_val, a_val = row.get("Mennyiség"), row.get("Nettó egységár")
        if m_val is None or a_val is None or pd.isna(m_val) or pd.isna(a_val): continue
        menny, ar = Decimal(str(m_val)), Decimal(str(a_val))
        szamla_tetel_br = Decimal(str(row.get("Számlán bruttó (Ft)") or 0))
        if menny == 0 and ar == 0 and szamla_tetel_br == 0: continue
        afa_val = row.get("ÁFA (%)")
        afa = Decimal(str(afa_val)) if (afa_val is not None and not pd.isna(afa_val)) else Decimal("27.0")
        netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ossz_netto += netto
        ossz_brutto += brutto
        megn = str(row.get("Megnevezés") or f"{idx+1}. sor")
        tetel_elt = abs(szamla_tetel_br - brutto) if szamla_tetel_br > 0 else Decimal("0")
        if szamla_tetel_br > 0 and tetel_elt > Decimal("2.00"):
            hibas_sorok.append(f"**{megn}** (számított: {brutto:,.0f} Ft, számlán: {szamla_tetel_br:,.0f} Ft, eltérés: {tetel_elt:,.0f} Ft)")
        tetelek.append({"megnevezes_eredeti": megn, "mennyiseg": float(menny), "mertekegyseg": str(row.get("Mértékegység") or "kWh"), "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})
    return ossz_netto, ossz_brutto, tetelek, hibas_sorok

def mentes_adatbazisba(uj_szamla, tetelek, fajl):
    letezo = supabase.table("szamlak").select("id").eq("szolgaltato_id", uj_szamla["szolgaltato_id"]).eq("szamlaszam", uj_szamla["szamlaszam"]).execute().data
    if letezo:
        st.warning(f"⚠️ Ez a számlaszám ({uj_szamla['szamlaszam']}) már rögzítve van ennél a szolgáltatónál!")
        return
    res = supabase.table("szamlak").insert(uj_szamla).execute()
    sid = next(iter(res.data))["id"]
    for t in tetelek: t["szamla_id"] = sid
    supabase.table("szamla_tetelek").insert(tetelek).execute()
    if fajl is not None and fajl.name.lower().endswith(".pdf"):
        fajl.seek(0)
        fnev = f"{date.today().year}_{uj_szamla['szamlaszam'].replace('/', '_')}.pdf"
        supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
        supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
    st.session_state["utolso_mentes"] = f"✅ Sikeresen megtörtént a számla rögzítése! (Számlaszám: {uj_szamla['szamlaszam']}, ID: #{sid})"
    urit_urlap()
    st.rerun()

def mutat_kimutatasok():
    st.header("Rögzített számlák és kimutatások")
    lista = supabase.table("szamlak").select("*").order("jovahagyas_ideje", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        return
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}
    sorok = list({"Számlaszám": s["szamlaszam"], "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""), "Időszak kezdete": s["idoszak_tol"], "Időszak vége": s["idoszak_ig"], "Fizetési határidő": s["fizetesi_hatarido"], "Bruttó összeg (Ft)": float(s["szamlan_szereplo_brutto"]), "Státusz": s["ellenorzes_statusz"], "PDF fájl": s["pdf_utvonal"]} for s in lista)
    df = pd.DataFrame(sorok)
    st.bar_chart(df.groupby("Felhasználási hely")["Bruttó összeg (Ft)"].sum())
    st.dataframe(df, use_container_width=True)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Kimutatás letöltése Excelbe (CSV)", data=csv, file_name=f"energia_riport_{date.today()}.csv", mime="text/csv")

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok")), on_change=urit_urlap)
if menu == "📊 Kimutatások és Riportok":
    mutat_kimutatasok()
    st.stop()

fv = st.session_state["form_ver"]
col_fej, col_gomb = st.columns((4, 1))
col_fej.header("Energiaszámla ellenőrzése és rögzítése")
if col_gomb.button("🗑️ Új számla / Ürítés"):
    urit_urlap()
    st.rerun()

if "utolso_mentes" in st.session_state:
    st.success(st.session_state.pop("utolso_mentes"))

helyek = get_helyek(user_id)
if not helyek:
    st.warning("Nincs hozzád rendelt felhasználási hely a rendszerben.")
    st.stop()

fajl = st.file_uploader("1. Húzd ide a számlát (Digitális PDF, XML vagy Excel/CSV) – opcionális", type=list(("pdf", "xml", "xlsx", "csv")), key=f"fajl_{fv}")
f_id = f"{fv}_{fajl.name}" if fajl else f"{fv}_ures"
dokumentum_adat = None

if fajl and fajl.name.lower().endswith(".pdf"):
    fajl.seek(0)
    dokumentum_adat = feldolgoz_pdf(fajl)
    if not dokumentum_adat["szoveg"]: st.warning("Szkennelt (képalapú) PDF: a tételeket lent kézzel írd be, a PDF fájlt ugyanúgy elmenti a rendszer.")
    elif dokumentum_adat["tetelek"]: st.success(f"Digitális PDF sikeresen beolvasva! Számlaszám: **{dokumentum_adat['szamlaszam']}** ({len(dokumentum_adat['tetelek'])} díjtétel felismerve)")
    elif dokumentum_adat["szamlaszam"]: st.success(f"PDF feldolgozva! Számlaszám: **{dokumentum_adat['szamlaszam']}**")

if fajl and fajl.name.lower().endswith(".xml"):
    fajl.seek(0)
    dokumentum_adat = feldolgoz_xml(fajl)
    st.success(f"E-számla (XML) sikeresen beolvasva! Számlaszám: **{dokumentum_adat['szamlaszam']}** ({len(dokumentum_adat['tetelek'])} díjtétel)")

hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
alap_idx = 0
if dokumentum_adat and dokumentum_adat["pod"]:
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if dokumentum_adat["pod"] in lbl), 0)

st.subheader("2. Számla alapadatok")
c1, c2, c3 = st.columns(3)
v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx, key=f"hely_{f_id}")
hely_id = hely_opciok[v_hely]
szamlaszam = c1.text_input("Számlaszám", value=dokumentum_adat["szamlaszam"] if dokumentum_adat else "", key=f"szsz_{f_id}")

szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()), key=f"szolg_{f_id}")
szolg_id = szolg_opciok[v_szolg]
szamlan_brutto = c2.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=dokumentum_adat["brutto"] if dokumentum_adat else 0.0, step=1.0, format="%.2f", key=f"br_{f_id}")

kelte = c3.date_input("Számla kelte", value=dokumentum_adat["kelte"] if dokumentum_adat else date.today(), key=f"k_{f_id}")
hatarido = c3.date_input("Fizetési határidő", value=dokumentum_adat["hatarido"] if dokumentum_adat else date.today(), key=f"h_{f_id}")
tol = c3.date_input("Időszak kezdete", value=dokumentum_adat["tol"] if dokumentum_adat else date.today(), key=f"t_{f_id}")
ig = c3.date_input("Időszak vége", value=dokumentum_adat["ig"] if dokumentum_adat else date.today(), key=f"i_{f_id}")

st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
if dokumentum_adat and dokumentum_adat.get("tetelek"):
    alap_df = pd.DataFrame(dokumentum_adat["tetelek"])
elif fajl and fajl.name.lower().endswith(".xlsx"):
    alap_df = pd.read_excel(fajl)
elif fajl and fajl.name.lower().endswith(".csv"):
    alap_df = pd.read_csv(fajl)
else:
    alap_df = pd.DataFrame(dict(Megnevezés=["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"], Mennyiség=[0.0, 0.0, 0.0, 0.0], Mértékegység=["kWh", "kWh", "kWh", "hó"], Nettó_egységár=[0.0, 0.0, 0.0, 0.0], ÁFA=[27.0, 27.0, 27.0, 27.0], Számlán_bruttó=[0.0, 0.0, 0.0, 0.0]))
    alap_df.columns = list(("Megnevezés", "Mennyiség", "Mértékegység", "Nettó egységár", "ÁFA (%)", "Számlán bruttó (Ft)"))

szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True, key=f"ed_{f_id}")
ossz_netto, ossz_brutto, tetelek, hibas_sorok = szamol_tetelek(szerk_df)

brutto_dec = Decimal(str(szamlan_brutto)).quantize(Decimal("0.01"))
elteres = abs(brutto_dec - ossz_brutto)

m1, m2, m3 = st.columns(3)
m1.metric("Számított Nettó", f"{ossz_netto:,.0f} Ft")
m2.metric("Számított Bruttó", f"{ossz_brutto:,.0f} Ft")
m3.metric("Eltérés a végösszegtől", f"{elteres:,.2f} Ft")

if hibas_sorok:
    st.error("🔴 Eltérés található az alábbi díjtételeknél:\n- " + "\n- ".join(hibas_sorok))

egyezik = (elteres <= Decimal("2.00")) and (len(hibas_sorok) == 0) and (len(tetelek) > 0) and (szamlan_brutto > 0)
if egyezik: st.success("✅ A számított értékek megegyeznek a számlán szereplőkkel!")
if not egyezik and not hibas_sorok: st.warning("A mentés gomb akkor válik aktívvá, ha a végösszeg és a megadott tételösszegek is egyeznek.")

gomb = st.button("💾 Számla mentése az adatbázisba", type="primary", disabled=not egyezik)
forras = "XML" if (fajl and fajl.name.lower().endswith(".xml")) else ("PDF" if dokumentum_adat else ("TABLAZAT" if fajl else "KEZI"))
stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
