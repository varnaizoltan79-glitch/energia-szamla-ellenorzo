import base64
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
import altair as alt
import fitz
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
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
    szov = st.session_state.get(kulcs, "")
    neg = "-" in szov
    nyers = re.sub(r"\D", "", szov)
    if nyers:
        formazott = f"{int(nyers):,}".replace(",", " ")
        st.session_state[kulcs] = f"-{formazott}" if neg else formazott
    else:
        st.session_state[kulcs] = "-" if neg else ""


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

st.markdown(
    """
    <style>
    div[role="dialog"] {
        width: 950px !important;
        max-width: 92vw !important;
    }
    div[role="dialog"] [data-testid="stImage"] img {
        width: 100% !important;
        max-width: 100% !important;
    }
    </style>
    """,
    unsafe_allow_html=True
)

@st.dialog("📄 Számla PDF előnézet", width="large")
def mutat_pdf_ablak(pdf_utvonal, szamlaszam):
    st.subheader(f"Számlaszám: {szamlaszam}")
    pdf_bytes = supabase.storage.from_("szamlak-pdf").download(pdf_utvonal)
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    for idx, oldal in enumerate(dok):
        kep = oldal.get_pixmap(dpi=160)
        st.image(kep.tobytes("png"), caption=f"{idx + 1}. oldal", use_container_width=True)
    st.download_button("📥 PDF letöltése a gépre", data=pdf_bytes, file_name=pdf_utvonal, mime="application/pdf")




def _hu_szam(szoveg):
    if not szoveg:
        return None
    tiszta = szoveg.replace("\xa0", " ").replace("\u2212", "-").replace("–", "-").strip()
    tiszta = re.sub(r"^-\s+", "-", tiszta)
    tiszta = re.sub(r"(?<=\d)\s+(?=\d)", "", tiszta)
    if "," in tiszta and "." in tiszta:
        tiszta = tiszta.replace(".", "").replace(",", ".")
    elif "," in tiszta:
        tiszta = tiszta.replace(",", ".")
    elif re.search(r"(?<=\d)\.(?=\d{3}(?!\d))", tiszta):
        tiszta = re.sub(r"(?<=\d)\.(?=\d{3}(?!\d))", "", tiszta)
    try:
        return float(tiszta)
    except ValueError:
        return None

SZOLGALTATO_SABLONOK = {
    "ALTEO": {
        "azonositok": ("alteo energiakereskedő", "17783893"),
        "szolgaltato_nev_kulcs": "ALTEO",
    },
    "MVM": {
        "azonositok": ("mvm next", "26713111", "mvmenergiakereskedo"),
        "szolgaltato_nev_kulcs": "MVM",
    },
    "VIZMU": {
        "azonositok": ("debreceni vízmű", "23458208", "tiszamenti regionális vízművek", "11265832", "trvzrt.hu", "érv. zrt", "érv zrt", "11069186", "ervzrt.hu", "nyírségvíz", "11492809", "nyirsegviz.hu", "drv zrt", "dunántúli regionális vízmű", "11226002", "drv.hu"),
        "szolgaltato_nev_kulcs": "vízmű",
    },
    "TAVHO": {
        "azonositok": ("debreceni hőszolgáltató", "23506088", "dhrt.hu", "távhőszolgáltatási számla", "nyírtávhő", "nyíregyházi távhőszolgáltató", "11241256", "nyirtavho.hu", "távhőszolgáltatási részszámla"),
        "szolgaltato_nev_kulcs": "távhő",
    }
}


def _vizszintes_sorok(oldal):
    szavak = oldal.get_text("words")
    if not szavak:
        return list()
    rendezett = sorted(szavak, key=lambda w: (round(w[1] / 3.0), w[0]))
    sorok = list()
    _, akt_y, *_ = next(iter(rendezett))
    akt_szavak = list()
    for w in rendezett:
        x0, y0, x1, _, szo, *_ = w
        if abs(y0 - akt_y) <= 3.5:
            akt_szavak.append((x0, x1, szo))
        else:
            akt_szavak.sort(key=lambda item: item[0])
            sor_reszek = list()
            elozo_x1 = None
            for sx0, sx1, sszo in akt_szavak:
                if elozo_x1 is not None:
                    sor_reszek.append("  " if (sx0 - elozo_x1) > 8.5 else " ")
                sor_reszek.append(sszo)
                elozo_x1 = sx1
            sorok.append("".join(sor_reszek))
            akt_y = y0
            akt_szavak = [(x0, x1, szo)]
    if akt_szavak:
        akt_szavak.sort(key=lambda item: item[0])
        sor_reszek = list()
        elozo_x1 = None
        for sx0, sx1, sszo in akt_szavak:
            if elozo_x1 is not None:
                sor_reszek.append("  " if (sx0 - elozo_x1) > 8.5 else " ")
            sor_reszek.append(sszo)
            elozo_x1 = sx1
        sorok.append("".join(sor_reszek))
    return sorok

def _feldolgoz_alteo_gaz_pdf(dok, szoveg, adat):
    adat["szolgaltato_kulcs"] = "ALTEO"
    m_pod = re.search(r"(?:POD\s*:?\s*)?(39N[A-Z0-9]{10,})", szoveg, re.I)
    if m_pod:
        adat["pod"] = m_pod.group(1).strip()

    oldal_sorok = list()
    for oldal in dok:
        o_szoveg = oldal.get_text("text") or ""
        if "számlarészletezés" in re.sub(r"\s+", "", o_szoveg[:350].lower()):
            continue
        oldal_sorok.extend(_vizszintes_sorok(oldal))

    m_sz_teljes = re.search(r"Számlaszám\s*:?\s*(\d{6,})", szoveg)
    if m_sz_teljes:
        adat["szamlaszam"] = m_sz_teljes.group(1)

    for s in oldal_sorok:
        m_sz = re.search(r"Számlaszám[:\s]+(\d{6,})", s)
        if m_sz:
            adat["szamlaszam"] = m_sz.group(1)
        m_ido = re.search(r"Időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", s)
        if m_ido:
            adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
            adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()
        m_k = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_k:
            adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()
        m_h = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_h:
            adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

        rendezett_szoveg = "\n".join(oldal_sorok)
    
        rendezett_szoveg = "\n".join(oldal_sorok)
    m_br = (
        re.search(r"Fizetendő\s*összesen\s*:?\s*([-−–]?\s*[\d\.\s\xa0]+(?:,\d{2})?)\s*Ft", rendezett_szoveg)
        or re.search(r"Fizetendő\s*összesen\s*:?\s*([-−–]?\s*[\d\.\s\xa0]+(?:,\d{2})?)\s*Ft", szoveg)
        or re.search(r"Bruttó\s*érték\s*([-−–]?\s*[\d\.\s\xa0]+(?:,\d{2})?)\s*Ft", szoveg)
        or re.search(r"Összesen\s*:?\s*[-−–]?\s*[\d\.\s\xa0]+,\d{2}\s+[-−–]?\s*[\d\.\s\xa0]+,\d{2}\s+([-−–]?\s*[\d\.\s\xa0]+,\d{2})", rendezett_szoveg)
    )
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val is not None:
            adat["brutto"] = int(round(br_val))


        szam_minta = r"-?\d[\d\.]*,\d+"
    egesz_vagy_tizedes = r"-?\d[\d\.]*(?:,\d+)?"
    me_minta = r"(kWh|MWh|csp|kW|kVAhr|db|hó|m3|MJ|GJ)"
    tetel_minta = re.compile(rf"^(.+?)\s+({szam_minta})\s+{me_minta}\s+({szam_minta})\s*Ft\s+({egesz_vagy_tizedes})\s+(27|5|18|0|AHK)\b")
    jovairo_minta = re.compile(rf"^(Részszámlákban\s+elszámolt\s+.+?)\s+({egesz_vagy_tizedes})\s+(27|5|18|0|AHK)\s+({szam_minta})\s+({szam_minta})\s*$")
    kizaro = ("összesen", "Tétel megnevezés", "Pénzeszközök", "Energia-jövedéki", "Rendszerhasználati", "Közbeszerzési díjak", "EKR díj", "Számlaszám", "Oldal:", "Forgalmi tételek")

    talalatok = list()
    for idx, s in enumerate(oldal_sorok):
        m = tetel_minta.match(s)
        mj = None if m else jovairo_minta.match(s)
        if not m and not mj:
            continue

        if m:
            nev = m.group(1).strip()
            menny = _hu_szam(m.group(2))
            me = m.group(3)
            ar = _hu_szam(m.group(4))
            afa_nyers = m.group(6)
        else:
            nev = mj.group(1).strip()
            netto_jovairo = _hu_szam(mj.group(2)) or 0.0
            afa_nyers = mj.group(3)
            nev_low = nev.lower()

            kapcsolodo = [
                t for t in talalatok
                if t["Mértékegység"] == "kWh" and t["Mennyiség"] > 0 and (
                    ("földgáz díj" in nev_low and "földgáz díj" in t["Megnevezés"].lower())
                    or ("forgalomarányos rhd" in nev_low and "forgalomarányos rhd" in t["Megnevezés"].lower() and "közbeszerzési" not in t["Megnevezés"].lower())
                    or ("különleges készlet" in nev_low and "különleges készlet" in t["Megnevezés"].lower())
                    or ("mszksz díj" in nev_low and "mszksz díj" in t["Megnevezés"].lower())
                    or ("jövedéki adó" in nev_low and "jövedéki adó" in t["Megnevezés"].lower())
                )
            ]

            if kapcsolodo and netto_jovairo != 0:
                ossz_menny_k = sum(t["Mennyiség"] for t in kapcsolodo)
                ossz_netto_k = sum(t["Mennyiség"] * t["Nettó egységár"] for t in kapcsolodo)
                sulyozott_ar = round(ossz_netto_k / ossz_menny_k, 5) if ossz_menny_k > 0 else kapcsolodo["Nettó egységár"]
                me = "kWh"
                ar = sulyozott_ar
                menny = round(netto_jovairo / ar, 2) if ar != 0 else 1.0
            else:
                menny = 1.0
                me = "Ft"
                ar = netto_jovairo

        afa = 0.0 if afa_nyers == "AHK" else float(afa_nyers)

        if idx + 1 < len(oldal_sorok):
            kov = oldal_sorok[idx + 1].strip()
            if not tetel_minta.match(kov) and not jovairo_minta.match(kov) and not any(kz.lower() in kov.lower() for kz in kizaro) and not re.search(r"szolgáltatást tartalmaz", kov, re.I):
                nev = f"{nev} {kov}".strip()

        if menny is not None and ar is not None:
            talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    if talalatok:
        adat["tetelek_df"] = pd.DataFrame(talalatok)
    return adat


def _feldolgoz_alteo_pdf(dok, szoveg, adat):
    if "földgáz" in szoveg.lower() or re.search(r"\b39N[A-Z0-9]{10,}\b", szoveg):
        return _feldolgoz_alteo_gaz_pdf(dok, szoveg, adat)
    adat["szolgaltato_kulcs"] = "ALTEO"

    elso_oldal = next(iter(dok))
    oldal1_sorok = _vizszintes_sorok(elso_oldal)

    m_sz_teljes = re.search(r"Számlaszám\s*:?\s*(\d{6,})", szoveg)
    if m_sz_teljes:
        adat["szamlaszam"] = m_sz_teljes.group(1)

    for s in oldal1_sorok:
        m_sz = re.search(r"Számlaszám[:\s]+(\d{6,})", s)
        if m_sz:
            adat["szamlaszam"] = m_sz.group(1)
        m_ido = re.search(r"Időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", s)
        if m_ido:
            adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
            adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()
        m_k = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_k:
            adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()
        m_h = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", s)
        if m_h:
            adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

    m_br = re.search(r"Fizetendő\s*összesen[:\s]+([\d\.\s\xa0]+,\d{2})\s*Ft", szoveg)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))

    szam_minta = r"-?\d[\d\.]*,\d+"
    me_minta = r"(kWh|MWh|csp|kW|kVAhr|db|hó|m3|MJ|GJ)"
    tetel_minta = re.compile(rf"^(.+?)\s+({szam_minta})\s+{me_minta}\s+({szam_minta})\s*Ft\s+({szam_minta}|-?\d[\d\.]*)\s+(27|5|18|0|AHK)\b")
    kizaro = ("összesen", "Tétel megnevezés", "Pénzeszközök", "Energia-jövedéki", "Rendszerhasználati", "Közbeszerzési díjak", "EKR díj", "Számlaszám", "Oldal:", "Forgalmi tételek")

    talalatok = list()
    for idx, s in enumerate(oldal1_sorok):
        m = tetel_minta.match(s)
        if not m:
            continue
        nev = m.group(1).strip()
        menny = _hu_szam(m.group(2))
        me = m.group(3)
        ar = _hu_szam(m.group(4))
        afa_nyers = m.group(6)
        afa = 0.0 if afa_nyers == "AHK" else float(afa_nyers)

        if idx + 1 < len(oldal1_sorok):
            kov = oldal1_sorok[idx + 1].strip()
            if not tetel_minta.match(kov) and not any(kz.lower() in kov.lower() for kz in kizaro) and not re.search(r"szolgáltatást tartalmaz", kov, re.I):
                nev = f"{nev} {kov}".strip()

        if menny is not None and ar is not None:
            talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    if talalatok:
        adat["tetelek_df"] = pd.DataFrame(talalatok)
    return adat

def _feldolgoz_mvm_gaz_pod_blokk(sorok, alap_tol, alap_ig, alap_brutto):
    blokk_szoveg = "\n".join(sorok)
    m_pod = re.search(r"(39N[A-Z0-9]{10,})", blokk_szoveg)
    pod = m_pod.group(1).strip() if m_pod else ""

    pod_tol, pod_ig = alap_tol, alap_ig
    m_ido = re.search(r"(\d{4}\.\d{2}\.\d{2})\s+(\d{4}\.\d{2}\.\d{2})", blokk_szoveg)
    if m_ido:
        pod_tol = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        pod_ig = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()

    szam_minta = r"-?\d{1,3}(?:[ .]\d{3})*(?:,\d+)?|-?\d+(?:,\d+)?"
    sor_re = re.compile(rf"(\d{{4}}\.\d{{2}}\.\d{{2}})\s+(\d{{4}}\.\d{{2}}\.\d{{2}})\s+({szam_minta})\s*(kWh/h|kWh|m3|m³|MJ|GJ|HUF|Ft|db|hó)\s+(.+?)\s+({szam_minta})\s+(\d{{1,2}})\b")

    talalatok = list()
    elozo_nev = ""
    for s in sorok:
        s_tisztitott = re.sub(r"kWh\s*/\s*h", "KWHPERH", s.strip())
        s_tisztitott = re.sub(r"(?<=[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű%])(?=-?\d)", " ", s_tisztitott)
        s_tisztitott = re.sub(r"(?<=\d)(?=[A-Za-zÁÉÍÓÖŐÚÜŰáéíóöőúüű%])", " ", s_tisztitott)
        s_tisztitott = s_tisztitott.replace("KWHPERH", "kWh/h")
        s_tisztitott = re.sub(r"\bm\s+3\b", "m3", s_tisztitott)
        m = sor_re.search(s_tisztitott)
        if not m:
            if not re.search(r"\d", s_tisztitott) and 4 <= len(s_tisztitott) <= 60 and "összesen" not in s_tisztitott.lower() and "díjtétel" not in s_tisztitott.lower():
                elozo_nev = s_tisztitott.strip(" -–:|")
            continue

        bal_nev = s_tisztitott[:m.start()].strip(" -–:|")
        menny = _hu_szam(m.group(3))
        me_nyers = m.group(4)
        kozepso = m.group(5)
        netto_oszlop = _hu_szam(m.group(6))
        afa = float(m.group(7))

        alap_cimke = "RHD kapacitásdíj*" if me_nyers == "kWh/h" else ("Földgáz díjtétel" if me_nyers == "kWh" else "Földgáz díjtétel")
        nev = bal_nev or elozo_nev or alap_cimke
        elozo_nev = ""

        kozep_szamok = re.findall(szam_minta, kozepso)
        ar = _hu_szam(next(iter(kozep_szamok))) if kozep_szamok else None

        if menny is None or ar is None:
            continue

        if "%" in kozepso or me_nyers in ("HUF", "Ft"):
            me = "%"
            ar = round(ar / 100.0, 6)
        else:
            me = me_nyers.replace("m³", "m3")
            if netto_oszlop is not None and menny != 0 and abs((menny * ar) - netto_oszlop) > 5.0:
                ar = round(netto_oszlop / menny, 6)

        talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    tetelek_df = pd.DataFrame(talalatok) if talalatok else None
    pod_brutto = alap_brutto
    if pod_brutto == 0 and tetelek_df is not None:
        _, szam_br, _ = szamol_tetelek(tetelek_df)
        pod_brutto = int(szam_br)

    return {"pod": pod, "tol": pod_tol, "ig": pod_ig, "brutto": pod_brutto, "tetelek_df": tetelek_df}





def _feldolgoz_mvm_gaz_pdf(dok, szoveg, adat):
    adat["szolgaltato_kulcs"] = "MVM"
    osszes_sor = list()
    for oldal in dok:
        szavak = oldal.get_text("words")
        if not szavak:
            continue
        rendezett = sorted(szavak, key=_rendez_tavho_szo)
        _, akt_y, *_ = next(iter(rendezett))
        akt_sz = list()
        for x0, y0, _, _, szo, *_ in rendezett:
            if abs(y0 - akt_y) <= 6.5:
                akt_sz.append((x0, szo))
            else:
                akt_sz.sort(key=_rendez_x_szo)
                osszes_sor.append(" ".join(ss for _, ss in akt_sz))
                akt_y = y0
                akt_sz = [(x0, szo)]
        if akt_sz:
            akt_sz.sort(key=_rendez_x_szo)
            osszes_sor.append(" ".join(ss for _, ss in akt_sz))

    rendezett_szoveg = "\n".join(osszes_sor)

    m_sz = re.search(r"Számla\s*száma\s*:?\s*([A-Z0-9/-]{6,})", rendezett_szoveg, re.I)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_k = re.search(r"Számla\s*kelte\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_k:
        adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()

    m_h = re.search(r"Fizetési\s*határidő\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_h:
        adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

    m_br = re.search(r"Fizetendő\s*:?\s*([\d\.\s\xa0]+(?:,\d+)?)\s*Ft", rendezett_szoveg, re.I)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))
            adat["foszamla_brutto"] = adat["brutto"]

    pod_indulo_sorok = list()
    for idx, s in enumerate(osszes_sor):
        if re.search(r"39N[A-Z0-9]{10,}", s):
            pod_indulo_sorok.append(idx)

    if len(pod_indulo_sorok) > 1:
        pod_adatok = dict()
        for i, start_idx in enumerate(pod_indulo_sorok):
            end_idx = pod_indulo_sorok[i + 1] if (i + 1 < len(pod_indulo_sorok)) else len(osszes_sor)
            resz = _feldolgoz_mvm_gaz_pod_blokk(osszes_sor[start_idx:end_idx], adat["tol"], adat["ig"], 0)
            if resz["pod"] and resz["tetelek_df"] is not None:
                pod_adatok[resz["pod"]] = resz
        if pod_adatok:
            adat["pod_adatok"] = pod_adatok
            elso_pod = next(iter(pod_adatok.values()))
            adat["pod"] = elso_pod["pod"]
            adat["brutto"] = elso_pod["brutto"]
            adat["tol"] = elso_pod["tol"]
            adat["ig"] = elso_pod["ig"]
            adat["tetelek_df"] = elso_pod["tetelek_df"]
    else:
        resz_sorok = osszes_sor[pod_indulo_sorok[0]:] if pod_indulo_sorok else osszes_sor
        resz = _feldolgoz_mvm_gaz_pod_blokk(resz_sorok, adat["tol"], adat["ig"], adat["brutto"])
        if resz["pod"]:
            adat["pod"] = resz["pod"]
        if resz["tol"]:
            adat["tol"] = resz["tol"]
        if resz["ig"]:
            adat["ig"] = resz["ig"]
        if resz["tetelek_df"] is not None:
            adat["tetelek_df"] = resz["tetelek_df"]

    return adat



def _feldolgoz_mvm_gaz_pdf(dok, szoveg, adat):
    adat["szolgaltato_kulcs"] = "MVM"
    osszes_sor = list()
    for oldal in dok:
        osszes_sor.extend(_vizszintes_sorok(oldal))
    rendezett_szoveg = "\n".join(osszes_sor)

    m_sz = re.search(r"Számla\s*száma\s*:?\s*([A-Z0-9/-]{6,})", rendezett_szoveg, re.I)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_k = re.search(r"Számla\s*kelte\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_k:
        adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()

    m_h = re.search(r"Fizetési\s*határidő\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_h:
        adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

    m_br = re.search(r"Fizetendő\s*:?\s*([\d\.\s\xa0]+(?:,\d+)?)\s*Ft", rendezett_szoveg, re.I)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))
            adat["foszamla_brutto"] = adat["brutto"]

    pod_indulo_sorok = list()
    for idx, s in enumerate(osszes_sor):
        if re.search(r"39N[A-Z0-9]{10,}", s):
            pod_indulo_sorok.append(idx)

    if len(pod_indulo_sorok) > 1:
        pod_adatok = dict()
        for i, start_idx in enumerate(pod_indulo_sorok):
            end_idx = pod_indulo_sorok[i + 1] if (i + 1 < len(pod_indulo_sorok)) else len(osszes_sor)
            resz = _feldolgoz_mvm_gaz_pod_blokk(osszes_sor[start_idx:end_idx], adat["tol"], adat["ig"], 0)
            if resz["pod"] and resz["tetelek_df"] is not None:
                pod_adatok[resz["pod"]] = resz
        if pod_adatok:
            adat["pod_adatok"] = pod_adatok
            elso_pod = next(iter(pod_adatok.values()))
            adat["pod"] = elso_pod["pod"]
            adat["brutto"] = elso_pod["brutto"]
            adat["tol"] = elso_pod["tol"]
            adat["ig"] = elso_pod["ig"]
            adat["tetelek_df"] = elso_pod["tetelek_df"]
    else:
        resz_sorok = osszes_sor[pod_indulo_sorok[0]:] if pod_indulo_sorok else osszes_sor
        resz = _feldolgoz_mvm_gaz_pod_blokk(resz_sorok, adat["tol"], adat["ig"], adat["brutto"])
        if resz["pod"]:
            adat["pod"] = resz["pod"]
        if resz["tol"]:
            adat["tol"] = resz["tol"]
        if resz["ig"]:
            adat["ig"] = resz["ig"]
        if resz["tetelek_df"] is not None:
            adat["tetelek_df"] = resz["tetelek_df"]

    return adat

    

def _feldolgoz_mvm_pod_blokk(sorok, alap_tol, alap_ig, alap_brutto):
    blokk_szoveg = "\n".join(sorok)
    m_pod = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", blokk_szoveg)
    pod = m_pod.group(1).strip() if m_pod else ""

    pod_tol, pod_ig = alap_tol, alap_ig
    m_ido = re.search(r"Elszámolt\s*időszak\s*:?\s*(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", blokk_szoveg, re.I)
    if m_ido:
        pod_tol = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        pod_ig = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()

    szam_minta = r"-?\d{1,3}(?:[ .]\d{3})*(?:,\d+)?|-?\d+(?:,\d+)?"
    sor_re = re.compile(rf"^(.+?)\s+(\d{{4}}\.\d{{2}}\.\d{{2}})\s*[-–]\s*(\d{{4}}\.\d{{2}}\.\d{{2}})\s*({szam_minta})\s+({szam_minta})(?:\s+([A-ZÁÉÍÓÖŐÚÜŰ0-9/-]+))?\s+({szam_minta})\s*$")

    kizaro_eleje = ("összesen", "kereskedelmi díjak", "pénzeszközök", "közvetített szolgáltatás", "megnevezés", "áfa szerinti bontás", "mérő gyári", "mért időszak", "szerződésszám", "mérési pont", "fogyasztási hely", "telephelyre vonatkozó", "oldalak száma")

    akt_me = "kWh"
    akt_afa = 27.0
    talalatok = list()
    pod_brutto = 0

    for idx, s in enumerate(sorok):
        s_low = s.lower().strip()
        if "kereskedelmi díjak" in s_low:
            akt_me = "kWh"
            akt_afa = 27.0
            continue
        elif "pénzeszközök" in s_low or "adóalapon kívüli" in s_low:
            akt_me = "kWh"
            akt_afa = 0.0
            continue
        elif "mennyiség (kvarh)" in s_low:
            akt_me = "kVArh"
            akt_afa = 27.0
            continue
        elif "mennyiség (csatl" in s_low or "csatl.pont/db" in s_low:
            akt_me = "db"
            akt_afa = 27.0
            continue
        elif "mennyiség (kw)" in s_low:
            akt_me = "kW"
            akt_afa = 27.0
            continue
        elif "ellenérték alap" in s_low and "mérték" in s_low:
            akt_me = "%"
            akt_afa = 27.0
            continue
        elif "mennyiség (kwh)" in s_low or "közvetített szolgáltatás" in s_low:
            akt_me = "kWh"
            akt_afa = 27.0
            continue

        if s_low.startswith("összesen") and idx > 0:
            kornyezet = " ".join(sorok[max(0, idx - 4):idx]).lower()
            if "áfa szerinti bontás" in kornyezet or "adóval növelt" in kornyezet:
                szamok = re.findall(r"-?\d{1,3}(?: \d{3})+|-?\d+", s)
                if szamok:
                    val_b = _hu_szam(szamok[-1])
                    if val_b is not None and val_b != 0:
                        pod_brutto = int(round(val_b))
            continue


        if any(s_low.startswith(kz) for kz in kizaro_eleje):
            continue

        s_tiszta = re.sub(r"(\d{4}\.\d{2}\.\d{2}\s*[-–]\s*\d{4}\.\d{2}\.\d{2})(?=\d)", r"\1 ", s.strip())
        m = sor_re.match(s_tiszta)
        if not m:
            continue

        nev = m.group(1).strip(" -–:|")
        menny = _hu_szam(m.group(4))
        ar = _hu_szam(m.group(5))
        if menny is None or ar is None:
            continue

        if akt_me == "%":
            ar = round(ar / 100.0, 6)

        talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": akt_me, "Nettó egységár": ar, "ÁFA (%)": akt_afa})

    tetelek_df = pd.DataFrame(talalatok) if talalatok else None
    if pod_brutto == 0 and tetelek_df is not None:
        _, szam_br, _ = szamol_tetelek(tetelek_df)
        pod_brutto = int(szam_br) if szam_br > 0 else alap_brutto

    return {"pod": pod, "tol": pod_tol, "ig": pod_ig, "brutto": pod_brutto, "tetelek_df": tetelek_df}


def _feldolgoz_mvm_pdf(dok, szoveg, adat):
    if "földgáz" in szoveg.lower() or re.search(r"\b39N[A-Z0-9]{10,}\b", szoveg):
        return _feldolgoz_mvm_gaz_pdf(dok, szoveg, adat)
    adat["szolgaltato_kulcs"] = "MVM"
    osszes_sor = list()
    for oldal in dok:
        osszes_sor.extend(_vizszintes_sorok(oldal))
    rendezett_szoveg = "\n".join(osszes_sor)

    m_sz = re.search(r"Számla\s*száma\s*:?\s*([A-Z0-9/-]{6,})", rendezett_szoveg, re.I)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_ido = re.search(r"Áramdíj\s*és\s*egyéb\s*díjak\s*:?\s*(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if not m_ido:
        m_ido = re.search(r"(?:Elszámolási|Elszámolt|Teljesítési)\s*időszak(?:ok)?\s*:?\s*(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_ido:
        adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()

    m_k = re.search(r"Számla\s*kelte\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_k:
        adat["kelte"] = datetime.strptime(m_k.group(1), "%Y.%m.%d").date()

    m_h = re.search(r"Fizetési\s*határidő\s*:?\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if m_h:
        adat["hatarido"] = datetime.strptime(m_h.group(1), "%Y.%m.%d").date()

        m_br = re.search(r"Fizetendő\s*:?\s*(-?[\d\.\s\xa0]+(?:,\d+)?)\s*Ft", rendezett_szoveg, re.I)
    if m_br:
        br_val = _hu_szam(m_br.group(1))
        if br_val is not None and br_val != 0:
            adat["brutto"] = int(round(br_val))
            adat["foszamla_brutto"] = adat["brutto"]
    else:
        m_veg = re.search(r"Számla\s*végösszege\s+(-?[\d\.\s\xa0]+)\s+(-?[\d\.\s\xa0]+)\s+(-?[\d\.\s\xa0]+)", rendezett_szoveg, re.I)
        if m_veg:
            br_val = _hu_szam(m_veg.group(3))
            if br_val is not None and br_val != 0:
                adat["brutto"] = int(round(br_val))
                adat["foszamla_brutto"] = adat["brutto"]


    pod_indulo_sorok = list()
    for idx, s in enumerate(osszes_sor):
        if re.search(r"HU\d{6}[-A-Z0-9]{10,}", s):
            pod_indulo_sorok.append(idx)

    if len(pod_indulo_sorok) > 1:
        pod_adatok = dict()
        for i, start_idx in enumerate(pod_indulo_sorok):
            end_idx = pod_indulo_sorok[i + 1] if (i + 1 < len(pod_indulo_sorok)) else len(osszes_sor)
            resz = _feldolgoz_mvm_pod_blokk(osszes_sor[start_idx:end_idx], adat["tol"], adat["ig"], 0)
            if resz["pod"] and resz["tetelek_df"] is not None:
                pod_adatok[resz["pod"]] = resz
        if pod_adatok:
            adat["pod_adatok"] = pod_adatok
            elso_pod = next(iter(pod_adatok.values()))
            adat["pod"] = elso_pod["pod"]
            adat["brutto"] = elso_pod["brutto"]
            adat["tol"] = elso_pod["tol"]
            adat["ig"] = elso_pod["ig"]
            adat["tetelek_df"] = elso_pod["tetelek_df"]
    else:
        resz_sorok = osszes_sor[pod_indulo_sorok[0]:] if pod_indulo_sorok else osszes_sor
        resz = _feldolgoz_mvm_pod_blokk(resz_sorok, adat["tol"], adat["ig"], adat["brutto"])
        if resz["pod"]:
            adat["pod"] = resz["pod"]
        if resz["tetelek_df"] is not None:
            adat["tetelek_df"] = resz["tetelek_df"]
        if resz["brutto"] > 0 and adat["brutto"] == 0:
            adat["brutto"] = resz["brutto"]

    return adat






def _rendez_yx_szo(elem):
    x0, y0, _ = elem
    return (round(y0 / 3.0), x0)

def _rendez_x_szo(elem):
    x0, _ = elem
    return x0

def _rendez_sor_x_szo(elem):
    sor_y, x0, _ = elem
    return (sor_y, x0)

def _feldolgoz_vizmu_pdf(dok, szoveg, adat):
    szoveg_low = szoveg.lower()
    if "tiszamenti" in szoveg_low or "11265832" in szoveg_low or "trvzrt" in szoveg_low:
        adat["szolgaltato_kulcs"] = "tiszamenti"
    elif "érv. zrt" in szoveg_low or "érv zrt" in szoveg_low or "északmagyarországi regionális" in szoveg_low or "11069186" in szoveg_low or "ervzrt" in szoveg_low:
        adat["szolgaltato_kulcs"] = "érv"
    elif "nyírségvíz" in szoveg_low or "11492809" in szoveg_low or "nyirsegviz" in szoveg_low:
        adat["szolgaltato_kulcs"] = "nyírségvíz"
    elif "drv zrt" in szoveg_low or "dunántúli regionális" in szoveg_low or "11226002" in szoveg_low or "drv.hu" in szoveg_low:
        adat["szolgaltato_kulcs"] = "drv"
    else:
        adat["szolgaltato_kulcs"] = "debreceni vízmű"

    osszes_sor = list()
    for oldal in dok:
        osszes_sor.extend(_vizszintes_sorok(oldal))
    rendezett_szoveg = "\n".join(osszes_sor)

    m_sz = re.search(r"Számla\s*sorszáma\s*:?\s*([A-Z0-9/-]{6,})", rendezett_szoveg, re.I)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_az = re.search(r"(?:Felhasználási\s*hely\s*azonosító|Azonosító)\s*:?\s*([0-9A-Z-]{6,})", rendezett_szoveg, re.I)
    if m_az:
        adat["pod"] = m_az.group(1).strip()

    m_ido = re.search(r"Elszámol(?:ási|t)\s*időszak\s*:?\s*(\d{4}\.\d{2}\.\d{2})\s*[-–.]+\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    if not m_ido:
        m_ido = re.search(r"(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg)
    if m_ido:
        adat["tol"] = datetime.strptime(m_ido.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(m_ido.group(2), "%Y.%m.%d").date()


    m_k = re.search(r"Számla\s*kelte\s*:?\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})", rendezett_szoveg, re.I)
    if m_k:
        adat["kelte"] = date(int(m_k.group(1)), int(m_k.group(2)), int(m_k.group(3)))

    m_h = re.search(r"Fizetési\s*határidő\s*:?\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})", rendezett_szoveg, re.I)
    if m_h:
        adat["hatarido"] = date(int(m_h.group(1)), int(m_h.group(2)), int(m_h.group(3)))

    for s in osszes_sor:
        m_br = re.search(r"Fizetendő\s*összeg\s*:?\s*(-?[\d\.\s\xa0]+(?:,\d+)?)\s*Ft\b", s, re.I)
        if m_br:
            br_val = _hu_szam(m_br.group(1))
            if br_val is not None and br_val != 0:
                adat["brutto"] = int(round(br_val))
                break

    egyes_szam = r"-?\d{1,3}(?:[ .]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?"
    me_minta = r"(m3|m³|hó|db|lm3)"
    tetel_minta = re.compile(rf"({egyes_szam})\s*{me_minta}\s+({egyes_szam})\s*Ft(?:/[A-Za-z0-9³]+)?(?:\s+({egyes_szam}))?(?:\s+(27|5|18|0|AHK)\b)?")

    talalatok = list()
    elozo_nev = ""
    for s in osszes_sor:
        s_low = s.lower().strip()
        if "bruttó számlaérték összesen" in s_low or "áfa összesítő" in s_low:
            break

        if (
            s_low.startswith("fizetendő")
            or s_low.startswith("kerekítés")
            or "összesen" in s_low
            or "tétel megnevezése" in s_low
            or "számlarészletező" in s_low
            or re.match(r"^\d+\s*/\s*\d+\.?\s*oldal", s_low)
        ):
            elozo_nev = ""
            continue

        m = tetel_minta.search(s)
        if not m:
            tiszta_sor = re.sub(r"\d{4}\.\d{2}\.\d{2}\s*[-–]?", "", s)
            tiszta_sor = re.sub(r"\b\d+\s*-\s*\d+\b", "", tiszta_sor).strip(" -–:|")
            if tiszta_sor and len(tiszta_sor) >= 5 and any(k in tiszta_sor.lower() for k in ("vízmérő", "részszámlában", "ivóvíz", "szennyvíz", "vízterhelési", "alapdíj", "díj")):
                elozo_nev = f"{elozo_nev} {tiszta_sor}".strip()
            continue

        bal_resz = s[:m.start()]
        bal_resz = re.sub(r"\d{4}\.\d{2}\.\d{2}\s*[-–]\s*\d{4}\.\d{2}\.\d{2}", "", bal_resz)
        bal_resz = re.sub(r"\b\d+\s*-\s*\d+\b", "", bal_resz).strip(" -–:|")
        nev = f"{elozo_nev} {bal_resz}".strip() or "Víziközmű díjtétel"
        elozo_nev = ""

        menny = _hu_szam(m.group(1))
        me = m.group(2).replace("m³", "m3")
        ar = _hu_szam(m.group(3))
        afa_nyers = m.group(5)
        afa = 27.0 if not afa_nyers else (0.0 if afa_nyers == "AHK" else float(afa_nyers))

        if menny is not None and ar is not None:
            talalatok.append({"Megnevezés": nev, "Mennyiség": menny, "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    if talalatok:
        adat["tetelek_df"] = pd.DataFrame(talalatok)
    return adat


def _rendez_tavho_szo(w):
    x0, y0, *_ = w
    return (y0, x0)

def _feldolgoz_tavho_pdf(dok, szoveg, adat):
    szoveg_low = szoveg.lower()
    if "nyírtávhő" in szoveg_low or "nyíregyházi távhő" in szoveg_low or "11241256" in szoveg_low or "nyirtavho" in szoveg_low:
        adat["szolgaltato_kulcs"] = "nyírtávhő"
    else:
        adat["szolgaltato_kulcs"] = "debreceni hőszolgáltató"

    osszes_sor = list()
    for oldal in dok:
        osszes_sor.extend(_vizszintes_sorok(oldal))
    rendezett_szoveg = "\n".join(osszes_sor)

    m_sz = re.search(r"Számla\s*sorszáma\s*:?\s*([A-Z0-9/-]{5,})", rendezett_szoveg, re.I)
    if m_sz:
        adat["szamlaszam"] = m_sz.group(1).strip()

    m_az_nyir = re.search(r"Felhasználó\s*azonosító\s*száma\s*:?\s*([A-Z0-9]{4,})\b", rendezett_szoveg, re.I)
    m_az_dhrt = re.search(r"(U\d{5,}\s*-\s*F\d{4,})", szoveg, re.I)
    m_hk = re.search(r"Hőközpont\s*azonosító\s*:?\s*([A-Z0-9:/-]+)", rendezett_szoveg, re.I)
    if m_az_dhrt:
        adat["pod"] = re.sub(r"\s*-\s*", " - ", m_az_dhrt.group(1).strip()).upper()
    elif m_az_nyir:
        adat["pod"] = m_az_nyir.group(1).strip()
    elif m_hk:
        adat["pod"] = m_hk.group(1).strip()

    m_ido_teljes = re.search(r"(?:Elszámolási|Elszámolt)\s*időszak\s*:?\s*(\d{4}\.\d{2}\.\d{2})\s*[-–.]+\s*(\d{4}\.\d{2}\.\d{2})", rendezett_szoveg, re.I)
    m_ido_rovid = re.search(r"(?:Elszámolási|Elszámolt)\s*időszak\s*:?\s*(\d{2})\.(\d{2})\.(\d{2})\s*[-–.]+\s*(\d{2})\.(\d{2})\.(\d{2})", rendezett_szoveg, re.I)
    m_ido_ho = re.search(r"(?:Elszámolási|Elszámolt)\s*időszak\s*:?\s*(\d{4})\.(\d{2})(?!\.\d)", rendezett_szoveg, re.I)
    if m_ido_teljes:
        adat["tol"] = datetime.strptime(m_ido_teljes.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(m_ido_teljes.group(2), "%Y.%m.%d").date()
    elif m_ido_rovid:
        adat["tol"] = date(2000 + int(m_ido_rovid.group(1)), int(m_ido_rovid.group(2)), int(m_ido_rovid.group(3)))
        adat["ig"] = date(2000 + int(m_ido_rovid.group(4)), int(m_ido_rovid.group(5)), int(m_ido_rovid.group(6)))
    elif m_ido_ho:
        ev_h, ho_h = int(m_ido_ho.group(1)), int(m_ido_ho.group(2))
        utolso_nap = 31 if ho_h in (1, 3, 5, 7, 8, 10, 12) else (30 if ho_h in (4, 6, 9, 11) else (29 if ev_h % 4 == 0 else 28))
        adat["tol"] = date(ev_h, ho_h, 1)
        adat["ig"] = date(ev_h, ho_h, utolso_nap)

    m_k = re.search(r"Számla\s*kelte\s*:?\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})", rendezett_szoveg, re.I)
    if m_k:
        adat["kelte"] = date(int(m_k.group(1)), int(m_k.group(2)), int(m_k.group(3)))

    m_h = re.search(r"Fizetési\s*határidő\s*:?\s*(\d{4})\.\s*(\d{2})\.\s*(\d{2})", rendezett_szoveg, re.I)
    if m_h:
        adat["hatarido"] = date(int(m_h.group(1)), int(m_h.group(2)), int(m_h.group(3)))


    for s in osszes_sor:
        m_br = re.search(r"Fizetendő\s*összeg(?:\s*összesen\s*\(Ft\))?\s*:?\s*([\d\.\s\xa0]+(?:,\d+)?)\s*(?:Ft)?\b", s, re.I)
        if m_br:
            br_val = _hu_szam(m_br.group(1))
            if br_val and br_val > 0:
                adat["brutto"] = int(round(br_val))
                break

    egyes_szam = r"-?\d{1,3}(?:[ .]\d{3})+(?:[.,]\d+)?|-?\d+(?:[.,]\d+)?"
    szorzo_szam = r"\d+,\d{4}"
    me_minta = r"(kW|MW|GJ|db|lm3|m3|m³|kWh|MWh|hó)"
    tetel_minta = re.compile(rf"(?:({szorzo_szam})\s+)?({egyes_szam})\s*{me_minta}\s*({egyes_szam})\s*Ft(?:/[A-Za-z0-9³]+)?(?:\s+({egyes_szam}))?(?:\s+(27|5|18|0|AHK)\b)?")

    talalatok = list()
    kategoria = ""
    elozo_nev = ""
    for s in osszes_sor:
        s_low = s.lower()
        if "fűtési célú költségek" in s_low:
            kategoria = "Fűtési célú - "
            elozo_nev = ""
            continue
        elif "vízfelmelegítési célú költségek" in s_low:
            kategoria = "Vízfelmelegítési - "
            elozo_nev = ""
            continue
        elif "viziközmű-szolgáltatási díjak" in s_low or "víziközmű-szolgáltatási díjak" in s_low:
            kategoria = "Víziközmű - "
            elozo_nev = ""
            continue
        elif "bruttó számlaérték" in s_low or "számla áfa összesítője" in s_low or "hőközponti és épület" in s_low:
            break

        if s_low.startswith("fizetendő ") or "összesen" in s_low or "tétel megnevezése" in s_low:
            continue

        m = tetel_minta.search(s)
        if not m:
            tiszta_sor = re.sub(r"\d{4}\.\d{2}(?:\.\d{2})?(?:\s*[-–]?\s*\d{4}\.\d{2}\.\d{2}|\s*hó)?", "", s).strip(" -–:|")
            if tiszta_sor and not re.search(r"\d", tiszta_sor) and len(tiszta_sor) >= 4:
                elozo_nev = tiszta_sor
            continue

        bal_resz = s[:m.start()].strip()
        bal_resz = re.sub(r"\d{4}\.\d{2}(?:\.\d{2})?(?:\s*[-–]?\s*\d{4}\.\d{2}\.\d{2}|\s*hó)?", "", bal_resz).strip(" -–:|")
        me = m.group(3).replace("m³", "m3")
        alap_nev_tipus = "Alapdíj" if me in ("MW", "kW", "hó") else ("Hődíj" if me in ("GJ", "MWh", "kWh") else "Távhő díjtétel")
        nyers_nev = bal_resz or elozo_nev or alap_nev_tipus
        elozo_nev = ""
        nev = f"{kategoria}{nyers_nev}".strip()

        szorzo = _hu_szam(m.group(1)) or 1.0
        menny = (_hu_szam(m.group(2)) or 0.0) * szorzo
        ar = _hu_szam(m.group(4))
        netto_oszlop = _hu_szam(m.group(5))
        afa_nyers = m.group(6)
        afa = 5.0 if not afa_nyers else (0.0 if afa_nyers == "AHK" else float(afa_nyers))

        if ar is not None and netto_oszlop is not None and menny != 0:
            if abs((menny * ar) - netto_oszlop) > 5.0:
                ar = round(netto_oszlop / menny, 4)

        if ar is not None and menny != 0:
            talalatok.append({"Megnevezés": nev, "Mennyiség": round(menny, 4), "Mértékegység": me, "Nettó egységár": ar, "ÁFA (%)": afa})

    if talalatok:
        adat["tetelek_df"] = pd.DataFrame(talalatok)
    return adat




def feldolgoz_pdf(fajl):
    pdf_bytes = fajl.read()
    dok = fitz.open(stream=pdf_bytes, filetype="pdf")
    szoveg = "\n".join(list(oldal.get_text("text") or "" for oldal in dok))
    adat = {"szoveg": szoveg.strip(), "szamlaszam": "", "kelte": date.today(), "hatarido": date.today(), "tol": date.today(), "ig": date.today(), "brutto": 0, "pod": "", "szolgaltato_kulcs": "", "tetelek_df": None, "pod_adatok": {}}

    mk = re.search(r"Számla\s*kelte[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mk: adat["kelte"] = datetime.strptime(mk.group(1), "%Y.%m.%d").date()
    mh = re.search(r"Fizetési\s*határidő[:\s]+(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mh: adat["hatarido"] = datetime.strptime(mh.group(1), "%Y.%m.%d").date()
    mp = re.search(r"(HU\d{6}[-A-Z0-9]{10,})", szoveg)
    if mp: adat["pod"] = mp.group(1).strip()

    szoveg_low = szoveg.lower()
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["ALTEO"]["azonositok"]):
        return _feldolgoz_alteo_pdf(dok, szoveg, adat)
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["MVM"]["azonositok"]):
        return _feldolgoz_mvm_pdf(dok, szoveg, adat)
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["TAVHO"]["azonositok"]):
        return _feldolgoz_tavho_pdf(dok, szoveg, adat)
    if any(az in szoveg_low for az in SZOLGALTATO_SABLONOK["VIZMU"]["azonositok"]):
        return _feldolgoz_vizmu_pdf(dok, szoveg, adat)

    m = re.search(r"(?:Számla\s*sorszáma|Számlaszám|Bizonylat\s*sorszáma)[:\s]+([A-Za-z0-9/-]{5,})", szoveg, re.I)
    if m: adat["szamlaszam"] = m.group(1).strip()
    alap_nev = next(iter(fajl.name.rsplit(".", 1)))
    mf = re.search(r"(\d{6,})", alap_nev)
    if not adat["szamlaszam"] and mf: adat["szamlaszam"] = mf.group(1)
    if not adat["szamlaszam"] and len(alap_nev) >= 5: adat["szamlaszam"] = alap_nev

    mi = re.search(r"(?:Elszámolt|Teljesítési|Elszámolási)\s*időszak[:\s]+(\d{4}\.\d{2}\.\d{2})\s*[-–]\s*(\d{4}\.\d{2}\.\d{2})", szoveg, re.I)
    if mi:
        adat["tol"] = datetime.strptime(mi.group(1), "%Y.%m.%d").date()
        adat["ig"] = datetime.strptime(mi.group(2), "%Y.%m.%d").date()
    mb = re.search(r"(?:Fizetendő\s*összeg|Fizetendő\s*összesen|Számla\s*végösszege)[:\s]+([\d\.\s\xa0]+(?:,\d+)?)\s*Ft", szoveg, re.I)
    if mb:
        br_val = _hu_szam(mb.group(1))
        if br_val:
            adat["brutto"] = int(round(br_val))
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
        megn_str = str(row.get("Megnevezés") or "").strip()
        if megn_str == "ÖSSZESEN":
            continue
        if megn_str in ("Fizetési felszólítás díja", "Késedelmi kamat/ÁHK", "Bruttó díj kompenzáció"):
            br_nyers = str(row.get("Bruttó összeg (Ft)") or "")
            negativ = "-" in br_nyers
            szamjegyek = re.sub(r"\D", "", br_nyers)
            br_ertek = (-int(szamjegyek) if negativ else int(szamjegyek)) if szamjegyek else 0
            if br_ertek != 0:
                ossz_brutto += Decimal(br_ertek)
                tetelek.append({"megnevezes_eredeti": megn_str, "mennyiseg": 1.0, "mertekegyseg": "Ft", "egysegar_netto": 0.0, "szamitott_netto": 0.0, "afa_kulcs": 0.0, "szamitott_brutto": float(br_ertek)})
            continue


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
        tetelek.append({"megnevezes_eredeti": megn_str or "Díjtétel", "mennyiseg": float(menny), "mertekegyseg": str(row.get("Mértékegység") or "kWh"), "egysegar_netto": float(ar), "szamitott_netto": float(netto), "afa_kulcs": float(afa), "szamitott_brutto": float(brutto)})
    return ossz_netto, ossz_brutto, tetelek

def mentes_adatbazisba(uj_szamla, tetelek, fajl, gyujto_marad=False):
    try:
        alap_szamlaszam = uj_szamla["szamlaszam"].strip()
        uj_szamla["szamlaszam"] = alap_szamlaszam
        try:
            res = supabase.table("szamlak").insert(uj_szamla).execute()
        except Exception as ins_hiba:
            if "duplicate" in str(ins_hiba).lower() or "23505" in str(ins_hiba):
                st.warning("⚠️ Ez a felhasználási hely (POD) már rögzítve van ehhez a számlaszámhoz! Válaszd a következő mérési pontot a fenti legördülő listából.")
                return
            raise ins_hiba

        sid = next(iter(res.data))["id"]
        for t in tetelek:
            t["szamla_id"] = sid
        supabase.table("szamla_tetelek").insert(tetelek).execute()
        if fajl is not None:
            fajl.seek(0)
            tiszta_szam = re.sub(r"[^A-Za-z0-9_-]", "_", alap_szamlaszam)
            fnev = f"{date.today().year}_{tiszta_szam}.pdf"
            supabase.storage.from_("szamlak-pdf").upload(path=fnev, file=fajl.read(), file_options={"content-type": "application/pdf", "upsert": "true"})
            supabase.table("szamlak").update({"pdf_utvonal": fnev}).eq("id", sid).execute()
        st.session_state["utolso_mentes"] = f"✅ A POD számlarészlet rögzítése megtörtént (Számlaszám: {alap_szamlaszam}, Elszámolási hónap: {uj_szamla['elszamolasi_honap']}, ID: #{sid})"
        if not gyujto_marad:
            urit_urlap()
        st.rerun()
    except Exception as hiba:
        st.error(f"Pontos hiba oka: {hiba}")




def mutat_helyettesitesek():
    st.header("🔄 Helyettesítések kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    h_map = {h["id"]: h["megnevezes"] for h in osszes_hely}

    helyett_lista = supabase.table("helyettesitesek").select("*").order("kezdet_datum", desc=True).execute().data or list()
    if helyett_lista:
        h_sorok = list({"ID": x["id"], "Helyettesített": u_map.get(x["helyettesitett_energetikus_id"], ""), "Helyettesítő": u_map.get(x["helyettesito_energetikus_id"], ""), "Felhasználási hely": h_map.get(x["felhasznalasi_hely_id"], "Minden telephelye"), "Kezdet": x["kezdet_datum"], "Vége": x["vege_datum"]} for x in helyett_lista)
        st.dataframe(pd.DataFrame(h_sorok), use_container_width=True, hide_index=True)
        torol_h = st.selectbox("Helyettesítés törlése:", list(f"#{x['id']} – {u_map.get(x['helyettesitett_energetikus_id'], '')} -> {u_map.get(x['helyettesito_energetikus_id'], '')}" for x in helyett_lista))
        if st.button("🗑️ Kiválasztott helyettesítés törlése"):
            hid = int(next(iter(torol_h.split(" – "))).replace("#", ""))
            supabase.table("helyettesitesek").delete().eq("id", hid).execute()
            st.rerun()

    st.subheader("➕ Új helyettesítés rögzítése")
    with st.form("uj_helyettesites_form"):
        hc1, hc2 = st.columns(2)
        kit = hc1.selectbox("Helyettesített energetikus:", list(aktiv_user_opciok.keys()))
        kivel = hc2.selectbox("Helyettesítő energetikus:", list(aktiv_user_opciok.keys()))
        hely_valasztek = {"Minden telephelyére érvényes": None}
        for h in osszes_hely:
            hely_valasztek[f"{h['megnevezes']} ({h['pod_eic_azonosito']})"] = h["id"]
        melyik_hely = st.selectbox("Érintett felhasználási hely:", list(hely_valasztek.keys()))
        hd1, hd2 = st.columns(2)
        h_tol = hd1.date_input("Kezdet", value=date.today())
        h_ig = hd2.date_input("Vége", value=date.today())
        if st.form_submit_button("💾 Helyettesítés mentése", type="primary"):
            if aktiv_user_opciok[kit] != aktiv_user_opciok[kivel] and h_tol <= h_ig:
                supabase.table("helyettesitesek").insert({"helyettesitett_energetikus_id": aktiv_user_opciok[kit], "helyettesito_energetikus_id": aktiv_user_opciok[kivel], "felhasznalasi_hely_id": hely_valasztek[melyik_hely], "kezdet_datum": h_tol.isoformat(), "vege_datum": h_ig.isoformat(), "aktiv": True}).execute()
                st.rerun()

def mutat_torzsadatok():
    st.header("⚙️ Törzsadatok kezelése")
    osszes_user = supabase.table("energetikusok").select("*").order("nev").execute().data or list()
    u_map = {u["id"]: u["nev"] for u in osszes_user}
    aktiv_user_opciok = {f"{u['nev']} ({u['szerepkor']})": u["id"] for u in osszes_user if u.get("aktiv")}
    osszes_hely = supabase.table("felhasznalasi_helyek").select("*").order("megnevezes").execute().data or list()
    osszes_szolg = supabase.table("szolgaltatok").select("*").order("nev").execute().data or list()
    energia_opciok = list(("Villamos energia", "Földgáz", "Távhő", "Víziközmű", "PB-gáz"))

    t_hely, t_user, t_szolg = st.tabs(list(("🏭 Felhasználási helyek", "👤 Energetikusok", "🏢 Szolgáltatók")))

    with t_hely:
        if osszes_hely:
            st.caption("💡 Kattints duplán a cellákra a **Megnevezés**, **Energiatípus**, **POD / EIC**, **Felelős**, **Állandó helyettes** vagy **Aktív** mezők módosításához!")
            nev_to_id = {"Nincs kijelölve": None}
            for u in osszes_user:
                nev_to_id[u["nev"]] = u["id"]
            user_nevek = list(nev_to_id.keys())

            h_sorok = list()
            for h in osszes_hely:
                fel_nev = u_map.get(h.get("felelos_energetikus_id")) or "Nincs kijelölve"
                helyett_nev = u_map.get(h.get("allando_helyettes_id")) or "Nincs kijelölve"
                e_tipus = h.get("energiatipus") if h.get("energiatipus") in energia_opciok else "Villamos energia"
                h_sorok.append({"ID": h["id"], "Megnevezés": h["megnevezes"], "Energiatípus": e_tipus, "POD / EIC": h["pod_eic_azonosito"], "Felelős": fel_nev, "Állandó helyettes": helyett_nev, "Aktív": bool(h.get("aktiv", True))})

            szerk_hely_df = st.data_editor(
                pd.DataFrame(h_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Energiatípus": st.column_config.SelectboxColumn("Energiatípus", options=energia_opciok, required=True),
                    "Felelős": st.column_config.SelectboxColumn("Felelős", options=user_nevek, required=True),
                    "Állandó helyettes": st.column_config.SelectboxColumn("Állandó helyettes", options=user_nevek, required=True),
                    "Aktív": st.column_config.CheckboxColumn("Aktív")
                },
                key="torzs_hely_editor"
            )

            if st.button("💾 Felhasználási helyek módosításainak mentése", type="primary"):
                eredeti_h = {h["id"]: h for h in osszes_hely}
                modositva = 0
                for _, sor in szerk_hely_df.iterrows():
                    hid = int(sor["ID"])
                    regi = eredeti_h[hid]
                    uj_adat = {"megnevezes": str(sor["Megnevezés"]).strip(), "energiatipus": sor["Energiatípus"], "pod_eic_azonosito": str(sor["POD / EIC"]).strip(), "felelos_energetikus_id": nev_to_id.get(sor["Felelős"]), "allando_helyettes_id": nev_to_id.get(sor["Állandó helyettes"]), "aktiv": bool(sor["Aktív"])}
                    if uj_adat["megnevezes"] != regi["megnevezes"] or uj_adat["energiatipus"] != (regi.get("energiatipus") or "Villamos energia") or uj_adat["pod_eic_azonosito"] != regi["pod_eic_azonosito"] or uj_adat["felelos_energetikus_id"] != regi.get("felelos_energetikus_id") or uj_adat["allando_helyettes_id"] != regi.get("allando_helyettes_id") or uj_adat["aktiv"] != bool(regi.get("aktiv", True)):
                        supabase.table("felhasznalasi_helyek").update(uj_adat).eq("id", hid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db felhasználási hely módosítása mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új felhasználási hely hozzáadása"):
            with st.form("uj_hely_form"):
                fc1, fc2, fc3 = st.columns(3)
                uj_h_nev = fc1.text_input("Megnevezés:")
                uj_h_energia = fc2.selectbox("Energiatípus:", energia_opciok)
                uj_h_pod = fc3.text_input("POD / EIC azonosító:")
                fel_opc = {"Nincs kijelölve": None}
                fel_opc.update(aktiv_user_opciok)
                fc4, fc5 = st.columns(2)
                uj_h_fel = fc4.selectbox("Felelős energetikus:", list(fel_opc.keys()))
                uj_h_helyett = fc5.selectbox("Állandó helyettes:", list(fel_opc.keys()))
                if st.form_submit_button("💾 Új felhasználási hely mentése", type="primary") and uj_h_nev.strip() and uj_h_pod.strip():
                    tisztitott_pod = uj_h_pod.strip()
                    letezo_h = next((h for h in osszes_hely if str(h.get("pod_eic_azonosito") or "").strip().lower() == tisztitott_pod.lower()), None)
                    if letezo_h:
                        st.warning(f"⚠️ Meglévő felhasználási hely: ezzel a POD / EIC azonosítóval (**{tisztitott_pod}**) már szerepel telephely (**{letezo_h['megnevezes']}**) a rendszerben!")
                    else:
                        try:
                            supabase.table("felhasznalasi_helyek").insert({"megnevezes": uj_h_nev.strip(), "energiatipus": uj_h_energia, "pod_eic_azonosito": tisztitott_pod, "felelos_energetikus_id": fel_opc[uj_h_fel], "allando_helyettes_id": fel_opc[uj_h_helyett], "aktiv": True}).execute()
                            st.rerun()
                        except Exception as hiba:
                            if "duplicate" in str(hiba).lower() or "23505" in str(hiba):
                                st.warning("⚠️ Meglévő felhasználási hely: ezzel a POD / EIC azonosítóval már rögzítve van telephely a rendszerben!")
                            else:
                                st.error(f"Adatbázis hiba: {hiba}")

        if osszes_hely:
            st.divider()
            st.subheader("🗑️ Felhasználási hely törlése")
            h_ver = st.session_state.get("hely_torles_ver", 0)
            h_torol_opc = {f"#{h['id']} – {h['megnevezes']} ({h['pod_eic_azonosito']})": h for h in osszes_hely}
            val_h_torol = st.selectbox("Válaszd ki a törlendő felhasználási helyet:", list(h_torol_opc.keys()), index=None, placeholder="Válassz ki egy telephelyet...", key=f"h_torol_sel_{h_ver}")
            if val_h_torol:
                kiv_h = h_torol_opc[val_h_torol]
                if st.button("🗑️ Kiválasztott felhasználási hely törlése"):
                    st.session_state["h_torles_megerosites_id"] = kiv_h["id"]
                if st.session_state.get("h_torles_megerosites_id") == kiv_h["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja a felhasználási helyet? (**{kiv_h['megnevezes']}**)")
                    hc1, hc2, _ = st.columns((1.2, 1.2, 4))
                    if hc1.button("✅ Igen", type="primary", key=f"h_igen_{kiv_h['id']}"):
                        try:
                            supabase.table("felhasznalasi_helyek").delete().eq("id", kiv_h["id"]).execute()
                            st.session_state.pop("h_torles_megerosites_id", None)
                            st.session_state["hely_torles_ver"] = h_ver + 1
                            st.success(f"✅ {kiv_h['megnevezes']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez a telephely nem törölhető véglegesen, mert már rögzített számla tartozik hozzá.")
                            if kiv_h.get("aktiv"):
                                if st.button("🚫 Inaktiválás törlés helyett", key=f"h_inakt_{kiv_h['id']}"):
                                    supabase.table("felhasznalasi_helyek").update({"aktiv": False}).eq("id", kiv_h["id"]).execute()
                                    st.session_state.pop("h_torles_megerosites_id", None)
                                    st.session_state["hely_torles_ver"] = h_ver + 1
                                    st.rerun()
                    if hc2.button("❌ Nem", key=f"h_nem_{kiv_h['id']}"):
                        st.session_state.pop("h_torles_megerosites_id", None)
                        st.session_state["hely_torles_ver"] = h_ver + 1
                        st.rerun()



    with t_user:
        szerep_opciok = list(("Energetikus", "Vezető energetikus", "Adminisztrátor"))
        if osszes_user:
            st.caption("💡 Kattints duplán a **Név**, **Szerepkör** vagy **Aktív** cellákra a módosításhoz!")
            u_sorok = list({"ID": u["id"], "Név": u["nev"], "Szerepkör": u["szerepkor"] if u.get("szerepkor") in szerep_opciok else "Energetikus", "Aktív": bool(u.get("aktiv", True))} for u in osszes_user)
            szerk_u_df = st.data_editor(
                pd.DataFrame(u_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Szerepkör": st.column_config.SelectboxColumn("Szerepkör", options=szerep_opciok, required=True),
                    "Aktív": st.column_config.CheckboxColumn("Aktív")
                },
                key="torzs_user_editor"
            )
            if st.button("💾 Energetikusok módosításainak mentése", type="primary"):
                eredeti_u = {u["id"]: u for u in osszes_user}
                modositva = 0
                for _, sor in szerk_u_df.iterrows():
                    uid = int(sor["ID"])
                    regi = eredeti_u[uid]
                    uj_nev = str(sor["Név"]).strip()
                    uj_szerep = sor["Szerepkör"]
                    uj_aktiv = bool(sor["Aktív"])
                    if uj_nev and (uj_nev != regi["nev"] or uj_szerep != regi.get("szerepkor") or uj_aktiv != bool(regi.get("aktiv", True))):
                        supabase.table("energetikusok").update({"nev": uj_nev, "szerepkor": uj_szerep, "aktiv": uj_aktiv}).eq("id", uid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db energetikus adatai mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új energetikus hozzáadása"):
            with st.form("uj_user_form"):
                uc1, uc2 = st.columns(2)
                uj_u_nev = uc1.text_input("Új energetikus teljes neve:")
                uj_u_szerep = uc2.selectbox("Szerepkör:", szerep_opciok)
                if st.form_submit_button("💾 Energetikus mentése", type="primary") and uj_u_nev.strip():
                    uj_adat = {"nev": uj_u_nev.strip(), "szerepkor": uj_u_szerep, "aktiv": True}
                    if osszes_user and "email" in next(iter(osszes_user)):
                        uj_adat["email"] = f"{re.sub(r'[^a-z0-9]', '', uj_u_nev.lower())}@ceg.hu"
                    supabase.table("energetikusok").insert(uj_adat).execute()
                    st.rerun()

        if osszes_user:
            st.divider()
            st.subheader("🗑️ Energetikus törlése")
            u_ver = st.session_state.get("user_torles_ver", 0)
            u_torol_opc = {f"#{u['id']} – {u['nev']} ({u['szerepkor']})": u for u in osszes_user}
            val_u_torol = st.selectbox("Válaszd ki a törlendő energetikust:", list(u_torol_opc.keys()), index=None, placeholder="Válassz ki egy energetikust...", key=f"u_torol_sel_{u_ver}")
            if val_u_torol:
                kiv_u = u_torol_opc[val_u_torol]
                if st.button("🗑️ Kiválasztott energetikus törlése"):
                    st.session_state["u_torles_megerosites_id"] = kiv_u["id"]
                if st.session_state.get("u_torles_megerosites_id") == kiv_u["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja az energetikust? (**{kiv_u['nev']}**)")
                    uc_igen, uc_nem, _ = st.columns((1.2, 1.2, 4))
                    if uc_igen.button("✅ Igen", type="primary", key=f"u_igen_{kiv_u['id']}"):
                        try:
                            supabase.table("energetikusok").delete().eq("id", kiv_u["id"]).execute()
                            st.session_state.pop("u_torles_megerosites_id", None)
                            st.session_state["user_torles_ver"] = u_ver + 1
                            st.success(f"✅ {kiv_u['nev']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez az energetikus nem törölhető véglegesen, mert számlához, telephelyhez vagy helyettesítéshez van kapcsolva.")
                            if kiv_u.get("aktiv"):
                                if st.button("🚫 Inaktiválás törlés helyett", key=f"u_inakt_{kiv_u['id']}"):
                                    supabase.table("energetikusok").update({"aktiv": False}).eq("id", kiv_u["id"]).execute()
                                    st.session_state.pop("u_torles_megerosites_id", None)
                                    st.session_state["user_torles_ver"] = u_ver + 1
                                    st.rerun()
                    if uc_nem.button("❌ Nem", key=f"u_nem_{kiv_u['id']}"):
                        st.session_state.pop("u_torles_megerosites_id", None)
                        st.session_state["user_torles_ver"] = u_ver + 1
                        st.rerun()

            st.divider()
            st.subheader("🔀 Telephelyek szétosztása energetikusok között")
            rendezett_userek = sorted(osszes_user, key=lambda x: (bool(x.get("aktiv")), x["nev"]))
            szuro_opciok = {"Összes felhasználási hely": None}
            for u in rendezett_userek:
                szuro_opciok[f"{u['nev']} [{'Inaktív' if not u.get('aktiv') else 'Aktív'}]"] = u["id"]
            val_szuro = st.selectbox("Kinek a telephelyeit szeretnéd szétosztani?", list(szuro_opciok.keys()))
            szurt_forras_id = szuro_opciok[val_szuro]

            szurt_helyek = osszes_hely if szurt_forras_id is None else list(h for h in osszes_hely if h.get("felelos_energetikus_id") == szurt_forras_id or h.get("allando_helyettes_id") == szurt_forras_id)
            if not szurt_helyek:
                st.info("A kiválasztott szűrőhöz nem tartozik felhasználási hely.")
            else:
                st.caption("💡 Kattints duplán a **Felelős energetikus** vagy az **Állandó helyettes** oszlop celláira a kiosztás módosításához!")
                nev_to_id_akt = {"Nincs kijelölve": None}
                for u in osszes_user:
                    if u.get("aktiv"):
                        nev_to_id_akt[u["nev"]] = u["id"]
                valaszthato_nevek = list(nev_to_id_akt.keys())

                kiosztas_sorok = list()
                for h in szurt_helyek:
                    fel_nev = u_map.get(h.get("felelos_energetikus_id")) or "Nincs kijelölve"
                    helyett_nev = u_map.get(h.get("allando_helyettes_id")) or "Nincs kijelölve"
                    if fel_nev not in valaszthato_nevek:
                        valaszthato_nevek.append(fel_nev)
                    if helyett_nev not in valaszthato_nevek:
                        valaszthato_nevek.append(helyett_nev)
                    kiosztas_sorok.append({"ID": h["id"], "Megnevezés": h["megnevezes"], "Energiatípus": h.get("energiatipus") or "", "POD / EIC": h["pod_eic_azonosito"], "Felelős energetikus": fel_nev, "Állandó helyettes": helyett_nev})

                szerk_kiosztas = st.data_editor(
                    pd.DataFrame(kiosztas_sorok),
                    use_container_width=True,
                    hide_index=True,
                    disabled=list(("ID", "Megnevezés", "Energiatípus", "POD / EIC")),
                    column_config={
                        "Felelős energetikus": st.column_config.SelectboxColumn("Felelős energetikus", options=valaszthato_nevek, required=True),
                        "Állandó helyettes": st.column_config.SelectboxColumn("Állandó helyettes", options=valaszthato_nevek, required=True)
                    },
                    key=f"kiosztas_ed_{szurt_forras_id}"
                )

                if st.button("💾 Telephely-kiosztás mentése", type="primary"):
                    eredeti_map = {h["id"]: h for h in szurt_helyek}
                    modositva = 0
                    for _, sor in szerk_kiosztas.iterrows():
                        hid = int(sor["ID"])
                        uj_fel_id = nev_to_id_akt.get(sor["Felelős energetikus"], eredeti_map[hid].get("felelos_energetikus_id"))
                        uj_helyett_id = nev_to_id_akt.get(sor["Állandó helyettes"], eredeti_map[hid].get("allando_helyettes_id"))
                        if uj_fel_id != eredeti_map[hid].get("felelos_energetikus_id") or uj_helyett_id != eredeti_map[hid].get("allando_helyettes_id"):
                            supabase.table("felhasznalasi_helyek").update({"felelos_energetikus_id": uj_fel_id, "allando_helyettes_id": uj_helyett_id}).eq("id", hid).execute()
                            modositva += 1
                    if modositva > 0:
                        st.success(f"✅ {modositva} db felhasználási hely kiosztása sikeresen mentve!")
                        st.rerun()
                    else:
                        st.info("Nem történt változás a kiosztásban.")

    with t_szolg:
        if osszes_szolg:
            st.caption("💡 Kattints duplán a szolgáltató **Név** vagy **Energiatípus** cellájára a módosításhoz!")
            sz_sorok = list({"ID": s["id"], "Név": s["nev"], "Energiatípus": s.get("energiatipus") if s.get("energiatipus") in energia_opciok else "Villamos energia"} for s in osszes_szolg)
            szerk_sz_df = st.data_editor(
                pd.DataFrame(sz_sorok),
                use_container_width=True,
                hide_index=True,
                disabled=list(("ID",)),
                column_config={
                    "Energiatípus": st.column_config.SelectboxColumn("Energiatípus", options=energia_opciok, required=True)
                },
                key="torzs_szolg_editor"
            )
            if st.button("💾 Szolgáltatók módosításainak mentése", type="primary"):
                eredeti_sz = {s["id"]: s for s in osszes_szolg}
                modositva = 0
                for _, sor in szerk_sz_df.iterrows():
                    sid = int(sor["ID"])
                    regi = eredeti_sz[sid]
                    uj_nev = str(sor["Név"]).strip()
                    uj_et = sor["Energiatípus"]
                    if uj_nev and (uj_nev != regi["nev"] or uj_et != (regi.get("energiatipus") or "Villamos energia")):
                        supabase.table("szolgaltatok").update({"nev": uj_nev, "energiatipus": uj_et}).eq("id", sid).execute()
                        modositva += 1
                if modositva > 0:
                    st.success(f"✅ {modositva} db szolgáltató módosítása mentve!")
                    st.rerun()
                else:
                    st.info("Nem történt változás a táblázatban.")

        with st.expander("➕ Új szolgáltató hozzáadása"):
            with st.form("uj_szolg_form"):
                sc1, sc2 = st.columns(2)
                uj_sz_nev = sc1.text_input("Szolgáltató neve:")
                uj_sz_et = sc2.selectbox("Energiatípus:", energia_opciok)
                if st.form_submit_button("💾 Új szolgáltató mentése", type="primary") and uj_sz_nev.strip():
                    try:
                        supabase.table("szolgaltatok").insert({"nev": uj_sz_nev.strip(), "energiatipus": uj_sz_et}).execute()
                        st.rerun()
                    except Exception as hiba:
                        st.error(f"Adatbázis hiba: {hiba}")

        if osszes_szolg:
            st.divider()
            st.subheader("🗑️ Szolgáltató törlése")
            sz_ver = st.session_state.get("szolg_torles_ver", 0)
            sz_torol_opc = {f"#{s['id']} – {s['nev']} ({s.get('energiatipus') or ''})": s for s in osszes_szolg}
            val_sz_torol = st.selectbox("Válaszd ki a törlendő szolgáltatót:", list(sz_torol_opc.keys()), index=None, placeholder="Válassz ki egy szolgáltatót...", key=f"sz_torol_sel_{sz_ver}")
            if val_sz_torol:
                kiv_sz = sz_torol_opc[val_sz_torol]
                if st.button("🗑️ Kiválasztott szolgáltató törlése"):
                    st.session_state["sz_torles_megerosites_id"] = kiv_sz["id"]
                if st.session_state.get("sz_torles_megerosites_id") == kiv_sz["id"]:
                    st.warning(f"⚠️ Biztos, hogy törölni kívánja a szolgáltatót? (**{kiv_sz['nev']}**)")
                    szc1, szc2, _ = st.columns((1.2, 1.2, 4))
                    if szc1.button("✅ Igen", type="primary", key=f"sz_igen_{kiv_sz['id']}"):
                        try:
                            supabase.table("szolgaltatok").delete().eq("id", kiv_sz["id"]).execute()
                            st.session_state.pop("sz_torles_megerosites_id", None)
                            st.session_state["szolg_torles_ver"] = sz_ver + 1
                            st.success(f"✅ {kiv_sz['nev']} törölve lett!")
                            st.rerun()
                        except Exception:
                            st.error("⚠️ Ez a szolgáltató nem törölhető, mert már rögzített számla tartozik hozzá.")
                    if szc2.button("❌ Nem", key=f"sz_nem_{kiv_sz['id']}"):
                        st.session_state.pop("sz_torles_megerosites_id", None)
                        st.session_state["szolg_torles_ver"] = sz_ver + 1
                        st.rerun()

def _betolt_szurt_szamlak(kulcs_prefix, mutass_energiatipus=False):
    szamlak_data = supabase.table("szamlak").select("*").order("id", desc=True).execute().data or list()
    helyek_data = supabase.table("felhasznalasi_helyek").select("id, megnevezes, energiatipus").execute().data or list()
    szolg_data = supabase.table("szolgaltatok").select("id, nev").execute().data or list()

    h_map = {h["id"]: h["megnevezes"] for h in helyek_data}
    h_energia_map = {h["id"]: (h.get("energiatipus") or "Villamos energia") for h in helyek_data}
    s_map = {s["id"]: s["nev"] for s in szolg_data}

    if not szamlak_data:
        st.info("Még nincs rögzített számla az adatbázisban.")
        return None, None, None, None, None, None, None

    akt_uid = st.session_state.get("akt_user_id")
    osszes_mutatasa = st.checkbox("🌐 Összes számla megjelenítése (nem csak a sajátjaim)", value=False, key=f"{kulcs_prefix}_osszes_mutat")
    if not osszes_mutatasa and akt_uid is not None:
        sajat_hely_idk = set(int(h["id"]) for h in get_helyek(int(akt_uid)))
        szamlak_data = list(
            s for s in szamlak_data
            if int(s.get("felhasznalasi_hely_id") or 0) in sajat_hely_idk
            or int(s.get("jovahagyta_energetikus_id") or 0) == int(akt_uid)
        )
        if not szamlak_data:
            st.info("Nincs még hozzád tartozó rögzített számla. Pipáld be a fenti négyzetet a rendszerben lévő összes számla megtekintéséhez.")
            return None, None, None, None, None, None, None


    fc1, fc2, fc3, fc4 = st.columns(4)
    energia_opciok = ["Összes"] + sorted(list(set(h_energia_map.values())))
    val_energia = fc1.selectbox("⚡ Energiatípus:", energia_opciok, key=f"{kulcs_prefix}_f_energia")

    hely_nevek = ["Összes"] + sorted(list(set(h_map.get(s["felhasznalasi_hely_id"], "Ismeretlen") for s in szamlak_data)))
    val_hely = fc2.selectbox("🏭 Felhasználási hely:", hely_nevek, key=f"{kulcs_prefix}_f_hely")

    szolg_nevek = ["Összes"] + sorted(list(set(s_map.get(s["szolgaltato_id"], "Ismeretlen") for s in szamlak_data)))
    val_szolg = fc3.selectbox("🏢 Szolgáltató:", szolg_nevek, key=f"{kulcs_prefix}_f_szolg")

    honapok = sorted(list(set(tiszta_honap(s.get("elszamolasi_honap")) for s in szamlak_data)), key=honap_kulcs)
    val_honapok = fc4.multiselect("📅 Elszámolási hónap:", honapok, default=[], placeholder="Összes hónap", key=f"{kulcs_prefix}_f_ho")


    szurt_lista = list()
    for s in szamlak_data:
        hid = s["felhasznalasi_hely_id"]
        e_tipus = h_energia_map.get(hid, "Villamos energia")
        h_nev = h_map.get(hid, "Ismeretlen")
        s_nev = s_map.get(s["szolgaltato_id"], "Ismeretlen")
        ho_cimke = tiszta_honap(s.get("elszamolasi_honap"))

        if val_energia != "Összes" and e_tipus != val_energia:
            continue
        if val_hely != "Összes" and h_nev != val_hely:
            continue
        if val_szolg != "Összes" and s_nev != val_szolg:
            continue
        if val_honapok and ho_cimke not in val_honapok:
            continue
        szurt_lista.append(s)

    if not szurt_lista:
        st.warning("A megadott szűrőfeltételekkel nincs találat.")
        return None, None, None, None, None, None, None

    szurt_lista.sort(
        key=lambda s: (
            (0, 0) if honap_kulcs(tiszta_honap(s.get("elszamolasi_honap"))) == (9999, 99) else honap_kulcs(tiszta_honap(s.get("elszamolasi_honap"))),
            int(s.get("id") or 0)
        ),
        reverse=True
    )

    szamla_idk = list(s["id"] for s in szurt_lista)
    osszes_tetel = supabase.table("szamla_tetelek").select("*").in_("szamla_id", szamla_idk).execute().data or list()

    netto_per_szamla = dict()
    fogyasztas_per_szamla = dict()
    fogyasztas_me_per_szamla = dict()
    tartalek_m3_per_szamla = dict()

    for t in osszes_tetel:
        sid = t["szamla_id"]
        netto_per_szamla[sid] = netto_per_szamla.get(sid, 0.0) + float(t.get("szamitott_netto") or 0.0)
        megn = str(t.get("megnevezes_eredeti") or "").strip().lower()
        me = str(t.get("mertekegyseg") or "").strip().lower()
        menny = float(t.get("mennyiseg") or 0.0)

        if ("energiadíj" in megn or "energia díj" in megn or "áramdíj" in megn or "földgáz" in megn or "gázdíj" in megn) and me in ("kwh", "m3", "m³", "mj", "gj"):
            fogyasztas_per_szamla[sid] = fogyasztas_per_szamla.get(sid, 0.0) + menny
            fogyasztas_me_per_szamla[sid] = "m³" if me in ("m3", "m³") else t.get("mertekegyseg", "kWh")
        elif "hődíj" in megn and me in ("gj", "mj"):
            fogyasztas_per_szamla[sid] = fogyasztas_per_szamla.get(sid, 0.0) + menny
            fogyasztas_me_per_szamla[sid] = t.get("mertekegyseg", "GJ")
        elif "ivóvíz" in megn and me in ("m3", "m³"):
            fogyasztas_per_szamla[sid] = fogyasztas_per_szamla.get(sid, 0.0) + menny
            fogyasztas_me_per_szamla[sid] = "m³"
        elif ("szennyvíz" in megn or "vízfelmelegítési" in megn) and me in ("m3", "m³"):
            tartalek_m3_per_szamla[sid] = tartalek_m3_per_szamla.get(sid, 0.0) + menny

    for sid, m3_menny in tartalek_m3_per_szamla.items():
        if fogyasztas_per_szamla.get(sid, 0.0) == 0.0 and m3_menny != 0:
            fogyasztas_per_szamla[sid] = m3_menny
            fogyasztas_me_per_szamla[sid] = "m³"

    sorok = list()
    for s in szurt_lista:
        sid = s["id"]
        hid = s["felhasznalasi_hely_id"]
        fogy_menny = fogyasztas_per_szamla.get(sid, 0.0)
        fogy_me = fogyasztas_me_per_szamla.get(sid, "kWh")
        netto_ossz = netto_per_szamla.get(sid, 0.0)
        menny_szov = f"{fogy_menny:,.2f}".replace(",", " ").replace(".", ",")
        menny_str = f"{menny_szov} {fogy_me}" if fogy_menny != 0 else f"0,00 {fogy_me}"
        egysegar_str = f"{netto_ossz / fogy_menny:,.2f} Ft/{fogy_me}".replace(",", " ").replace(".", ",") if fogy_menny != 0 else None

        sor_adat = {
            "ID": sid,
            "Számlaszám": s.get("szamlaszam", ""),
            "Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")),
            "Energiatípus": h_energia_map.get(hid, "Villamos energia"),
            "Felhasználási hely": h_map.get(hid, "Ismeretlen"),
            "Szolgáltató": s_map.get(s["szolgaltato_id"], "Ismeretlen"),
            "Időszak kezdete": s.get("idoszak_tol", ""),
            "Időszak vége": s.get("idoszak_ig", ""),
            "Mennyiség": menny_str,
            "Nettó összeg (Ft)": int(round(netto_ossz)),
            "Bruttó összeg (Ft)": int(round(float(s.get("szamlan_szereplo_brutto") or 0))),
            "Szumma nettó egységár": egysegar_str,
            "Státusz": s.get("ellenorzes_statusz", ""),
            "PDF fájl": s.get("pdf_utvonal") or s.get("pdf_fajlnev") or ""
        }
        sorok.append(sor_adat)

    df = pd.DataFrame(sorok)
    return szurt_lista, df, osszes_tetel, netto_per_szamla, fogyasztas_per_szamla, h_map, s_map



def _alkalmaz_oszlop_szuroket(df, szurt_lista, kulcs_prefix="rogz_col"):
    oszlopok = [c for c in df.columns if c != "ID"]
    if not oszlopok:
        return df, szurt_lista

    aktiv_szurok = dict()
    with st.container(border=True):
        st.caption("🔎 **Oszlopszűrők (intelligens keresővel – kattints bármelyik oszlopba és kezdj el gépelni):**")
        sor1 = st.columns(6)
        sor2 = st.columns(len(oszlopok) - 6) if len(oszlopok) > 6 else list()
        osszes_cella = list(sor1) + list(sor2)

        for idx, col in enumerate(oszlopok):
            if col == "Bruttó összeg (Ft)":
                egyedi_ertekek = sorted(list(set(int(v) for v in df[col].dropna())), reverse=True)
                opcio_map = {ft_szoveg(v): v for v in egyedi_ertekek}
                opciok = list(opcio_map.keys())
                val = osszes_cella[idx].multiselect(col, opciok, placeholder="Keresés...", key=f"{kulcs_prefix}_{col}")
                if val:
                    aktiv_szurok[col] = set(opcio_map[x] for x in val)
            else:
                nyers_lista = [str(v) for v in df[col].tolist() if v is not None and str(v).strip() != "" and str(v) != "None"]
                if col == "Elszámolási hónap":
                    opciok = sorted(list(set(nyers_lista)), key=honap_kulcs, reverse=True)
                else:
                    opciok = sorted(list(set(nyers_lista)))
                val = osszes_cella[idx].multiselect(col, opciok, placeholder="Keresés...", key=f"{kulcs_prefix}_{col}")
                if val:
                    aktiv_szurok[col] = set(val)

        if aktiv_szurok:
            if st.button("🧹 Oszlopszűrők törlése", key=f"{kulcs_prefix}_reset"):
                for col in oszlopok:
                    st.session_state[f"{kulcs_prefix}_{col}"] = list()
                st.rerun()

    if not aktiv_szurok:
        return df, szurt_lista

    szurt_df = df.copy()
    for col, engedelyezett in aktiv_szurok.items():
        if col == "Bruttó összeg (Ft)":
            szurt_df = szurt_df[szurt_df[col].isin(engedelyezett)]
        else:
            szurt_df = szurt_df[szurt_df[col].astype(str).isin(engedelyezett)]

    megmaradt_idk = set(int(x) for x in szurt_df["ID"].tolist())
    uj_lista = [s for s in szurt_lista if int(s["id"]) in megmaradt_idk]
    return szurt_df, uj_lista



def mutat_rogzitett_szamlak():
    st.header("📑 Rögzített számlák")
    szurt_lista, df, osszes_tetel, netto_per_szamla, fogyasztas_per_szamla, h_map, s_map = _betolt_szurt_szamlak("rogz", mutass_energiatipus=True)
    if szurt_lista is None:
        return

    ossz_netto = int(df["Nettó összeg (Ft)"].sum())
    ossz_brutto = int(df["Bruttó összeg (Ft)"].sum())
    utolso_res = supabase.table("szamlak").select("id").order("id", desc=True).limit(1).execute().data or list()
    utolso_id = int(next(iter(utolso_res), {}).get("id") or -1)

    menny_per_me = dict()
    for m_txt in df["Mennyiség"].tolist():
        m_illeszt = re.match(r"^(-?[\d\s]+,\d+)\s*(.*)$", str(m_txt or "").strip())
        if m_illeszt:
            ertek = float(m_illeszt.group(1).replace(" ", "").replace(",", "."))
            egyseg = m_illeszt.group(2).strip()
            if ertek != 0.0 and egyseg:
                menny_per_me[egyseg] = menny_per_me.get(egyseg, 0.0) + ertek
    ossz_menny_str = " | ".join(
        f"{v:,.2f}".replace(",", " ").replace(".", ",") + f" {k}"
        for k, v in menny_per_me.items()
    )

    osszesen_sor = {
        "ID": -1,
        "Számlaszám": "ÖSSZESEN",
        "Elszámolási hónap": f"{len(df)} db számla",
        "Energiatípus": "",
        "Felhasználási hely": "",
        "Szolgáltató": "",
        "Időszak kezdete": "",
        "Időszak vége": "",
        "Mennyiség": ossz_menny_str,
        "Nettó összeg (Ft)": ossz_netto,
        "Bruttó összeg (Ft)": ossz_brutto,
        "Szumma nettó egységár": "",
        "Státusz": "",
        "PDF fájl": ""
    }

    aggrid_aktiv = False
    kiv_szamla_id = None
    try:
        from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode
        aggrid_aktiv = True
    except ImportError:
        aggrid_aktiv = False

    akcio_hely = st.container()

    if aggrid_aktiv:
        gb = GridOptionsBuilder.from_dataframe(df)
        gb.configure_default_column(
            filter=True,
            floatingFilter=True,
            suppressMenuHide=True,
            sortable=True,
            resizable=True,
            filterParams={"buttons": ["reset", "apply"], "closeOnApply": True}
        )
        gb.configure_column("ID", hide=True)
        gb.configure_column("Számlaszám", checkboxSelection=True, headerCheckboxSelection=False, minWidth=160)
        ft_formatter = JsCode("function(params) { return params.value != null ? Number(params.value).toLocaleString('hu-HU') + ' Ft' : ''; }")
        gb.configure_column("Nettó összeg (Ft)", type=["numericColumn"], valueFormatter=ft_formatter)
        gb.configure_column("Bruttó összeg (Ft)", type=["numericColumn"], valueFormatter=ft_formatter)
        gb.configure_selection(selection_mode="single", use_checkbox=True)

        frissit_osszesen_js = JsCode("""
        function(params) {
            let db = 0;
            let nettoOsszeg = 0;
            let bruttoOsszeg = 0;
            let mennyMap = {};
            params.api.forEachNodeAfterFilter(function(node) {
                if (node.data && node.data.ID > 0) {
                    db += 1;
                    nettoOsszeg += Number(node.data['Nettó összeg (Ft)'] || 0);
                    bruttoOsszeg += Number(node.data['Bruttó összeg (Ft)'] || 0);
                    let mStr = String(node.data['Mennyiség'] || '').trim();
                    let mMatch = mStr.match(/^(-?[\\d\\s]+,\\d+)\\s*(.*)$/);
                    if (mMatch) {
                        let val = parseFloat(mMatch[1].replace(/\\s+/g, '').replace(',', '.'));
                        let unit = mMatch[2].trim();
                        if (!isNaN(val) && val !== 0 && unit) {
                            mennyMap[unit] = (mennyMap[unit] || 0) + val;
                        }
                    }
                }
            });
            let mennyReszek = [];
            for (let u in mennyMap) {
                let p = mennyMap[u].toFixed(2).split('.');
                p[0] = p[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
                mennyReszek.push(p.join(',') + ' ' + u);
            }
            params.api.setGridOption('pinnedBottomRowData', [{
                'ID': -1,
                'Számlaszám': 'ÖSSZESEN',
                'Elszámolási hónap': db + ' db számla',
                'Energiatípus': '',
                'Felhasználási hely': '',
                'Szolgáltató': '',
                'Időszak kezdete': '',
                'Időszak vége': '',
                'Mennyiség': mennyReszek.join(' | '),
                'Nettó összeg (Ft)': nettoOsszeg,
                'Bruttó összeg (Ft)': bruttoOsszeg,
                'Szumma nettó egységár': '',
                'Státusz': '',
                'PDF fájl': ''
            }]);
        }
        """)

        gb.configure_grid_options(
            pinnedBottomRowData=[osszesen_sor],
            onFilterChanged=frissit_osszesen_js,
            getRowStyle=JsCode(f"""
            function(params) {{
                if (params.node.rowPinned) {{
                    return {{'font-weight': 'bold', 'background-color': '#f0f2f6'}};
                }}
                if (params.data && Number(params.data.ID) === {utolso_id}) {{
                    return {{'background-color': '#d4edda', 'color': '#155724', 'font-weight': 'bold'}};
                }}
            }}
            """)
        )


        grid_valasz = AgGrid(
            df,
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.SELECTION_CHANGED,
            update_on=["selectionChanged"],
            allow_unsafe_jscode=True,
            fit_columns_on_grid_load=False,
            height=450,
            key="rogzitett_szamlak_aggrid"
        )
        sel = getattr(grid_valasz, "selected_rows", None)
        if sel is None and isinstance(grid_valasz, dict):
            sel = grid_valasz.get("selected_rows")

        if isinstance(sel, pd.DataFrame) and not sel.empty:
            rekord = next(iter(sel.to_dict(orient="records")))
            if int(rekord.get("ID") or -1) > 0:
                kiv_szamla_id = int(rekord["ID"])
        elif isinstance(sel, list) and len(sel) > 0:
            rekord = next(iter(sel))
            if isinstance(rekord, dict) and int(rekord.get("ID") or -1) > 0:
                kiv_szamla_id = int(rekord["ID"])
    else:
        df_megjelenit = pd.concat([df, pd.DataFrame([osszesen_sor])], ignore_index=True)

        def _szinez_sorok(sor):
            if str(sor.get("Számlaszám")) == "ÖSSZESEN":
                return ["font-weight: bold; background-color: #f0f2f6"] * len(sor)
            if int(sor.get("ID") or -1) == utolso_id:
                return ["background-color: #d4edda; color: #155724; font-weight: bold"] * len(sor)
            return [""] * len(sor)

        esemeny = st.dataframe(
            df_megjelenit.style.apply(_szinez_sorok, axis=1),
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={"ID": None, "Bruttó összeg (Ft)": st.column_config.NumberColumn("Bruttó összeg (Ft)", format="%d Ft")},
            key="rogzitett_szamlak_df"
        )
        kiv_sorok = esemeny.selection.rows if esemeny and hasattr(esemeny, "selection") else list()
        elso_idx = next(iter(kiv_sorok), None)
        if elso_idx is not None and elso_idx < len(df):
            kiv_szamla_id = int(df.iloc[elso_idx]["ID"])

    if kiv_szamla_id:
        szamla_map_id = {int(s["id"]): s for s in szurt_lista}
        szamla_obj = szamla_map_id.get(kiv_szamla_id)
        if szamla_obj:
            pdf_ut = szamla_obj.get("pdf_utvonal") or szamla_obj.get("pdf_fajlnev")
            with akcio_hely:
                c_info, c_pdf, c_torol = st.columns((3.2, 1.3, 1.3))
                c_info.markdown(f"**🧾 Kijelölt számla:** `{szamla_obj.get('szamlaszam', '')}` — {h_map.get(szamla_obj.get('felhasznalasi_hely_id'), '')} ({ft_szoveg(szamla_obj.get('szamlan_szereplo_brutto') or 0)})")
                if pdf_ut:
                    if c_pdf.button("📄 PDF megnyitása", type="primary", use_container_width=True, key=f"pdf_btn_{kiv_szamla_id}"):
                        mutat_pdf_ablak(pdf_ut, szamla_obj.get("szamlaszam", ""))
                else:
                    c_pdf.caption("Nincs csatolt PDF")

                if c_torol.button("🗑️ Számla törlése", use_container_width=True, key=f"del_btn_{kiv_szamla_id}"):
                    st.session_state["torles_megerosites_id"] = kiv_szamla_id

                if st.session_state.get("torles_megerosites_id") == kiv_szamla_id:
                    st.warning(f"⚠️ Biztosan törölni szeretnéd a(z) **{szamla_obj.get('szamlaszam', '')}** számlát és minden díjtételét?")
                    tc1, tc2, _ = st.columns((1.3, 1.2, 4))
                    if tc1.button("✅ Igen, végleges törlés", type="primary", key=f"del_yes_{kiv_szamla_id}"):
                        supabase.table("szamla_tetelek").delete().eq("szamla_id", kiv_szamla_id).execute()
                        supabase.table("szamlak").delete().eq("id", kiv_szamla_id).execute()
                        if pdf_ut:
                            try:
                                supabase.storage.from_("szamlak-pdf").remove(list((pdf_ut,)))
                            except Exception:
                                pass
                        st.session_state.pop("torles_megerosites_id", None)
                        st.success("✅ Számla sikeresen törölve!")
                        st.rerun()
                    if tc2.button("❌ Mégse", key=f"del_no_{kiv_szamla_id}"):
                        st.session_state.pop("torles_megerosites_id", None)
                        st.rerun()

            st.divider()
            szamla_tetelek = [t for t in osszes_tetel if int(t["szamla_id"]) == kiv_szamla_id]
            st.divider()
            szamla_tetelek = [t for t in osszes_tetel if int(t["szamla_id"]) == kiv_szamla_id]
            st.subheader(f"📋 Számla díjtételei ({szamla_obj.get('szamlaszam', '')})")
            if szamla_tetelek:
                t_megj = list()
                ossz_netto = 0.0
                ossz_brutto = 0.0
                ossz_fogy = 0.0
                fogy_me = ""
                tartalek_m3 = 0.0

                for t in szamla_tetelek:
                    megn_t = str(t.get("megnevezes_eredeti") or "").strip()
                    megn_low = megn_t.lower()
                    menny_t = float(t.get("mennyiseg") or 0.0)
                    me_t = str(t.get("mertekegyseg") or "").strip()
                    me_low = me_t.lower()
                    ar_t = float(t.get("egysegar_netto") or 0.0)
                    netto_t = float(t.get("szamitott_netto") or 0.0)
                    afa_t = float(t.get("afa_kulcs") or 0.0)
                    brutto_t = float(t.get("szamitott_brutto") or 0.0)

                    ossz_netto += netto_t
                    ossz_brutto += brutto_t

                    if "kompenzáció" not in megn_low:
                        if ("energiadíj" in megn_low or "energia díj" in megn_low or "áramdíj" in megn_low or "földgáz" in megn_low or "gázdíj" in megn_low) and me_low in ("kwh", "m3", "m³", "mj", "gj"):
                            ossz_fogy += menny_t
                            fogy_me = "m3" if me_low in ("m3", "m³") else me_t
                        elif "hődíj" in megn_low and me_low in ("gj", "mj"):
                            ossz_fogy += menny_t
                            fogy_me = me_t
                        elif "ivóvíz" in megn_low and me_low in ("m3", "m³"):
                            ossz_fogy += menny_t
                            fogy_me = "m3"
                        elif ("szennyvíz" in megn_low or "vízfelmelegítési" in megn_low) and me_low in ("m3", "m³"):
                            tartalek_m3 += menny_t

                    menny_szov = f"{menny_t:,.4f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",") if menny_t != 0 else "0"
                    ar_szov = f"{ar_t:,.6f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",") if ar_t != 0 else "0"
                    ar_me_str = f"{ar_szov} Ft/{me_t}" if me_t else f"{ar_szov} Ft"
                    afa_szov = f"{afa_t:.1f}".rstrip("0").rstrip(".").replace(".", ",")

                    t_megj.append({
                        "Megnevezés": megn_t,
                        "Mennyiség": menny_szov,
                        "Mértékegység": me_t,
                        "Nettó egységár": ar_me_str,
                        "Nettó összeg (Ft)": ft_szoveg(int(round(netto_t))),
                        "ÁFA (%)": afa_szov,
                        "Bruttó összeg (Ft)": ft_szoveg(int(round(brutto_t)))
                    })

                if ossz_fogy == 0.0 and tartalek_m3 != 0.0:
                    ossz_fogy = tartalek_m3
                    fogy_me = "m3"

                ossz_menny_str = f"{ossz_fogy:,.2f}".replace(",", " ").replace(".", ",")
                szumma_ea = (ossz_netto / ossz_fogy) if ossz_fogy != 0.0 else 0.0
                szumma_ea_str = f"{szumma_ea:,.2f}".replace(",", " ").replace(".", ",")
                szumma_ea_me = f"{szumma_ea_str} Ft/{fogy_me}" if fogy_me else f"{szumma_ea_str} Ft"

                t_megj.append({
                    "Megnevezés": "ÖSSZESEN",
                    "Mennyiség": ossz_menny_str,
                    "Mértékegység": fogy_me,
                    "Nettó egységár": szumma_ea_me,
                    "Nettó összeg (Ft)": ft_szoveg(int(round(ossz_netto))),
                    "ÁFA (%)": "",
                    "Bruttó összeg (Ft)": ft_szoveg(int(round(ossz_brutto)))
                })

                t_df = pd.DataFrame(t_megj)

                def _stilus_rogz_tetel_ossz(sor):
                    if str(sor.get("Megnevezés") or "").strip() == "ÖSSZESEN":
                        return ["font-weight: bold; background-color: #e6f3ff; color: #0b3d91"] * len(sor)
                    return [""] * len(sor)

                st.dataframe(t_df.style.apply(_stilus_rogz_tetel_ossz, axis=1), use_container_width=True, hide_index=True)

            
def mutat_kimutatasok():
    st.header("📊 Kimutatások és Riportok")
    szurt_lista, df, osszes_tetel, netto_per_szamla, energia_kwh_per_szamla, h_map, _ = _betolt_szurt_szamlak("kim", mutass_energiatipus=True)
    if szurt_lista is None:
        return

    mertekegysegek = set()
    for val in df["Szumma nettó egységár"].dropna():
        if "/" in str(val):
                        mertekegysegek.add(str(val).split("/", 1)[-1].strip())
    egyseg_cimke = next(iter(mertekegysegek)) if len(mertekegysegek) == 1 else "egység"

    ossz_netto_mind = sum(netto_per_szamla.values())
    ossz_kwh_mind = sum(energia_kwh_per_szamla.values())
    atlag_egysegar_str = f"{ossz_netto_mind / ossz_kwh_mind:,.2f} Ft/{egyseg_cimke}".replace(",", " ").replace(".", ",") if ossz_kwh_mind > 0 else "Nincs adat"

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Számlák száma", f"{len(df)} db")
    k2.metric("Összesített bruttó költség", ft_szoveg(df["Bruttó összeg (Ft)"].sum()))
    k3.metric("Átl. szumma nettó egységár", atlag_egysegar_str)
    k4.metric("Érintett hónapok", f"{df['Elszámolási hónap'].nunique()} hónap")

    y_tengely = alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ') + ' Ft'", title="Bruttó összeg (Ft)")
    tab_ho, tab_hely, tab_egysegar, tab_tetel = st.tabs(list(("📅 Havi bontású költségek", "🏭 Telephelyek szerinti összesítés", "📈 Szumma nettó egységár", "⚡ Fogyasztás és Díjtételek")))
    with tab_ho:
        ho_df = df.groupby(["Elszámolási hónap", "Felhasználási hely"], as_index=False)["Bruttó összeg (Ft)"].sum()
        ho_df["Összeg"] = ho_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        ho_sorrend = sorted(list(ho_df["Elszámolási hónap"].unique()), key=honap_kulcs)
        st.altair_chart(alt.Chart(ho_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")), y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely), color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"), xOffset="Felhasználási hely:N", tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]).properties(height=360), use_container_width=True)
    with tab_hely:
        hely_df = df.groupby("Felhasználási hely", as_index=False)["Bruttó összeg (Ft)"].sum()
        hely_df["Összeg"] = hely_df["Bruttó összeg (Ft)"].apply(ft_szoveg)
        st.altair_chart(alt.Chart(hely_df).mark_bar(color="#0068c9").encode(x=alt.X("Felhasználási hely:N", axis=alt.Axis(labelAngle=0, title="Felhasználási hely")), y=alt.Y("Bruttó összeg (Ft):Q", axis=y_tengely), tooltip=[alt.Tooltip("Felhasználási hely:N"), alt.Tooltip("Összeg:N", title="Bruttó összeg")]).properties(height=360), use_container_width=True)
    with tab_egysegar:
        ea_sorok = list({"Elszámolási hónap": tiszta_honap(s.get("elszamolasi_honap")), "Felhasználási hely": h_map.get(s["felhasznalasi_hely_id"], ""), "Nettó": netto_per_szamla.get(s["id"], 0.0), "kWh": energia_kwh_per_szamla.get(s["id"], 0.0)} for s in szurt_lista)
        ea_df = pd.DataFrame(ea_sorok).groupby(["Elszámolási hónap", "Felhasználási hely"], as_index=False)[["Nettó", "kWh"]].sum()
        ea_df = ea_df[ea_df["kWh"] > 0].copy()
        if ea_df.empty:
            st.info("Nincs fogyasztás alapú (kWh vagy m³) díjtétel a kiválasztott számlákban.")
        else:
            oszlop_nev = f"Szumma nettó egységár (Ft/{egyseg_cimke})"
            ea_df[oszlop_nev] = (ea_df["Nettó"] / ea_df["kWh"]).round(2)
            ea_sorrend = sorted(list(ea_df["Elszámolási hónap"].unique()), key=honap_kulcs)
            st.altair_chart(alt.Chart(ea_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ea_sorrend, axis=alt.Axis(labelAngle=0, title="Elszámolási hónap")), y=alt.Y(f"{oszlop_nev}:Q", axis=alt.Axis(title=oszlop_nev)), color=alt.Color("Felhasználási hely:N", title="Felhasználási hely"), xOffset="Felhasználási hely:N", tooltip=[alt.Tooltip("Elszámolási hónap:N"), alt.Tooltip("Felhasználási hely:N"), alt.Tooltip(f"{oszlop_nev}:Q", format=",.2f")]).properties(height=360), use_container_width=True)
    with tab_tetel:
        szamla_map = {s["id"]: s for s in szurt_lista}
        if not osszes_tetel:
            st.info("A kiválasztott számlákhoz nem tartozik rögzített díjtétel.")
        else:
            t_sorok = list({"Elszámolási hónap": tiszta_honap(szamla_map.get(t["szamla_id"], {}).get("elszamolasi_honap")), "Felhasználási hely": h_map.get(szamla_map.get(t["szamla_id"], {}).get("felhasznalasi_hely_id"), ""), "Számlaszám": szamla_map.get(t["szamla_id"], {}).get("szamlaszam", ""), "Díjtétel": t["megnevezes_eredeti"], "Mennyiség": float(t["mennyiseg"]), "Mértékegység": t["mertekegyseg"], "Nettó egységár (Ft)": float(t["egysegar_netto"]), "Nettó összeg (Ft)": int(round(float(t["szamitott_netto"]))), "Bruttó összeg (Ft)": int(round(float(t["szamitott_brutto"])))} for t in osszes_tetel)
            t_df = pd.DataFrame(t_sorok)
            val_tetel = st.selectbox("🔍 Díjtétel kiválasztása:", sorted(list(t_df["Díjtétel"].unique())))
            szurt_t_df = t_df[t_df["Díjtétel"] == val_tetel].copy()
            ho_sorrend_t = sorted(list(szurt_t_df["Elszámolási hónap"].unique()), key=honap_kulcs)
            tc1, tc2 = st.columns(2)
            with tc1:
                st.altair_chart(alt.Chart(szurt_t_df).mark_bar().encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0)), y=alt.Y("sum(Mennyiség):Q", axis=alt.Axis(format=",.0f", labelExpr="replace(datum.label, /,/g, ' ')", title="Mennyiség")), color="Felhasználási hely:N", xOffset="Felhasználási hely:N").properties(height=280), use_container_width=True)
            with tc2:
                st.altair_chart(alt.Chart(szurt_t_df).mark_line(point=True).encode(x=alt.X("Elszámolási hónap:N", sort=ho_sorrend_t, axis=alt.Axis(labelAngle=0)), y=alt.Y("mean(Nettó egységár (Ft)):Q", axis=alt.Axis(title="Nettó egységár (Ft)")), color="Felhasználási hely:N").properties(height=280), use_container_width=True)
            st.dataframe(t_df, use_container_width=True, hide_index=True)



def mutat_szamla_rogzites(user_id):
    fv = st.session_state["form_ver"]
    col_fej, col_gomb = st.columns((4, 1))
    col_fej.header("Energiaszámla ellenőrzése és rögzítése")
    if col_gomb.button("🗑️ Új számla / Ürítés"):
        urit_urlap()
        st.rerun()

    if "utolso_mentes" in st.session_state:
        st.success(st.session_state.pop("utolso_mentes"))
        components.html(
            """
            <script>
                const doc = window.parent.document;
                const targets = [
                    doc.querySelector('[data-testid="stMain"]'),
                    doc.querySelector('[data-testid="stAppViewContainer"]'),
                    doc.querySelector('section.main'),
                    doc.documentElement,
                    doc.body
                ];
                targets.forEach(el => { if (el) el.scrollTop = 0; });
                window.parent.scrollTo(0, 0);
            </script>
            """,
            height=0
        )

    helyek = get_helyek(user_id)
    if not helyek:
        st.warning("Nincs hozzád rendelt felhasználási hely a rendszerben.")
        return

    fajl = st.file_uploader("1. Húzd ide a számlát (PDF vagy Excel/CSV) – opcionális", type=list(("pdf", "xlsx", "csv")), key=f"fajl_{fv}")
    f_id = f"{fv}_{fajl.name}" if fajl else f"{fv}_ures"
    pdf_adat = None
    if fajl and fajl.name.lower().endswith(".pdf"):
        fajl.seek(0)
        pdf_adat = feldolgoz_pdf(fajl)
        if not pdf_adat["szoveg"]:
            st.warning("Szkennelt PDF: a tételeket lent kézzel írd be, a PDF fájlt ugyanúgy elmenti a rendszer.")
        else:
            pod_db = len(pdf_adat.get("pod_adatok") or {})
            if pod_db > 1:
                st.success(f"Gyűjtőszámla feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}** ({pod_db} db mérési pont / POD található benne)")
            else:
                tetel_db = len(pdf_adat["tetelek_df"]) if pdf_adat.get("tetelek_df") is not None else 0
                st.success(f"PDF számla feldolgozva! Számlaszám: **{pdf_adat['szamlaszam']}** ({tetel_db} db díjtétel kinyerve)")

    pod_adatok = (pdf_adat.get("pod_adatok") if pdf_adat else None) or {}
    gyujto_mod = len(pod_adatok) > 1
    kiv_pod = pdf_adat["pod"] if pdf_adat else ""
    kiv_pod_info = None
    meglevo_hely_idk = set()

    if pod_adatok:
        foszamla_br = pdf_adat.get("foszamla_brutto", 0) if pdf_adat else 0
        podok_osszesen = sum(p["brutto"] for p in pod_adatok.values())
        if len(pod_adatok) > 1 and foszamla_br > 0:
            gy_elteres = foszamla_br - podok_osszesen
            g1, g2, g3 = st.columns(3)
            g1.metric("Gyűjtőszámla főösszege", ft_szoveg(foszamla_br))
            g2.metric("POD-ok összesített bruttója", ft_szoveg(podok_osszesen))
            g3.metric("Gyűjtőszámla eltérés", ft_szoveg(gy_elteres))

        hely_pod_nev = {h["pod"]: h["nev"] for h in helyek}
        if pdf_adat and pdf_adat.get("szamlaszam"):
            meglevo = supabase.table("szamlak").select("felhasznalasi_hely_id").eq("szamlaszam", pdf_adat["szamlaszam"].strip()).execute().data or list()
            meglevo_hely_idk = set(x["felhasznalasi_hely_id"] for x in meglevo)
        hely_pod_to_id = {h["pod"]: int(h["id"]) for h in helyek}

        pod_cimkek = dict()
        elso_nem_rogzitett_idx = 0
        talalt_nem_rogzitett = False
        for idx_p, (p_kod, p_info) in enumerate(pod_adatok.items()):
            h_nev = hely_pod_nev.get(p_kod, "Nincs a saját telephelyek között")
            hid_check = hely_pod_to_id.get(p_kod)
            mar_kesz = bool(hid_check and hid_check in meglevo_hely_idk)
            if not mar_kesz and not talalt_nem_rogzitett:
                elso_nem_rogzitett_idx = idx_p
                talalt_nem_rogzitett = True
            stat_jel = " ✅ [Már rögzítve]" if mar_kesz else ""
            pod_cimkek[f"{p_kod} — {h_nev} ({ft_szoveg(p_info['brutto'])}){stat_jel}"] = p_kod

        val_pod_cimke = st.selectbox("📍 Mérési pont (POD) kiválasztása a számlából:", list(pod_cimkek.keys()), index=elso_nem_rogzitett_idx, key=f"pod_sel_{f_id}")
        kiv_pod = pod_cimkek[val_pod_cimke]
        kiv_pod_info = pod_adatok[kiv_pod]

    form_kulcs = f"{f_id}_{kiv_pod}" if kiv_pod else f_id

    hely_opciok = {f"{h['nev']} ({h['pod']}) [{h['jog']}]": int(h["id"]) for h in helyek}
    alap_idx = next((i for i, lbl in enumerate(hely_opciok.keys()) if kiv_pod and kiv_pod in lbl), 0)

    st.subheader("2. Számla alapadatok")
    c1, c2, c3 = st.columns(3)
    v_hely = c1.selectbox("Felhasználási hely", list(hely_opciok.keys()), index=alap_idx, key=f"hely_{form_kulcs}")
    hely_id = hely_opciok[v_hely]
    szamlaszam = c1.text_input("Számlaszám", value=pdf_adat["szamlaszam"] if pdf_adat else "", key=f"szsz_{form_kulcs}")

    szolg_data = supabase.table("szolgaltatok").select("id, nev").order("nev").execute().data or list()
    szolg_opciok = {s["nev"]: int(s["id"]) for s in szolg_data}
    szolg_idx = next((i for i, nev in enumerate(szolg_opciok.keys()) if pdf_adat and pdf_adat.get("szolgaltato_kulcs") and pdf_adat["szolgaltato_kulcs"].lower() in nev.lower()), 0)
    v_szolg = c2.selectbox("Szolgáltató", list(szolg_opciok.keys()), index=szolg_idx, key=f"szolg_{form_kulcs}")
    szolg_id = szolg_opciok[v_szolg]

    br_kulcs = f"br_{form_kulcs}"
    br_forras = kiv_pod_info["brutto"] if kiv_pod_info else (pdf_adat["brutto"] if pdf_adat else 0)
    alap_br_szoveg = f"{int(br_forras):,}".replace(",", " ") if br_forras != 0 else ""

    szamlan_brutto_str = c2.text_input("Számlán szereplő BRUTTÓ összeg (Ft)", value=alap_br_szoveg, placeholder="pl. 6 648", key=br_kulcs, on_change=formaz_ezres, args=(br_kulcs,))
    br_negativ = "-" in szamlan_brutto_str
    br_szamjegyek = re.sub(r"\D", "", szamlan_brutto_str)
    szamlan_brutto = (-int(br_szamjegyek) if br_negativ else int(br_szamjegyek)) if br_szamjegyek else 0


    kelte = pdf_adat["kelte"] if pdf_adat else date.today()
    hatarido = pdf_adat["hatarido"] if pdf_adat else date.today()
    alap_tol = kiv_pod_info["tol"] if kiv_pod_info else (pdf_adat["tol"] if pdf_adat else date.today())
    alap_ig = kiv_pod_info["ig"] if kiv_pod_info else (pdf_adat["ig"] if pdf_adat else date.today())
    tol = c3.date_input("Elszámolási időszak kezdete", value=alap_tol, key=f"t_{form_kulcs}")
    ig = c3.date_input("Elszámolási időszak vége", value=alap_ig, key=f"i_{form_kulcs}")
    datum_rendben = tol <= ig
    if not datum_rendben:
        c3.error("⚠️ Az időszak kezdete nem lehet későbbi, mint a vége!")
    elszamolasi_honap = szamol_elszamolasi_honap(ig)
    c3.info(f"📅 Elszámolási hónap: **{elszamolasi_honap}**")
    st.subheader("3. Díjtételek ellenőrzése (kattints duplán a cellákba az íráshoz)")
    alap_df = pd.DataFrame(dict(Megnevezés=["Energiadíj", "Átviteli forgalmi díj", "Elosztói forgalmi díj", "Alapdíj"], Mennyiség=[0.0, 0.0, 0.0, 0.0], Mértékegység=["kWh", "kWh", "kWh", "hó"], Nettó_egységár=[0.0, 0.0, 0.0, 0.0], ÁFA=[27.0, 27.0, 27.0, 27.0]))
    alap_df.columns = list(("Megnevezés", "Mennyiség", "Mértékegység", "Nettó egységár", "ÁFA (%)"))
    if kiv_pod_info and kiv_pod_info.get("tetelek_df") is not None:
        alap_df = kiv_pod_info["tetelek_df"].copy()
    elif pdf_adat and pdf_adat.get("tetelek_df") is not None:
        alap_df = pdf_adat["tetelek_df"].copy()
    elif fajl and fajl.name.lower().endswith(".xlsx"):
        alap_df = pd.read_excel(fajl)
    elif fajl and fajl.name.lower().endswith(".csv"):
        alap_df = pd.read_csv(fajl)

    komp_sor = pd.DataFrame([{"Megnevezés": "Bruttó díj kompenzáció", "Mennyiség": None, "Mértékegység": "Ft", "Nettó egységár": None, "ÁFA (%)": None}])
    alap_df = pd.concat([alap_df, komp_sor], ignore_index=True)


    fix_brutto_nevek = ("Fizetési felszólítás díja", "Késedelmi kamat/ÁHK", "Bruttó díj kompenzáció")

    def _frissit_tetel_osszegek(df_be):
        normal_sorok = list()
        fix_sorok_map = {
            nev: {"Megnevezés": nev, "Mennyiség": None, "Mértékegység": "Ft", "Nettó egységár": None, "ÁFA (%)": None, "Nettó összeg (Ft)": "", "Bruttó összeg (Ft)": "0 Ft"}
            for nev in fix_brutto_nevek
        }

        for _, sor in df_be.iterrows():
            nev_s = str(sor.get("Megnevezés") or "").strip()
            if nev_s == "ÖSSZESEN":
                continue
            if nev_s in fix_sorok_map:
                fix_sorok_map[nev_s] = sor.to_dict()
            else:
                normal_sorok.append(sor.to_dict())

        rendezett_lista = normal_sorok + [fix_sorok_map[nev] for nev in fix_brutto_nevek]
        df_ki = pd.DataFrame(rendezett_lista)

        nettos = list()
        bruttos = list()
        komp_idx = None
        komp_val = 0
        egyeb_brutto_ossz = 0
        mas_netto_ossz = Decimal("0")
        mas_brutto_ossz = Decimal("0")

        ossz_fogy = 0.0
        fogy_me = ""
        tartalek_m3 = 0.0
        elso_fogy = 0.0
        elso_me = ""

        for idx_r, sor in df_ki.iterrows():
            megn_str = str(sor.get("Megnevezés") or "").strip()
            if megn_str in fix_brutto_nevek:
                df_ki.at[idx_r, "Mennyiség"] = None
                df_ki.at[idx_r, "Mértékegység"] = "Ft"
                df_ki.at[idx_r, "Nettó egységár"] = None
                df_ki.at[idx_r, "ÁFA (%)"] = None
                br_nyers = str(sor.get("Bruttó összeg (Ft)") or "")
                negativ = "-" in br_nyers
                szamjegyek = re.sub(r"\D", "", br_nyers)
                sor_br_val = (-int(szamjegyek) if negativ else int(szamjegyek)) if szamjegyek else 0
                nettos.append("")
                if megn_str == "Bruttó díj kompenzáció":
                    komp_val = sor_br_val
                    komp_idx = len(bruttos)
                else:
                    egyeb_brutto_ossz += sor_br_val
                bruttos.append(ft_szoveg(sor_br_val))
                continue

            m_v = sor.get("Mennyiség")
            a_v = sor.get("Nettó egységár")
            if m_v is None or a_v is None or pd.isna(m_v) or pd.isna(a_v):
                nettos.append("0 Ft")
                bruttos.append("0 Ft")
                continue

            menny_d = Decimal(str(m_v))
            ar_d = Decimal(str(a_v))
            afa_v = sor.get("ÁFA (%)")
            afa_d = Decimal(str(afa_v)) if (afa_v is not None and not pd.isna(afa_v)) else Decimal("27.0")
            n_val = (menny_d * ar_d).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            b_val = (n_val * (Decimal("1") + afa_d / Decimal("100"))).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            mas_netto_ossz += n_val
            mas_brutto_ossz += b_val
            nettos.append(ft_szoveg(n_val))
            bruttos.append(ft_szoveg(b_val))

            megn_low = megn_str.lower()
            me_nyers = str(sor.get("Mértékegység") or "").strip()
            me_low = me_nyers.lower()
            menny_f = float(menny_d)
            if ("energiadíj" in megn_low or "energia díj" in megn_low or "áramdíj" in megn_low or "földgáz" in megn_low or "gázdíj" in megn_low) and me_low in ("kwh", "m3", "m³", "mj", "gj"):
                ossz_fogy += menny_f
                fogy_me = "m3" if me_low in ("m3", "m³") else me_nyers
            elif "hődíj" in megn_low and me_low in ("gj", "mj"):
                ossz_fogy += menny_f
                fogy_me = me_nyers
            elif "ivóvíz" in megn_low and me_low in ("m3", "m³"):
                ossz_fogy += menny_f
                fogy_me = "m3"
            elif ("szennyvíz" in megn_low or "vízfelmelegítési" in megn_low) and me_low in ("m3", "m³"):
                tartalek_m3 += menny_f

            if elso_fogy == 0.0 and me_low in ("kwh", "m3", "m³", "gj", "mj") and menny_f != 0.0:
                elso_fogy = menny_f
                elso_me = "m3" if me_low in ("m3", "m³") else me_nyers

        if ossz_fogy == 0.0 and tartalek_m3 != 0.0:
            ossz_fogy = tartalek_m3
            fogy_me = "m3"
        if ossz_fogy == 0.0 and elso_fogy != 0.0:
            ossz_fogy = elso_fogy
            fogy_me = elso_me

        vegso_brutto = mas_brutto_ossz + Decimal(egyeb_brutto_ossz) + Decimal(komp_val)
        if komp_idx is not None and szamlan_brutto != 0:
            akt_elt = Decimal(szamlan_brutto) - vegso_brutto
            if akt_elt != Decimal("0"):
                bruttos[komp_idx] = f"🔴 {ft_szoveg(komp_val)}"
            elif komp_val != 0:
                bruttos[komp_idx] = f"🟢 {ft_szoveg(komp_val)}"

        szumma_egysegar = round(float(mas_netto_ossz) / ossz_fogy, 2) if ossz_fogy != 0.0 else 0
        ossz_menny_cella = round(ossz_fogy, 2) if ossz_fogy != 0.0 else 0

        osszesen_sor = pd.DataFrame([{
            "Megnevezés": "ÖSSZESEN",
            "Mennyiség": ossz_menny_cella,
            "Mértékegység": fogy_me,
            "Nettó egységár": szumma_egysegar,
            "ÁFA (%)": None
        }])
        df_ki = pd.concat([df_ki, osszesen_sor], ignore_index=True)
        nettos.append(ft_szoveg(mas_netto_ossz))
        bruttos.append(ft_szoveg(vegso_brutto))

        df_ki["Nettó összeg (Ft)"] = nettos
        df_ki["Bruttó összeg (Ft)"] = bruttos
        return df_ki

    df_state_kulcs = f"tetel_tabla_{form_kulcs}"
    if df_state_kulcs not in st.session_state:
        st.session_state[df_state_kulcs] = _frissit_tetel_osszegek(alap_df)
    else:
        st.session_state[df_state_kulcs] = _frissit_tetel_osszegek(st.session_state[df_state_kulcs])

    if st.button("➕ Új díjtétel sor", key=f"add_row_{form_kulcs}"):
        uj_sor = pd.DataFrame([{"Megnevezés": "Új díjtétel", "Mennyiség": 0.0, "Mértékegység": "kWh", "Nettó egységár": 0.0, "ÁFA (%)": 27.0}])
        st.session_state[df_state_kulcs] = _frissit_tetel_osszegek(pd.concat([st.session_state[df_state_kulcs], uj_sor], ignore_index=True))
        st.rerun()

    akt_tabla_df = st.session_state[df_state_kulcs]
    display_df = akt_tabla_df.astype(object).where(pd.notnull(akt_tabla_df), None)

    try:
        from st_aggrid import AgGrid, GridOptionsBuilder, GridUpdateMode, JsCode

        szerkesztheto_js = JsCode("""
        function(params) {
            if (!params.data) return false;
            let nev = params.data.Megnevezés;
            if (nev === 'ÖSSZESEN') return false;
            if (nev === 'Fizetési felszólítás díja' || nev === 'Késedelmi kamat/ÁHK' || nev === 'Bruttó díj kompenzáció') {
                return params.colDef.field === 'Bruttó összeg (Ft)';
            }
            return true;
        }
        """)
        gb = GridOptionsBuilder.from_dataframe(display_df)
        gb.configure_default_column(editable=szerkesztheto_js, sortable=False, filter=False, resizable=True)
        gb.configure_column("Nettó összeg (Ft)", editable=False)
        gb.configure_column(
            "Bruttó összeg (Ft)",
            valueFormatter=JsCode("""
            function(params) {
                if (params.value === null || params.value === undefined || params.value === '') {
                    return '0 Ft';
                }
                let raw = String(params.value);
                let prefix = '';
                if (raw.indexOf('🔴') !== -1) prefix = '🔴 ';
                if (raw.indexOf('🟢') !== -1) prefix = '🟢 ';
                let isNeg = raw.indexOf('-') !== -1;
                let digits = raw.replace(/\\D/g, '');
                if (!digits) return prefix + '0 Ft';
                let numStr = String(Number(digits)).replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
                return prefix + (isNeg && Number(digits) !== 0 ? '-' : '') + numStr + ' Ft';
            }
            """)
        )

        gb.configure_column(
            "Mennyiség",
            valueFormatter=JsCode("""
            function(params) {
                if (params.value === null || params.value === undefined || params.value === '') {
                    return '';
                }
                let num = Number(params.value);
                if (isNaN(num)) {
                    return params.value;
                }
                let isOsszesen = (params.data && params.data.Megnevezés === 'ÖSSZESEN');
                let reszek = isOsszesen ? num.toFixed(2).split('.') : String(Number(num.toFixed(4))).split('.');
                reszek[0] = reszek[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
                return reszek.join(',');
            }
            """)
        )
        gb.configure_column(
            "Nettó egységár",
            valueFormatter=JsCode("""
            function(params) {
                if (params.value === null || params.value === undefined || params.value === '') {
                    return '';
                }
                let num = Number(params.value);
                if (isNaN(num)) {
                    return params.value;
                }
                let isOsszesen = (params.data && params.data.Megnevezés === 'ÖSSZESEN');
                let reszek = isOsszesen ? num.toFixed(2).split('.') : String(Number(num.toFixed(6))).split('.');
                reszek[0] = reszek[0].replace(/\\B(?=(\\d{3})+(?!\\d))/g, ' ');
                let formazott = reszek.join(',');
                let me = (params.data && params.data['Mértékegység']) ? String(params.data['Mértékegység']).trim() : '';
                return me ? (formazott + ' Ft/' + me) : (formazott + ' Ft');
            }
            """)
        )
        gb.configure_grid_options(
            stopEditingWhenCellsLoseFocus=True,
            getRowStyle=JsCode("""
            function(params) {
                if (params.data && params.data.Megnevezés === 'ÖSSZESEN') {
                    return {'font-weight': 'bold', 'background-color': '#e6f3ff', 'color': '#0b3d91'};
                }
            }
            """)
        )

        tabla_magassag = min(650, len(display_df) * 29 + 46)
        grid_res = AgGrid(
            display_df,
            gridOptions=gb.build(),
            update_mode=GridUpdateMode.VALUE_CHANGED,
            reload_data=True,
            allow_unsafe_jscode=True,
            fit_columns_on_grid_load=True,
            height=tabla_magassag,
            key=f"ag_ed_{form_kulcs}"
        )


        nyers_data = getattr(grid_res, "data", None)
        if nyers_data is None and isinstance(grid_res, dict):
            nyers_data = grid_res.get("data")
        szerk_df = pd.DataFrame(nyers_data) if nyers_data is not None else akt_tabla_df
    except ImportError:
        szerk_df = st.data_editor(
            akt_tabla_df,
            num_rows="fixed",
            use_container_width=True,
            disabled=list(("Nettó összeg (Ft)",)),
            key=f"ed_{form_kulcs}"
        )


    ujraszamolt_df = _frissit_tetel_osszegek(szerk_df)
    regi_parok = list(zip(
        akt_tabla_df["Mennyiség"].fillna(-999999),
        akt_tabla_df["Nettó egységár"].fillna(-999999),
        akt_tabla_df["Nettó összeg (Ft)"],
        akt_tabla_df["Bruttó összeg (Ft)"]
    ))
    uj_parok = list(zip(
        ujraszamolt_df["Mennyiség"].fillna(-999999),
        ujraszamolt_df["Nettó egységár"].fillna(-999999),
        ujraszamolt_df["Nettó összeg (Ft)"],
        ujraszamolt_df["Bruttó összeg (Ft)"]
    ))
    if regi_parok != uj_parok:
        st.session_state[df_state_kulcs] = ujraszamolt_df
        st.rerun()

    ossz_netto, ossz_brutto, tetelek = szamol_tetelek(ujraszamolt_df)




    brutto_dec = Decimal(szamlan_brutto)
    elteres = brutto_dec - ossz_brutto

    m1, m2, m3 = st.columns(3)
    m1.metric("Számított Nettó", ft_szoveg(ossz_netto))
    m2.metric("Számított Bruttó", ft_szoveg(ossz_brutto))
    m3.metric("Eltérés a számlától", ft_szoveg(elteres))

    egyezik = (elteres == Decimal("0")) and (len(tetelek) > 0) and (szamlan_brutto != 0) and datum_rendben
    if egyezik: st.success("✅ A számított bruttó összeg megegyezik a számlán szereplővel!")
    if not egyezik: st.warning("A mentés gomb akkor válik aktívvá, ha az eltérés pontosan 0 Ft (írd be a különbséget a Bruttó díj kompenzáció sorba) és a dátumok helyesek.")


    mentes_fajl = fajl if (fajl and fajl.name.lower().endswith(".pdf")) else None
    gomb = st.button("💾 PDF feltöltése és Számla mentése az adatbázisba", type="primary", disabled=not egyezik)
    forras = "PDF" if pdf_adat else ("TABLAZAT" if fajl else "KEZI")
    stat = "JO" if elteres == Decimal("0") else "KEREKITESI_ELTERES"
    uj_szamla = {"szamlaszam": szamlaszam, "szolgaltato_id": szolg_id, "felhasznalasi_hely_id": hely_id, "forras_tipus": forras, "szamla_kelte": kelte.isoformat(), "fizetesi_hatarido": hatarido.isoformat(), "idoszak_tol": tol.isoformat(), "idoszak_ig": ig.isoformat(), "elszamolasi_honap": elszamolasi_honap, "szamlan_szereplo_brutto": float(brutto_dec), "szamitott_brutto": float(ossz_brutto), "elteres": float(elteres), "ellenorzes_statusz": stat, "jovahagyta_energetikus_id": user_id}
    minden_pod_kesz = (not gyujto_mod) or (len(meglevo_hely_idk | {hely_id}) >= len(pod_adatok))
    if gomb and egyezik:
        mentes_adatbazisba(uj_szamla, tetelek, mentes_fajl, gyujto_marad=not minden_pod_kesz)

st.sidebar.title("⚡ Energia Portál")
users_data = supabase.table("energetikusok").select("id, nev, szerepkor").eq("aktiv", True).order("nev").execute().data or list()
users_map = {u["nev"]: u for u in users_data}
valasztott_nev = st.sidebar.selectbox("Belépett energetikus:", list(users_map.keys()))
user_sor = users_map[valasztott_nev]
st.session_state["akt_user_id"] = int(user_sor["id"])
st.sidebar.info(f"Szerepkör: **{user_sor['szerepkor']}**")


menu = st.sidebar.radio("Menü", list(("🧾 Számla ellenőrzés és feltöltés", "📑 Rögzített számlák", "📊 Kimutatások és Riportok", "🔄 Helyettesítések", "⚙️ Törzsadatok")), on_change=urit_urlap)
if menu == "🧾 Számla ellenőrzés és feltöltés":
    mutat_szamla_rogzites(int(user_sor["id"]))
elif menu == "📑 Rögzített számlák":
    mutat_rogzitett_szamlak()
elif menu == "📊 Kimutatások és Riportok":
    mutat_kimutatasok()
elif menu == "🔄 Helyettesítések":
    mutat_helyettesitesek()
else:
    mutat_torzsadatok()
