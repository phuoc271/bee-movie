# app/utils/tmdb.py
from flask import current_app
from app.extensions import cache
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter

tmdb_session = requests.Session()
@cache.memoize(timeout=600)
def fetch_from_tmdb(endpoint, params=None):
    if params is None:
        params = {}
    params = dict(params)
    params['api_key'] = current_app.config.get("TMDB_API_KEY")
    base = current_app.config.get("TMDB_BASE_URL", "https://api.themoviedb.org/3")
    url = f"{base}/{endpoint.lstrip('/')}"
    try:
        resp = tmdb_session.get(url, params=params, timeout=30) 
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[TMDB ERROR] endpoint={endpoint} -> {e}")
        return {}

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

@cache.memoize(timeout=86400)
def tmdb_movie_detail(movie_id, language='vi-VN'):
    if not movie_id:
        return {}

    from app.models.MovieExtra import MovieExtra 
    extra = MovieExtra.query.get(str(movie_id))

    if extra:
        return {
            'id': extra.movie_id,
            'title': extra.title,
            'poster_path': extra.poster_url,
            'video_id': extra.trailer_id,
            'runtime': extra.runtime,
            'cast': extra.cast,
            'overview': extra.overview,
            'is_local': True
        }

    endpoint = f"movie/{movie_id}"
    params = {'language': language}
    movie_data = fetch_from_tmdb(endpoint, params=params) or {}

    if extra and movie_data:
        if extra.title: movie_data['title'] = extra.title
        if extra.poster_url: movie_data['poster_path'] = extra.poster_url
        if extra.trailer_id: movie_data['video_id'] = extra.trailer_id
        if extra.runtime: movie_data['runtime'] = extra.runtime
        if extra.cast: movie_data['cast'] = extra.cast
        if extra.overview: movie_data['overview'] = extra.overview
        movie_data['has_local_fix'] = True

    return movie_data

def tmdb_image_base(poster_path=None, size="w500"):
    if poster_path and poster_path.startswith('http'):
        return "" 
    base = current_app.config.get("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p/")
    return f"{base}{size}"
def create_tmdb_session():
    session = requests.Session()
    # Thử lại 3 lần, mỗi lần cách nhau một khoảng (backoff_factor)
    # status_forcelist: Thử lại nếu gặp các lỗi server phổ biến
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    
    # Giả lập User-Agent để tránh bị TMDB chặn vì nghi ngờ là bot
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    })
    return session

tmdb_session = create_tmdb_session()