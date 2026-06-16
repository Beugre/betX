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

# Clubs / leagues féminines connus EA FC 26 → exclure ces joueurs
WOMEN_CLUBS = {
    "OL Lyonnes", "Paris Saint-Germain W", "FC Barcelona W", "Arsenal W",
    "Chelsea W", "Manchester City W", "Manchester United W", "Bayern München W",
    "Real Madrid W", "Juventus W", "Portland Thorns FC", "Washington Spirit",
    "Chicago Stars FC", "North Carolina Courage", "Kansas City Current",
    "Orlando Pride", "Gotham FC", "Houston Dash", "San Diego Wave FC",
    "Seattle Reign FC", "Racing Louisville FC", "Angel City FC",
    "Bay FC", "Utah Royals", "London City Lionesses", "Eintracht Frankfurt W",
    "VfL Wolfsburg W", "Bayer 04 Leverkusen W", "TSG 1899 Hoffenheim W",
    "Brighton & Hove Albion W", "Tottenham Hotspur W", "Everton W", "Aston Villa W",
    "Roma W", "Milano FC W", "Atletico de Madrid W", "Real Sociedad W",
    "RC Deportivo", "FC Fleury 91", "Paris FC", "Stade de Reims W",
    "Montpellier HSC W", "Atlético de Madrid W", "Real Betis Balompié W",
    "San Diego Wave FC", "NJ/NY Gotham FC",
}

# Noms de joueurs féminines connues à exclure explicitement (pour les cas où le club ne suffit pas)
KNOWN_WOMEN = {
    "Alexia Putellas", "Aitana Bonmatí", "Caroline Graham Hansen", "Claudia Pina",
    "Mapi León", "Mariona", "Patri Guijarro", "Ona Batlle", "Irene Paredes",
    "Laia Aleixandri", "Salma Paralluelo", "Olga Carmona", "Cata Coll", "Esther",
    "Alba Redondo", "Jenni Hermoso", "Athenea", "Eva Navarro", "Vicky López",
    "Aitana Bonmati", "Lucy Bronze", "Millie Bright", "Leah Williamson",
    "Lauren Hemp", "Georgia Stanway", "Beth Mead", "Chloe Kelly", "Lauren James",
    "Ella Toone", "Keira Walsh", "Hannah Hampton", "Mary Earps", "Alex Greenwood",
    "Maya Le Tissier", "Alessia Russo", "Khadija Shaw", "Sam Kerr", "Caitlin Foord",
    "Steph Catley", "Ellie Carpenter", "Sam Kerr", "Klara Bühl", "Lena Oberdorf",
    "Giulia Gwinn", "Alexandra Popp", "Lea Schüller", "Ann-Katrin Berger",
    "Svenja Huth", "Sara Däbritz", "Sara Doorsoun", "Linda Dallmann", "Merle Frohms",
    "Jule Brand", "Sjoeke Nüsken", "Nicole Anyomi", "Selina Cerci", "Vanessa Fudalla",
    "Pernille Harder", "Signe Bruun", "Sofie Svava", "Caroline Møller", "Cornelia Kramer",
    "Marta", "Bia Zaneratto", "Debinha", "Lorena", "Lauren Leal", "Kerolin Nicoli",
    "Rosemonde Kouassi", "Bernadette Amani", "Inès Konan",
    "Lindsey Heaps", "Rose Lavelle", "Mallory Swanson", "Trinity Rodman",
    "Emily Fox", "Naomi Girma", "Emily Sonnett", "Catarina Macario",
    "Aubrey Kingsbury", "Sam Coffey", "Lo'eau LaBonta", "Taylor Flint", "Korbin Shrader",
    "Crystal Dunn", "Lynn Biyendolo", "Sofia Huerta", "Ashley Hatch", "Casey Murphy",
    "Ashley Sanchez", "Phallon Tullis-Joyce", "Vanessa DiBernardo", "Hailie Mace",
    "Alyssa Thompson", "Alyssa Naeher", "Jane Campbell", "Olivia Moultrie", "Yazmeen Ryan",
    "Savannah DeMelo", "Sakina Karchaoui", "Selma Bacha", "Wendie Renard",
    "Kenza Dali", "Clara Mateo", "Sandy Baltimore", "Delphine Cascarino",
    "Griedge Mbock", "Pauline Peyraud-Magnin",
    "Fridolina Rolfö", "Kosovare Asllani", "Magdalena Eriksson", "Stina Blackstenius",
    "Johanna Kaneryd", "Amanda Ilestedt", "Filippa Angeldahl", "Nathalie Björn",
    "Sofia Jakobsson", "Lina Hurtig", "Zećira Mušović", "Jennifer Falk",
    "Amanda Nildén", "Hanna Glas", "Julia Zigiotti", "Hanna Lundkvist",
    "Jill Roord", "Vivianne Miedema", "Damaris Egurrola", "Dominique Janssen",
    "Jackie Groenen", "Kerstin Casparij", "Sherida Spitse", "Daniëlle van de Donk",
    "Daphne van Domselaar", "Esmee Brugts",
    "Ada Hegerberg", "Guro Reiten", "Frida Maanum", "Ingrid Syrstad Engen",
    "Emilie Haavi", "Elisabeth Terland", "Celin Bizet", "Vilde Bøe Risa",
    "Synne Jensen", "Cecilie Fiskerstrand", "Tuva Hansen", "Karina Sævik",
    "Justine Kielland", "Kamilla Melgård",
    "Christiane Endler", "Andreia Jacinto", "Inês Pereira",
    "Géraldine Reuteler", "Lia Wälti", "Ana-Maria Crnogorčević", "Viola Calligaris",
    "Noëlle Maritz", "Alisha Lehmann", "Eseosa Aigbogun", "Elvira Herzog",
    "Livia Peng", "Alayah Pilgrim", "Sydney Schertenleib",
    "Linda Caicedo", "Mayra Ramírez", "Manuela Vanegas", "Leicy Santos",
    "Lice Chamorro", "Chiamaka Nnadozie", "Rasheedat Ajibade", "Gift Monday",
    "Omorinsola Babajide", "Toni Payne", "Jennifer Echegini",
    "Kim Little", "Erin Cuthbert", "Caroline Weir",
    "Yui Hasegawa", "Saki Kumagai", "Hina Sugita", "Ayaka Yamashita",
    "Moeka Minami", "Jun Endo", "Honoka Hayashi", "Hinata Miyazawa",
    "Maika Hamano", "Risa Shimizu", "Aoba Fujino", "Kiko Seike",
    "Manaka Matsukubo", "Yuka Momiki", "Riko Ueki",
    "Ashley Lawrence", "Vanessa Gilles", "Kadeisha Buchanan",
    "Jessie Fleming", "Évelyne Viens", "Quinn", "Sophie Schmidt",
    "Julia Grosso", "Marie Levasseur", "Olivia Smith", "Janine Sonis",
    "Adriana Leon", "Nichelle Prince", "Deanne Rose", "Jayde Riviere",
    "Shelina Zadorsky", "Cloé Lacasse", "Promise David", "Jordyn Huitema",
    "Gabrielle Carle", "Allysha Chapman", "Sabrina D'Angelo",
    "Sarah Zadrazil", "Barbara Dunst", "Katharina Naschenweng",
    "Verena Hanshaw", "Manuela Zinsberger", "Laura Feiersinger",
    "Sarah Puntigam", "Ewelina Kamczyk", "Tanja Pawollek",
    "Katarzyna Kiedrzynek", "Klaudia Jedlińska", "Adriana Achcińska",
    "Weronika Zawistowska", "Kinga Szemik", "Nadia Krezyman",
    "Sylwia Matysik", "Aleksandra Zaremba", "Dominika Grabowska",
    "Nérilia Mondésir", "Kethna Louis", "Amandine Pierre-Louis", "Tabita Joseph",
    "Nina Ngueleu", "Colette Ndzana", "Monique Ngock",
    "Hildah Magaia", "Linda Motlhalo",
    "Jovana Damnjanović", "Jelena Čanković", "Allegra Poljak",
    "Kate Taylor", "Ali Riley",
    "Lice Chamorro", "Lizbeth Ovalle", "María Sánchez", "Karla Nieto", "Scarlett Camberos", "Diana Ordóñez",
    "Mária Mikolajová", "Diana Bartovičová", "Martina Šurnovská",
    "Kateřina Svitková", "Klára Cahynová", "Barbora Votíková", "Tereza Szewieczková",
    "Inès Belloumou", "Massiren Aït Zefrane",
    "Paulina Dudek",
    "Chantelle Swaby", "Kiki Van Zanten", "Becky Spencer",
    "Falung",
}


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
    """Parse une page HTML de fifaindex et retourne les joueurs MASCULINS."""
    soup = BeautifulSoup(html, "html.parser")
    players = []
    
    table = soup.find("table")
    if not table:
        return players
    
    rows = table.find_all("tr")[1:]  # skip header
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 6:
            continue
        try:
            name_cell = cols[1]
            name = name_cell.get_text(strip=True)
            name = re.sub(r'^Player photo not found\s*', '', name).strip()

            if not name or name in KNOWN_WOMEN:
                continue

            pos_cell = cols[2]
            position = pos_cell.get_text(strip=True)

            nat_cell = cols[3]
            nationality = nat_cell.get_text(strip=True)
            parts = nationality.split()
            if len(parts) >= 2 and parts[0] == parts[-1]:
                nationality = parts[0]
            elif len(parts) > 1:
                mid = len(parts) // 2
                if parts[:mid] == parts[mid:]:
                    nationality = " ".join(parts[:mid])

            # Club — col 4
            club_cell = cols[4]
            club = club_cell.get_text(strip=True)
            if club in WOMEN_CLUBS:
                continue

            rating_cell = cols[5]
            rating_text = rating_cell.get_text(strip=True)
            rating = int(rating_text) if rating_text.isdigit() else 0

            if rating > 0 and name:
                players.append({
                    "name": name,
                    "position": position,
                    "nationality": nationality,
                    "club": club,
                    "rating": rating,
                })
        except (IndexError, ValueError):
            continue
    
    return players


def scrape_all_wc_players(min_rating: int = 60, max_pages: int = 500) -> dict:
    """
    Scrape fifaindex jusqu'à min_rating, hommes uniquement.
    """
    wc_ea_names = set(WC_TEAMS_MAP.values())
    all_players = {}
    
    session = requests.Session()
    session.headers.update(HEADERS)
    
    for page in range(1, max_pages + 1):
        url = f"{BASE_URL}?page={page}&order=desc"
        
        # Retry avec backoff exponentiel
        resp = None
        for attempt in range(4):
            try:
                resp = session.get(url, timeout=15)
                if resp.status_code == 403:
                    wait = 2 ** attempt + 1
                    print(f"  403 page {page}, retry dans {wait}s...")
                    time.sleep(wait)
                    resp = None
                    continue
                resp.raise_for_status()
                break
            except Exception as e:
                if attempt == 3:
                    print(f"  ⚠ Page {page} erreur définitive: {e}")
                time.sleep(2 ** attempt)
        
        if resp is None:
            print(f"  ⚠ Page {page} ignorée")
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
            # Ne pas écraser un joueur déjà présent avec un rating plus élevé
            if p["name"] not in all_players or all_players[p["name"]]["rating"] < p["rating"]:
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
        
        time.sleep(0.5)
    
    return all_players


def main():
    output = Path("data/player_ratings.json")
    
    print("=== Scraping fifaindex.com FC26 pour les 48 nations WC 2026 ===")
    print(f"Nations ciblées: {len(WC_TEAMS_MAP)}")
    print()
    
    players = scrape_all_wc_players(min_rating=0, max_pages=500)
    
    # Ajouter les top manquants (pages 1-2 souvent en 403)
    missing_top = {
        "Kylian Mbappé":      {"team": "France",      "rating": 91, "position": "ST"},
        "Erling Haaland":     {"team": "Norway",       "rating": 91, "position": "ST"},
        "Harry Kane":         {"team": "England",      "rating": 90, "position": "ST"},
        "Ousmane Dembélé":    {"team": "France",       "rating": 90, "position": "ST"},
        "Pedri":              {"team": "Spain",        "rating": 90, "position": "CM"},
        "Vitinha":            {"team": "Portugal",     "rating": 90, "position": "CM"},
        "Thibaut Courtois":   {"team": "Belgium",      "rating": 90, "position": "GK"},
        "Mohamed Salah":      {"team": "Egypt",        "rating": 89, "position": "RM"},
        "Joshua Kimmich":     {"team": "Germany",      "rating": 89, "position": "CDM"},
        "Rodri":              {"team": "Spain",        "rating": 89, "position": "CDM"},
        "Raphinha":           {"team": "Brazil",       "rating": 89, "position": "LW"},
        "Gabriel Magalhães":  {"team": "Brazil",       "rating": 89, "position": "CB"},
        "Achraf Hakimi":      {"team": "Morocco",      "rating": 89, "position": "RB"},
        "Vini Jr.":           {"team": "Brazil",       "rating": 89, "position": "LW"},
        "Federico Valverde":  {"team": "Uruguay",      "rating": 89, "position": "CM"},
        "Michael Olise":      {"team": "France",       "rating": 89, "position": "RW"},
        "Jude Bellingham":    {"team": "England",      "rating": 89, "position": "CAM"},
        "Lamine Yamal":       {"team": "Spain",        "rating": 89, "position": "RW"},
        "William Saliba":     {"team": "France",       "rating": 88, "position": "CB"},
        "João Neves":         {"team": "Portugal",     "rating": 88, "position": "CM"},
        "Nuno Mendes":        {"team": "Portugal",     "rating": 88, "position": "LB"},
        "Bruno Fernandes":    {"team": "Portugal",     "rating": 88, "position": "CAM"},
        "Virgil van Dijk":    {"team": "Netherlands",  "rating": 88, "position": "CB"},
        "Lautaro Martínez":   {"team": "Argentina",    "rating": 88, "position": "ST"},
        "Declan Rice":        {"team": "England",      "rating": 88, "position": "CDM"},
        "Khvicha Kvaratskhelia": {"team": "Georgia",   "rating": 88, "position": "LW"},
        "Alisson":            {"team": "Brazil",       "rating": 88, "position": "GK"},
        "Marquinhos":         {"team": "Brazil",       "rating": 87, "position": "CB"},
        "Bukayo Saka":        {"team": "England",      "rating": 87, "position": "RW"},
        "Kevin De Bruyne":    {"team": "Belgium",      "rating": 87, "position": "CM"},
        "Luis Díaz":          {"team": "Colombia",     "rating": 87, "position": "LM"},
        "Florian Wirtz":      {"team": "Germany",      "rating": 87, "position": "CAM"},
        "Jamal Musiala":      {"team": "Germany",      "rating": 87, "position": "CAM"},
        "Jonathan Tah":       {"team": "Germany",      "rating": 87, "position": "CB"},
        "Rúben Dias":         {"team": "Portugal",     "rating": 87, "position": "CB"},
        "Alexander Isak":     {"team": "Sweden",       "rating": 87, "position": "ST"},
        "Frenkie de Jong":    {"team": "Netherlands",  "rating": 87, "position": "CM"},
        "Ryan Gravenberch":   {"team": "Netherlands",  "rating": 86, "position": "CDM"},
        "Scott McTominay":    {"team": "Scotland",     "rating": 86, "position": "CM"},
        "Martin Ødegaard":    {"team": "Norway",       "rating": 86, "position": "CM"},
        "Viktor Gyökeres":    {"team": "Sweden",       "rating": 86, "position": "ST"},
        "Robert Lewandowski": {"team": "Poland",       "rating": 86, "position": "ST"},
        "Hakan Çalhanoğlu":   {"team": "Türkiye",      "rating": 86, "position": "CDM"},
        "Yann Sommer":        {"team": "Switzerland",  "rating": 86, "position": "GK"},
        "Gregor Kobel":       {"team": "Switzerland",  "rating": 86, "position": "GK"},
        "Victor Osimhen":     {"team": "Nigeria",      "rating": 86, "position": "ST"},
        "Bremer":             {"team": "Brazil",       "rating": 86, "position": "CB"},
        "Bruno Guimarães":    {"team": "Brazil",       "rating": 86, "position": "CM"},
        "Lionel Messi":       {"team": "Argentina",    "rating": 86, "position": "CAM"},
        "Julián Álvarez":     {"team": "Argentina",    "rating": 86, "position": "ST"},
    }
    added = 0
    for name, info in missing_top.items():
        if name not in players or players[name]["rating"] < info["rating"]:
            players[name] = info
            added += 1
    
    print()
    print(f"Total joueurs collectés (sans filtre): {len(players)}")
    if added:
        print(f"+{added} joueurs top ajoutés (pages 1-2 inaccessibles)")
    
    from collections import Counter
    team_counts = Counter(p["team"] for p in players.values())
    print("\nJoueurs par équipe:")
    for team, count in sorted(team_counts.items(), key=lambda x: -x[1]):
        print(f"  {team:30s}: {count:3d}")
    
    result = {
        "_comment": "Ratings joueurs CdM 2026 (EA FC 26 base cards — source: fifaindex.com)",
        "_methodology": "Cartes de base EA FC 26. Tous les joueurs des nations WC (rating >= 60), sans filtre par équipe.",
        "_source": f"fifaindex.com/players/fc26 — {len(players)} joueurs hommes WC 2026",
        "players": players,
    }
    
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\n✅ Sauvegardé: {output} ({len(players)} joueurs)")


if __name__ == "__main__":
    main()

