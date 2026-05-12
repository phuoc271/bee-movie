import os
import sys
import traceback
import warnings
from datetime import datetime
from flask import Flask
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from sqlalchemy import exc
from app.extensions import db, cache, mail, login_manager
from app.controllers import register_controllers

warnings.filterwarnings("ignore", category=exc.SAWarning)
load_dotenv()
def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("APP_SECRET_KEY")
    app.config["SECURITY_PASSWORD_SALT"] = os.getenv("SECURITY_PASSWORD_SALT", "some-random-salt-value")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["UPLOAD_FOLDER"] = os.getenv("UPLOAD_FOLDER", "static/uploads/avatars")
    app.config.update(
        MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com'),
        MAIL_PORT = int(os.getenv('MAIL_PORT', "587")),
        MAIL_USE_TLS = True,
        MAIL_USERNAME = os.getenv('MAIL_USERNAME'),
        MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
    )
    app.config["TMDB_API_KEY"] = os.getenv("TMDB_API_KEY")
    app.config["TMDB_BASE_URL"] = os.getenv("TMDB_BASE_URL", "https://api.themoviedb.org/3")
    app.config["TMDB_IMAGE_BASE_URL"] = os.getenv("TMDB_IMAGE_BASE_URL", "https://image.tmdb.org/t/p/w500")
    app.config["TMDB_BACKDROP_BASE_URL"] = os.getenv("TMDB_BACKDROP_BASE_URL", "https://image.tmdb.org/t/p/original")

    return app

app = create_app()

db.init_app(app)
cache.init_app(app)
mail.init_app(app)

login_manager.init_app(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = "Vui lòng đăng nhập để tiếp tục."
login_manager.login_message_category = "info"

try:
    register_controllers(app)
except Exception:
    print("LỖI KHI ĐĂNG KÝ CONTROLLERS:")
    traceback.print_exc(file=sys.stdout)

def startup_tasks(app):
    """Tạo bảng nếu chưa có, tự động cập nhật Foreign Keys và seed dữ liệu mẫu."""
    try:
        from app.controllers.booking_controller import seed_showtimes_rolling
    except Exception:
        seed_showtimes_rolling = None
        print("Không import được seed_showtimes_rolling.")

    try:
        from app.controllers.movie_controller import fetch_genres as load_genres
    except Exception:
        load_genres = None

    with app.app_context():
        try:
            print("--- BẮT ĐẦU KHỞI TẠO HỆ THỐNG ---")
            
            from app import models
            from app.models.MovieExtra import MovieExtra
            db.create_all()
            print("1. Đã kiểm tra/tạo bảng Database.")

            try:
                updates = [
                    "ALTER TABLE bookings ADD CONSTRAINT fk_booking_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE",
                    "ALTER TABLE bookings ADD CONSTRAINT fk_booking_showtime FOREIGN KEY (showtime_id) REFERENCES showtimes(id) ON DELETE CASCADE",
                    "ALTER TABLE showtimes ADD CONSTRAINT fk_showtime_room FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE",
                    "ALTER TABLE rooms ADD CONSTRAINT fk_room_cinema FOREIGN KEY (cinema_id) REFERENCES cinemas(id) ON DELETE CASCADE"
                ]
                
                for sql in updates:
                    try:
                        db.session.execute(db.text(sql))
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
                print("2. Đã kiểm tra và cập nhật các liên kết Foreign Keys.")
            except Exception as e:
                print(f"Lưu ý về Foreign Keys: {e}")

            try:
                from app.models import Showtime
                existing = Showtime.query.count()
            except Exception:
                existing = None

            flask_env = os.environ.get("FLASK_ENV", "").lower()
            dev_seed_flag = os.environ.get("DEV_SEED", "true").lower() in ("1", "true", "yes")
            should_seed = (flask_env == "development" or dev_seed_flag) and (existing == 0 or existing is None)

            if should_seed and seed_showtimes_rolling:
                print("2. Bắt đầu tạo lịch chiếu mẫu (rolling, 7 ngày)...")
                seed_showtimes_rolling(days=7)
                print("   Đã tạo lịch chiếu mẫu (rolling).")
            else:
                print(f"3. Bỏ qua seed. FLASK_ENV={flask_env}, DEV_SEED={dev_seed_flag}, existing={existing}")

            if load_genres:
                try:
                    load_genres()
                    print("4. Đã tải danh sách thể loại (genres).")
                except Exception as e:
                    print(f"4. CẢNH BÁO: Chưa tải được thể loại do lỗi mạng: {e}")
                    
            print("--- HỆ THỐNG SẴN SÀNG ---")
        except Exception:
            print("LỖI KHỞI ĐỘNG CHUNG:")
            traceback.print_exc(file=sys.stdout)

def init_scheduler(app):
    """Khởi tạo scheduler chạy ngầm để seed lịch chiếu và dọn dẹp vé quá hạn."""
    from app.controllers.booking_controller import seed_showtimes_rolling
    from app.models import Booking, BookingConcession

    scheduler = BackgroundScheduler()
    scheduler.add_job(lambda: seed_showtimes_rolling(days=7), 'cron', hour=0, minute=0)

    def cleanup_expired_bookings():
        with app.app_context():
            now = datetime.now()
            
            expired_bookings = Booking.query.filter(
                Booking.status == 'pending',
                Booking.hold_expiry < now
            ).all()

            if expired_bookings:
                for b in expired_bookings:
                    db.session.delete(b)
                print(f"--- [SCHEDULER] Đã dọn {len(expired_bookings)} vé quá hạn ---")

            expired_concessions = BookingConcession.query.filter(
                BookingConcession.status == 'pending',
                BookingConcession.booking_id == None,
                BookingConcession.hold_expiry < now
            ).all()

            if expired_concessions:
                for item in expired_concessions:
                    db.session.delete(item)
                print(f"--- [SCHEDULER] Đã dọn {len(expired_concessions)} bắp nước lẻ quá hạn ---")
            db.session.commit()

    scheduler.add_job(cleanup_expired_bookings, 'interval', minutes=1)
    
    scheduler.start()

with app.app_context():
    startup_tasks(app)
    init_scheduler(app)

if __name__ == '__main__':
    debug_mode = os.environ.get("FLASK_ENV", "").lower() == "development"
    app.run(host="127.0.0.1", port=5000, debug=debug_mode)
