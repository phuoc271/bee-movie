# app/controllers/booking_controller.py
from flask import (
    render_template, request, redirect, url_for, flash, session,
    jsonify, current_app
)
from collections import defaultdict
from datetime import datetime, timedelta, time as timeobj
from app.extensions import db, cache
from app.models import Booking, Showtime, Room, Cinema, User
from app.utils.tmdb import fetch_from_tmdb, fetch_movies_list, tmdb_image_base
from sqlalchemy.orm import joinedload
@cache.memoize(timeout=3600)  
def tmdb_movie_detail(movie_id, language="vi-VN"):
    return fetch_from_tmdb(f"movie/{movie_id}", params={'language': language})

@cache.memoize(timeout=1800)  
def tmdb_list(endpoint, page=1, language="vi-VN", region="VN"):
    return fetch_movies_list(endpoint, {"page": page, "language": language, "region": region})


def register_movie_routes(app):
    @cache.cached(timeout=60, key_prefix="lich_chieu_page")
    @app.route("/lich-chieu")
    def all_showtimes():
        ensure_rolling_window(days=7)

        now = datetime.now()
        end_date = now + timedelta(days=7)

        all_st = Showtime.query.options(
            joinedload(Showtime.room).joinedload(Room.cinema)
        ).filter(
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

        poster_base = current_app.config.get("TMDB_IMAGE_BASE_URL", "")

        movie_ids = {st.movie_id for st in all_st}
        movie_infos = {mid: tmdb_movie_detail(mid, language='vi-VN') for mid in movie_ids}

        for st in all_st:
            date_key = st.start_time.strftime('%d/%m/%Y')
            if date_key not in grouped:
                continue

            m_id = st.movie_id
            if m_id not in grouped[date_key]["movies"]:
                m_info = movie_infos.get(m_id) or {}
                poster_path = m_info.get('poster_path')
                grouped[date_key]["movies"][m_id] = {
                    "title": m_info.get("title", "Phim không tên"),
                    "poster": f"{poster_base}{poster_path}" if poster_base and poster_path else "",
                    "cinemas": {}
                }

            room = getattr(st, "room", None)
            cinema_name = room.cinema.name if room and getattr(room, "cinema", None) else "N/A"
            grouped[date_key]["movies"][m_id]["cinemas"].setdefault(cinema_name, []).append(st)

        return render_template("all_showtimes.html", grouped=grouped)


    @app.route('/recruitment')
    def recruitment():
        return render_template('recruitment.html')

    @app.route("/about")
    def about():
        return render_template("about.html")
    @app.route("/faqs")
    def faqs():
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


def register_booking_routes(app):
    @app.route("/booking/<int:showtime_id>")
    def booking(showtime_id):
        if "user_email" not in session:
            flash("Vui lòng đăng nhập để đặt vé", "warning")
            return redirect(url_for("login"))

        Booking.query.filter(
            (Booking.status == 'pending') & (Booking.hold_expiry < datetime.utcnow())
        ).delete()
        db.session.commit()

        showtime = Showtime.query.get(showtime_id)
        if not showtime:
            flash("Suất chiếu không tồn tại", "danger")
            return redirect(url_for("home"))

        movie_id = showtime.movie_id
        movie_data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN'})

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

        price_thuong = 65000 + (showtime.final_price - showtime.price)
        price_vip = 75000 + (showtime.final_price - showtime.price)

        return render_template("booking.html",
                            showtime_id=showtime_id,
                            movie=movie_data,
                            occupied_seats=occupied_seats,
                            cinema_name=cinema_name,
                            room_name=room_name,
                            show_date=show_date,
                            show_hour=show_hour,
                            price=showtime.final_price,
                            price_thuong=price_thuong,
                            price_vip=price_vip)


    @app.route("/confirm_booking", methods=['POST'])
    def confirm_booking():
        data = request.get_json()
        showtime = Showtime.query.get(data.get('showtime_id'))
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({"status": "error", "message": "Vui lòng đăng nhập lại"}), 401

        expiry = datetime.utcnow() + timedelta(minutes=3)
        total_price = len(data.get('seats')) * showtime.final_price

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
                'total_price': total_price,
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
            return redirect(url_for("home"))

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
            movie_info = tmdb_movie_detail(movie_id, language='vi-VN')
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

            poster_base = tmdb_image_base()
            poster_path = movie_info.get('poster_path') if movie_info else None
            poster_url = f"{poster_base}{poster_path}" if poster_base and poster_path else ""

            grouped_tickets.append({
                'booking_time': bookings[0].booking_time,
                'movie_title': movie_title,
                'movie_poster': poster_url,
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
    data = tmdb_movie_detail(movie_id, language='vi-VN')
    runtime = data.get('runtime') if data else None
    try:
        return int(runtime) if runtime else 120
    except:
        return 120

def pick_hot_and_normal_movies():
    """Lấy danh sách phim now_playing và phân 5 phim hot + danh sách thường."""
    movies = tmdb_list("movie/now_playing", page=1, language="vi-VN", region="VN")
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

def seed_day_for_cinema(c, target_date, hot_movies, normal_movies):
    """Sinh lịch cho 1 rạp trong 1 ngày—giữ nguyên pattern của bạn."""
    rooms = Room.query.filter_by(cinema_id=c.id).order_by(Room.name).all()
    if len(rooms) < 7:
        return

    day_start_time = timeobj(7, 0)
    day_end_time = timeobj(23, 30)
    start_dt = datetime.combine(target_date, day_start_time)
    end_dt = datetime.combine(target_date, day_end_time)

    for i in range(5):
        room_id = rooms[i].id
        movie_id = hot_movies[i] if i < len(hot_movies) else normal_movies[i % len(normal_movies)]
        slot_start = start_dt
        while True:
            runtime_min = fetch_runtime_minutes(movie_id)
            slot_end = slot_start + timedelta(minutes=runtime_min + 30)
            if slot_end > end_dt:
                break
            db.session.add(Showtime(movie_id=movie_id, room_id=room_id, start_time=slot_start))
            slot_start = slot_end

    mixed_movies = (normal_movies or []) + (hot_movies or [])
    if not mixed_movies:
        return

    room6_id = rooms[5].id
    room7_id = rooms[6].id

    slot_start_6 = start_dt
    idx6 = 0
    while True:
        mid = mixed_movies[idx6 % len(mixed_movies)]
        runtime_min = fetch_runtime_minutes(mid)
        slot_end = slot_start_6 + timedelta(minutes=runtime_min + 30)
        if slot_end > end_dt:
            break
        db.session.add(Showtime(movie_id=mid, room_id=room6_id, start_time=slot_start_6))
        slot_start_6 = slot_end
        idx6 += 1

    slot_start_7 = start_dt
    idx7 = len(mixed_movies) // 2
    while True:
        mid = mixed_movies[idx7 % len(mixed_movies)]
        runtime_min = fetch_runtime_minutes(mid)
        slot_end = slot_start_7 + timedelta(minutes=runtime_min + 30)
        if slot_end > end_dt:
            break
        db.session.add(Showtime(movie_id=mid, room_id=room7_id, start_time=slot_start_7))
        slot_start_7 = slot_end
        idx7 += 1

def ensure_rolling_window(days=7):
    """
    Đảm bảo lịch chiếu luôn có từ hôm nay đến hôm nay+days.
    - Xóa showtimes của ngày < hôm nay (chỉ ngày đã qua).
    - Nếu thiếu ngày nào trong khoảng, sinh thêm cho ngày đó.
    """
    cinemas = ensure_cinemas_and_rooms()
    hot_movies, normal_movies = pick_hot_and_normal_movies()
    if len(hot_movies) < 5 and not normal_movies:
        return

    today = datetime.now().date()
    window_end = today + timedelta(days=days)

    past_cutoff = datetime.combine(today, timeobj(0, 0))
    Showtime.query.filter(Showtime.start_time < past_cutoff).delete(synchronize_session=False)
    db.session.flush()

    for d in range(days + 1):
        target_date = today + timedelta(days=d)
        day_start = datetime.combine(target_date, timeobj(0, 0))
        day_end = datetime.combine(target_date, timeobj(23, 59))
        count = Showtime.query.filter(Showtime.start_time >= day_start, Showtime.start_time <= day_end).count()
        if count == 0:
            for c in cinemas:
                seed_day_for_cinema(c, target_date, hot_movies, normal_movies)
            db.session.commit()

def seed_showtimes_strict_schedule(days=7):
    """
    Giữ lại hàm gốc của bạn (nếu bạn muốn chạy thủ công), 
    nhưng khuyến nghị dùng ensure_rolling_window() để không xóa toàn bộ.
    """
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
            continue
        for d in range(days):
            target_date = (today + timedelta(days=d)).date()
            seed_day_for_cinema(c, target_date, hot_movies, normal_movies)

        db.session.commit()
    print("Đã tạo lịch chiếu: không xóa toàn bộ, có thể chạy bổ sung.")
    
def seed_showtimes_rolling(days=7):
    ensure_rolling_window(days=days)

booking_routes = register_booking_routes
