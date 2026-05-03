# app/controllers/booking_controller.py
from flask import (
    Blueprint, render_template, request, redirect, url_for, flash, session,
    jsonify, current_app
)
from flask_login import current_user, login_required
from collections import defaultdict
from datetime import datetime, timedelta, time as timeobj
from app.extensions import db, cache
from app.models import Booking, Showtime, Room, Cinema, User, Concession, BookingConcession, MovieExtra
from app.models.showtime import SystemConfig
from app.utils.tmdb import fetch_from_tmdb, fetch_movies_list, tmdb_image_base
from sqlalchemy.orm import joinedload
from sqlalchemy import func
from collections import defaultdict
from datetime import datetime, timedelta, time as timeobj
import random, string, traceback

booking_bp = Blueprint('booking', __name__)
@cache.memoize(timeout=3600)  
def tmdb_movie_detail(movie_id, language="vi-VN"):
    return fetch_from_tmdb(f"movie/{movie_id}", params={'language': language})

@cache.memoize(timeout=1800)  
def tmdb_list(endpoint, page=1, language="vi-VN", region="VN"):
    return fetch_movies_list(endpoint, {"page": page, "language": language, "region": region})


@cache.cached(timeout=15, key_prefix="lich_chieu_page")
@booking_bp.route("/lich-chieu")
def all_showtimes():
    config = SystemConfig.query.filter_by(config_key='auto_seed').first()

    if not config or config.is_active:
        ensure_rolling_window(days=7)
    else:
        print(">>> TRANG LỊCH CHIẾU: Đang tắt tự động nên không tạo thêm suất mới.")

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
    movie_infos = {}
    for mid in movie_ids:
        m_info = tmdb_movie_detail(mid, language='vi-VN')
        if not m_info or not m_info.get("title"):
            extra = MovieExtra.query.get(str(mid))
            if extra:
                m_info = {
                    "title": extra.title,
                    "poster_path": None, 
                    "local_poster": extra.poster_url
                }
        movie_infos[mid] = m_info

    for st in all_st:
        date_key = st.start_time.strftime('%d/%m/%Y')
        if date_key not in grouped:
            continue

        m_id = st.movie_id
        if m_id not in grouped[date_key]["movies"]:
            m_info = movie_infos.get(m_id) or {}
            poster_path = m_info.get('poster_path')
            if poster_path:
                final_poster = f"{poster_base}{poster_path}"
            else:
                final_poster = m_info.get("local_poster", "")
            grouped[date_key]["movies"][m_id] = {
                "title": m_info.get("title", "Phim không tên"),
                "poster": final_poster,
                "cinemas": {}
            }

        room = getattr(st, "room", None)
        cinema_name = room.cinema.name if room and getattr(room, "cinema", None) else "N/A"
        grouped[date_key]["movies"][m_id]["cinemas"].setdefault(cinema_name, []).append(st)

    return render_template("all_showtimes.html", grouped=grouped)


@booking_bp.route('/recruitment')
def recruitment():
    return render_template('recruitment.html')

@booking_bp.route("/about")
def about():
    return render_template("about.html")
@booking_bp.route("/faqs")
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

def get_holiday_surcharge(target_datetime):

    holidays = ["01/01", "10/03", "30/04", "01/05", "02/09"]
    date_str = target_datetime.strftime("%d/%m")
    
    if date_str in holidays:
        return 30000
        
    if target_datetime.weekday() >= 5: 
        return 20000
        
    return 0

@booking_bp.route("/booking/<showtime_id>")
def booking(showtime_id):
    if "user_id" not in session:
        flash("Vui lòng đăng nhập để đặt vé", "warning")
        return redirect(url_for('auth.login'))

    now = datetime.now()
    Booking.query.filter(
        (Booking.status == 'pending') & 
        (Booking.hold_expiry < now)
    ).delete()
    db.session.commit()

    showtime = Showtime.query.get(showtime_id)
    if not showtime:
        flash("Suất chiếu không tồn tại", "danger")
        return redirect(url_for("movie.home"))

    movie_id = showtime.movie_id
    extra = MovieExtra.query.get(str(movie_id))
    
    if extra:
        movie_data = {
            "title": extra.title,
            "poster_path": extra.poster_url, 
            "runtime": extra.runtime
        }
    else:
        movie_data = fetch_from_tmdb(f"movie/{movie_id}", params={'language': 'vi-VN'})

    now = datetime.now()
    booked_seats = Booking.query.filter(
        (Booking.showtime_id == showtime_id) &
        (
            (Booking.status == 'confirmed') |
            (Booking.status == 'used') |
            ((Booking.status == 'pending') & (Booking.hold_expiry > now))
        )
    ).all()

    occupied_seats = []
    for b in booked_seats:
        if b.seat_code:
            occupied_seats.extend(b.seat_code.split(','))
    cinema_name = showtime.room.cinema.name
    room_name = showtime.room.name
    show_date = showtime.start_time.strftime("%d/%m/%Y")
    show_hour = showtime.start_time.strftime("%H:%M")
    surcharge = get_holiday_surcharge(showtime.start_time)

    price_thuong = 65000 + surcharge
    price_vip = 75000 + surcharge
    auto_cleanup_pending_orders()
    return render_template("booking.html",
                        showtime_id=showtime_id,
                        movie=movie_data,
                        movie_id=movie_id,
                        occupied_seats=occupied_seats,
                        cinema_name=cinema_name,
                        room_name=room_name,
                        show_date=show_date,
                        show_hour=show_hour,
                        price=65000 + surcharge,
                        price_thuong=price_thuong,
                        price_vip=price_vip)

@booking_bp.route("/confirm_booking", methods=['POST'])
def confirm_booking():
    data = request.get_json()
    showtime_id = data.get('showtime_id')
    showtime = Showtime.query.get(showtime_id)
    
    if not showtime:
        return jsonify({"status": "error", "message": "Suất chiếu không tồn tại"}), 404

    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "message": "Vui lòng đăng nhập lại"}), 401

    try:
        seats = data.get('seats', [])
        surcharge = get_holiday_surcharge(showtime.start_time)
        total_price = 0
        
        for seat in seats:
            if seat.startswith(('A', 'B', 'C')):
                total_price += (65000 + surcharge)
            else:
                total_price += (75000 + surcharge)

        now = datetime.now()
        expiry = now + timedelta(minutes=6)

        Booking.query.filter_by(
            user_id=user_id,
            showtime_id=showtime_id,
            status='pending'
        ).delete()

        seat_code_string = ",".join(seats) 

        new_hold = Booking(
            user_id=user_id,
            showtime_id=showtime_id,
            movie_id=data.get('movie_id'),
            seat_code=seat_code_string, 
            status='pending',
            booking_time=now,
            hold_expiry=expiry,
            total_price=total_price
        )
        db.session.add(new_hold)
        db.session.commit()

        session['temp_booking'] = {
            'booking_id': new_hold.id,
            'showtime_id': showtime_id,
            'movie_id': data.get('movie_id'),
            'seats': seats,
            'total_price': total_price,
            'movie_title': data.get('movie_title'),
            'cinema_name': showtime.room.cinema.name,
            'room_name': showtime.room.name,
            'show_date': showtime.start_time.strftime("%d/%m/%Y"),
            'show_time': showtime.start_time.strftime("%H:%M"),
            'hold_expiry': expiry.isoformat()
        }
        session.modified = True 

        return jsonify({
            "status": "success",
            "redirect": url_for('booking.concessions', from_booking=1), 
            "booking": session['temp_booking']
        })

    except Exception as e:
        db.session.rollback()
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


@booking_bp.route("/cancel_booking", methods=['POST'])
def cancel_booking():
    user_id = session.get('user_id')

    if not user_id:
        return jsonify({"status": "error", "message": "Bạn chưa đăng nhập"}), 401

    try:
        Booking.query.filter_by(user_id=user_id, status='pending').delete(synchronize_session=False)
        
        BookingConcession.query.filter_by(
            user_id=user_id,
            status='pending'
        ).delete(synchronize_session=False)

        session.pop('temp_booking', None)
        session.modified = True

        db.session.commit()
        
        return jsonify({"status": "success", "message": "Đã hủy và xóa sạch giao dịch"})

    except Exception as e:
        db.session.rollback()
        traceback.print_exc() 
        return jsonify({"status": "error", "message": str(e)}), 500

@booking_bp.route("/payment")
def payment_page():
    user_id = session.get('user_id')
    if not user_id:
        flash("Vui lòng đăng nhập!", "danger")
        return redirect(url_for('auth.login'))

    booking_session = session.get('temp_booking')
    show_info = None
    ticket_total = 0
    time_remaining = 0
    now = datetime.now()

    if booking_session:
        actual_booking = Booking.query.filter_by(
            user_id=user_id,
            status='pending',
            showtime_id=booking_session.get('showtime_id')
        ).first()

        if actual_booking:
            ticket_total = booking_session.get('total_price', 0)
            show_info = {
                'movie_title': booking_session.get('movie_title'),
                'seat_names': ", ".join(booking_session.get('seats', [])),
                'show_date': booking_session.get('show_date'),
                'show_time': booking_session.get('show_time')
            }
            if actual_booking.hold_expiry:
                    diff = (actual_booking.hold_expiry - now).total_seconds()
                    time_remaining = int(max(0, diff))
        else:
            session.pop('temp_booking', None)

    pending_concessions = BookingConcession.query.filter_by(
        user_id=user_id, 
        status='pending'
    ).all()

    if not show_info and pending_concessions:
        first_item = pending_concessions[0]
        
        if not first_item.hold_expiry:
            for item_db in pending_concessions:
                item_db.booking_time = now 
                item_db.hold_expiry = now + timedelta(minutes=6)
            time_remaining = 360
        else:
            diff = (first_item.hold_expiry - now).total_seconds()
            time_remaining = int(max(0, diff))

    cart_items = []
    concession_total = 0

    for item_db in pending_concessions:
        item = item_db.concession 
        if item:
            line_total = item.price * item_db.quantity
            concession_total += line_total
            cart_items.append({
                'id': item.id,
                'name': item.name,
                'quantity': item_db.quantity,
                'price': item.price
            })

    db.session.commit()
    final_total = ticket_total + concession_total
    
    if not show_info and not cart_items:
        flash("Không có giao dịch nào đang chờ thanh toán.", "info")
        return redirect(url_for("movie.home"))

    return render_template("payment.html", 
                            show_info=show_info, 
                            ticket_total=ticket_total,
                            cart_items=cart_items, 
                            concession_total=concession_total,
                            total_price=final_total,
                            time_remaining=time_remaining)

@booking_bp.route('/concessions')
def concessions():
    is_from_booking = request.args.get('from_booking') == '1'
    combos_from_db = Concession.query.filter_by(category='combo').all()
    singles_from_db = Concession.query.filter_by(category='single').all()

    return render_template("concessions.html", combos=combos_from_db, singles=singles_from_db, is_from_booking=is_from_booking)

@booking_bp.route('/cart')
def view_cart():
    user_id = session.get('user_id')
    if not user_id:
        flash("Vui lòng đăng nhập!", "warning")
        return redirect(url_for('auth.login'))

    cart_items_db = BookingConcession.query.filter_by(user_id=user_id, status='hold').all()
    
    cart_items = []
    total_price = 0
    
    for item_db in cart_items_db:
        item = item_db.concession 
        if item:
            subtotal = item.price * item_db.quantity
            total_price += subtotal
            cart_items.append({
                'id': item.id,
                'name': item.name,
                'price': item.price,
                'img': item.img,
                'quantity': item_db.quantity,
                'category': item.category
            })
                
    return render_template('cart.html', cart_items=cart_items, total_price=total_price)

@booking_bp.route('/add-to-cart/<int:item_id>')
def add_to_cart(item_id):
    user_id = session.get('user_id')
    if not user_id:
        flash("Vui lòng đăng nhập để thêm món vào giỏ hàng!", "warning")
        return redirect(url_for('auth.login'))

    try:
        item_in_cart = BookingConcession.query.filter_by(
            user_id=user_id, 
            concession_id=item_id, 
            status='hold'
        ).first()

        if item_in_cart:
            item_in_cart.quantity += 1
            flash(f"Đã tăng số lượng món ăn trong giỏ hàng!", "success")
        else:
            new_item = BookingConcession(
                user_id=user_id,
                concession_id=item_id,
                quantity=1,
                status='hold',
                booking_id=None, 
                payment_method='N/A'
            )
            db.session.add(new_item)
            flash("Đã thêm món vào giỏ hàng!", "success")

        db.session.commit()

    except Exception as e:
        db.session.rollback()
        print(f"Lỗi khi thêm vào giỏ hàng: {str(e)}")
        flash("Có lỗi xảy ra, vui lòng thử lại sau.", "danger")

    return redirect(request.referrer or url_for('booking.concessions'))

@booking_bp.route('/add-to-cart-and-pay/<int:item_id>', methods=['POST'])
def add_to_cart_and_pay(item_id):
    user_id = session.get('user_id')
    if not user_id:
        return jsonify({"status": "error", "message": "Vui lòng đăng nhập"}), 401

    item = Concession.query.get(item_id)
    
    current_booking_id = session.get('current_booking_id') 

    try:
        new_item = BookingConcession(
            user_id=user_id,
            concession_id=item.id,
            booking_id=current_booking_id, 
            quantity=1,
            status='pending', 
            payment_method='N/A',
            hold_expiry=datetime.now() + timedelta(minutes=6)
        )
        db.session.add(new_item)
        db.session.commit()
        return jsonify({"status": "success"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500
    
@booking_bp.route('/update-cart/<int:item_id>/<string:action>')
def update_cart(item_id, action):
    user_id = session.get('user_id')
    item_db = BookingConcession.query.filter_by(
        user_id=user_id, 
        concession_id=item_id, 
        status='hold'
    ).first()
    
    if item_db:
        if action == 'increase':
            item_db.quantity += 1
        elif action == 'decrease':
            item_db.quantity -= 1
            if item_db.quantity <= 0:
                db.session.delete(item_db)
        
        db.session.commit()
        
    return redirect(url_for('booking.view_cart'))

@booking_bp.route('/remove-from-cart/<int:item_id>')
def remove_from_cart(item_id):
    user_id = session.get('user_id')
    item_db = BookingConcession.query.filter_by(
        user_id=user_id, 
        concession_id=item_id, 
        status='hold'
    ).first()
    
    if item_db:
        db.session.delete(item_db)
        db.session.commit()
        flash("Đã xóa món khỏi giỏ hàng", "success")
            
    return redirect(url_for('booking.view_cart'))

@booking_bp.app_context_processor
def inject_cart_count():
    user_id = session.get('user_id')
    count = 0
    if user_id:
        count = db.session.query(func.sum(BookingConcession.quantity)).filter_by(
            user_id=user_id, 
            status='hold'
        ).scalar() or 0 
    return dict(cart_count=count)

@booking_bp.route('/cart/checkout')
def cart_checkout():
    user_id = session.get('user_id')
    if not user_id:
        flash("Vui lòng đăng nhập để thanh toán!", "warning")
        return redirect(url_for('auth.login'))

    user_holds = BookingConcession.query.filter_by(
        user_id=user_id, 
        status='hold'
    ).all()

    if not user_holds:
        flash("Giỏ hàng của bạn đang trống!", "warning")
        return redirect(url_for('booking.view_cart'))

    try:
        expiry_time = datetime.now() + timedelta(minutes=10)

        for item in user_holds:
            item.status = 'pending'
            item.hold_expiry = expiry_time
        
        db.session.commit()

        session.pop('cart', None) 
        
        flash("Đang chuyển đến trang thanh toán...", "info")
        return redirect(url_for('booking.payment_page'))

    except Exception as e:
        db.session.rollback()
        print(f"Lỗi khi xử lý checkout: {str(e)}")
        flash("Có lỗi xảy ra trong quá trình xử lý, vui lòng thử lại.", "danger")
        return redirect(url_for('booking.view_cart'))

@booking_bp.route('/final_confirm_db', methods=['POST'])
def final_confirm_db():
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'status': 'error', 'message': 'Vui lòng đăng nhập lại'}), 401

        data = request.get_json()
        payment_method = data.get('payment_method', 'cash') 
        booking_session = session.get('temp_booking')
        
        unique_code = generate_ticket_code()
    
        t_ticket = booking_session.get('total_price', 0) if booking_session else 0
        new_booking = None
        seat_code = "ONLY_FOOD" 
        showtime_id = None
        movie_id = None
        
        if booking_session and booking_session.get('seats'):
            showtime_id = booking_session.get('showtime_id')
            movie_id = booking_session.get('movie_id')
            seats = booking_session.get('seats', [])
            seat_code = ",".join(seats)

            existing_booking = Booking.query.filter(
                Booking.user_id == user_id,
                Booking.showtime_id == showtime_id,
                Booking.status == 'pending',
                Booking.hold_expiry > datetime.now()
            ).first()

            if existing_booking:
                new_booking = existing_booking
                new_booking.status = 'confirmed'
                new_booking.ticket_code = unique_code
                new_booking.payment_method = payment_method
                new_booking.booking_time = datetime.now()
                new_booking.seat_code = seat_code
                new_booking.hold_expiry = None
            else:
                return jsonify({'status': 'error', 'message': 'Hết thời gian giữ ghế!'}), 400
        else:
            new_booking = Booking(
                user_id=user_id, 
                showtime_id=showtime_id, 
                movie_id=movie_id,
                ticket_code=unique_code,
                seat_code=seat_code, 
                status='confirmed', 
                payment_method=payment_method, 
                booking_time=datetime.now(),
                hold_expiry=None
            )
            db.session.add(new_booking)
        db.session.flush() 

        t_con = 0
        pending_concessions = BookingConcession.query.filter(
            BookingConcession.user_id == user_id,
            BookingConcession.status == 'pending'
        ).all()

        if pending_concessions:
            for item in pending_concessions:
                item.status = 'confirmed'
                item.booking_id = new_booking.id
                t_con += (item.quantity * item.concession.price)
                item.payment_method = payment_method
                item.hold_expiry = None 
        new_booking.total_price = t_ticket + t_con
        t_qr = ""
        m_name = "Bắp Nước"

        from datetime import timedelta
        expiry_date = (datetime.now() + timedelta(hours=24)).strftime('%H:%M %d/%m/%Y')

        if booking_session:
            m_name = booking_session.get('movie_title', 'Phim')
            if booking_session.get('seats'):
                t_qr = unique_code

        f_qr = ""
        if pending_concessions:
            f_list = ", ".join([f"{c.quantity}x {c.concession.name}" for c in pending_concessions])
            f_qr = unique_code

            if not t_qr:
                t_qr = f_qr
            elif t_qr and f_qr:
                t_qr += unique_code

        db.session.commit()
        return jsonify({
            'status': 'success',
            'booking_id': unique_code, 
            'ticket_code': unique_code,
            'ticket_qr': t_qr,  
            'food_qr': f_qr
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'status': 'error', 'message': str(e)}), 500
    
@booking_bp.route('/my-tickets')
def my_tickets():
    user_id = session.get('user_id')
    if not user_id:
        return redirect(url_for('auth.login'))

    all_bookings = Booking.query.filter_by(
        user_id=user_id, 
        status='confirmed' 
    ).order_by(Booking.booking_time.desc()).all()
    
    grouped = defaultdict(list)
    for b in all_bookings:
        time_key = b.booking_time.strftime('%Y-%m-%d %H:%M:%S')
        key = (b.showtime_id, b.movie_id, time_key)
        grouped[key].append(b)

    final_list = []
    for (showtime_id, movie_id, time_key), b_list in grouped.items():
        if movie_id:
            movie_info = tmdb_movie_detail(movie_id, language='vi-VN')
            if not movie_info or not movie_info.get('title'):
                extra = MovieExtra.query.get(str(movie_id))
                if extra:
                    movie_info = {'title': extra.title, 'poster_path': None}
                    movie_poster = extra.poster_url or url_for('static', filename='image/default_food.jpg')
                else:
                    movie_info = {'title': f'Phim (ID: {movie_id})', 'poster_path': None}
                    movie_poster = url_for('static', filename='image/default_food.jpg')
            else:
                movie_poster = f"https://image.tmdb.org/t/p/w500{movie_info.get('poster_path')}" if movie_info.get('poster_path') else ""
        else:
            movie_info = None
            movie_title = "ĐƠN HÀNG BẮP NƯỚC LẺ"
            movie_poster = url_for('static', filename='image/default_food.jpg')
        showtime = Showtime.query.get(showtime_id) if showtime_id is not None else None
        movie_title = movie_info.get('title') if movie_info else "ĐƠN HÀNG BẮP NƯỚC LẺ"
        seats_list = [b.seat_code for b in b_list if b.seat_code and b.seat_code not in ['ONLY_FOOD', 'ONLY_CONCESSION']]
        seats_str = ", ".join(seats_list)
        pay_map = {'momo': 'Ví MoMo', 'vnpay': 'VNPAY', 'cash': 'Tiền mặt'}
        raw_pay = b_list[0].payment_method if hasattr(b_list[0], 'payment_method') else "N/A"
        payment_text = pay_map.get(raw_pay, raw_pay)
        booking_ids = [b.id for b in b_list]
        concession_links = BookingConcession.query.filter(BookingConcession.booking_id.in_(booking_ids)).all()
        
        has_food = False
        food_items = ""
        if concession_links:
                has_food = True
                items_list = []
                for c in concession_links:
                    if c.concession:
                        items_list.append(f"{c.quantity}x {c.concession.name}")
                    else:
                        items_list.append(f"{c.quantity}x Sản phẩm #{c.concession_id}")
                food_items = ", ".join(items_list)
        ticket_code = b_list[0].ticket_code if b_list[0].ticket_code else str(b_list[0].id)
        qr_content = ticket_code 

        final_list.append({
            'movie_id': movie_id, 
            'movie_title': movie_title,
            'movie_poster': movie_poster,
            'cinema_name': showtime.room.cinema.name if (showtime and showtime.room) else "Bee Cinema",
            'show_time': showtime.start_time.strftime('%H:%M %d/%m/%Y') if showtime else "",
            'seats': seats_str,
            'booking_time': b_list[0].booking_time,
            
            'full_info_qr': qr_content, 
            'concession_qr': qr_content if (has_food or movie_id is None) else "",
            
            'ticket_code': ticket_code,
            'has_concession': has_food if movie_id else True, 
            'concessions': food_items, 
            'payment_method_vn': payment_text
        })

    return render_template('my_tickets.html', bookings=final_list)
def auto_cleanup_pending_orders():
    try:
        now = datetime.now()
        threshold_time = now - timedelta(minutes=3)
        expired_bookings = Booking.query.filter(
            Booking.status == 'pending'
        ).filter(
            (Booking.hold_expiry < now) | 
            (Booking.booking_time < threshold_time) |
            (Booking.showtime_id.is_(None))
        ).all()

        for b in expired_bookings:
            db.session.delete(b)
        
        expired_concessions = BookingConcession.query.filter(
            BookingConcession.status == 'pending'
        ).filter(
            (BookingConcession.hold_expiry < now) |
            (BookingConcession.booking_id.is_(None)) 
        ).all()

        for c in expired_concessions:
            db.session.delete(c)

        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"Lỗi Auto-Cleanup: {str(e)}")
    
def fetch_runtime_minutes(movie_id):
    m_id_str = str(movie_id).strip()
    extra = MovieExtra.query.get(m_id_str)
    if extra and extra.runtime:
        return int(extra.runtime)
    try:
        int(m_id_str)
        data = tmdb_movie_detail(m_id_str, language='vi-VN')
        runtime = data.get('runtime') if data else None
        return int(runtime) if runtime else 120
    except (ValueError, TypeError):
        return 120

def pick_hot_and_normal_movies():
    """Lấy danh sách phim now_playing và phân 5 phim hot + danh sách thường."""
    movies = tmdb_list("movie/now_playing", page=1, language="vi-VN", region="VN")
    if not movies:
        return [], []
    movies_sorted = sorted(movies, key=lambda m: m.get('popularity', 0), reverse=True)
    hot = [str(m['id']) for m in movies_sorted[:5]]
    normal = [str(m['id']) for m in movies_sorted[5:]] 
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

def seed_room_for_day(room_id, target_date, movie_id, is_mixed=False, mixed_movies=None, start_idx=0):
    day_start_time = timeobj(7, 0) 
    day_end_time = timeobj(23, 30)
    slot_start = datetime.combine(target_date, day_start_time)
    end_dt = datetime.combine(target_date, day_end_time)
    
    show_count = 0 
    
    while True:
        if is_mixed and mixed_movies:
            current_movie = mixed_movies[(start_idx + show_count) % len(mixed_movies)]
        else:
            current_movie = movie_id
        if not current_movie:
            break
        runtime_min = fetch_runtime_minutes(current_movie)
        slot_end = slot_start + timedelta(minutes=runtime_min + 30)
        if slot_end > end_dt:
            break
        custom_id = f"ST_{current_movie}_{room_id}_{int(slot_start.timestamp())}"
        new_show = Showtime(
            id=custom_id,
            movie_id=current_movie, 
            room_id=room_id, 
            start_time=slot_start,
            price=75000 
        )
        db.session.add(new_show)
        slot_start = slot_end
        show_count += 1 
    
    db.session.flush()

def ensure_rolling_window(days=7):
    config = SystemConfig.query.filter_by(config_key='auto_seed').first()
    if config and not config.is_active:
        return
    cinemas = ensure_cinemas_and_rooms()
    hot_movies, normal_movies = pick_hot_and_normal_movies()
    if not hot_movies and not normal_movies:
        return

    today = datetime.now().date()

    start_period = datetime.combine(today, timeobj(0,0))
    end_period = datetime.combine(today + timedelta(days=days), timeobj(23,59))
    
    existing_st = Showtime.query.filter(
        Showtime.start_time >= start_period,
        Showtime.start_time <= end_period
    ).all()
    
    occupied_slots = {(st.room_id, st.start_time.date()) for st in existing_st}

    for d in range(days + 1):
        target_date = today + timedelta(days=d)

        for c in cinemas:
            rooms = Room.query.filter_by(cinema_id=c.id).order_by(Room.name).all()
            for i, room in enumerate(rooms):
                if (room.id, target_date) not in occupied_slots:
                    if i < 5:
                        m_id = hot_movies[i] if i < len(hot_movies) else (normal_movies[0] if normal_movies else None)
                        if m_id:
                            seed_room_for_day(room.id, target_date, m_id)
                    else: 
                        mixed = (normal_movies or []) + (hot_movies or [])
                        start_idx = 0 if i == 5 else len(mixed) // 2
                        seed_room_for_day(room.id, target_date, None, is_mixed=True, mixed_movies=mixed, start_idx=start_idx)
            
    db.session.commit()
    print(f"--- [HỆ THỐNG] ĐÃ CẬP NHẬT LỊCH CHO {days} NGÀY THÀNH CÔNG ---")

def seed_showtimes_strict_schedule(days=7):
    cinemas = ensure_cinemas_and_rooms()
    hot_movies, normal_movies = pick_hot_and_normal_movies()
    
    if len(hot_movies) < 5:
        print("Không đủ 5 phim hot. Vui lòng kiểm tra TMDB.")
        return

    today = datetime.now().date()

    for c in cinemas:
        rooms = Room.query.filter_by(cinema_id=c.id).order_by(Room.name).all()
        for d in range(days):
            target_date = today + timedelta(days=d)
            for i, room in enumerate(rooms):
                count = Showtime.query.filter(
                    Showtime.room_id == room.id,
                    db.func.date(Showtime.start_time) == target_date
                ).count()

                if count == 0:
                    if i < 5: 
                        m_id = hot_movies[i] if i < len(hot_movies) else normal_movies[0]
                        seed_room_for_day(room.id, target_date, m_id)
                    else: 
                        mixed = (normal_movies or []) + (hot_movies or [])
                        start_idx = 0 if i == 5 else len(mixed) // 2
                        seed_room_for_day(room.id, target_date, None, is_mixed=True, mixed_movies=mixed, start_idx=start_idx)
            
            db.session.commit()
    print(f"Đã cập nhật lịch chiếu cho {days} ngày (chỉ lấp đầy phòng trống).")
    
def seed_showtimes_rolling(days=7):
    config = SystemConfig.query.filter_by(config_key='auto_seed').first()
    
    if config and config.is_active == False:
        print("--- [HỆ THỐNG] CHẾ ĐỘ TỰ ĐỘNG ĐANG TẮT. BỎ QUA VIỆC TẠO LỊCH ---")
        return 
    print(f"--- [HỆ THỐNG] ĐANG TỰ ĐỘNG TẠO LỊCH CHO {days} NGÀY ---")
    ensure_rolling_window(days=days)

def generate_ticket_code():
    date_part = datetime.now().strftime('%y%m%d') 
    random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
    return f"BM{date_part}-{random_part}"