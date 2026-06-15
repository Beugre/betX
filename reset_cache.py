import json
from pathlib import Path
cache = json.load(open('data/cache/national_teams.json'))
cache['fixtures'] = {}
cache.pop('_goalscorers_loaded_at', None)
cache.pop('_martj42_loaded_at', None)
Path('data/cache/national_teams.json').write_text(json.dumps(cache, ensure_ascii=False))
print(f"Cache purgé")
from betx.data.martj42_loader import load_into_cache
n = load_into_cache(force=True)
print(f"martj42: {n} équipes")
from betx.data.update_wc_dataset import run
print(f"CdM 2026: {run()} résultats")
