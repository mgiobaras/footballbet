import streamlit as st
import requests
import numpy as np
import unicodedata
import re
import difflib
import csv
import os
from datetime import datetime, timedelta
# ============================================================

st.set_page_config(
    page_title="Sharp Football Monte Carlo Predictor",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded"
)

ITERATIONS = 500_000  # Meiwsh se 500k gia grhgori apodosi sto cloud (mpori na ginei kai 1.000.000)
LAST_GAMES = 8  
SEARCH_DAYS = 180  
MIN_ODDS = 1.60
MAX_ODDS = 3.50
MIN_EV = 3.0  
MIN_PROB_TO_SAVE = 60.0  
KELLY_FRACTION = 0.25  
MAX_STAKE_PERCENT = 2.0  
MAX_DEVIATION_PCT = 15.0 

HISTORY_FILE = "bets_history.json"

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

def load_history():
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def save_history(history):
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
    except Exception as e:
        st.error(f"[!] Σφάλμα αποθήκευσης ιστορικού: {e}")

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

# --- STREAMLIT UI ---
st.title("⚽ Sharp Football Monte Carlo Predictor")
st.markdown("Προσομοιώσεις Poisson Monte Carlo για ποδοσφαιρικούς αγώνες, ανάλυση Value Bets και διαχείριση ιστορικού.")

# Sidebar settings
st.sidebar.header("⚙️ Ρυθμίσεις & Επιλογή Αγώνα")
league_name = st.sidebar.selectbox("Επιλογή Διοργάνωσης:", list(LEAGUES.keys()))
league = LEAGUES[league_name]

target_date = st.sidebar.date_input("Ημερομηνία Αγώνα", datetime.now().date())
target_date_str = target_date.strftime("%Y-%m-%d")

# Ανάκτηση αγώνων
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

if st.sidebar.button("🔄 Ανάκτηση Στατιστικών Ομάδων"):
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

    if st.button("🚀 Εκτέλεση Προσομοίωσης Poisson"):
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

        st.success("Η προσομοίωση ολοκληρώθηκε!")

        col1, col2, col3 = st.columns(3)
        col1.metric(f"Νίκη {home_stats['name']} (1)", f"{p_home:.1f}%")
        col2.metric("Ισοπαλία (X)", f"{p_draw:.1f}%")
        col3.metric(f"Νίκη {away_stats['name']} (2)", f"{p_away:.1f}%")

        st.markdown("---")
        st.subheader("📊 Πιθανότητες Γκολ & Goal/Goal")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Over 1.5", f"{p_over15:.1f}%")
        c2.metric("Over 2.5", f"{p_over25:.1f}%")
        c3.metric("Over 3.5", f"{p_over35:.1f}%")
        c4.metric("Goal/Goal", f"{p_btts:.1f}%")

        st.markdown("---")
        st.subheader("💡 Έλεγχος Value Bets & Expected Value")

        raw_odds_1x2 = [odds.get('1'), odds.get('X'), odds.get('2')]
        fair_probs_1x2 = [None, None, None]
        if all(o is not None and o > 1 for o in raw_odds_1x2):
            implied_probs = [1.0 / o for o in raw_odds_1x2]
            total_prob = sum(implied_probs)
            fair_probs_1x2 = [(p / total_prob) * 100 for p in implied_probs]

        markets = [
            ("Νίκη Γηπεδούχου (1)", p_home, odds.get('1'), fair_probs_1x2[0]),
            ("Ισοπαλία (X)", p_draw, odds.get('X'), fair_probs_1x2[1]),
            ("Νίκη Φιλοξενουμένου (2)", p_away, odds.get('2'), fair_probs_1x2[2]),
            ("Over 1.5", p_over15, odds.get('over15'), None),
            ("Over 2.5", p_over25, odds.get('over25'), None),
            ("Over 3.5", p_over35, odds.get('over35'), None),
            ("Goal/Goal (GG)", p_btts, odds.get('gg'), None)
        ]

        history = load_history()
        found_value = False

        for market, prob, odd, fair_prob in markets:
            if odd is not None and MIN_ODDS <= odd <= MAX_ODDS and 0 < prob < 100:
                if fair_prob is not None and abs(prob - fair_prob) > MAX_DEVIATION_PCT:
                    st.info(f"❌ Απόρριψη **{market}**: Υπερβολική απόκλιση από αγορά (Model: {prob:.1f}% vs Fair Market: {fair_prob:.1f}%)")
                    continue

                val = ((prob / 100) * odd - 1) * 100
                b = odd - 1
                p = prob / 100
                q = 1 - p
                kelly_full = (b * p - q) / b if b > 0 else 0
                kelly_stake = min(max(kelly_full * KELLY_FRACTION * 100, 0), MAX_STAKE_PERCENT)

                if val >= MIN_EV:
                    found_value = True
                    st.success(f"🔥 **{market}** | Απόδοση: `{odd:.2f}` | Πιθανότητα: `{prob:.1f}%` | **EV: +{val:.2f}%** | Stake: `{kelly_stake:.2f}%`")
                    
                    if prob >= MIN_PROB_TO_SAVE:
                        exists = any(b["home_team"] == home_stats['name'] and b["away_team"] == away_stats['name'] and b["market"] == market and b["date"] == target_date_str for b in history)
                        if not exists:
                            history.append({
                                "date": target_date_str, "league_code": league["code"],
                                "home_team": home_stats['name'], "away_team": away_stats['name'],
                                "market": market, "odds": odd, "prob": prob, "ev": val,
                                "stake_units": kelly_stake, "status": "pending", "home_score": None, "away_score": None
                            })
                            save_history(history)
                else:
                    st.write(f"• {market} | Απόδοση: `{odd:.2f}` | Πιθανότητα: `{prob:.1f}%` | EV: `{val:+.2f}%` (Χωρίς Value)")

        if not found_value:
            st.warning("⚠️ Καμία επιλογή δεν πέρασε τα φίλτρα απόδοσης/EV.")

# --- ΙΣΤΟΡΙΚΟ & ΣΤΑΤΙΣΤΙΚΑ ---
st.markdown("---")
st.subheader("📂 Ιστορικό & Στατιστικά Στοιχιμάτων")
history = load_history()
if history:
    st.write(f"Συνολικά αποθηκευμένες προτάσεις: **{len(history)}**")
    settled = [b for b in history if b["status"] in ["won", "lost"]]
    if settled:
        wins = len([b for b in settled if b["status"] == "won"])
        win_rate = (wins / len(settled)) * 100
        st.metric("Συνολική Επιτυχία Ολοκληρωμένων", f"{win_rate:.1f}%", f"{wins} / {len(settled)} κερδισμένα")
else:
    st.info("Δεν υπάρχουν ακόμη αποθηκευμένες προτάσεις στο ιστορικό.")
