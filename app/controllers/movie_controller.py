from flask import Blueprint, render_template, request, jsonify, session, flash, redirect, url_for, current_app
from datetime import datetime, timedelta
from app.models import Comment, Rating, User, Showtime, Cinema, Room, Concession, MovieExtra
from app.extensions import db, cache
from app.utils.tmdb import fetch_from_tmdb, fetch_movies_list
import google.generativeai as genai
import json, os, re
from dotenv import load_dotenv
from sqlalchemy import func

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
        
        if extra and extra.poster_url:
            custom_poster = extra.poster_url
        else:
            custom_poster = f"{current_app.config['TMDB_IMAGE_BASE_URL']}{movie.get('poster_path')}" if movie.get('poster_path') else None
        genre_names = get_genre_names(movie.get("genre_ids", []), genre_map)
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
        combined_average = 0
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
        current_movies_list.append(movie_info)
        if movie_info.get('backdrop_url') and len(featured_movies_list) < 7:
            featured_movies_list.append(movie_info)

    for movie in upcoming_data:
        movie_id = movie.get("id")
        extra = MovieExtra.query.get(str(movie_id))
        
        if extra and extra.poster_url:
            custom_poster = extra.poster_url
        else:
            custom_poster = f"{current_app.config['TMDB_IMAGE_BASE_URL']}{movie.get('poster_path')}" if movie.get('poster_path') else None
        genre_names = get_genre_names(movie.get("genre_ids", []), genre_map)
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
        combined_average = 0
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

    return render_template('home.html',
                            movies=current_movies_list[:8],
                            featured_movies=featured_movies_list,
                            upcoming=upcoming_list)

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
            img = f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url"
            tmdb_score = movie.get("vote_average", 0)
            tmdb_count = movie.get("vote_count", 0)
        else:
            extra = MovieExtra.query.get(movie_id)
            if not extra:
                continue
            title = extra.title
            desc = extra.overview or "Không có mô tả."
            genre_names = [extra.genres] if extra.genres else ["Khác"]
            img = extra.poster_url or "placeholder_url"
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
            img_url = extra.poster_url
        else:
            img_url = f"https://image.tmdb.org/t/p/w500{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url"
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
        combined_average = 0

    LANG_MAP = {"en": "Anh Quốc", "vi": "Việt Nam", "th": "Thái Lan", "ko": "Hàn Quốc", "ja": "Nhật Bản", "zh": "Trung Quốc"}
    raw_lang = extra.original_language if (extra and extra.original_language) else movie_data.get("original_language")
    details = {
        "title": extra.title if (extra and extra.title) else movie_data.get("title", "Phim chưa có tên"),
        "release_date": extra.release_date if (extra and extra.release_date) else movie_data.get("release_date", "N/A"),
        "overview": extra.overview if (extra and extra.overview) else movie_data.get("overview", "Đang cập nhật nội dung..."),
        "poster_url": extra.poster_url if (extra and extra.poster_url) else (f"{current_app.config['TMDB_IMAGE_BASE_URL']}{movie_data.get('poster_path')}" if movie_data.get('poster_path') else url_for('static', filename='img/default_poster.jpg')),
        "backdrop_url": extra.backdrop_url if (extra and extra.backdrop_url) else (f"{current_app.config['TMDB_BACKDROP_BASE_URL']}{movie_data.get('backdrop_path')}" if movie_data.get('backdrop_path') else None),
        "original_language": LANG_MAP.get(raw_lang, raw_lang if raw_lang else "Không xác định"),
        "runtime": extra.runtime if (extra and extra.runtime) else movie_data.get("runtime", 0),
        "director": extra.director if (extra and extra.director) else next((c["name"] for c in movie_data.get("credits", {}).get("crew", []) if c.get("job") == "Director"), "Đang cập nhật"),
        "cast": extra.cast.split(",") if (extra and extra.cast) else [c["name"] for c in movie_data.get("credits", {}).get("cast", [])[:10]],
        "genres": extra.genres.split(",") if (extra and extra.genres) else [g["name"] for g in movie_data.get("genres", [])],
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

@movie_bp.route("/all-movies")
def all_movies():
    return render_template("all-movies.html")

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

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def analyze_sentiment(comment_text):
    model = genai.GenerativeModel('gemini-3-flash-preview')
    prompt = f"""
    Phân tích cảm xúc bình luận phim: "{comment_text}"
    Trả về duy nhất JSON: {{"score": float}}
    Score từ -1.0 đến 1.0. Chỉ trả về JSON.
    """
    try:
        response = model.generate_content(prompt)
        clean_text = response.text.strip().replace('```json', '').replace('```', '')
        data = json.loads(clean_text)
        return float(data.get('score', 0.0))
    except:
        return 0.0