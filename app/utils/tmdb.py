# app/utils/tmdb.py
from flask import current_app
from app.extensions import cache
import requests

@cache.memoize(timeout=600)
def fetch_from_tmdb(endpoint, params=None):
    if params is None:
        params = {}
    params = dict(params)
    params['api_key'] = current_app.config.get("TMDB_API_KEY")
    base = current_app.config.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")
    url = f"{base}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[TMDB ERROR] endpoint={endpoint} params={params} -> {e}")
        return None

@cache.memoize(timeout=7200)
def fetch_movies_list(endpoint, params=None):
    data = fetch_from_tmdb(endpoint, params=params)
    if not data or not data.get('results'):
        params = params or {}
        params.pop('region', None)
        params['language'] = 'en-US'
        data = fetch_from_tmdb(endpoint, params=params)
    if not data:
        return []
    return data.get('results', [])

def tmdb_image_base():
    try:
        return current_app.config.get("TMDB_IMAGE_BASE_URL", "")
    except RuntimeError:
        return ""
