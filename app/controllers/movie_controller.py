from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for, current_app
from datetime import datetime, timedelta
from app.models import Comment, Rating, User, Showtime, Cinema, Room, Concession, MovieExtra, Booking
from app.extensions import db, cache
from app.utils.tmdb import fetch_from_tmdb, fetch_movies_list
import google.generativeai as genai
from dotenv import load_dotenv
from sqlalchemy import func
from sklearn.feature_extraction.text import CountVectorizer
from sklearn.metrics.pairwise import cosine_similarity
from flask_login import current_user
import json, os, re, random, pandas as pd

movie_bp = Blueprint('movie', __name__)

GENRE_MAP = {}
load_dotenv()
@cache.memoize(timeout=7200) 
def fetch_movie_videos(movie_id):
    return fetch_from_tmdb(f"movie/{movie_id}/videos")

def get_trailer_key(videos_data):
    if not videos_data or not videos_data.get('results'):
        return None
    for v in videos_data['results']:
        if v.get('site') == 'YouTube' and v.get('type') == 'Trailer':
            return v.get('key')
    return videos_data['results'][0].get('key') if videos_data['results'] else None

@cache.cached(timeout=7200, key_prefix="tmdb_genres_viVN")
def fetch_genres():
    global GENRE_MAP
    if GENRE_MAP:
        return GENRE_MAP
    data = fetch_from_tmdb("genre/movie/list", params={"language": "vi-VN"})
    if not data:
        return {}
    GENRE_MAP = {g['id']: g['name'] for g in data.get('genres', [])}
    return GENRE_MAP

@cache.memoize(timeout=3600)  
def fetch_movie_detail_cached(movie_id, language="vi-VN", append_credits=False):
    params = {'language': language}
    if append_credits:
        params['append_to_response'] = 'credits'
    return fetch_from_tmdb(f"movie/{movie_id}", params=params)

@cache.memoize(timeout=1800)  
def fetch_list_cached(endpoint, page=1, language="vi-VN", region="VN"):
    return fetch_movies_list(endpoint, {"page": page, "language": language, "region": region})

def get_genre_names(genre_ids, genre_map):
    if not genre_ids or not genre_map:
        return "Chưa rõ"
    names = [genre_map.get(gid, 'N/A') for gid in genre_ids]
    return " / ".join(names)

@movie_bp.route('/')
def home():
    genre_map = fetch_genres()
    now_playing_movies_data = fetch_list_cached("movie/now_playing", page=1, language="vi-VN", region="VN")
    upcoming_data = fetch_list_cached("movie/upcoming", page=1, language="vi-VN", region="VN")

    current_movies_list = []
    featured_movies_list = []
    upcoming_list = []
    
    for movie in now_playing_movies_data:
        movie_id = movie.get("id")
        extra = MovieExtra.query.get(str(movie_id))
        tmdb_path = movie.get('poster_path')

        if extra and extra.poster_url:
            if extra.poster_url.startswith('http'):
                custom_poster = extra.poster_url
            else:
                path = extra.poster_url if extra.poster_url.startswith('/') else f"/{extra.poster_url}"
                custom_poster = f"https://image.tmdb.org/t/p/w500{path}"
        elif tmdb_path:
            custom_poster = f"https://image.tmdb.org/t/p/w500{tmdb_path}"        
        else:
            custom_poster = url_for('static', filename='image/404.jpg')
        raw_genres = get_genre_names(movie.get("genre_ids", []), genre_map)
        full_str = ", ".join(raw_genres) if isinstance(raw_genres, list) else str(raw_genres)
        clean_str = full_str.replace("/", ",").replace("Phim ", "").replace("phim ", "").strip()
        genre_names = [g.strip() for g in clean_str.split(',') if g.strip()] or ["Khác"]

        local_ratings = Rating.query.filter_by(movie_id=str(movie.get("id"))).all()
        local_sum = sum(r.score for r in local_ratings)
        local_count = len(local_ratings)

        user_avg_ratings = db.session.query(
            func.avg(Comment.final_rating).label('avg_score')
        ).filter(
            Comment.story_id == movie.get("id"), 
            Comment.final_rating > 0,
            Comment.content != ""
        ).group_by(Comment.user_id).all()

        ai_sum = sum(r.avg_score for r in user_avg_ratings)
        ai_count = len(user_avg_ratings)
        
        tmdb_score = movie.get("vote_average", 0)
        tmdb_count = movie.get("vote_count", 0)
        
        total_votes = tmdb_count + local_count + ai_count
        if total_votes > 0:
            combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes
        else:
            combined_average = tmdb_score
        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names,
            "vote_average": round(combined_average, 1),
            "release_date": movie.get("release_date"),
            "tmdb_score": tmdb_score, 
            "tmdb_count": tmdb_count, 
            "poster_url": custom_poster,
            "overview": movie.get("overview"),
            "backdrop_url": f"{current_app.config['TMDB_BACKDROP_BASE_URL']}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        current_movies_list.append(movie_info)
        if movie_info.get('backdrop_url') and len(featured_movies_list) < 7:
            featured_movies_list.append(movie_info)

    for movie in upcoming_data:
        movie_id = movie.get("id")
        extra = MovieExtra.query.get(str(movie_id))
        tmdb_path = movie.get('poster_path')

        if extra and extra.poster_url:
            if extra.poster_url.startswith('http'):
                custom_poster = extra.poster_url
            else:
                path = extra.poster_url if extra.poster_url.startswith('/') else f"/{extra.poster_url}"
                custom_poster = f"https://image.tmdb.org/t/p/w500{path}"
        elif tmdb_path:
            custom_poster = f"https://image.tmdb.org/t/p/w500{tmdb_path}"        
        else:
            custom_poster = url_for('static', filename='image/404.jpg')
            
        raw_genres = get_genre_names(movie.get("genre_ids", []), genre_map)
        full_str = ", ".join(raw_genres) if isinstance(raw_genres, list) else str(raw_genres)
        clean_str = full_str.replace("/", ",").replace("Phim ", "").replace("phim ", "").strip()
        genre_names = [g.strip() for g in clean_str.split(',') if g.strip()] or ["Khác"]

        local_ratings = Rating.query.filter_by(movie_id=movie_id).all()
        local_sum = sum(r.score for r in local_ratings)
        local_count = len(local_ratings)

        user_avg_ratings = db.session.query(
            func.avg(Comment.final_rating).label('avg_score')
        ).filter(
            Comment.story_id == movie_id, 
            Comment.final_rating > 0,
            Comment.content != ""
        ).group_by(Comment.user_id).all()

        ai_sum = sum(r.avg_score for r in user_avg_ratings)
        ai_count = len(user_avg_ratings)
        tmdb_score = movie.get("vote_average", 0)
        tmdb_count = movie.get("vote_count", 0)
        total_votes = tmdb_count + local_count + ai_count
        combined_average = tmdb_score
        if total_votes > 0:
            combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes
        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "release_date": movie.get("release_date"),
            "overview": movie.get("overview"),
            "genre": genre_names,
            "vote_average": round(combined_average, 1),
            "poster_url": custom_poster,
            "backdrop_url": f"{current_app.config['TMDB_BACKDROP_BASE_URL']}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        upcoming_list.append(movie_info)

    recommended_list = []
    
    if current_user.is_authenticated:
            active_showtime_ids = db.session.query(Showtime.movie_id).distinct().all()
            active_ids = [str(s[0]) for s in active_showtime_ids]

            all_movies = current_movies_list + upcoming_list
            existing_ids = {str(m['id']) for m in all_movies}

            for mid in active_ids:
                if mid not in existing_ids:
                    extra = MovieExtra.query.get(mid)
                    if extra:
                        if extra.genres:
                            clean_genres = [g.strip() for g in extra.genres.replace("Phim ", "").split(',')]
                        else:
                            clean_genres = ["Khác"]
                        
                        all_movies.append({
                            "id": mid,
                            "title": extra.title,
                            "genre": clean_genres, 
                            "vote_average": 0.0,
                            "poster_url": extra.poster_url,
                            "overview": extra.overview or ""
                        })

            movies_with_schedules = [m for m in all_movies if str(m['id']) in active_ids]
            recommended_list = get_personalized_recommendations(current_user.id, movies_with_schedules)
    return render_template('home.html',
                            movies=current_movies_list[:8],
                            featured_movies=featured_movies_list,
                            upcoming=upcoming_list,
                            recommended_movies=recommended_list)

@movie_bp.route("/now-playing")
def now_playing():
    genre_map = fetch_genres()
    now = datetime.now()
    all_now_playing_movies = fetch_list_cached("movie/now_playing", page=1, language="vi-VN", region="VN")
    
    active_movie_ids = db.session.query(Showtime.movie_id).filter(
        Showtime.start_time >= now
    ).distinct().all()
    movie_ids = [str(m[0]) for m in active_movie_ids]
    
    tmdb_dict = {str(m.get("id")): m for m in all_now_playing_movies}
    
    movie_list = []
    for movie_id in movie_ids:
        movie = tmdb_dict.get(movie_id)
        
        if movie:
            title = movie.get("title")
            desc = movie.get("overview")
            genre_names = get_genre_names(movie.get("genre_ids", []), genre_map)
            poster_path = movie.get('poster_path')
            if poster_path:
                if poster_path.startswith('http'):
                    img = poster_path
                else:
                    clean_path = poster_path if poster_path.startswith('/') else f"/{poster_path}"
                    img = f"https://image.tmdb.org/t/p/w500{clean_path}"
            else:
                img = "https://via.placeholder.com/500x750?text=No+Poster"
            tmdb_score = movie.get("vote_average", 0)
            tmdb_count = movie.get("vote_count", 0)
        else:
            extra = MovieExtra.query.get(movie_id)
            if not extra:
                continue
            title = extra.title
            desc = extra.overview or "Không có mô tả."
            genre_names = [extra.genres] if extra.genres else ["Khác"]
            if extra.poster_url:
                if extra.poster_url.startswith('http'):
                    img = extra.poster_url
                else:
                    clean_path = extra.poster_url if extra.poster_url.startswith('/') else f"/{extra.poster_url}"
                    img = f"https://image.tmdb.org/t/p/w500{clean_path}"
            else:
                img = "https://via.placeholder.com/500x750?text=No+Poster"
            tmdb_score = 0
            tmdb_count = 0

        local_ratings = Rating.query.filter_by(movie_id=movie_id).all()
        local_sum = sum(r.score for r in local_ratings)
        local_count = len(local_ratings)

        user_avg_ratings = db.session.query(func.avg(Comment.final_rating)).filter(
            Comment.story_id == movie_id, Comment.final_rating > 0, Comment.content != ""
        ).group_by(Comment.user_id).all()

        ai_sum = sum(r[0] for r in user_avg_ratings if r[0] is not None)
        ai_count = len(user_avg_ratings)

        total_votes = tmdb_count + local_count + ai_count
        combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes if total_votes > 0 else 0
        
        movie_list.append({
            "id": movie_id,
            "title": title,
            "genre": genre_names,
            "desc": desc,
            "vote_average": round(combined_average, 1),
            "img": img,
        })
        
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Đang Chiếu")

@movie_bp.route("/upcoming")
def upcoming():
    genre_map = fetch_genres()
    upcoming_data = fetch_list_cached("movie/upcoming", page=1, language="vi-VN", region="VN")
    movie_list = []
    for movie in upcoming_data:
        movie_id = str(movie.get("id"))
        extra = MovieExtra.query.get(str(movie_id))
        
        if extra and extra.poster_url:
            if extra.poster_url.startswith('http'):
                img_url = extra.poster_url
            else:
                path = extra.poster_url if extra.poster_url.startswith('/') else f"/{extra.poster_url}"
                img_url = f"https://image.tmdb.org/t/p/w500{path}"
        elif movie.get('poster_path'):
            img_url = f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}"
        else:
            img_url = url_for('static', filename='image/404.jpg')
        genre_names = get_genre_names(movie.get("genre_ids", []), genre_map)
        local_ratings = Rating.query.filter_by(movie_id=movie_id).all()
        local_sum = sum(r.score for r in local_ratings)
        local_count = len(local_ratings)

        user_avg_ratings = db.session.query(func.avg(Comment.final_rating)).filter(
            Comment.story_id == movie_id, Comment.final_rating > 0, Comment.content != ""
        ).group_by(Comment.user_id).all()

        ai_sum = sum(r[0] for r in user_avg_ratings if r[0] is not None)
        ai_count = len(user_avg_ratings)

        tmdb_score = movie.get("vote_average", 0)
        tmdb_count = movie.get("vote_count", 0)

        total_votes = tmdb_count + local_count + ai_count
        combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes if total_votes > 0 else 0
        movie_list.append({
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names,
            "vote_average": round(combined_average, 1),
            "desc": movie.get("overview"),
            "img": img_url,
        })
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Sắp Chiếu")

@movie_bp.route("/movie/<movie_id>")
def movie_detail(movie_id):
    movie_id_str = str(movie_id)
    movie_data = fetch_movie_detail_cached(movie_id, language='vi-VN', append_credits=True)
    extra = MovieExtra.query.get(movie_id_str)

    if not movie_data and not extra:
        flash("Không tìm thấy thông tin phim.", "danger")
        return redirect(url_for('movie.home'))

    if not movie_data:
        movie_data = {}

    if extra and extra.trailer_id:
        match = re.search(r'(?:v=|\/|embed\/|youtu\.be\/)([a-zA-Z0-9_-]{11})', extra.trailer_id)
        t_id = match.group(1) if match else extra.trailer_id
        trailer_url = f"https://www.youtube.com/embed/{t_id}?autoplay=1"
    else:
        videos_data = fetch_movie_videos(movie_id)
        trailer_key = get_trailer_key(videos_data)
        trailer_url = f"https://www.youtube.com/embed/{trailer_key}?autoplay=1" if trailer_key else None

    tmdb_score = movie_data.get("vote_average", 0)
    tmdb_count = movie_data.get("vote_count", 0)
    local_ratings = Rating.query.filter_by(movie_id=movie_id_str).all()
    local_count = len(local_ratings)
    local_sum = sum(r.score for r in local_ratings)
    user_avg_ratings = db.session.query(
        func.avg(Comment.final_rating).label('avg_score')
    ).filter(
        Comment.story_id == movie_id_str,
        Comment.final_rating > 0,
        Comment.content != ""
    ).group_by(Comment.user_id).all()

    ai_count = len(user_avg_ratings)
    ai_sum = sum(r.avg_score for r in user_avg_ratings)
    ai_overall_score = ai_sum / ai_count if ai_count > 0 else 0
    total_votes = tmdb_count + local_count + ai_count

    if total_votes > 0:
        combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes
    else:
        combined_average = tmdb_score

    if extra and extra.poster_url:
        p_path = extra.poster_url
        p_url = p_path if p_path.startswith('http') else f"https://image.tmdb.org/t/p/w500/{p_path.lstrip('/')}"
    elif movie_data.get('poster_path'):
        p_path = movie_data.get('poster_path')
        p_url = f"https://image.tmdb.org/t/p/w500/{p_path.lstrip('/')}"
    else:
        p_url = url_for('static', filename='image/404.jpg')

    if extra and extra.backdrop_url:
        b_path = extra.backdrop_url
        b_url = b_path if b_path.startswith('http') else f"https://image.tmdb.org/t/p/original/{b_path.lstrip('/')}"
    elif movie_data.get('backdrop_path'):
        b_path = movie_data.get('backdrop_path')
        b_url = f"https://image.tmdb.org/t/p/original/{b_path.lstrip('/')}"
    else:
        b_url = None

    LANG_MAP = {"en": "Anh Quốc", "vi": "Việt Nam", "th": "Thái Lan", "ko": "Hàn Quốc", "ja": "Nhật Bản", "zh": "Trung Quốc"}
    raw_lang = extra.original_language if (extra and extra.original_language) else movie_data.get("original_language")

    raw_genres = extra.genres.split(",") if (extra and extra.genres) else [g["name"] for g in movie_data.get("genres", [])]
    clean_genres = [g.replace("Phim ", "").strip() for g in raw_genres]
    details = {
        "title": extra.title if (extra and extra.title) else movie_data.get("title", "Phim chưa có tên"),
        "release_date": extra.release_date if (extra and extra.release_date) else movie_data.get("release_date", "N/A"),
        "overview": extra.overview if (extra and extra.overview) else movie_data.get("overview", "Đang cập nhật nội dung..."),
        "poster_url": p_url,
        "backdrop_url": b_url,
        "original_language": LANG_MAP.get(raw_lang, raw_lang if raw_lang else "Không xác định"),
        "runtime": extra.runtime if (extra and extra.runtime) else movie_data.get("runtime", 0),
        "director": extra.director if (extra and extra.director) else next((c["name"] for c in movie_data.get("credits", {}).get("crew", []) if c.get("job") == "Director"), "Đang cập nhật"),
        "cast": extra.cast.split(",") if (extra and extra.cast) else [c["name"] for c in movie_data.get("credits", {}).get("cast", [])[:10]],
        "genres": clean_genres,
        "vote_average": round(combined_average, 1),
        "vote_count": total_votes,
        "ai_score": round(ai_overall_score, 1),
        "ai_count": ai_count,
        "trailer_url": trailer_url
    }

    comments = Comment.query.filter_by(story_id=movie_id_str, parent_id=None).order_by(Comment.date_commented.desc()).all()

    current_user = None
    user_rating = None
    if "user_email" in session:
        current_user = User.query.filter_by(email=session["user_email"]).first()
        if current_user:
            rating_obj = Rating.query.filter_by(user_id=current_user.id, movie_id=movie_id_str).first()
            user_rating = rating_obj.score if rating_obj else None

    now = datetime.now()
    end_date = now + timedelta(days=7)
    showtimes = Showtime.query.filter(
        Showtime.movie_id == movie_id_str,
        Showtime.start_time >= now,
        Showtime.start_time < end_date
    ).order_by(Showtime.start_time).all()

    has_real_showtimes = len(showtimes) > 0

    weekday_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    grouped_showtimes = {}
    today_date = now.date()

    for i in range(7):
        target_date = today_date + timedelta(days=i)
        date_key = target_date.strftime('%d/%m/%Y')
        weekday = weekday_map[target_date.weekday()]
        label = "Hôm Nay" if i == 0 else weekday
        grouped_showtimes[date_key] = {"label": label, "cinemas": {}}

    for st in showtimes:
        date_key = st.start_time.strftime('%d/%m/%Y')
        cinema_name = st.room.cinema.name
        if date_key in grouped_showtimes:
            if cinema_name not in grouped_showtimes[date_key]["cinemas"]:
                grouped_showtimes[date_key]["cinemas"][cinema_name] = []
            grouped_showtimes[date_key]["cinemas"][cinema_name].append(st)

    return render_template("movies.html",
                        movie=details,
                        movie_id=movie_id_str,
                        comments=comments,
                        current_user=current_user,
                        user_rating=user_rating,
                        grouped_showtimes=grouped_showtimes,
                        has_showtimes=has_real_showtimes)

@movie_bp.route('/phim')
def all_movies():
    genre_map = fetch_genres()
    now = datetime.now()
    
    active_showtimes = db.session.query(Showtime.movie_id).filter(
        Showtime.start_time >= now
    ).distinct().all()
    active_ids = [str(s[0]) for s in active_showtimes]

    now_playing_raw = fetch_list_cached("movie/now_playing", page=1, language="vi-VN", region="VN")
    upcoming_raw = fetch_list_cached("movie/upcoming", page=1, language="vi-VN", region="VN")
    
    tmdb_dict = {str(m.get("id")): m for m in now_playing_raw + upcoming_raw}

    def process_with_extra(movie_id):
        movie = tmdb_dict.get(str(movie_id))
        
        tmdb_score = 0
        tmdb_count = 0
        title = "Chưa có tiêu đề"
        desc = "Không có mô tả."
        genre_names = ["Khác"]
        img = "https://via.placeholder.com/500x750?text=No+Poster"

        if movie:
            title = movie.get("title")
            desc = movie.get("overview")
            raw_genres = get_genre_names(movie.get("genre_ids", []), genre_map)
            full_str = ", ".join(raw_genres) if isinstance(raw_genres, list) else str(raw_genres)
            clean_str = full_str.replace("/", ",").replace("Phim ", "").replace("phim ", "").strip()
            
            if clean_str:
                genre_names = [g.strip() for g in clean_str.split(',') if g.strip()]
            poster_path = movie.get('poster_path')
            img = f"https://image.tmdb.org/t/p/w500{poster_path}" if poster_path else "https://via.placeholder.com/500x750?text=No+Poster"
            tmdb_score = movie.get("vote_average", 0)
            tmdb_count = movie.get("vote_count", 0)
        else:
            extra = MovieExtra.query.get(str(movie_id))
            if not extra:
                return None
            title = extra.title
            desc = extra.overview or "Không có mô tả."
            if extra.genres:
                genre_names = [g.strip() for g in extra.genres.split(',')]
            else:
                genre_names = ["Khác"]
            img = extra.poster_url if extra.poster_url else "https://via.placeholder.com/500x750?text=No+Poster"
            tmdb_score = 0
            tmdb_count = 0

        local_ratings = Rating.query.filter_by(movie_id=str(movie_id)).all()
        local_sum = sum(r.score for r in local_ratings)
        local_count = len(local_ratings)

        user_avg_ratings = db.session.query(func.avg(Comment.final_rating)).filter(
            Comment.story_id == str(movie_id), 
            Comment.final_rating > 0, 
            Comment.content != ""
        ).group_by(Comment.user_id).all()

        ai_sum = sum(r[0] for r in user_avg_ratings if r[0] is not None)
        ai_count = len(user_avg_ratings)

        total_votes = tmdb_count + local_count + ai_count
        combined_average = ((tmdb_score * tmdb_count) + local_sum + ai_sum) / total_votes if total_votes > 0 else 0

        return {
            "id": movie_id,
            "title": title,
            "genre": genre_names,
            "desc": desc,
            "vote_average": round(float(combined_average), 1),
            "img": img
        }

    movies_data_from_server = []
    for mid in active_ids:
        p = process_with_extra(mid)
        if p: movies_data_from_server.append(p)
    
    upcoming_data_from_server = []
    for m in upcoming_raw:
        p = process_with_extra(m.get("id"))
        if p: upcoming_data_from_server.append(p)

    return render_template("all-movies.html",
                           movies_data_from_server=movies_data_from_server,
                           upcoming_data_from_server=upcoming_data_from_server,
                           title="Danh Sách Phim")

@movie_bp.route("/movies")
def movies():
    return render_template("movies.html")

@movie_bp.route("/add_comment/<int:movie_id>", methods=["POST"])
def add_comment(movie_id):
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để bình luận", "warning")
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=session["user_email"]).first()
    content = request.form.get("content")
    parent_id = request.form.get("parent_id")
    reply_to_id = request.form.get("reply_to_id")

    user_stars = request.form.get("score", 0)

    if content:
        ai_score = analyze_sentiment(content) 
        sentiment_10 = (ai_score + 1) * 5       
        
        final_rating = sentiment_10

        new_comment = Comment(
            story_id=movie_id,
            user_id=user.id,
            content=content,
            parent_id=parent_id if parent_id else None,
            reply_to_id=reply_to_id if reply_to_id else None,
            sentiment_score=ai_score,
            stars=user_stars,
            final_rating=final_rating
        )
        db.session.add(new_comment)
        db.session.commit()
    
    return redirect(url_for('movie.movie_detail', movie_id=movie_id))

@movie_bp.route("/delete_comment/<int:comment_id>")
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    movie_id = comment.story_id
    
    user = User.query.filter_by(email=session.get("user_email")).first()
    if user and comment.user_id == user.id:
        db.session.delete(comment)
        db.session.commit()
        flash("Đã xóa bình luận", "success")
    
    return redirect(url_for('movie.movie_detail', movie_id=movie_id))

@movie_bp.route('/rate_movie/<int:movie_id>', methods=['POST'])
def rate_movie(movie_id):
    if "user_email" not in session:
        flash("Bạn cần đăng nhập để đánh giá.", "warning")
        return redirect(url_for('auth.login'))

    user = User.query.filter_by(email=session["user_email"]).first()
    score = request.form.get('score')

    if user and score:
        rating = Rating.query.filter_by(user_id=user.id, movie_id=movie_id).first()
        if rating:
            rating.score = float(score) 
        else:
            new_rating = Rating(user_id=user.id, movie_id=movie_id, score=float(score))
            db.session.add(new_rating)
        
        db.session.commit()
        flash(f"Cảm ơn bạn đã đánh giá {score} sao!", "success")

    return redirect(url_for('movie.movie_detail', movie_id=movie_id))

@movie_bp.route('/cinemas')
@movie_bp.route('/cinemas/<int:cinema_id>')
def cinemas(cinema_id=None):
    all_cinemas = Cinema.query.all()
    selected_cinema = None
    grouped_data = {}
    
    now = datetime.now()
    date_tabs = []
    weekday_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    
    for i in range(7):
        target_date = now + timedelta(days=i)
        date_str = target_date.strftime('%d/%m/%Y')
        date_tabs.append({'date': date_str, 'label': "Hôm Nay" if i == 0 else weekday_map[target_date.weekday()]})

    if cinema_id:
        selected_cinema = Cinema.query.get_or_404(cinema_id)
        end_date = now.replace(hour=23, minute=59) + timedelta(days=7)
        showtimes = Showtime.query.join(Room).filter(
            Room.cinema_id == cinema_id,
            Showtime.start_time >= now,
            Showtime.start_time < end_date
        ).order_by(Showtime.start_time).all()

        for st in showtimes:
            date_key = st.start_time.strftime('%d/%m/%Y')
            if date_key not in grouped_data:
                grouped_data[date_key] = {}

            m_id = st.movie_id
            if m_id not in grouped_data[date_key]:
                m_info = fetch_movie_detail_cached(m_id, language='vi-VN', append_credits=False)
                poster_base = current_app.config.get("TMDB_IMAGE_BASE_URL", "")
                grouped_data[date_key][m_id] = {
                    "title": m_info.get("title") if m_info else "Phim không tên",
                    "poster": f"{poster_base}{m_info.get('poster_path')}" if m_info and m_info.get('poster_path') else "",
                    "showtimes": []
                }
            
            time_str = st.start_time.strftime('%H:%M')
            room_name = st.room.name if getattr(st, "room", None) else "N/A"
            is_duplicate = any(s['time'] == time_str and s['room_name'] == room_name 
                            for s in grouped_data[date_key][m_id]["showtimes"])
            if not is_duplicate:
                grouped_data[date_key][m_id]["showtimes"].append({
                    'id': st.id,
                    'time': time_str,
                    'room_name': room_name,
                    'movie_id': m_id 
                })
    return render_template('cinemas.html', 
                        all_cinemas=all_cinemas, 
                        selected_cinema=selected_cinema, 
                        grouped_data=grouped_data,
                        date_tabs=date_tabs)

API_KEYS_STR = os.getenv("GEMINI_API_KEY", "")
API_KEYS_LIST = [key.strip() for key in API_KEYS_STR.split(",") if key.strip()]

def analyze_sentiment(comment_text):
    if not API_KEYS_LIST:
        print("!!! Lỗi: Không tìm thấy API Key nào trong biến môi trường.")
        return 0.0

    selected_key = random.choice(API_KEYS_LIST)
    genai.configure(api_key=selected_key)
    
    model = genai.GenerativeModel('gemini-3-flash-preview') 
    
    prompt = f"""
    Phân tích cảm xúc bình luận phim: "{comment_text}"
    Trả về duy nhất JSON: {{"score": float}}
    Score từ -1.0 đến 1.0. Chỉ trả về JSON.
    """
    
    try:
        response = model.generate_content(
            prompt, 
            generation_config={"response_mime_type": "application/json"}
        )
        
        data = json.loads(response.text)
        return float(data.get('score', 0.0))
    except Exception as e:
        print(f"Lỗi với Key {selected_key[:10]}... : {e}")
        return 0.0

def get_personalized_recommendations(user_id, movies_from_showtimes):
    user_bookings = Booking.query.filter_by(user_id=user_id).all()
    if not user_bookings or not movies_from_showtimes:
        return sorted(movies_from_showtimes, key=lambda x: x.get('vote_average', 0), reverse=True)[:5]

    watched_movie_ids = {str(b.movie_id) for b in user_bookings if b.movie_id}
    user_interests_list = []

    for b in user_bookings:
        mid_str = str(b.movie_id)
        current_match = next((m for m in movies_from_showtimes if str(m.get('id')) == mid_str), None)
        if current_match:
            g_list = current_match.get('genre', [])
            user_interests_list.extend([str(i).lower() for i in g_list] if isinstance(g_list, list) else [str(g_list).lower()])
        else:
            extra = MovieExtra.query.get(mid_str)
            if extra and extra.genres:
                genres = extra.genres.lower().replace("/", " ").replace(",", " ").split()
                user_interests_list.extend(genres)

    user_profile_text = " ".join(set(user_interests_list)).strip()

    try:
        def normalize_movie_text(m):
            g = m.get('genre', [])
            return " ".join(g).lower() if isinstance(g, list) else str(g).lower()

        movie_texts = [normalize_movie_text(m) for m in movies_from_showtimes]
        cv = CountVectorizer()
        vectors = cv.fit_transform(movie_texts + [user_profile_text])
        sim_scores = cosine_similarity(vectors[-1], vectors[:-1])[0]
    except:
        return sorted(movies_from_showtimes, key=lambda x: x.get('vote_average', 0), reverse=True)[:5]

    all_candidates = []
    
    for i, sim in enumerate(sim_scores):
        movie = movies_from_showtimes[i]
        m_id = str(movie.get('id'))

        t_score = float(movie.get("tmdb_score", 0))
        t_count = int(movie.get("tmdb_count", 0))
        
        local_ratings = Rating.query.filter_by(movie_id=m_id).all()
        l_count = len(local_ratings)
        l_sum = float(sum(r.score for r in local_ratings) or 0)

        user_avg_ratings = db.session.query(
            func.avg(Comment.final_rating).label('avg_score')
        ).filter(
            Comment.story_id == m_id,
            Comment.final_rating > 0,
            Comment.content != ""
        ).group_by(Comment.user_id).all()

        a_count = len(user_avg_ratings)
        a_sum = float(sum(r.avg_score for r in user_avg_ratings) or 0)
        
        total_v = t_count + l_count + a_count
        
        if total_v > 0:
            final_score = ((t_score * t_count) + l_sum + a_sum) / total_v
            movie['vote_average'] = round(final_score, 1)
        else:
            movie['vote_average'] = t_score

        if m_id not in watched_movie_ids:
            all_candidates.append({'movie': movie, 'sim': float(sim)})

    all_candidates.sort(key=lambda x: x['sim'], reverse=True)
    top_5_sim_objects = all_candidates[:5]

    if len(top_5_sim_objects) < 5:
        already_ids = {str(obj['movie']['id']) for obj in top_5_sim_objects} | watched_movie_ids
        fillers = [m for m in movies_from_showtimes if str(m.get('id')) not in already_ids]
        fillers.sort(key=lambda x: x.get('vote_average', 0), reverse=True)
        for f in fillers[:(5 - len(top_5_sim_objects))]:
            top_5_sim_objects.append({'movie': f, 'sim': 0})

    top_5_sim_objects.sort(key=lambda x: x['movie'].get('vote_average', 0), reverse=True)
    return [obj['movie'] for obj in top_5_sim_objects]