import streamlit as st
import requests
import numpy as np
import unicodedata
import re
import difflib
import csv
import os
import json
from datetime import datetime, timedelta

# ============================================================
# STREAMLIT SHARP FOOTBALL MONTE CARLO WEB APP (FIXED JSON & UI)
# ============================================================

st.set_page_config(
    page_title="Sharp Football Monte Carlo Predictor",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

ITERATIONS = 100_000  
HISTORY_FILE = "football_predictions_history.csv"
MIN_ODDS = 1.60
MAX_ODDS = 3.50
MIN_EV = 3.0  
MIN_PROB_TO_SAVE = 60.0  
KELLY_FRACTION = 0.25  
MAX_STAKE_PERCENT = 2.0  
MAX_DEVIATION_PCT = 15.0 
LAST_GAMES = 8  
SEARCH_DAYS = 90  

LEAGUES = {
    "Ελλάδα - Super League": {"code": "gre.1", "cutoff": datetime(2026, 7, 1).date()},
    "Αγγλία - Premier League": {"code": "eng.1", "cutoff": datetime(2026, 7, 1).date()},
    "Ισπανία - La Liga": {"code": "esp.1", "cutoff": datetime(2026, 7, 1).date()},
    "Ιταλία - Serie A": {"code": "ita.1", "cutoff": datetime(2026, 7, 1).date()},
    "Γερμανία - Bundesliga": {"code": "ger.1", "cutoff": datetime(2026, 7, 1).date()},
    "Γαλλία - Ligue 1": {"code": "fra.1", "cutoff": datetime(2026, 7, 1).date()},
    "Βέλγιο - Pro League": {"code": "bel.1", "cutoff": datetime(2026, 7, 1).date()},
    "Ολλανδία - Eredivisie": {"code": "ned.1", "cutoff": datetime(2026, 7, 1).date()},
    "Νορβηγία - Eliteserien": {"code": "nor.1", "cutoff": datetime(2026, 7, 1).date()},
    "Champions League": {"code": "uefa.champions", "cutoff": datetime(2026, 6, 1).date()},
    "Europa League": {"code": "uefa.europa", "cutoff": datetime(2026, 6, 1).date()},
    "Conference League": {"code": "uefa.europa.conf", "cutoff": datetime(2026, 6, 1).date()}
}

ALIASES = {
    "παο": "Panathinaikos", "pao": "Panathinaikos", "παναθηναικος": "Panathinaikos",
    "αεκ": "AEK Athens", "aek": "AEK Athens",
    "ολυμπιακος": "Olympiacos", "οσφπ": "Olympiacos", "olympiacos": "Olympiacos",
    "παοκ": "PAOK", "paok": "PAOK",
    "αρης": "Aris", "aris": "Aris",
    "cska sofia": "CSKA Sofia", "τσσκα σοφιας": "CSKA Sofia",
    "monaco": "AS Monaco", "μονακο": "AS Monaco",
    "man united": "Manchester United", "man utd": "Manchester United",
    "man city": "Manchester City", "arsenal": "Arsenal", "liverpool": "Liverpool", "chelsea": "Chelsea",
    "real madrid": "Real Madrid", "barcelona": "Barcelona", "barca": "Barcelona",
    "inter": "Inter Milan", "milan": "AC Milan", "juventus": "Juventus", "napoli": "Napoli",
    "bayern": "Bayern Munich", "dortmund": "Borussia Dortmund", "psg": "Paris Saint-Germain"
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0 Safari/537.36",
    "Accept": "application/json"
})

def normalize(text):
    if text is None: return ""
    text = str(text).lower().strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^a-zA-Z0-9α-ωΑ-Ω\s]", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def api_get(url, params=None):
    try:
        response = session.get(url, params=params, timeout=5)
        if response.status_code == 200: return response.json()
    except Exception: pass
    return None

def resolve_input_name(user_input):
    value = normalize(user_input)
    return ALIASES.get(value, user_input.strip())

def team_matches(input_name, api_name):
    a, b = normalize(input_name), normalize(api_name)
    if a == b: return True
    if a in ALIASES and normalize(ALIASES[a]) == b: return True
    if len(a) >= 3 and (a in b or b in a): return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.70

@st.cache_data(ttl=3600)
def get_matches_for_date(league_code, target_date):
    date_str = target_date.strftime("%Y%m%d")
    codes_to_try = [league_code]
    if league_code == "uefa.europa.conf": codes_to_try.extend(["uefa.europa.conf_qual", "uefa.echl"])
    matches = []
    for code in codes_to_try:
        url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{code}/scoreboard"
        data = api_get(url, params={"dates": date_str, "limit": 100})
        if data and data.get("events"):
            for event in data.get("events", []):
                comps = event.get("competitions", [])
                if not comps: continue
                competitors = comps[0].get("competitors", [])
                if len(competitors) < 2: continue
                home = next((c for c in competitors if c.get("homeAway") == "home"), None)
                away = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if home and away:
                    matches.append({
                        "home": home.get("team", {}).get("displayName", home.get("team", {}).get("name", "")),
                        "away": away.get("team", {}).get("displayName", away.get("team", {}).get("name", ""))
                    })
        if matches: break
    return matches

def find_team_stats(league_info, team_name, target_date=None):
    league_code = league_info["code"]
    resolved = resolve_input_name(team_name)
    search_leagues = [league_code]
    if "uefa" in league_code: search_leagues.extend(["gre.1", "eng.1", "esp.1", "ita.1", "ger.1", "fra.1"])
    games = []
    seen_ids = set()
    today = target_date or datetime.now().date()
    cutoff_date = today - timedelta(days=SEARCH_DAYS)
    last_match_date = None
    official_name = resolved

    for l_code in search_leagues:
        if len(games) >= LAST_GAMES: break
        for days_back in range(SEARCH_DAYS):
            if len(games) >= LAST_GAMES: break
            curr_date = today - timedelta(days=days_back + 1)
            if curr_date < cutoff_date: break
            s_data = api_get(f"https://site.api.espn.com/apis/site/v2/sports/soccer/{l_code}/scoreboard", params={"dates": curr_date.strftime("%Y%m%d"), "limit": 50})
            if not s_data or not s_data.get("events"): continue
            for event in s_data.get("events", []):
                comps = event.get("competitions", [])
                if not comps: continue
                comp = comps[0]
                competitors = comp.get("competitors", [])
                if len(competitors) < 2: continue
                h = next((c for c in competitors if c.get("homeAway") == "home"), None)
                a = next((c for c in competitors if c.get("homeAway") == "away"), None)
                if not h or not a: continue
                h_name = h.get("team", {}).get("displayName", h.get("team", {}).get("name", ""))
                a_name = a.get("team", {}).get("displayName", a.get("team", {}).get("name", ""))
                if team_matches(resolved, h_name) or team_matches(resolved, a_name):
                    status = comp.get("status", {}).get("type", {}).get("completed", False)
                    if status and h.get("score") is not None and a.get("score") is not None:
                        gid = event.get("id")
                        if gid and gid not in seen_ids:
                            seen_ids.add(gid)
                            if last_match_date is None: last_match_date = curr_date
                            if team_matches(resolved, h_name): official_name = h_name
                            elif team_matches(resolved, a_name): official_name = a_name
                            games.append({
                                "home": h_name, "away": a_name,
                                "home_score": int(h.get("score")), "away_score": int(a.get("score"))
                            })
            if len(games) >= LAST_GAMES: break
        if len(games) >= LAST_GAMES: break

    if not games:
        return {"name": official_name, "avg_gf": 1.35, "avg_ga": 1.15, "rest_days": 7, "sample_size": 0}

    gf_values, ga_values, weights = [], [], []
    for index, g in enumerate(games):
        is_home = team_matches(official_name, g["home"])
        gf_values.append(g["home_score"] if is_home else g["away_score"])
        ga_values.append(g["away_score"] if is_home else g["home_score"])
        weights.append(0.88 ** index)

    played = len(games)
    avg_gf = float(np.average(gf_values, weights=weights))
    avg_ga = float(np.average(ga_values, weights=weights))
    rest_days = (today - last_match_date).days if last_match_date else 7

    return {"name": official_name, "avg_gf": avg_gf, "avg_ga": avg_ga, "rest_days": rest_days, "sample_size": played}

def save_prediction(row):
    fields = ["created_at", "date", "league_code", "home_team", "away_team", "market", "odds", "prob", "ev", "stake_units", "status", "home_score", "away_score"]
    exists = os.path.exists(HISTORY_FILE)
    try:
        with open(HISTORY_FILE, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            if not exists: writer.writeheader()
            writer.writerow({key: row.get(key, "") for key in fields})
    except Exception as e:
        st.error(f"[!] Σφάλμα αποθήκευσης ιστορικού: {e}")

def evaluate_bet_win(b):
    try:
        hs = int(b["home_score"])
        as_ = int(b["away_score"])
    except Exception:
        return False
    market = b["market"]
    if market == "Νίκη Γηπεδούχου (1)": return hs > as_
    elif market == "Ισοπαλία (X)": return hs == as_
    elif market == "Νίκη Φιλοξενουμένου (2)": return hs < as_
    elif market == "Over 1.5": return (hs + as_) > 1.5
    elif market == "Over 2.5": return (hs + as_) > 2.5
    elif market == "Over 3.5": return (hs + as_) > 3.5
    elif market == "Goal/Goal (GG)": return hs > 0 and as_ > 0
    return False

# --- STREAMLIT UI ---
st.title("⚽ Sharp Football Monte Carlo Predictor")
st.markdown("Προσομοιώσεις Poisson Monte Carlo για ποδοσφαιρικούς αγώνες, επιλογή Value Bets με checkboxes και παρακολούθηση ROI/Μπάνκας.")

# Sidebar settings
st.sidebar.header("⚙️ Ρυθμίσεις & Επιλογή Αγώνα")
league_name = st.sidebar.selectbox("Επιλογή Διοργάνωσης:", list(LEAGUES.keys()))
league = LEAGUES[league_name]

target_date = st.sidebar.date_input("Ημερομηνία Αγώνα", datetime.now().date())
target_date_str = target_date.strftime("%Y-%m-%d")

if "prev_league_f" not in st.session_state or st.session_state["prev_league_f"] != league_name:
    st.session_state["prev_league_f"] = league_name
    if "last_sim_data_f" in st.session_state: del st.session_state["last_sim_data_f"]

matches = get_matches_for_date(league["code"], target_date)
match_options = [f"{m['home']} vs {m['away']}" for m in matches] if matches else []
match_options.append("➕ Χειροκίνητη Εισαγωγή Ομάδων")

selected_match_str = st.sidebar.selectbox("Επιλογή Αγώνα:", match_options)

home_name, away_name = "", ""
if selected_match_str != "➕ Χειροκίνητη Εισαγωγή Ομάδων" and matches:
    idx = match_options.index(selected_match_str)
    home_name = matches[idx]["home"]
    away_name = matches[idx]["away"]
else:
    home_name = st.sidebar.text_input("Γηπεδούχος Ομάδα", "Olympiacos")
    away_name = st.sidebar.text_input("Φιλοξενούμενη Ομάδα", "Panathinaikos")

if st.sidebar.button("🔄 Καθαρισμός / Ανανέωση"):
    if "last_sim_data_f" in st.session_state: del st.session_state["last_sim_data_f"]
    st.rerun()

if home_name and away_name:
    with st.spinner("Ανάκτηση πρόσφατων στατιστικών από API..."):
        home_stats = find_team_stats(league, home_name, target_date)
        away_stats = find_team_stats(league, away_name, target_date)

    st.sidebar.markdown("---")
    st.sidebar.subheader("📊 Στατιστικά Ομάδων")
    st.sidebar.markdown(f"**🏠 {home_stats['name']}**\n- M.O. Υπέρ: `{home_stats['avg_gf']:.2f}` | Κατά: `{home_stats['avg_ga']:.2f}`\n- Ξεκούραση: `{home_stats['rest_days']} μέρες`")
    st.sidebar.markdown(f"**✈️ {away_stats['name']}**\n- M.O. Υπέρ: `{away_stats['avg_gf']:.2f}` | Κατά: `{away_stats['avg_ga']:.2f}`\n- Ξεκούραση: `{away_stats['rest_days']} μέρες`")

    st.sidebar.markdown("---")
    st.sidebar.subheader("💰 Αποδόσεις Bookmakers")
    odds = {
        '1': st.sidebar.number_input("Απόδοση 1 (Νίκη)", value=2.00, min_value=1.01, step=0.01),
        'X': st.sidebar.number_input("Απόδοση X (Ισοπαλία)", value=3.20, min_value=1.01, step=0.01),
        '2': st.sidebar.number_input("Απόδοση 2 (Νίκη)", value=3.60, min_value=1.01, step=0.01),
        'over15': st.sidebar.number_input("Over 1.5", value=1.35, min_value=1.01, step=0.01),
        'over25': st.sidebar.number_input("Over 2.5", value=2.05, min_value=1.01, step=0.01),
        'over35': st.sidebar.number_input("Over 3.5", value=3.50, min_value=1.01, step=0.01),
        'gg': st.sidebar.number_input("Goal/Goal (GG)", value=1.85, min_value=1.01, step=0.01),
    }

    if st.button("🚀 Εκτέλεση Προσομοίωσης Poisson") or st.session_state.get("sim_run_f", False):
        st.session_state["sim_run_f"] = True
        
        if "last_sim_data_f" not in st.session_state:
            with st.spinner(f"Εκτέλεση {ITERATIONS:,} προσομοιώσεων... Παρακαλώ περιμένετε."):
                home_rest_mult = 0.95 if home_stats["rest_days"] < 4 else (1.03 if home_stats["rest_days"] >= 7 else 1.0)
                away_rest_mult = 0.93 if away_stats["rest_days"] < 4 else (1.03 if away_stats["rest_days"] >= 7 else 1.0)

                lam_home = max(((home_stats["avg_gf"] + away_stats["avg_ga"]) / 2) * 1.10 * home_rest_mult, 0.05)
                lam_away = max(((away_stats["avg_gf"] + home_stats["avg_ga"]) / 2) * 0.95 * away_rest_mult, 0.05)

                np.random.seed(42)
                hg = np.random.poisson(lam_home, ITERATIONS)
                ag = np.random.poisson(lam_away, ITERATIONS)

                p_home = np.mean(hg > ag) * 100
                p_draw = np.mean(hg == ag) * 100
                p_away = np.mean(ag > hg) * 100

                p_btts = np.mean((hg > 0) & (ag > 0)) * 100
                p_over15 = np.mean((hg + ag) > 1.5) * 100
                p_over25 = np.mean((hg + ag) > 2.5) * 100
                p_over35 = np.mean((hg + ag) > 3.5) * 100

                st.session_state["last_sim_data_f"] = {
                    "p_home": p_home, "p_draw": p_draw, "p_away": p_away,
                    "p_btts": p_btts, "p_over15": p_over15, "p_over25": p_over25, "p_over35": p_over35
                }

        sim = st.session_state["last_sim_data_f"]

        st.success("Η προσομοίωση ολοκληρώθηκε!")

        col1, col2, col3 = st.columns(3)
        col1.metric(f"Νίκη {home_stats['name']} (1)", f"{sim['p_home']:.1f}%")
        col2.metric("Ισοπαλία (X)", f"{sim['p_draw']:.1f}%")
        col3.metric(f"Νίκη {away_stats['name']} (2)", f"{sim['p_away']:.1f}%")

        st.markdown("---")
        st.subheader("📊 Πιθανότητες Γκολ & Goal/Goal")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Over 1.5", f"{sim['p_over15']:.1f}%")
        c2.metric("Over 2.5", f"{sim['p_over25']:.1f}%")
        c3.metric("Over 3.5", f"{sim['p_over35']:.1f}%")
        c4.metric("Goal/Goal", f"{sim['p_btts']:.1f}%")

        st.markdown("---")
        st.subheader("💡 Επιλογή Value Bets & Αποθήκευση στο Ιστορικό")

        raw_odds_1x2 = [odds.get('1'), odds.get('X'), odds.get('2')]
        fair_probs_1x2 = [None, None, None]
        if all(o is not None and o > 1 for o in raw_odds_1x2):
            implied_probs = [1.0 / o for o in raw_odds_1x2]
            total_prob = sum(implied_probs)
            fair_probs_1x2 = [(p / total_prob) * 100 for p in implied_probs]

        markets = [
            ("Νίκη Γηπεδούχου (1)", sim['p_home'], odds.get('1'), fair_probs_1x2[0]),
            ("Ισοπαλία (X)", sim['p_draw'], odds.get('X'), fair_probs_1x2[1]),
            ("Νίκη Φιλοξενουμένου (2)", sim['p_away'], odds.get('2'), fair_probs_1x2[2]),
            ("Over 1.5", sim['p_over15'], odds.get('over15'), None),
            ("Over 2.5", sim['p_over25'], odds.get('over25'), None),
            ("Over 3.5", sim['p_over35'], odds.get('over35'), None),
            ("Goal/Goal (GG)", sim['p_btts'], odds.get('gg'), None)
        ]

        valid_value_bets = []
        for market, prob, odd, fair_prob in markets:
            if odd is not None and MIN_ODDS <= odd <= MAX_ODDS and 0 < prob < 100:
                if fair_prob is not None and abs(prob - fair_prob) > MAX_DEVIATION_PCT:
                    continue

                val = ((prob / 100) * odd - 1) * 100
                b = odd - 1
                p = prob / 100
                q = 1 - p
                kelly_full = (b * p - q) / b if b > 0 else 0
                kelly_stake = min(max(kelly_full * KELLY_FRACTION * 100, 0), MAX_STAKE_PERCENT)

                if val >= MIN_EV:
                    valid_value_bets.append({
                        "market": market, "prob": prob, "odd": odd, "ev": val, "stake": kelly_stake
                    })

        if valid_value_bets:
            st.write("Επιλέξτε ποια από τα παρακάτω Value Bets θέλετε να προσθέσετε στο ιστορικό σας:")
            
            selected_bets_to_save = []
            for i, vb in enumerate(valid_value_bets):
                label = f"🔥 {vb['market']} | Απόδοση: {vb['odd']:.2f} | Πιθανότητα: {vb['prob']:.1f}% | EV: +{vb['ev']:.2f}% | Stake: {vb['stake']:.1f}%"
                is_checked = st.checkbox(label, value=True, key=f"f_chk_{i}")
                if is_checked:
                    selected_bets_to_save.append(vb)

            if st.button("💾 Αποθήκευση Επιλεγμένων στο Ιστορικό", key="f_save_btn"):
                if selected_bets_to_save:
                    saved_count = 0
                    for vb in selected_bets_to_save:
                        bet_entry = {
                            "created_at": datetime.now().isoformat(),
                            "date": target_date_str, 
                            "league_code": league["code"],
                            "home_team": home_stats['name'], 
                            "away_team": away_stats['name'],
                            "market": vb["market"], 
                            "odds": vb["odd"], 
                            "prob": vb["prob"], 
                            "ev": vb["ev"],
                            "stake_units": vb["stake"], 
                            "status": "pending", 
                            "home_score": None, 
                            "away_score": None
                        }
                        save_prediction(bet_entry)
                        saved_count += 1
                    
                    st.success(f"Αποθηκεύτηκαν επιτυχώς {saved_count} επιλογές στο ιστορικό!")
                    if "last_sim_data_f" in st.session_state:
                        del st.session_state["last_sim_data_f"]
                    st.rerun()
                else:
                    st.warning("Δεν έχετε επιλέξει καμία πρόταση προς αποθήκευση.")
        else:
            st.info("Δεν βρέθηκαν Value Bets που να ξεπερνούν τα κριτήρια απόδοσης/EV.")

# --- ΙΣΤΟΡΙΚΟ, ROI & ΜΠΑΝΚΑΣ ΠΟΔΟΣΦΑΙΡΟΥ ---
st.markdown("---")
st.subheader("📂 Ιστορικό, ROI & Παρακολούθηση Μπάνκας (Ποδόσφαιρο)")

if os.path.exists(HISTORY_FILE):
    history = []
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            history = list(reader)
    except Exception:
        history = []

    if history:
        st.write(f"Συνολικά αποθηκευμένες προτάσεις: **{len(history)}**")
        
        pending_history = [b for b in history if b.get("status") == "pending" or b.get("home_score") in [None, ""]]
        
        if pending_history:
            with st.expander("📝 Εισαγωγή Τελικών Σκόρ για Εκκρεμή Στοιχήματα"):
                for idx, b in enumerate(pending_history):
                    st.write(f"⚽ **{b.get('date')}** | {b.get('home_team')} vs {b.get('away_team')} | Επιλογή: `{b.get('market')}`")
                    col_h, col_a, col_btn = st.columns([2, 2, 2])
                    
                    hs = col_h.number_input(f"Γκολ {b.get('home_team')}", min_value=0, value=0, key=f"fh_{idx}")
                    as_ = col_a.number_input(f"Γκολ {b.get('away_team')}", min_value=0, value=0, key=f"fa_{idx}")
                    
                    if col_btn.button("💾 Αποθήκευση Σκόρ", key=f"fbtn_{idx}"):
                        for orig_b in history:
                            if orig_b.get("date") == b.get("date") and orig_b.get("home_team") == b.get("home_team") and orig_b.get("away_team") == b.get("away_team") and orig_b.get("market") == b.get("market"):
                                orig_b["home_score"] = int(hs)
                                orig_b["away_score"] = int(as_)
                                market = orig_b["market"]
                                won = False
                                if market == "Νίκη Γηπεδούχου (1)": won = hs > as_
                                elif market == "Ισοπαλία (X)": won = hs == as_
                                elif market == "Νίκη Φιλοξενουμένου (2)": won = hs < as_
                                elif market == "Over 1.5": won = (hs + as_) > 1.5
                                elif market == "Over 2.5": won = (hs + as_) > 2.5
                                elif market == "Over 3.5": won = (hs + as_) > 3.5
                                elif market == "Goal/Goal (GG)": won = hs > 0 and as_ > 0
                                orig_b["status"] = "won" if won else "lost"
                                break
                        
                        fields = ["created_at", "date", "league_code", "home_team", "away_team", "market", "odds", "prob", "ev", "stake_units", "status", "home_score", "away_score"]
                        with open(HISTORY_FILE, "w", newline="", encoding="utf-8-sig") as f_out:
                            writer = csv.DictWriter(f_out, fieldnames=fields, extrasaction='ignore')
                            writer.writeheader()
                            writer.writerows(history)
                        
                        st.success(f"Το σκόρ {hs}-{as_} αποθηκεύτηκε!")
                        st.rerun()

        settled = [b for b in history if b.get("status") in ["won", "lost"]]
        
        if settled:
            wins = len([b for b in settled if b["status"] == "won"])
            win_rate = (wins / len(settled)) * 100
            
            total_staked = 0.0
            total_return = 0.0
            
            for b in settled:
                try: stake = float(b.get("stake_units", 1.0))
                except ValueError: stake = 1.0
                try: odds_val = float(b.get("odds", 1.90))
                except ValueError: odds_val = 1.90
                
                total_staked += stake
                if b["status"] == "won":
                    total_return += stake * odds_val
                    
            net_profit = total_return - total_staked
            roi = (net_profit / total_staked) * 100 if total_staked > 0 else 0.0
            
            st.markdown("### 📊 Συνολικά Στατιστικά & Μπάνκα Ποδοσφαίρου")
            col_m1, col_m2, col_m3, col_m4 = st.columns(4)
            col_m1.metric("Ποσοστό Επιτυχίας", f"{win_rate:.1f}%", f"{wins} / {len(settled)}")
            col_m2.metric("Καθάρο Κέρδος/Ζημιά", f"{net_profit:+.2f} μ.")
            col_m3.metric("ROI (%)", f"{roi:+.2f}%")
            col_m4.metric("Συνολικό Ποντάρισμα", f"{total_staked:.2f} μ.")
            
            display_data = []
            for b in settled:
                is_won = (b["status"] == "won")
                try: stake_val = float(b.get("stake_units", 1.0))
                except ValueError: stake_val = 1.0
                try: odd_val = float(b.get("odds", 1.90))
                except ValueError: odd_val = 1.90
                
                profit_loss = (stake_val * odd_val - stake_val) if is_won else (-stake_val)
                bet_roi = (profit_loss / stake_val) * 100 if stake_val > 0 else 0.0
                
                display_data.append({
                    "Ημερομηνία": b.get("date", ""),
                    "Αγώνας": f"{b.get('home_team')} - {b.get('away_team')}",
                    "Επιλογή": b.get("market", ""),
                    "Απόδοση": f"{odd_val:.2f}",
                    "Σκόρ": f"{b.get('home_score')}-{b.get('away_score')}",
                    "Κατάσταση": "🟢 ΚΕΡΔΟΣ" if is_won else "🔴 ΧΑΜΕΝΟ",
                    "Κέρδος/Ζημιά": f"{profit_loss:+.2f} μ.",
                    "ROI (%)": f"{bet_roi:+.1f}%"
                })
            
            st.dataframe(display_data, use_container_width=True)
        else:
            st.info("Δεν υπάρχουν ακόμα ολοκληρωμένα στοιχήματα ποδοσφαίρου με καταχωρημένο σκόρ.")
    else:
        st.info("Δεν υπάρχουν ακόμη αποθηκευμένες προτάσεις στο ιστορικό ποδοσφαίρου.")
else:
    st.info("Το αρχείο ιστορικού ποδοσφαίρου θα δημιουργηθεί αυτόματα με την πρώτη αποθήκευση πρότασης.")
