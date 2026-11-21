import re
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

def feldolgoz_pdf(fajl):
    reader = PdfReader(fajl)
    oldalak = list(p.extract_text() or "" for p in reader.pages)
    szoveg = "\n".join(oldalak)
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": ""}
    m = re.search(r"Számla\s*sorszáma[:\s]+([A-Za-z0-9/-]{6,})", szoveg, re.I)
    if m:
        adat["szamlaszam"] = m.group(1).strip()
    mf = re.search(r"(\d{8,})", fajl.name)
    if not adat["szamlaszam"] and mf:
        adat["szamlaszam"] = mf.group(1)
    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk:
        adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh:
        adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mi = re.search(r"Elszámolt\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mi:
        adat["tol"] = datetime.strptime(mi.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(mi.group(2), "%Y.%m.%d").date()
    mb = re.search(r"(?:Fizetendő\s*összeg|Számla\s*végösszege)[:\s]+([\d\.\s]+)\s*Ft", szoveg, re.I)
    if mb:
        adat["brutto"] = float(mb.group(1).replace(".", "").replace(" ", "").replace(",", "."))
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp:
        adat["pod"] = mp.group(1).strip()
    return adat

def hely_jogosultsag(h, uid, helyett_lista):
    if h.get("felelos_energetikus_id") == uid or h.get("allando_helyettes_id") == uid:
        return "Saját"
    for hel in helyett_lista:
        egyezik_user = h.get("felelos_energetikus_id") == hel.get("helyettesitett_energetikus_id")
        egyezik_hely = hel.get("felhasznalasi_hely_id") is None or hel.get("felhasznalasi_hely_id") == h["id"]
        if egyezik_user and egyezik_hely:
            return "Helyettesítés"
    return None

def get_helyek(uid):
    ma = date.today().isoformat()
    helyek = supabase.table("felhasznalasi_helyek").select("*").eq("aktiv", True).execute().data or list()
    helyett = supabase.table("helyettesitesek").select("*").eq("helyettesito_energetikus_id", uid).eq("aktiv", True).lte("kezdet_datum", ma).gte("vege_datum", ma).execute().data or list()
    lista = list()
    for h in helyek:
        jog = hely_jogosultsag(h, uid, helyett)
        if jog:
            lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": jog})
    return lista

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
if not users_data:
    st.warning("Nincs aktív energetikus az adatbázisban!")
    st.stop()

users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok")))

if menu == "📊 Kimutatások és Riportok":
    st.header("Rögzített számlák és kimutatások")
    lista = supabase.table("szamlak").select("*").order("jovahagyas_ideje", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        st.stop()
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}
    e_map = {u["id"]: u["nev"] for u in (supabase.table("energetikusok").select("id, nev").execute().data or list())}
    sorok = list({"Számlaszám": s["szamlaszam"], "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""), "Időszak kezdete": s["idoszak_tol"], "Időszak vége": s["idoszak_ig"], "Fizetési határidő": s["fizetesi_hatarido"], "Bruttó összeg (Ft)": float(s["szamlan_szereplo_brutto"]), "Státusz": s["ellenorzes_statusz"], "PDF fájl": s["pdf_utvonal"], "Jóváhagyta": e_map.get(s["jovahagyta_energetikus_id"], "")} for s in lista)
    df = pd.DataFrame(sorok)
    st.bar_chart(df.groupby("Felhasználási hely")["Bruttó összeg (Ft)"].sum())
    st.dataframe(df, use_container_width=True)
    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Kimutatás letöltése Excelbe (CSV)", data=csv, file_name=f"energia_riport_{date.today()}.csv", mime="text/csv")
    st.stop()

st.header("Energiaszámla ellenőrzése és rögzítése")
helyek = get_helyek(user_id)
if not helyek:
    st.warning("Nincs hozzád rendelt felhasználási hely.")
    st.stop()

fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=list(("pdf", "xlsx", "csv")))
pdf_adat = None
if fajl and fajl.name.lower().endswith(".pdf"):
    fajl.seek(0)
    pdf_adat = feldolgoz_pdf(fajl)
    if not pdf_adat["szoveg"]:
        st.warning("Ez a PDF szkennelt képként készült, ezért a tételeket lent kézzel írd be (a PDF fájlt ugyanúgy elmenti a rendszer).")
    if pdf_adat["szamlaszam"]:
        st.success(f"PDF feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}**")

hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
alap_idx = 0
if pdf_adat and pdf_adat["pod"]:
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if pdf_adat["pod"] in lbl), 0)

st.subheader("2. Számla alapadatok")
c1, c2, c3 = st.columns(3)
v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx)
hely_id = hely_opciok[v_hely]
szamlaszam = c1.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "")

szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()))
szolg_id = szolg_opciok[v_szolg]
szamlan_brutto = c2.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=pdf_adat["brutto"] if pdf_adat else 0.0, step=1.0, format="%.2f")

kelte = c3.date_input("Számla kelte", value=pdf_adat["kelte"] if pdf_adat else date.today())
hatarido = c3.date_input("Fizetési határidő", value=pdf_adat["hatarido"] if pdf_adat else date.today())
tol = c3.date_input("Időszak kezdete", value=pdf_adat["tol"] if pdf_adat else date.today())
ig = c3.date_input("Időszak vége", value=pdf_adat["ig"] if pdf_adat else date.today())

st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
alap_df = pd.DataFrame({
    "Megnevezés": list(("Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj")),
    "Mennyiség": list((0.0, 0.0, 0.0, 0.0)),
    "Mértékegység": list(("kWh", "kWh", "kWh", "hó")),
    "Nettó egységár": list((0.0, 0.0, 0.0, 0.0)),
    "ÁFA (%)": list((27.0, 27.0, 27.0, 27.0))
})
if fajl and fajl.name.lower().endswith(".xlsx"):
    alap_df = pd.read_excel(fajl)
if fajl and fajl.name.lower().endswith(".csv"):
    alap_df = pd.read_csv(fajl)

szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True)

ossz_netto = Decimal("0")
ossz_brutto = Decimal("0")
tetelek = list()

for _, row in szerk_df.iterrows():
    if pd.isna(row.get("Mennyiség")) or pd.isna(row.get("Nettó egységár")):
        continue
    menny = Decimal(str(row["Mennyiség"]))
    ar = Decimal(str(row["Nettó egységár"]))
    if menny == 0 and ar == 0:
        continue
    afa = Decimal(str(row.get("ÁFA (%)", 27.0)))
    netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    ossz_netto += netto
    ossz_brutto += brutto
    tetelek.append({"megnevezes_eredeti": str(row.get("Megnevezés", "Díjtétel")), "mennyiseg": float(menny), "mertekegyseg": str(row.get("Mértékegység", "kWh")), "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})

brutto_dec = Decimal(str(szamlan_brutto)).quantize(Decimal("0.01"))
elteres = abs(brutto_dec - ossz_brutto)

m1, m2, m3 = st.columns(3)
m1.metric("Számított Nettó", f"{ossz_netto:,.0f} Ft")
m2.metric("Számított Bruttó", f"{ossz_brutto:,.0f} Ft")
m3.metric("Eltérés a számlától", f"{elteres:,.2f} Ft")

if elteres > Decimal("2.00") or len(tetelek) == 0 or szamlan_brutto <= 0:
    st.warning("A mentés gomb akkor jelenik meg, ha a számított bruttó összeg egyezik a számla összegével.")
    st.stop()

st.success("✅ A számított bruttó összeg megegyezik a számlán szereplővel!")
if not st.button("💾 PDF feltöltése és Számla mentése az adatbázisba", type="primary"):
    st.stop()

if not szamlaszam:
    st.error("Add meg a számlaszámot!")
    st.stop()

forras = "PDF" if pdf_adat else ("TABLAZAT" if fajl else "KEZI")
stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
