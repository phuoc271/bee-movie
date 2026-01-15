from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from flask_caching import Cache
from flask_sqlalchemy import SQLAlchemy 
from flask_mail import Mail, Message
from itsdangerous import URLSafeTimedSerializer
from werkzeug.security import generate_password_hash, check_password_hash
from concurrent.futures import ThreadPoolExecutor
from werkzeug.utils import secure_filename
from datetime import datetime , timedelta ,time as timeobj
from collections import defaultdict

import google.auth.transport.requests
import google.oauth2.id_token
import requests
import os
import time

app = Flask(__name__)

app.secret_key = "mysecretkey123456"
app.config['SECRET_KEY'] = app.secret_key
app.config['SECURITY_PASSWORD_SALT'] = 'some-random-salt-value'


cache = Cache(app, config={'CACHE_TYPE': 'simple'})

app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:@localhost:3306/bee_movie_db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'static/uploads/avatars'

db = SQLAlchemy(app)

# USER MODEL 
class User(db.Model):
    """Mô hình người dùng ánh xạ tới bảng 'users' trong MySQL."""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False) 
    username = db.Column(db.String(80), unique=True, nullable=False)
    fullname = db.Column(db.String(120), nullable=True)
    gender = db.Column(db.String(10), nullable=True)
    avatar = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(256), nullable=True) 
    comments = db.relationship('Comment',foreign_keys='Comment.user_id',back_populates='user',cascade="all, delete-orphan")    
    def set_password(self, password):
        """Hash mật khẩu và lưu vào cột password_hash."""
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        """Kiểm tra mật khẩu nhập vào có khớp với mật khẩu hash không."""
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.email}>'

class Comment(db.Model):
    __tablename__ = 'comments'
    id = db.Column(db.Integer, primary_key=True)
    story_id = db.Column(db.Integer, nullable=False) 
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    content = db.Column(db.Text, nullable=False)
    date_commented = db.Column(db.DateTime, default=db.func.current_timestamp())
    
    parent_id = db.Column(db.Integer, db.ForeignKey('comments.id'), nullable=True)
    reply_to_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)

    user = db.relationship('User', foreign_keys=[user_id], backref='user_comments')
    reply_to_user = db.relationship('User', foreign_keys=[reply_to_id])
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Comment {self.content[:20]}>'

class Rating(db.Model):
    __tablename__ = 'ratings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    movie_id = db.Column(db.Integer, nullable=False) 
    score = db.Column(db.Float, nullable=False)
    user = db.relationship('User', backref=db.backref('user_ratings', lazy=True))

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    showtime_id = db.Column(db.Integer, nullable=False)  
    movie_id = db.Column(db.Integer, nullable=False)
    seat_code = db.Column(db.String(10), nullable=False) 
    booking_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')
    hold_expiry = db.Column(db.DateTime, nullable=True)

class Cinema(db.Model):
    __tablename__ = 'cinemas'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(255))
    rooms = db.relationship('Room', backref='cinema', lazy=True)

class Room(db.Model):
    __tablename__ = 'rooms'
    id = db.Column(db.Integer, primary_key=True)
    cinema_id = db.Column(db.Integer, db.ForeignKey('cinemas.id'), nullable=False)
    name = db.Column(db.String(50), nullable=False) 
    capacity = db.Column(db.Integer, default=50)
    showtimes = db.relationship('Showtime', backref='room', lazy=True)

class Showtime(db.Model):
    __tablename__ = 'showtimes'
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, nullable=False) 
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    price = db.Column(db.Integer, default=75000)
# Cấu hình Mail 
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'nguyenhoaiphuoc271@gmail.com'
app.config['MAIL_PASSWORD'] = 'khrh snlo wgth pdeu'
mail = Mail(app)

# TMDB config & HTTP session 
TMDB_API_KEY = "f39ba5c15f6a58e7a1bfec8acefe938e"
TMDB_BASE_URL = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE_URL = "https://image.tmdb.org/t/p/w500"
TMDB_BACKDROP_BASE_URL = "https://image.tmdb.org/t/p/original"
http = requests.Session()
REQUEST_TIMEOUT = 30

# Token reset password 
def get_user_by_email(email):
    """Tìm người dùng bằng email trong DB."""
    return User.query.filter_by(email=email).first()

def get_reset_token(user_email, expires_sec=1800):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return s.dumps(user_email, salt=app.config['SECURITY_PASSWORD_SALT'])

def verify_reset_token(token, max_age=1800):
    s = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = s.loads(token, salt=app.config['SECURITY_PASSWORD_SALT'], max_age=max_age)
    except Exception as e:
        print("VERIFY TOKEN ERROR:", e)
        return None
    return email

# Sử dụng memoize để cache theo args của hàm (endpoint + params) 
@cache.memoize(timeout=600)
def fetch_from_tmdb(endpoint, params=None):
    """Hàm chung gọi TMDB, cache theo endpoint + params (memoize)."""
    if params is None:
        params = {}
    params = dict(params) 
    params['api_key'] = TMDB_API_KEY
    url = f"{TMDB_BASE_URL}/{endpoint.lstrip('/')}"
    try:
        resp = http.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"[TMDB ERROR] endpoint={endpoint} params={params} -> {e}")
        return None

# Memoize: cache theo movie_id 
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

# Cache danh sách thể loại (một lần, hoặc 2 giờ) 
GENRE_MAP = {}
@cache.cached(timeout=7200, key_prefix="tmdb_genres")
def fetch_genres():
    global GENRE_MAP
    if GENRE_MAP:
        return GENRE_MAP
    data = fetch_from_tmdb("genre/movie/list", params={"language": "vi-VN"})
    if not data:
        return {}
    GENRE_MAP = {g['id']: g['name'] for g in data.get('genres', [])}
    return GENRE_MAP

def get_genre_names(genre_ids, genre_map):
    if not genre_ids or not genre_map:
        return "Chưa rõ"
    names = [genre_map.get(gid, 'N/A') for gid in genre_ids]
    return " / ".join(names)

# Thêm một hàm chuyên cho gọi list phim (memoize dựa trên endpoint+params) 
@cache.memoize(timeout=600)
def fetch_movies_list(endpoint, params=None):
    data = fetch_from_tmdb(endpoint, params=params)
    
    if not data or not data.get('results'):
        print(f"Dữ liệu {endpoint} bị trống, đang thử lấy bản quốc tế...")
        params.pop('region', None) 
        params['language'] = 'en-US'
        data = fetch_from_tmdb(endpoint, params=params)
        
    if not data:
        return []
    return data.get('results', [])

@app.context_processor
def inject_user():
    return dict(session=session)
@app.route('/')
def home():
    """Trang chủ: lấy now_playing + upcoming. Gọi song song để tăng tốc."""
    genre_map = fetch_genres()

    with ThreadPoolExecutor(max_workers=2) as ex:
        fut_now = ex.submit(fetch_movies_list, "movie/now_playing", {"page": 1, "language": "vi-VN", "region": "VN"})
        fut_up = ex.submit(fetch_movies_list, "movie/upcoming", {"page": 1, "language": "vi-VN", "region": "VN"})
        now_playing_movies_data = fut_now.result()
        upcoming_data = fut_up.result()

    current_movies_list = []
    featured_movies_list = []
    upcoming_list = []

    for movie in now_playing_movies_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)
        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "release_date": movie.get("release_date"),
            "overview": movie.get("overview"),
            "genre": genre_names,
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        current_movies_list.append(movie_info)
        if movie_info.get('backdrop_url') and len(featured_movies_list) < 7:
            featured_movies_list.append(movie_info)

    for movie in upcoming_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)
        movie_info = {
            "id": movie.get("id"),
            "title": movie.get("title"),
            "release_date": movie.get("release_date"),
            "overview": movie.get("overview"),
            "genre": genre_names,
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie.get('backdrop_path')}" if movie.get('backdrop_path') else None,
        }
        upcoming_list.append(movie_info)

    return render_template('home.html',
                           movies=current_movies_list[:8],
                           featured_movies=featured_movies_list,
                           upcoming=upcoming_list)

@app.route("/now-playing")
@cache.cached(timeout=600, key_prefix="cache_now_playing_page")
def now_playing():
    genre_map = fetch_genres()
    all_now_playing_movies = fetch_movies_list("movie/now_playing", {"page": 1, "language": "vi-VN", "region": "VN"})
    movie_list = []
    for movie in all_now_playing_movies:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)
        movie_list.append({
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names,
            "desc": movie.get("overview"),
            "img": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url",
        })
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Đang Chiếu")

@app.route("/upcoming")
@cache.cached(timeout=600, key_prefix="cache_upcoming_page")
def upcoming():
    genre_map = fetch_genres()
    upcoming_data = fetch_movies_list("movie/upcoming", {"page": 1, "language": "vi-VN", "region": "VN"})
    movie_list = []
    for movie in upcoming_data:
        genre_ids = movie.get("genre_ids", [])
        genre_names = get_genre_names(genre_ids, genre_map)
        movie_list.append({
            "id": movie.get("id"),
            "title": movie.get("title"),
            "genre": genre_names,
            "desc": movie.get("overview"),
            "img": f"{TMDB_IMAGE_BASE_URL}{movie.get('poster_path')}" if movie.get('poster_path') else "placeholder_url",
        })
    return render_template("all-movies.html", movies_data_from_server=movie_list, title="Phim Sắp Chiếu")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user = get_user_by_email(email) 

        if user and user.password_hash and user.check_password(password):
            session["user_id"] = user.id
            session["user_email"] = user.email
            session["username"] = user.username
            session["fullname"] = user.fullname
            session["avatar"] = user.avatar 
            session["gender"] = user.gender
            flash("Đăng nhập thành công!", "success")
            return redirect("/")
        else:
            flash("Email hoặc mật khẩu sai!", "danger")
    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        fullname = request.form["fullname"]
        email = request.form["email"]
        username = request.form["username"]
        gender = request.form.get("gender")
        password = request.form["password"]
        confirm_password = request.form["confirm_password"]
        if password != confirm_password:
            flash("Mật khẩu không khớp!", "danger")
            return redirect(url_for("register"))
        if get_user_by_email(email):
            flash("Email đã tồn tại!", "warning")
            return redirect(url_for("register"))
        if User.query.filter_by(username=username).first():
            flash("Tên người dùng đã tồn tại!", "warning")
            return redirect(url_for("register"))
        new_user = User(
            fullname=fullname,
            email=email,
            username=username,
            gender=gender,
        )
        new_user.set_password(password) 
        
        try:
            db.session.add(new_user)
            db.session.commit()
            session["user_id"] = new_user.id 
            session["user_email"] = new_user.email
            session["username"] = new_user.username
            flash("Tạo tài khoản thành công!", "success")
            return redirect(url_for("home")) 
        except Exception as e:
            db.session.rollback()
            print(f"LỖI DB KHI ĐĂNG KÝ: {e}")
            flash("Có lỗi xảy ra, không thể tạo tài khoản.", "danger")
            return redirect(url_for("register"))
            
    return render_template("register.html")

@app.route("/google-login", methods=["POST"])
def google_login():
    try:
        id_token = request.json.get("credential")
        request_adapter = google.auth.transport.requests.Request()
        user_info = google.oauth2.id_token.verify_oauth2_token(
            id_token,
            request_adapter,
            "326949134175-muq4egv1vofb5ln8dh1fov9vq6nkr25s.apps.googleusercontent.com"
        )
        email = user_info.get("email")
        name = user_info.get("name")
        
        user = get_user_by_email(email) 

        if not user:
            new_user = User(
                fullname=name,
                email=email,
                username=email.split("@")[0],
                password_hash=None 
            )
            db.session.add(new_user)
            db.session.commit()
            user = new_user

        session["user_id"] = user.id
        session["user_email"] = email
        session["username"] = user.username
        session["fullname"] = user.fullname
        session["avatar"] = user.avatar
        session["gender"] = user.gender
        return {"status": "ok"}
    except Exception as e:
        print("GOOGLE LOGIN ERROR:", e)
        if 'db' in globals() and db.session:
            db.session.rollback()
        return {"status": "error", "message": str(e)}, 400

@app.route("/logout")
def logout():
    session.clear()
    flash("Đã đăng xuất!", "info")
    return redirect(url_for('login'))

@app.route("/reset_password", methods=["GET", "POST"])
def reset_request():
    if request.method == "POST":
        email = request.form.get('email')
        
        user = get_user_by_email(email) 

        if user:
            token = get_reset_token(email)
            reset_link = url_for('reset_token', token=token, _external=True)
            msg = Message('Yêu cầu Đặt lại Mật khẩu',
                          sender=app.config['MAIL_USERNAME'],
                          recipients=[email])
            msg.body = f'''Để đặt lại mật khẩu của bạn, nhấn vào link:
{reset_link}

Link hết hạn sau 30 phút.
'''
            try:
                mail.send(msg)
                flash('Email đặt lại mật khẩu đã được gửi!', 'info')
            except Exception as e:
                print("MAIL SEND ERROR:", e)
                flash('Gửi email thất bại. Kiểm tra cấu hình SMTP.', 'danger')
            return redirect(url_for('login'))
        else:
            flash('Email không tồn tại trong hệ thống.', 'danger')
    return render_template('reset_request.html')

@app.route("/reset_password/<token>", methods=['GET', 'POST'])
def reset_token(token):
    email = verify_reset_token(token, max_age=1800)
    if email is None:
        flash('Liên kết đặt lại mật khẩu không hợp lệ hoặc đã hết hạn.', 'warning')
        return redirect(url_for('reset_request'))
    
    user = get_user_by_email(email) 

    if user is None:
        flash('Tài khoản không tồn tại.', 'danger')
        return redirect(url_for('reset_request'))
        
    if request.method == "POST":
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        if new_password != confirm_password:
            flash("Mật khẩu không khớp!", "danger")
            return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)
            
        user.set_password(new_password)
        
        try:
            db.session.commit()
            flash('Mật khẩu của bạn đã được cập nhật!', 'success')
            return redirect(url_for('login'))
        except Exception as e:
            db.session.rollback()
            print(f"LỖI DB KHI CẬP NHẬT MẬT KHẨU: {e}")
            flash('Có lỗi xảy ra, không thể cập nhật mật khẩu.', 'danger')
            
    return render_template('reset_token.html', title='Đặt lại Mật khẩu', token=token)

@app.route("/movie/<int:movie_id>")
def movie_detail(movie_id):
    movie_data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN', 'append_to_response': 'credits'})
    videos_data = fetch_movie_videos(movie_id)
    trailer_key = get_trailer_key(videos_data)
    trailer_url = f"https://www.youtube.com/embed/{trailer_key}?autoplay=1" if trailer_key else None

    if movie_data:
        tmdb_score = movie_data.get("vote_average", 0)
        tmdb_count = movie_data.get("vote_count", 0)
        local_ratings = Rating.query.filter_by(movie_id=movie_id).all()
        local_count = len(local_ratings)
        local_sum = sum(r.score for r in local_ratings)
        total_votes = tmdb_count + local_count
        combined_average = ((tmdb_score * tmdb_count) + local_sum) / total_votes if total_votes > 0 else 0

        LANG_MAP = {"en": "Anh Quốc", "vi": "Việt Nam", "th": "Thái Lan", "ko": "Hàn Quốc", "ja": "Nhật Bản", "zh": "Trung Quốc"}
        details = {
            "title": movie_data.get("title"),
            "release_date": movie_data.get("release_date"),
            "overview": movie_data.get("overview"),
            "tagline": movie_data.get("tagline"),
            "poster_url": f"{TMDB_IMAGE_BASE_URL}{movie_data.get('poster_path')}" if movie_data.get('poster_path') else None,
            "backdrop_url": f"{TMDB_BACKDROP_BASE_URL}{movie_data.get('backdrop_path')}" if movie_data.get('backdrop_path') else None,
            "vote_average": round(combined_average, 1), 
            "vote_count": total_votes,
            "genres": [g["name"] for g in movie_data.get("genres", [])],
            "original_language": LANG_MAP.get(movie_data.get("original_language"), "Không xác định"),
            "runtime": movie_data.get("runtime"),
            "director": next((c["name"] for c in movie_data.get("credits", {}).get("crew", []) if c.get("job") == "Director"), None),
            "cast": [c["name"] for c in movie_data.get("credits", {}).get("cast", [])[:10]],
            "trailer_url": trailer_url
        }

        comments = Comment.query.filter_by(story_id=movie_id, parent_id=None).order_by(Comment.date_commented.desc()).all()
        
        current_user = None
        user_rating = None 
        if "user_email" in session:
            current_user = User.query.filter_by(email=session["user_email"]).first()
            if current_user:
                rating_obj = Rating.query.filter_by(user_id=current_user.id, movie_id=movie_id).first()
                user_rating = rating_obj.score if rating_obj else None

        now = datetime.now()
        end_date = now + timedelta(days=7)
        showtimes = Showtime.query.filter(
            Showtime.movie_id == movie_id,
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
                               movie_id=movie_id, 
                               comments=comments, 
                               current_user=current_user,
                               user_rating=user_rating,
                               grouped_showtimes=grouped_showtimes,
                               has_showtimes=has_real_showtimes) 

    flash("Không tìm thấy thông tin phim.", "danger")
    return redirect(url_for('home'))

@app.route("/all-movies")
def all_movies():
    return render_template("all-movies.html")

@app.route("/movies")
def movies():
    return render_template("movies.html")

@app.route("/profile", methods=["GET", "POST"])
def update_profile():
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để xem hồ sơ.", "warning")
        return redirect(url_for("login"))

    user = get_user_by_email(session["user_email"])

    if request.method == "POST":
        user.fullname = request.form.get("fullname")
        user.username = request.form.get("username")
        user.gender = request.form.get("gender")

        if 'avatar' in request.files:
            file = request.files['avatar']
            if file and file.filename != '':
                if user.avatar:
                    old_physical_path = os.path.join(app.root_path, 'static', user.avatar)
                    
                    if os.path.exists(old_physical_path):
                        try:
                            os.remove(old_physical_path)
                            print(f"DEBUG: Đã xóa file cũ tại {old_physical_path}")
                        except Exception as e:
                            print(f"DEBUG: Lỗi khi xóa file vật lý: {e}")

                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])

                timestamp = int(time.time())
                filename = secure_filename(f"user_{user.id}_{timestamp}_{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                
                file.save(file_path)
                
                user.avatar = f"uploads/avatars/{filename}"

        try:
            db.session.commit()
            
            session["username"] = user.username
            session["fullname"] = user.fullname
            session["gender"] = user.gender 
            session["avatar"] = user.avatar 
            
            flash("Cập nhật hồ sơ thành công!", "success")
        except Exception as e:
            db.session.rollback()
            print(f"Lỗi DB: {e}")
            flash(f"Lỗi khi cập nhật database: {e}", "danger")
        
        return redirect(url_for("update_profile"))

    return render_template("profile.html", user=user)

@app.route("/delete_account", methods=["POST"])
def delete_account():
    if "user_email" not in session:
        return redirect(url_for("login"))
    
    user = get_user_by_email(session["user_email"])
    if not user:
        return redirect(url_for("home"))

    try:
        Comment.query.filter_by(reply_to_id=user.id).update({Comment.reply_to_id: None})
        
        db.session.delete(user)
        db.session.commit()
        session.clear()
        flash("Tài khoản của bạn đã được xóa vĩnh viễn.", "info")
    except Exception as e:
        db.session.rollback()
        print(f"LỖI XÓA TÀI KHOẢN: {e}") 
        flash("Không thể xóa tài khoản do có ràng buộc dữ liệu.", "danger")
        
    return redirect(url_for("home"))

@app.route("/add_comment/<int:movie_id>", methods=["POST"])
def add_comment(movie_id):
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để bình luận", "warning")
        return redirect(url_for('login'))

    user = User.query.filter_by(email=session["user_email"]).first()
    content = request.form.get("content")
    parent_id = request.form.get("parent_id")
    reply_to_id = request.form.get("reply_to_id")

    if content:
        new_comment = Comment(
            story_id=movie_id,
            user_id=user.id,
            content=content,
            parent_id=parent_id if parent_id else None,
            reply_to_id=reply_to_id if reply_to_id else None
        )
        db.session.add(new_comment)
        db.session.commit()
    
    return redirect(url_for('movie_detail', movie_id=movie_id))

@app.route("/delete_comment/<int:comment_id>")
def delete_comment(comment_id):
    comment = Comment.query.get_or_404(comment_id)
    movie_id = comment.story_id
    
    user = User.query.filter_by(email=session.get("user_email")).first()
    if user and comment.user_id == user.id:
        db.session.delete(comment)
        db.session.commit()
        flash("Đã xóa bình luận", "success")
    
    return redirect(url_for('movie_detail', movie_id=movie_id))

@app.route('/rate_movie/<int:movie_id>', methods=['POST'])
def rate_movie(movie_id):
    if "user_email" not in session:
        flash("Bạn cần đăng nhập để đánh giá.", "warning")
        return redirect(url_for('login'))

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

    return redirect(url_for('movie_detail', movie_id=movie_id))

@app.route("/booking/<int:showtime_id>")
def booking(showtime_id):
    if "user_email" not in session:
        flash("Vui lòng đăng nhập để đặt vé", "warning")
        return redirect(url_for('login'))
    Booking.query.filter(
        (Booking.status == 'pending') & (Booking.hold_expiry < datetime.utcnow())
    ).delete()
    db.session.commit()

    movie_id = request.args.get('movie_id') 
    movie_data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN'})
    showtime = Showtime.query.get(showtime_id)
    
    if not showtime:
        flash("Suất chiếu không tồn tại", "danger")
        return redirect(url_for("home"))
    now = datetime.utcnow()
    booked_seats = Booking.query.filter(
        (Booking.showtime_id == showtime_id) & 
        (
            (Booking.status == 'confirmed') | 
            ((Booking.status == 'pending') & (Booking.hold_expiry > now))
        )
    ).all()
    
    occupied_seats = [b.seat_code for b in booked_seats]
    cinema_name = showtime.room.cinema.name
    room_name = showtime.room.name
    show_date = showtime.start_time.strftime("%d/%m/%Y")
    show_hour = showtime.start_time.strftime("%H:%M")

    return render_template("booking.html", 
                           showtime_id=showtime_id, 
                           movie=movie_data, 
                           occupied_seats=occupied_seats,
                           cinema_name=cinema_name,
                           room_name=room_name,
                           show_date=show_date,
                           show_hour=show_hour)

@app.route("/confirm_booking", methods=['POST'])
def confirm_booking():
    data = request.get_json()
    showtime = Showtime.query.get(data.get('showtime_id'))
    user_id = session.get('user_id') 
    if not user_id:
        return jsonify({"status": "error", "message": "Vui lòng đăng nhập lại"}), 401
    expiry = datetime.utcnow() + timedelta(minutes=3)
    
    try:
        Booking.query.filter_by(
            user_id=user_id, 
            showtime_id=data.get('showtime_id'), 
            status='pending'
        ).delete()
        for seat in data.get('seats'):
            hold = Booking(
                user_id=user_id,
                showtime_id=data.get('showtime_id'),
                movie_id=data.get('movie_id'),
                seat_code=seat,
                status='pending',
                hold_expiry=expiry
            )
            db.session.add(hold)
        
        db.session.commit()
        session['temp_booking'] = {
            'showtime_id': data.get('showtime_id'),
            'movie_id': data.get('movie_id'),
            'seats': data.get('seats'),
            'total_price': data.get('total_price'),
            'movie_title': data.get('movie_title'),
            'cinema_name': showtime.room.cinema.name if showtime else None,
            'room_name': showtime.room.name if showtime else None,
            'show_date': showtime.start_time.strftime("%d/%m/%Y") if showtime else None,
            'show_time': showtime.start_time.strftime("%H:%M") if showtime else None
        }

        return jsonify({
            "status": "success",
            "redirect": url_for('payment_page'),
            "booking": session['temp_booking']   
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/cancel_booking", methods=['POST'])
def cancel_booking():
    booking_data = session.get('temp_booking')
    user_id = session.get('user_id')
    
    if booking_data and user_id:
        try:
            Booking.query.filter_by(
                user_id=user_id,
                showtime_id=booking_data['showtime_id'],
                status='pending'
            ).delete()
            
            db.session.commit()
            session.pop('temp_booking', None)
            return jsonify({"status": "success"})
        except Exception as e:
            db.session.rollback()
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "error", "message": "Không có giao dịch để hủy"}), 400

@app.route("/payment")
def payment_page():
    booking = session.get('temp_booking')
    if not booking:
        flash("Không có dữ liệu đặt vé!", "warning")
        return redirect(url_for('home'))

    return render_template("payment.html", 
                           movie_title=booking['movie_title'],
                           seat_names=", ".join(booking['seats']),
                           total_price=booking['total_price'],
                           showtime_id=booking['showtime_id'],
                           cinema_name=booking['cinema_name'],
                           room_name=booking['room_name'],
                           show_date=booking['show_date'],
                           show_time=booking['show_time'])


@app.route("/final_confirm_db", methods=['POST'])
def final_confirm_db():
    booking_data = session.get('temp_booking')
    if not booking_data:
        return jsonify({"status": "error", "message": "Phiên làm việc hết hạn"}), 400

    user = User.query.filter_by(email=session['user_email']).first()
    
    try:
        pending_tickets = Booking.query.filter_by(
            user_id=user.id, 
            showtime_id=booking_data['showtime_id'],
            status='pending'
        ).filter(Booking.seat_code.in_(booking_data['seats'])).all()

        if not pending_tickets:
            return jsonify({"status": "error", "message": "Không tìm thấy thông tin giữ chỗ"}), 404

        for ticket in pending_tickets:
            ticket.status = 'confirmed'
            ticket.hold_expiry = None  
        db.session.commit()
        session.pop('temp_booking', None)
        
        return jsonify({
            "status": "success",
            "movie_title": booking_data['movie_title'],
            "seats": ", ".join(booking_data['seats']),
            "total_price": booking_data['total_price']
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/my-tickets')
def my_tickets():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('login'))

    all_bookings = Booking.query.filter_by(user_id=user_id).order_by(Booking.booking_time.desc()).all()
    grouped = defaultdict(list)

    for b in all_bookings:
        key = (b.showtime_id, b.movie_id)
        grouped[key].append(b)

    grouped_tickets = []

    for (showtime_id, movie_id), bookings in grouped.items():
        movie_info = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN'})
        movie_title = movie_info.get('title') if movie_info else "Phim Bee Movie"
        seats_str = ", ".join([b.seat_code for b in bookings])
        booking_time = bookings[0].booking_time.strftime('%d/%m/%Y %H:%M')

        showtime = Showtime.query.get(showtime_id)
        cinema_name = showtime.room.cinema.name if showtime else "N/A"
        room_name = showtime.room.name if showtime else "N/A"
        show_date = showtime.start_time.strftime("%d/%m/%Y") if showtime else "N/A"
        show_hour = showtime.start_time.strftime("%H:%M") if showtime else "N/A"

        qr_text = (f"MÃ VÉ: BEE{bookings[0].id} | "
                   f"PHIM: {movie_title} | "
                   f"GHẾ: {seats_str} | "
                   f"RẠP: {cinema_name} | "
                   f"PHÒNG: {room_name} | "
                   f"NGÀY: {show_date} | "
                   f"SUẤT: {show_hour}")

        grouped_tickets.append({
            'booking_time': bookings[0].booking_time,
            'movie_title': movie_title,
            'movie_poster': f"{TMDB_IMAGE_BASE_URL}{movie_info.get('poster_path')}" if movie_info else "",
            'seats': seats_str,
            'cinema_name': cinema_name,
            'room_name': room_name,
            'show_date': show_date,
            'show_hour': show_hour,
            'full_info_qr': qr_text  
        })

    return render_template('my_tickets.html', bookings=grouped_tickets)

def fetch_runtime_minutes(movie_id):
    """Lấy runtime phim từ TMDB (phút). Nếu thiếu, default 120."""
    data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN'})
    runtime = data.get('runtime') if data else None
    try:
        return int(runtime) if runtime else 120
    except:
        return 120

def pick_hot_and_normal_movies():
    """Lấy danh sách phim now_playing và phân 5 phim hot + danh sách thường."""
    movies = fetch_movies_list("movie/now_playing", {"page": 1, "language": "vi-VN", "region": "VN"})
    if not movies:
        return [], []
    movies_sorted = sorted(movies, key=lambda m: m.get('popularity', 0), reverse=True)
    hot = [m['id'] for m in movies_sorted[:5]]
    normal = [m['id'] for m in movies_sorted[5:]]
    return hot, normal

def ensure_cinemas_and_rooms():
    """Tạo đúng 2 rạp, mỗi rạp 7 phòng nếu chưa có."""
    cinemas_cfg = [
        {"name": "Bee Movie Hùng Vương", "address": "123 Hùng Vương, Q.5"},
        {"name": "Bee Movie Quang Trung", "address": "190 Quang Trung, Gò Vấp"}
    ]
    cinemas = []
    for cfg in cinemas_cfg:
        c = Cinema.query.filter_by(name=cfg["name"]).first()
        if not c:
            c = Cinema(name=cfg["name"], address=cfg["address"])
            db.session.add(c)
            db.session.flush()
        existing_rooms = Room.query.filter_by(cinema_id=c.id).order_by(Room.id).all()
        if len(existing_rooms) < 7:
            for i in range(len(existing_rooms)+1, 8):
                r = Room(cinema_id=c.id, name=f"Phòng {i}")
                db.session.add(r)
            db.session.flush()
        cinemas.append(c)
    db.session.commit()
    return cinemas

def generate_room_timeline(start_dt, end_dt, movie_id):
    """Sinh một timeline liên tục cho 1 phòng với 1 phim (hot) từ 7:00 tới end."""
    slot_start = start_dt
    showtimes = []
    runtime_min = fetch_runtime_minutes(movie_id)
    buffer_min = 30
    while True:
        slot_end = slot_start + timedelta(minutes=runtime_min + buffer_min)
        if slot_end > end_dt:
            break
        showtimes.append((movie_id, slot_start))
        slot_start = slot_end
    return showtimes

def generate_room_timeline_multi(start_dt, end_dt, movie_ids_with_fixed_counts):
    """
    Sinh timeline cho phòng 6: quay vòng phim thường, mỗi phim có fixed 2 suất/ngày.
    movie_ids_with_fixed_counts: dict {movie_id: remaining_slots_today}
    """
    slot_start = start_dt
    buffer_min = 30
    showtimes = []

    while slot_start + timedelta(minutes=90 + buffer_min) <= end_dt: 
        next_movie_id = None
        for mid, remaining in movie_ids_with_fixed_counts.items():
            if remaining > 0:
                next_movie_id = mid
                break
        if next_movie_id is None:
            break 

        runtime_min = fetch_runtime_minutes(next_movie_id)
        slot_end = slot_start + timedelta(minutes=runtime_min + buffer_min)
        if slot_end > end_dt:
            break

        showtimes.append((next_movie_id, slot_start))
        movie_ids_with_fixed_counts[next_movie_id] -= 1
        slot_start = slot_end

    return showtimes

def seed_showtimes_strict_schedule(days=7):
    """Lên lịch 7 ngày, 2 rạp, mỗi rạp 7 phòng, theo yêu cầu đặt ra."""
    cinemas = ensure_cinemas_and_rooms()

    hot_movies, normal_movies = pick_hot_and_normal_movies()
    if len(hot_movies) < 5:
        print("Không đủ 5 phim hot. Vui lòng kiểm tra TMDB now_playing.")
        return

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    day_start_time = timeobj(7, 0)   
    day_end_time = timeobj(23, 30)   

    for c in cinemas:
        rooms = Room.query.filter_by(cinema_id=c.id).order_by(Room.name).all()
        if len(rooms) < 7:
            print("Không đủ 7 phòng cho rạp:", c.name)
            continue

        room_hot_map = {
            rooms[0].id: hot_movies[0], 
            rooms[1].id: hot_movies[1],  
            rooms[2].id: hot_movies[2],  
            rooms[3].id: hot_movies[3],  
            rooms[4].id: hot_movies[4],  
        }
        room6_id = rooms[5].id
        room7_id = rooms[6].id

        start_clean = today
        end_clean = today + timedelta(days=days)
        Showtime.query.filter(
            Showtime.room_id.in_([r.id for r in rooms]),
            Showtime.start_time >= start_clean,
            Showtime.start_time < end_clean
        ).delete(synchronize_session=False)
        db.session.flush()

        for d in range(days):
            target_date = today + timedelta(days=d)
            start_dt = datetime.combine(target_date.date(), day_start_time)
            end_dt = datetime.combine(target_date.date(), day_end_time)

            hot_daily_counts = {mid: 0 for mid in hot_movies}
            for room_id, movie_id in room_hot_map.items():
                show_list = generate_room_timeline(start_dt, end_dt, movie_id)
                for mid, st in show_list:
                    db.session.add(Showtime(
                        movie_id=mid,
                        room_id=room_id,
                        start_time=st,
                    ))
                    hot_daily_counts[mid] += 1

            sample_normals = normal_movies[:8] if len(normal_movies) >= 8 else normal_movies
            fixed_counts = {mid: 2 for mid in sample_normals} 
            show_list_room6 = generate_room_timeline_multi(start_dt, end_dt, fixed_counts)
            for mid, st in show_list_room6:
                db.session.add(Showtime(
                    movie_id=mid,
                    room_id=room6_id,
                    start_time=st,
                ))

            slot_start = start_dt
            buffer_min = 30
            while True:
                mid = min(hot_daily_counts.keys(), key=lambda k: hot_daily_counts[k])
                runtime_min = fetch_runtime_minutes(mid)
                slot_end = slot_start + timedelta(minutes=runtime_min + buffer_min)
                if slot_end > end_dt:
                    break
                if hot_daily_counts[mid] >= 6:
                    all_ok = all(v >= 6 for v in hot_daily_counts.values())
                    if all_ok:
                        pass
                    else:
                        candidates = [k for k, v in hot_daily_counts.items() if v < 6]
                        if not candidates:
                            pass
                        else:
                            mid = candidates[0]
                            runtime_min = fetch_runtime_minutes(mid)
                            slot_end = slot_start + timedelta(minutes=runtime_min + buffer_min)
                            if slot_end > end_dt:
                                break
                db.session.add(Showtime(
                    movie_id=mid,
                    room_id=room7_id,
                    start_time=slot_start,
                ))
                hot_daily_counts[mid] += 1
                slot_start = slot_end

        db.session.commit()
    print("Đã tạo lịch chiếu 7 ngày cho 2 rạp, 7 phòng/rạp theo yêu cầu.")

@app.route("/lich-chieu")
def all_showtimes():
    now = datetime.now()
    end_date = now + timedelta(days=7)

    all_st = Showtime.query.filter(
        Showtime.start_time >= now,
        Showtime.start_time < end_date
    ).order_by(Showtime.start_time).all()

    weekday_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    grouped = {}
    today_date = now.date()

    for i in range(7):
        target_date = today_date + timedelta(days=i)
        date_key = target_date.strftime('%d/%m/%Y')
        weekday = weekday_map[target_date.weekday()]
        grouped[date_key] = {"label": "Hôm Nay" if i == 0 else weekday, "movies": {}}

    for st in all_st:
        date_key = st.start_time.strftime('%d/%m/%Y')
        if date_key in grouped:
            m_id = st.movie_id
            if m_id not in grouped[date_key]["movies"]:
                m_info = fetch_from_tmdb(f"movie/{m_id}", params={'language': 'vi-VN'})
                grouped[date_key]["movies"][m_id] = {
                    "title": m_info.get("title") if m_info else "Phim không tên",
                    "poster": f"{TMDB_IMAGE_BASE_URL}{m_info.get('poster_path')}" if m_info and m_info.get('poster_path') else "",
                    "cinemas": {}
                }
            
            cinema_name = st.room.cinema.name
            if cinema_name not in grouped[date_key]["movies"][m_id]["cinemas"]:
                grouped[date_key]["movies"][m_id]["cinemas"][cinema_name] = []
            
            grouped[date_key]["movies"][m_id]["cinemas"][cinema_name].append(st)

    return render_template("all_showtimes.html", grouped=grouped)

@app.route("/about")
def about():
    return render_template("about.html")

if __name__ == '__main__':
    with app.app_context():
        try:
            print("Đang tạo bảng DB nếu chưa tồn tại...")
            db.create_all()
            print("Đã hoàn tất kiểm tra và tạo bảng DB.")
            print("Đang kiểm tra và tạo lịch chiếu mẫu (7 ngày)...")
            seed_showtimes_strict_schedule(days=7)
            print("Đã chuẩn bị xong dữ liệu lịch chiếu.")
        except Exception as e:
            print(f"LỖI KHI TẠO BẢNG DB: Vui lòng kiểm tra Laragon/MySQL và cấu hình kết nối. Lỗi: {e}")
    try:
        print("Đang tải danh sách thể loại (genres)...")
        fetch_genres()
        print("Đã tải xong thể loại.")
    except Exception as e:
        print("Lỗi tải genre khi start:", e)

@app.route("/uu-dai")
def promotions():
    # Thêm "id" vào từng từ điển trong danh sách
    promo_list = [
        {
            "id": 1, 
            "title": "HỘI VIÊN BEE MOVIE - ĐỒNG GIÁ 45K",
            "img": "image/uudai45k.jpg",
            "is_internal": True,
            "desc": "Áp dụng cho tất cả các suất chiếu từ Thứ 2 đến Thứ 6 hàng tuần cho chủ thẻ hội viên.",
        },
        {
            "id": 2,
            "title": "NGÀY HỘI CINE - THỨ 4 VUI VẺ",
            "img": "image/ngayhoi.jpg", 
            "is_internal": True,
            "desc": "Đồng giá vé chỉ từ 50.000đ cho mọi khách hàng vào mỗi thứ Tư hàng tuần.",
        }
    ]
    return render_template("promotions.html", promos=promo_list)

@app.route("/uu-dai/<int:promo_id>")
def promo_detail(promo_id):
    # Trong thực tế, bạn sẽ dùng Promo.query.get(promo_id)
    # Dưới đây là dữ liệu mẫu mô phỏng nội dung giống Mega GS
    promos = {
        1: {
            "title": "MEGA HOURS - ĐỒNG GIÁ 45K",
            "img": "image/uudai45k.jpg",
            "is_internal": True,
            "desc": "Ưu đãi đặc biệt dành cho các suất chiếu khung giờ vàng.",
            "content": """
                <h3>Nội dung chương trình:</h3>
                <ul>
                    <li>Đồng giá vé 45.000đ cho tất cả khách hàng.</li>
                    <li>Áp dụng cho các suất chiếu trước 12:00 và sau 22:00.</li>
                    <li>Không áp dụng đồng thời với các chương trình khuyến mãi khác.</li>
                </ul>
                <p>Địa điểm: Hệ thống rạp Bee Movie trên toàn quốc.</p>
            """
        },
        2: {
            "title": "NGÀY HỘI CINE - THỨ 4 VUI VẺ",
            "img": "image/ngayhoi.jpg",
            "is_internal": True,
            "desc": "Ưu đãi đặc biệt dành cho các suất chiếu khung giờ vàng.",
            "content": """
                <h3>Nội dung chương trình:</h3>
                <ul>
                    <li>Đồng giá vé 45.000đ cho tất cả khách hàng.</li>
                    <li>Áp dụng cho các suất chiếu trước 12:00 và sau 22:00.</li>
                    <li>Không áp dụng đồng thời với các chương trình khuyến mãi khác.</li>
                </ul>
                <p>Địa điểm: Hệ thống rạp Bee Movie trên toàn quốc.</p>
            """
        }
    }
    
    selected_promo = promos.get(promo_id)
    if not selected_promo:
        return "Không tìm thấy ưu đãi", 404
        
    return render_template("promo_detail.html", promo=selected_promo)

@app.route("/faqs")
def faqs():
    # Danh sách các câu hỏi thường gặp
    faq_list = [
        {
            "question": "Làm sao để đăng ký làm thành viên Bee Movie?",
            "answer": "Bạn có thể đăng ký trực tuyến tại trang Đăng Ký trên website hoặc đến trực tiếp quầy vé tại các cụm rạp Bee Movie để được nhân viên hỗ trợ."
        },
        {
            "question": "Giá vé của Bee Movie là bao nhiêu?",
            "answer": "Giá vé thay đổi tùy theo rạp, khung giờ và đối tượng (Học sinh, người lớn, VIP). Thông thường giá vé dao động từ 45.000đ đến 90.000đ."
        },
        {
            "question": "Tôi có thể hủy vé đã đặt trực tuyến không?",
            "answer": "Theo quy định hiện tại, vé đã thanh toán thành công không thể hủy hoặc thay đổi. Vui lòng kiểm tra kỹ thông tin trước khi xác nhận thanh toán."
        }
    ]
    return render_template("faqs.html", faqs=faq_list)

@app.route("/mega-plus")
def mega_plus():
    membership_levels = [
        {
            "id": "star",
            "name": "BEE STAR",
            "price": 50000, # Ví dụ 50,000 VND
            "condition": "Thẻ đăng ký mới hoặc chi tiêu dưới 2.000.000đ/năm",
            "benefits": [
                "Tích lũy 5% giá trị giao dịch vé",
                "Tích lũy 3% giá trị giao dịch bắp nước",
                "Quà tặng sinh nhật: 1 combo bắp nước"
            ],
            "color": "#cccccc"
        },
        {
            "id": "gold",
            "name": "BEE GOLD",
            "price": 200000, # Ví dụ 200,000 VND
            "condition": "Chi tiêu từ 2.000.000đ đến 4.000.000đ/năm",
            "benefits": [
                "Tích lũy 7% giá trị giao dịch vé",
                "Tích lũy 5% giá trị giao dịch bắp nước",
                "Quà tặng sinh nhật: 2 vé xem phim & 1 combo",
                "Ưu tiên nhận vé tại quầy VIP"
            ],
            "color": "#ffc107"
        }
    ]
    return render_template("mega_plus.html", levels=membership_levels)

@app.route("/buy-membership/<level_id>")
def buy_membership(level_id):
    # Kiểm tra đăng nhập
    if 'user_id' not in session:
        flash("Vui lòng đăng nhập để đăng ký hạng thẻ!", "warning")
        return redirect(url_for('login'))

    # Tìm hạng thẻ tương ứng để lấy giá
    membership_data = {
        "star": {"name": "BEE STAR", "price": 50000},
        "gold": {"name": "BEE GOLD", "price": 200000}
    }
    
    level = membership_data.get(level_id)
    if not level:
        return redirect(url_for('mega_plus'))

    # Lưu thông tin tạm thời vào session để trang payment hiển thị
    session['temp_booking'] = {
        'movie_title': f"Nâng cấp: {level['name']}",
        'seats': ["Thành viên " + level['name']],
        'total_price': level['price'],
        'showtime_id': 0, # Giá trị giả định vì không phải đặt phim
        'cinema_name': "Hệ thống Bee Movie",
        'room_name': "Membership",
        'show_date': "Vĩnh viễn",
        'show_time': "Bee+"
    }
    
    return redirect(url_for('payment_page'))

app.run(debug=True, use_reloader=False)

