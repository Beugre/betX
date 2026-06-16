"""
Scrape fifaindex.com/players/fc26 pour récupérer tous les joueurs des 48 nations WC 2026.
Génère data/player_ratings.json avec ~1200+ joueurs.

Usage: python3 scripts/scrape_fc26_ratings.py
"""
import json
import time
import re
import sys
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("pip install requests beautifulsoup4")
    sys.exit(1)

# Mapping nom WC → nom EA FC 26 (pour le filtre nationality)
# On mappe les noms du système vers les noms exacts utilisés par fifaindex
WC_TEAMS_MAP = {
    "France": "France",
    "Spain": "Spain",
    "England": "England",
    "Germany": "Germany",
    "Portugal": "Portugal",
    "Netherlands": "Netherlands",
    "Belgium": "Belgium",
    "Brazil": "Brazil",
    "Argentina": "Argentina",
    "Uruguay": "Uruguay",
    "Colombia": "Colombia",
    "Ecuador": "Ecuador",
    "Norway": "Norway",
    "Sweden": "Sweden",
    "Denmark": "Denmark",
    "Switzerland": "Switzerland",
    "Austria": "Austria",
    "Croatia": "Croatia",
    "Serbia": "Serbia",
    "Poland": "Poland",
    "Slovakia": "Slovakia",
    "Czechia": "Czechia",
    "Scotland": "Scotland",
    "Türkiye": "Türkiye",
    "Georgia": "Georgia",
    "Morocco": "Morocco",
    "Senegal": "Senegal",
    "Egypt": "Egypt",
    "Nigeria": "Nigeria",
    "Algeria": "Algeria",
    "Ghana": "Ghana",
    "Ivory Coast": "Ivory Coast",
    "Cameroon": "Cameroon",
    "Guinea": "Guinea",
    "South Africa": "South Africa",
    "Tunisia": "Tunisia",
    "Cape Verde": "Cape Verde",
    "Congo DR": "DR Congo",
    "Japan": "Japan",
    "South Korea": "Republic of Korea",
    "Australia": "Australia",
    "Iran": "Iran",
    "Saudi Arabia": "Saudi Arabia",
    "Iraq": "Iraq",
    "Jordan": "Jordan",
    "Uzbekistan": "Uzbekistan",
    "United States": "United States",
    "Canada": "Canada",
    "Mexico": "Mexico",
    "Panama": "Panama",
    "Jamaica": "Jamaica",
    "Haiti": "Haiti",
    "Paraguay": "Paraguay",
    "Qatar": "Qatar",
    "Bolivia": "Bolivia",
    "Bosnia-Herzegovina": "Bosnia and Herzegovina",
    "Curaçao": "Curaçao",
    "New Zealand": "New Zealand",
}

# Reverse: nom EA FC → nom système
EA_TO_WC = {v: k for k, v in WC_TEAMS_MAP.items()}

BASE_URL = "https://fifaindex.com/players/fc26"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

def parse_page(html: str) -> list[dict]:
    """Parse une page HTML de fifaindex et retourne les joueurs."""
    soup = BeautifulSoup(html, "html.parser")
    players = []
    
    # La table des joueurs
    table = soup.find("table")
    if not table:
        return players
    
    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 6:
            continue
        try:
            # Structure: rank | name | position | nationality | club | overall | potential
            name_cell = cols[1]
            name = name_cell.get_text(strip=True)
            # Nettoyer "Player photo not found" du nom
            name = re.sub(r'^Player photo not found\s*', '', name).strip()
            
            pos_cell = cols[2]
            position = pos_cell.get_text(strip=True)
            
            nat_cell = cols[3]
            # Nationality — chercher le texte
            nationality = nat_cell.get_text(strip=True)
            # Enlever le doublon (ex: "France France" → "France")
            parts = nationality.split()
            if len(parts) >= 2 and parts[0] == parts[-1]:
                nationality = parts[0]
            elif len(parts) > 1:
                # "Republic of Korea Republic of Korea" → "Republic of Korea"
                mid = len(parts) // 2
                if parts[:mid] == parts[mid:]:
                    nationality = " ".join(parts[:mid])
            
            rating_cell = cols[5]
            rating_text = rating_cell.get_text(strip=True)
            rating = int(rating_text) if rating_text.isdigit() else 0
            
            if rating > 0 and name:
                players.append({
                    "name": name,
                    "position": position,
                    "nationality": nationality,
                    "rating": rating,
                })
        except (IndexError, ValueError):
            continue
    
    return players


def scrape_all_wc_players(min_rating: int = 60, max_pages: int = 120) -> dict:
    """
    Scrape fifaindex jusqu'à min_rating.
    Retourne un dict {nom_joueur: {team, rating, position}} pour les nations WC.
    """
    wc_ea_names = set(WC_TEAMS_MAP.values())
    all_players = {}
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}?page={page}&order=desc"
        try:
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
        except Exception as e:
            print(f"  ⚠ Page {page} erreur: {e}")
            time.sleep(3)
            continue
        
        players = parse_page(resp.text)
        
        if not players:
            print(f"  Page {page}: aucun joueur, arrêt.")
            break
        
        min_on_page = min(p["rating"] for p in players)
        wc_on_page = [p for p in players if p["nationality"] in wc_ea_names]
        
        for p in wc_on_page:
            ea_nat = p["nationality"]
            wc_team = EA_TO_WC.get(ea_nat, ea_nat)
            all_players[p["name"]] = {
                "team": wc_team,
                "rating": p["rating"],
                "position": p["position"],
            }
        
        total_wc = len(all_players)
        print(f"  Page {page:3d} | rating min={min_on_page} | +{len(wc_on_page):3d} WC players | total={total_wc}")
        
        if min_on_page < min_rating:
            print(f"  → Rating {min_on_page} < {min_rating}, arrêt.")
            break
        
        time.sleep(0.4)  # poli avec le serveur
    
    return all_players


def main():
    output = Path("data/player_ratings.json")
    
    print("=== Scraping fifaindex.com FC26 pour les 48 nations WC 2026 ===")
    print(f"Nations ciblées: {len(WC_TEAMS_MAP)}")
    print()
    
    players = scrape_all_wc_players(min_rating=60, max_pages=150)
    
    print()
    print(f"Total joueurs collectés: {len(players)}")
    
    # Stats par équipe
    from collections import Counter
    team_counts = Counter(p["team"] for p in players.values())
    print("\nJoueurs par équipe:")
    for team, count in sorted(team_counts.items(), key=lambda x: -x[1]):
        print(f"  {team:30s}: {count:3d}")
    
    # Construire le JSON final
    result = {
        "_comment": "Ratings joueurs CdM 2026 (EA FC 26 base cards — source: fifaindex.com)",
        "_methodology": "Cartes de base EA FC 26. rating >= 88 = joueur décisif, 84-87 = joueur important, 80-83 = joueur solide, <80 = standard.",
        "_source": f"fifaindex.com/players/fc26 — scraping automatique {len(players)} joueurs WC 2026",
        "players": players,
    }
    
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n✅ Sauvegardé: {output} ({len(players)} joueurs)")


if __name__ == "__main__":
    main()
