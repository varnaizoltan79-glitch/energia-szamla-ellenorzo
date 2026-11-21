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
    szoveg = "\n".join(p.extract_text() or "" for p in reader.pages)
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0.0, "pod": ""}

    m = re.search(r"Számla\s*sorszáma[:\s]+([A-Za-z0-9/-]{6,})", szoveg, re.I)
    if m:
        adat["szamlaszam"] = m.group(1).strip()
    else:
        mf = re.search(r"(\d{8,})", fajl.name)
        if mf:
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
        try:
            adat["brutto"] = float(mb.group(1).replace(".", "").replace(" ", "").replace(",", "."))
        except ValueError:
            pass

    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp:
        adat["pod"] = mp.group(1).strip()

    return adat

def get_helyek(uid):
    ma = date.today().isoformat()
    helyek = supabase.table("felhasznalasi_helyek").select("*").eq("aktiv", True).execute().data or []
    helyett = supabase.table("helyettesitesek").select("*").eq("helyettesito_energetikus_id", uid).eq("aktiv", True).lte("kezdet_datum", ma).gte("vege_datum", ma).execute().data or []
    lista = []
    latott = set()
    for h in helyek:
        if h.get("felelos_energetikus_id") == uid or h.get("allando_helyettes_id") == uid:
            lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Saját"})
            latott.add(h["id"])
    for hel in helyett:
        for h in helyek:
            if h["id"] not in latott and h.get("felelos_energetikus_id") == hel.get("helyettesitett_energetikus_id"):
                if hel.get("felhasznalasi_hely_id") is None or hel.get("felhasznalasi_hely_id") == h["id"]:
                    lista.append({"id": h["id"], "nev": h["megnevezes"], "pod": h["pod_eic_azonosito"], "jog": "Helyettesítés"})
                    latott.add(h["id"])
    return lista

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or []
if not users_data:
    st.warning("Nincs aktív energetikus az adatbázisban!")
    st.stop()

users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", ["🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok"])

if menu == "🧾 Számla ellenőrzés és feltöltés":
    st.header("Energiaszámla ellenőrzése és rögzítése")
    helyek = get_helyek(user_id)
    if not helyek:
        st.warning("Nincs hozzád rendelt felhasználási hely.")
        st.stop()

    fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=["pdf", "xlsx", "csv"])
    pdf_adat = None
    if fajl and fajl.name.lower().endswith(".pdf"):
        fajl.seek(0)
        pdf_adat = feldolgoz_pdf(fajl)
        if not pdf_adat["szoveg"]:
            st.warning("Ez a PDF szkennelt képként készült, ezért a tételeket lent kézzel írd be (a PDF fájlt ugyanúgy elmenti a rendszer).")
        elif pdf_adat["szamlaszam"]:
            st.success(f"PDF feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}**")

    hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
    alap_idx = 0
    if pdf_adat and pdf_adat["pod"]:
        for i, lbl in enumerate(hely_opciok.keys()):
            if pdf_adat["pod"] in lbl:
                alap_idx = i
                break

    st.subheader("2. Számla alapadatok")
    c1, c2, c3 = st.columns(3)
    with c1:
        v_hely = st.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx)
        hely_id = hely_opciok[v_hely]
        szamlaszam = st.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "")
    with c2:
        szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or []
        szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
        v_szolg = st.selectbox("Szolgáltató", list(szolg_opciok.keys()))
        szolg_id = szolg_opciok[v_szolg]
        szamlan_brutto = st.number_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=pdf_adat["brutto"] if pdf_adat else 0.0, step=1.0, format="%.2f")
    with c3:
        kelte = st.date_input("Számla kelte", value=pdf_adat["kelte"] if pdf_adat else date.today())
        hatarido = st.date_input("Fizetési határidő", value=pdf_adat["hatarido"] if pdf_adat else date.today())
        tol = st.date_input("Időszak kezdete", value=pdf_adat["tol"] if pdf_adat else date.today())
        ig = st.date_input("Időszak vége", value=pdf_adat["ig"] if pdf_adat else date.today())

    st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
    if fajl and fajl.name.lower().endswith(".xlsx"):
        alap_df = pd.read_excel(fajl)
    elif fajl and fajl.name.lower().endswith(".csv"):
        alap_df = pd.read_csv(fajl)
    else:
        alap_df = pd.DataFrame({
            "Megnevezés": ["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"],
            "Mennyiség": [0.0, 0.0, 0.0, 0.0],
            "Mértékegység": ["kWh", "kWh", "kWh", "hó"],
            "Nettó egységár": [0.0, 0.0, 0.0, 0.0],
            "ÁFA (%)": [27.0, 27.0, 27.0, 27.0]
        })

    szerk_df = st.data_editor(alap_df, num_rows="dynamic", use_container_width=True)

    ossz_netto = Decimal("0")
    ossz_brutto = Decimal("0")
    tetelek = []

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

    if elteres <= Decimal("2.00") and len(tetelek) > 0 and szamlan_brutto > 0:
        st.success("✅ A számított bruttó összeg megegyezik a számlán szereplővel!")
        if st.button("💾 PDF feltöltése és Számla mentése az adatbázisba", type="primary"):
            if not szamlaszam:
                st.error("Add meg a számlaszámot!")
            else:
                try:
                    pdf_ut = None
                    if fajl is not None:
                        fajl.seek(0)
                        fnev = f"{date.today().year}/{szamlaszam.replace('/', '_')}_{fajl.name}"
                        supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
                        pdf_ut = fnev

                    forras = "PDF" if pdf_adat else ("TABLAZAT" if fajl else "KEZI")
                    stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
                    uj_szamla = {
                        "szamlaszam": szamlaszam, "szolgaltato_id": szolg_id, "felhasznalasi_hely_id": hely_id,
                        "forras_tipus": forras, "szamla_kelte": kelte.isoformat(), "fizetesi_hatarido": hatarido.isoformat(),
                        "idoszak_tol": tol.isoformat(), "idoszak_ig": ig.isoformat(), "szamlan_szereplo_brutto": float(brutto_dec),
                        "szamitott_brutto": float(ossz_brutto), "elteres": float(elteres), "ellenorzes_statusz": stat,
                        "pdf_utvonal": pdf_ut, "jovahagyta_energetikus_id": user_id
                    }
                    res = supabase.table("szamlak").insert(uj_szamla).execute()
                    sid = next(iter(res.data))["id"]
                    for t in tetelek:
                        t["szamla_id"] = sid
                    supabase.table("szamla_tetelek").insert(tetelek).execute()
                    st.balloons()
