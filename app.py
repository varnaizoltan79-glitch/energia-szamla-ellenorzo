import base64
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import altair as alt
import fitz
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

def formaz_ezres(kulcs):
    nyers = re.sub(r"\D", "", st.session_state.get(kulcs, ""))
    st.session_state[kulcs] = f"{int(nyers):,}".replace(",", " ") if nyers else ""

def ft_szoveg(ertek):
    return f"{int(round(float(ertek))):,} Ft".replace(",", " ")

HONAPOK_HU = ("", "január", "február", "március", "április", "május", "június", "július", "augusztus", "szeptember", "október", "november", "december")

def szamol_elszamolasi_honap(ig_datum):
    ev, ho = ig_datum.year, ig_datum.month
    if ig_datum.day <= 14:
        ho -= 1
        if ho == 0:
            ho = 12
            ev -= 1
    return f"{ev}. {HONAPOK_HU[ho]}"

def tiszta_honap(cimke):
    if not cimke:
        return "Nincs megadva"
    m = re.search(r"\((\d{4}\.\s*[a-záéíóöőúüű]+)\)", cimke)
    return m.group(1) if m else cimke

def honap_kulcs(cimke):
    m = re.match(r"(\d{4})\.\s*([a-záéíóöőúüű]+)", cimke)
    if m and m.group(2) in HONAPOK_HU:
        return (int(m.group(1)), HONAPOK_HU.index(m.group(2)))
    return (9999, 99)

@st.dialog("📄 Számla PDF előnézet", width="large")
def mutat_pdf_ablak(pdf_utvonal, szamlaszam):
    st.subheader(f"Számlaszám: {szamlaszam} ({pdf_utvonal})")
    pdf_bytes = supabase.storage.from_("szamlak-pdf").download(pdf_utvonal)
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    for idx, oldal in enumerate(dok):
        kep = oldal.get_pixmap(dpi=150)
        st.image(kep.tobytes("png"), caption=f"{idx + 1}. oldal", use_container_width=True)
    st.download_button("📥 PDF letöltése a gépre", data=pdf_bytes, file_name=pdf_utvonal, mime="application/pdf")

def feldolgoz_pdf(fajl):
    reader = PdfReader(fajl)
    oldalak = list(p.extract_text() or "" for p in reader.pages)
    szoveg = "\n".join(oldalak)
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0, "pod": ""}
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
    mb = re.search(r"(?:Fizetendő\s*összeg|Számla\s*végösszege)[:\s]+([\d\.\s]+)\s*Ft", szoveg, re.I)
    if mb: adat["brutto"] = int(round(float(mb.group(1).replace(".", "").replace(" ", "").replace(",", "."))))
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()
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
    for _, row in df.iterrows():
        m_val = row.get("Mennyiség")
        a_val = row.get("Nettó egységár")
        if m_val is None or a_val is None or pd.isna(m_val) or pd.isna(a_val):
            continue
        menny = Decimal(str(m_val))
        ar = Decimal(str(a_val))
        if menny == 0 and ar == 0:
            continue
        afa_val = row.get("ÁFA (%)")
        afa = Decimal(str(afa_val)) if (afa_val is not None and not pd.isna(afa_val)) else Decimal("27.0")
        netto = (menny * ar).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        brutto = (netto * (Decimal("1") + afa / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        ossz_netto += netto
        ossz_brutto += brutto
        tetelek.append({"megnevezes_eredeti": str(row.get("Megnevezés") or "Díjtétel"), "mennyiseg": float(menny), "mertekegyseg": str(row.get("Mértékegység") or "kWh"), "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})
    return ossz_netto, ossz_brutto, tetelek

def mentes_adatbazisba(uj_szamla, tetelek, fajl):
    try:
        res = supabase.table("szamlak").insert(uj_szamla).execute()
        sid = next(iter(res.data))["id"]
        for t in tetelek:
            t["szamla_id"] = sid
        supabase.table("szamla_tetelek").insert(tetelek).execute()
        if fajl is not None:
            fajl.seek(0)
            fnev = f"{date.today().year}_{uj_szamla['szamlaszam'].replace('/', '_')}.pdf"
            supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
            supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
        st.session_state["utolso_mentes"] = f"✅ A számla rögzítése megtörtént (Számlaszám: {uj_szamla['szamlaszam']}, Elszámolási hónap: {uj_szamla['elszamolasi_honap']}, ID: #{sid})"
        urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")
def mutat_torzsadatok():
    st.header("⚙️ Törzsadatok és Helyettesítések kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    h_map = {h["id"]: h["megnevezes"] for h in osszes_hely}

    t_helyett, t_hely, t_user = st.tabs(list(("🔄 Helyettesítések", "🏭 Felhasználási helyek", "👤 Energetikusok")))

    with t_helyett:
        st.subheader("Aktív és rögzített helyettesítések")
        helyett_lista = supabase.table("helyettesitesek").select("*").order("kezdet_datum", desc=True).execute().data or list()
        if helyett_lista:
            h_sorok = list({"ID": x["id"], "Helyettesített": u_map.get(x["helyettesitett_energetikus_id"], ""), "Helyettesítő": u_map.get(x["helyettesito_energetikus_id"], ""), "Felhasználási hely": h_map.get(x["felhasznalasi_hely_id"], "Minden telephelye"), "Kezdet": x["kezdet_datum"], "Vége": x["vege_datum"], "Aktív": x["aktiv"]} for x in helyett_lista)
            st.dataframe(pd.DataFrame(h_sorok), use_container_width=True, hide_index=True)
            torol_h = st.selectbox("Helyettesítés törlése:", list(f"#{x['id']} – {u_map.get(x['helyettesitett_energetikus_id'], '')} -> {u_map.get(x['helyettesito_energetikus_id'], '')} ({x['kezdet_datum']} - {x['vege_datum']})" for x in helyett_lista))
            if st.button("🗑️ Kiválasztott helyettesítés törlése"):
                hid = int(next(iter(torol_h.split(" – "))).replace("#", ""))
                supabase.table("helyettesitesek").delete().eq("id", hid).execute()
                st.success("✅ Helyettesítés törölve!")
                st.rerun()
        else:
            st.info("Még nincs rögzített helyettesítés.")

        st.divider()
        st.subheader("➕ Új helyettesítés rögzítése")
        with st.form("uj_helyettesites_form"):
            hc1, hc2 = st.columns(2)
            kit = hc1.selectbox("Helyettesített energetikus (aki távol van):", list(aktiv_user_opciok.keys()))
            kivel = hc2.selectbox("Helyettesítő energetikus (aki átveszi):", list(aktiv_user_opciok.keys()))
            hely_valasztek = {"Minden telephelyére érvényes": None}
            for h in osszes_hely:
                hely_valasztek[f"{h['megnevezes']} ({h['pod_eic_azonosito']})"] = h["id"]
            melyik_hely = st.selectbox("Érintett felhasználási hely:", list(hely_valasztek.keys()))
            hd1, hd2 = st.columns(2)
            h_tol = hd1.date_input("Helyettesítés kezdete", value=date.today())
            h_ig = hd2.date_input("Helyettesítés vége", value=date.today())
            if st.form_submit_button("💾 Helyettesítés mentése", type="primary"):
                if aktiv_user_opciok[kit] == aktiv_user_opciok[kivel]:
                    st.error("A helyettesített és a helyettesítő személy nem lehet ugyanaz!")
                elif h_tol > h_ig:
                    st.error("A kezdeti dátum nem lehet későbbi, mint a záró dátum!")
                else:
                    supabase.table("helyettesitesek").insert({"helyettesitett_energetikus_id": aktiv_user_opciok[kit], "helyettesito_energetikus_id": aktiv_user_opciok[kivel], "felhasznalasi_hely_id": hely_valasztek[melyik_hely], "kezdet_datum": h_tol.isoformat(), "vege_datum": h_ig.isoformat(), "aktiv": True}).execute()
                    st.success("✅ Helyettesítés sikeresen rögzítve!")
                    st.rerun()

    with t_hely:
        st.subheader("Felhasználási helyek listája")
        if osszes_hely:
            fh_sorok = list({"ID": h["id"], "Megnevezés": h["megnevezes"], "POD / EIC": h["pod_eic_azonosito"], "Felelős energetikus": u_map.get(h.get("felelos_energetikus_id"), ""), "Állandó helyettes": u_map.get(h.get("allando_helyettes_id"), "Nincs"), "Aktív": h["aktiv"]} for h in osszes_hely)
            st.dataframe(pd.DataFrame(fh_sorok), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("➕ Új felhasználási hely felvétele")
        with st.form("uj_hely_form"):
            fc1, fc2 = st.columns(2)
            uj_h_nev = fc1.text_input("Telephely / Fogyasztási hely megnevezése (pl. Debrecen Iroda):")
            uj_h_pod = fc2.text_input("POD / EIC mérési pont azonosító (pl. HU000130-...):")
            felelos_opciok = {"Nincs kijelölve": None}
            felelos_opciok.update(aktiv_user_opciok)
            fc3, fc4 = st.columns(2)
            uj_h_fel = fc3.selectbox("Felelős energetikus:", list(felelos_opciok.keys()))
            uj_h_helyett = fc4.selectbox("Állandó helyettes (opcionális):", list(felelos_opciok.keys()))
            if st.form_submit_button("💾 Felhasználási hely mentése", type="primary"):
                if not uj_h_nev.strip() or not uj_h_pod.strip():
                    st.error("Add meg a megnevezést és a POD azonosítót is!")
                else:
                    supabase.table("felhasznalasi_helyek").insert({"megnevezes": uj_h_nev.strip(), "pod_eic_azonosito": uj_h_pod.strip(), "felelos_energetikus_id": felelos_opciok[uj_h_fel], "allando_helyettes_id": felelos_opciok[uj_h_helyett], "aktiv": True}).execute()
                    st.success("✅ Új felhasználási hely elmentve!")
                    st.rerun()

    with t_user:
        st.subheader("Energetikusok listája")
        if osszes_user:
            u_sorok = list({"ID": u["id"], "Név": u["nev"], "Szerepkör": u["szerepkor"], "Aktív": u["aktiv"]} for u in osszes_user)
            st.dataframe(pd.DataFrame(u_sorok), use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("➕ Új energetikus felvétele")
        with st.form("uj_user_form"):
            uc1, uc2 = st.columns(2)
            uj_u_nev = uc1.text_input("Energetikus teljes neve:")
            uj_u_szerep = uc2.selectbox("Szerepkör:", list(("Energetikus", "Vezető energetikus", "Adminisztrátor")))
            if st.form_submit_button("💾 Energetikus mentése", type="primary"):
                if not uj_u_nev.strip():
                    st.error("Add meg az energetikus nevét!")
                else:
                    uj_adat = {"nev": uj_u_nev.strip(), "szerepkor": uj_u_szerep, "aktiv": True}
                    if osszes_user and "email" in next(iter(osszes_user)):
                        uj_adat["email"] = f"{re.sub(r'[^a-z0-9]', '', uj_u_nev.lower())}@ceg.hu"
                    supabase.table("energetikusok").insert(uj_adat).execute()
                    st.success("✅ Új energetikus sikeresen felvéve!")
                    st.rerun()
st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
user_id = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")

menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📊 Kimutatások és Riportok", "⚙️ Törzsadatok és Helyettesítések")), on_change=urit_urlap)

if menu == "⚙️ Törzsadatok és Helyettesítések":
    mutat_torzsadatok()
    st.stop()

if menu == "📊 Kimutatások és Riportok":
    st.header("Rögzített számlák és havi kimutatások")
    lista = supabase.table("szamlak").select("*").order("jovahagyas_ideje", desc=True).execute().data or list()
    if not lista:
        st.info("Még nincs mentett számla.")
        st.stop()
    h_map = {h["id"]: h["megnevezes"] for h in (supabase.table("felhasznalasi_helyek").select("id, megnevezes").execute().data or list())}
    sz_map = {s["id"]: s["nev"] for s in (supabase.table("szolgaltatok").select("id, nev").execute().data or list())}

    osszes_honap = sorted(list({tiszta_honap(s.get("elszamolasi_honap")) for s in lista}), key=honap_kulcs, reverse=True)
    osszes_hely = sorted(list({h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen") for s in lista}))

    fs1, fs2 = st.columns(2)
    val_honapok = fs1.multiselect("📅 Szűrés elszámolási hónapra (üresen = összes):", osszes_honap)
    val_helyek = fs2.multiselect("🏭 Szűrés felhasználási helyre (üresen = összes):", osszes_hely)

    szurt_lista = list()
    for s in lista:
        ho_cimke = tiszta_honap(s.get("elszamolasi_honap"))
        hely_cimke = h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen")
        if val_honapok and ho_cimke not in val_honapok:
            continue
        if val_helyek and hely_cimke not in val_helyek:
            continue
        szurt_lista.append(s)

    if not szurt_lista:
        st.warning("A megadott szűrőkkel nincs találat.")
        st.stop()

    sorok = list({"Számlaszám": s["szamlaszam"], "Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")), "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Szolgáltató": sz_map.get(s["szolgaltato_id"], ""), "Időszak kezdete": s["idoszak_tol"], "Időszak vége": s["idoszak_ig"], "Bruttó összeg (Ft)": int(round(float(s["szamlan_szereplo_brutto"]))), "Státusz": s["ellenorzes_statusz"], "PDF fájl": s["pdf_utvonal"]} for s in szurt_lista)
    df = pd.DataFrame(sorok)

    k1, k2, k3 = st.columns(3)
    k1.metric("Számlák száma", f"{len(df)} db")
    k2.metric("Összesített bruttó költség", ft_szoveg(df["Bruttó összeg (Ft)"].sum()))
    k3.metric("Érintett hónapok", f"{df['Elszámolási hónap'].nunique()} hónap")

    y_tengely = alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ') + ' Ft'", title="Bruttó összeg (Ft)")
    tab_ho, tab_hely, tab_tetel = st.tabs(list(("📅 Havi bontású költségek", "🏭 Telephelyek szerinti összesítés", "⚡ Fogyasztás (kWh) és Díjtételek")))
    with tab_ho:
        ho_df = df.groupby(["Elszámolási hónap", "Felhasználási hely"], as_index=False)["Bruttó összeg (Ft)"].sum()
        ho_df["Összeg"] = ho_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        ho_sorrend = sorted(list(ho_df["Elszámolási hónap"].unique()), key=honap_kulcs)
        graf_ho = alt.Chart(ho_df).mark_bar().encode(
            x=alt.X("Elszámolási hónap:N", sort=ho_sorrend, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")),
            y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely),
            color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"),
            xOffset="Felhasználási hely:N",
            tooltip=[alt.Tooltip("Elszámolási hónap:N", title="Elszámolási hónap"), alt.Tooltip("Felhasználási hely:N", title="Felhasználási hely"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]
        ).properties(height=360)
        st.altair_chart(graf_ho, use_container_width=True)
    with tab_hely:
        hely_df = df.groupby("Felhasználási hely", as_index=False)["Bruttó összeg (Ft)"].sum()
        hely_df["Összeg"] = hely_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        graf_hely = alt.Chart(hely_df).mark_bar(color="#0068c9").encode(
            x=alt.X("Felhasználási hely:N", axis=alt.Axis(labelAngle=0, title="Felhasználási hely")),
            y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely),
            tooltip=[alt.Tooltip("Felhasználási hely:N", title="Felhasználási hely"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]
        ).properties(height=360)
        st.altair_chart(graf_hely, use_container_width=True)
    with tab_tetel:
        szamla_map = {s["id"]: s for s in szurt_lista}
        osszes_tetel = supabase.table("szamla_tetelek").select("*").in_("szamla_id", list(szamla_map.keys())).execute().data or list()
        if not osszes_tetel:
            st.info("A kiválasztott számlákhoz nem tartozik rögzített díjtétel.")
        else:
            t_sorok = list()
            for t in osszes_tetel:
                sz = szamla_map.get(t["szamla_id"], {})
                t_sorok.append({"Elszámolási hónap": tiszta_honap(sz.get("elszamolasi_honap")), "Felhasználási hely": h_map.get(sz.get("felhasznalasi_hely_id"), ""), "Számlaszám": sz.get("szamlaszam", ""), "Díjtétel": t["megnevezes_eredeti"], "Mennyiség": float(t["mennyiseg"]), "Mértékegység": t["mertekegyseg"], "Nettó egységár (Ft)": float(t["egysegar_netto"]), "Nettó összeg (Ft)": int(round(float(t["szamitott_netto"]))), "Bruttó összeg (Ft)": int(round(float(t["szamitott_brutto"])))})
            t_df = pd.DataFrame(t_sorok)
            tetel_nevek = sorted(list(t_df["Díjtétel"].unique()))
            val_tetel = st.selectbox("🔍 Díjtétel kiválasztása a havi mennyiség és egységár összehasonlításához:", tetel_nevek)
            szurt_t_df = t_df[t_df["Díjtétel"] == val_tetel].copy()
            ho_sorrend_t = sorted(list(szurt_t_df["Elszámolási hónap"].unique()), key=honap_kulcs)
            tc1, tc2 = st.columns(2)
            with tc1:
                st.caption(f"📊 Mennyiség alakulása – {val_tetel}")
                graf_menny = alt.Chart(szurt_t_df).mark_bar().encode(
                    x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")),
                    y=alt.Y("sum(Mennyiség):Q", axis=alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ')", title="Mennyiség")),
                    color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"),
                    xOffset="Felhasználási hely:N",
                    tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("sum(Mennyiség):Q", title="Mennyiség", format=",.0f"), alt.Tooltip("Mértékegység:N")]
                ).properties(height=300)
                st.altair_chart(graf_menny, use_container_width=True)
            with tc2:
                st.caption(f"💰 Nettó egységár (Ft) alakulása – {val_tetel}")
                graf_ar = alt.Chart(szurt_t_df).mark_line(point=True).encode(
                    x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")),
                    y=alt.Y("mean(Nettó egységár (Ft)):Q", axis=alt.Axis(title="Nettó egységár (Ft)")),
                    color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"),
                    tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("mean(Nettó egységár (Ft)):Q", title="Egységár (Ft)", format=",.4f")]
                ).properties(height=300)
                st.altair_chart(graf_ar, use_container_width=True)
            st.dataframe(t_df, use_container_width=True, hide_index=True)
            t_csv = t_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button("📥 Díjtételek letöltése Excelbe (CSV)", data=t_csv, file_name=f"dijtetelek_riport_{date.today()}.csv", mime="text/csv")

    st.caption("💡 Kattints egy sor elejére a táblázatban a csatolt PDF számla megnyitásához!")
    esemeny = st.dataframe(df, use_container_width=True, on_select="rerun", selection_mode="single-row")
    if esemeny.selection.rows:
        kiv_idx = next(iter(esemeny.selection.rows))
        kiv_szamla = szurt_lista[kiv_idx]
        if kiv_szamla.get("pdf_utvonal"):
            mutat_pdf_ablak(kiv_szamla["pdf_utvonal"], kiv_szamla["szamlaszam"])
        else:
            st.warning("Ehhez a számlához nem lett PDF fájl feltöltve.")

    csv = df.to_csv(index=False).encode("utf-8-sig")
    st.download_button("📥 Szűrt kimutatás letöltése Excelbe (CSV)", data=csv, file_name=f"energia_riport_{date.today()}.csv", mime="text/csv")

    st.divider()
    st.subheader("🗑️ Számla törlése")
    torles_opciok = {f"#{s['id']} – {s['szamlaszam']} ({tiszta_honap(s.get('elszamolasi_honap'))}, {h_map.get(s['felhasznalasi_hely_id'], '')}, {ft_szoveg(s['szamlan_szereplo_brutto'])})": s for s in szurt_lista}
    valasztott_torles = st.selectbox("Válaszd ki a törlendő számlát:", list(torles_opciok.keys()))
    if st.button("🗑️ Kiválasztott számla végleges törlése"):
        kiv = torles_opciok[valasztott_torles]
        supabase.table("szamla_tetelek").delete().eq("szamla_id", kiv["id"]).execute()
        if kiv.get("pdf_utvonal"):
            supabase.storage.from_("szamlak-pdf").remove(list((kiv["pdf_utvonal"],)))
        supabase.table("szamlak").delete().eq("id", kiv["id"]).execute()
        st.success(f"✅ A(z) {kiv['szamlaszam']} számú számla sikeresen törölve lett!")
        st.rerun()
    st.stop()

fv = st.session_state["form_ver"]
col_fej, col_gomb = st.columns((4, 1))
col_fej.header("Energiaszámla ellenőrzése és rögzítése")
if col_gomb.button("🗑️ Új számla / Ürítés"):
    urit_urlap()
    st.rerun()

if "utolso_mentes" in st.session_state:
