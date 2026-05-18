from flask import Blueprint, render_template, request, redirect, url_for, current_app, jsonify, flash 
from flask_login import login_required, current_user
from app.extensions import db
from app.models.booking import Booking, BookingConcession
from app.models.showtime import Showtime, SystemConfig
from app.models.user import User
from app.models.concession import Concession
from app.models.comment import Comment
from app.models.room import Room
from app.models.cinema import Cinema
from app.models.MovieExtra import MovieExtra
from datetime import datetime, timedelta, timedelta as python_time, time as timeobj
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.utils.tmdb import tmdb_movie_detail
from app.controllers.booking_controller import seed_room_for_day
from dotenv import load_dotenv
import requests, random
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

load_dotenv()
@admin_bp.route('/dashboard')
def admin_dashboard():
    now = datetime.now()
    tmdb_key = current_app.config.get('TMDB_API_KEY')
    gemini_key = current_app.config.get('GEMINI_API_KEY')
    
    period = request.args.get('period', 'all')
    cinema_id = request.args.get('cinema_id')
    filter_date = request.args.get('filter_date')
    date_range = request.args.get('date_range') 
    start_date = None
    end_date = None
    
    if date_range and " to " in date_range:
        start_date_str, end_date_str = date_range.split(" to ")
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            start_date, end_date = None, None

    def apply_time_filter(q):
        if start_date and end_date:
            return q.filter(db.func.date(Booking.booking_time) >= start_date,
                            db.func.date(Booking.booking_time) <= end_date)
        if filter_date:
            return q.filter(db.func.date(Booking.booking_time) == filter_date)
        if period == 'day':
            return q.filter(db.func.date(Booking.booking_time) == now.date())
        elif period == 'week':
            start_week = now - timedelta(days=now.weekday())
            return q.filter(Booking.booking_time >= start_week)
        elif period == 'month':
            return q.filter(db.func.extract('month', Booking.booking_time) == now.month,
                            db.func.extract('year', Booking.booking_time) == now.year)
        return q
    
    concession_subquery = db.session.query(
        BookingConcession.booking_id,
        func.sum(Concession.price * BookingConcession.quantity).label('total_concession')
    ).join(Concession).group_by(BookingConcession.booking_id).subquery()

    top_selling_raw = apply_time_filter(
        db.session.query(
            Booking.movie_id,
            func.sum(
                Booking.total_price - func.coalesce(concession_subquery.c.total_concession, 0)
            ).label('revenue'),
            func.sum(
                func.length(Booking.seat_code) - func.length(func.replace(Booking.seat_code, ',', '')) + 1
            ).label('total_seats') 
        )
        .outerjoin(concession_subquery, Booking.id == concession_subquery.c.booking_id)
        .filter(Booking.status.in_(['confirmed', 'used']))
        .filter(Booking.movie_id.isnot(None))
    ).group_by(Booking.movie_id).order_by(func.sum(Booking.total_price).desc()).limit(10).all()
    
    top_selling_movies = []
    for item in top_selling_raw:
        extra = MovieExtra.query.get(str(item.movie_id))
        
        m_title = None
        m_poster = None
        
        if extra:
            m_title = extra.title
            m_poster = extra.poster_url 
            
        if not m_title or not m_poster:
            try:
                url = f"https://api.themoviedb.org/3/movie/{item.movie_id}?api_key={tmdb_key}&language=vi-VN"
                response = requests.get(url, timeout=5).json()
                
                if 'title' in response:
                    m_title = response.get('title')
                    m_poster = response.get('poster_path')
                    
                    if not extra:
                        new_extra = MovieExtra(movie_id=str(item.movie_id), title=m_title, poster_url=m_poster)
                        db.session.add(new_extra)
                        db.session.commit()
            except Exception as e:
                print(f"Lỗi gọi TMDB cho phim {item.movie_id}: {e}")

        final_title = m_title if m_title else f"Phim #{item.movie_id}"
        
        if m_poster:
            if m_poster.startswith('http'):
                poster_link = m_poster
            else:
                poster_link = f"https://image.tmdb.org/t/p/w500{m_poster}"
        else:
            poster_link = "https://via.placeholder.com/500x750?text=No+Poster"

        top_selling_movies.append({
            'title': final_title,
            'revenue': item.revenue,
            'seats': int(item.total_seats),
            'poster': poster_link
        })

    top_rate = "Dữ liệu xếp hạng được cập nhật tự động từ hệ thống."

    pending_rev_query = db.session.query(func.sum(Booking.total_price))\
        .join(Showtime, Booking.showtime_id == Showtime.id)\
        .filter(Booking.status == 'confirmed', Showtime.start_time > now)
    pending_revenue = apply_time_filter(pending_rev_query).scalar() or 0

    real_rev_query = db.session.query(func.sum(Booking.total_price))\
        .outerjoin(Showtime, Booking.showtime_id == Showtime.id)\
        .filter(
            db.or_(
                Booking.status == 'used',
                db.and_(
                    Booking.status == 'confirmed',
                    db.or_(Showtime.start_time <= now, Showtime.id == None)
                )
            )
        )
    real_revenue = apply_time_filter(real_rev_query).scalar() or 0
    can_rev_query = db.session.query(func.sum(Booking.total_price))\
        .filter(Booking.status == 'cancelled')
    cancelled_revenue = apply_time_filter(can_rev_query).scalar() or 0

    conf_count_q = Booking.query.filter(Booking.status.in_(['confirmed', 'used']))

    tickets = apply_time_filter(conf_count_q).count()
    users_count = User.query.count()
    cinemas = Cinema.query.options(
        selectinload(Cinema.rooms).selectinload(
            Room.showtimes.and_(Showtime.start_time >= now) 
        )
    ).all()
    rooms = Room.query.all()
    concessions = Concession.query.all()
    comments = Comment.query.order_by(Comment.date_commented.desc()).limit(20).all()

    cash_flow_query = Booking.query.filter(Booking.status.in_(['confirmed', 'cancelled', 'used']))
    cash_flow = apply_time_filter(cash_flow_query).order_by(Booking.booking_time.desc()).all()

    for cf in cash_flow:
        st_obj = Showtime.query.get(cf.showtime_id) if cf.showtime_id else None
        cf.total_amt = cf.total_price

        has_movie = cf.showtime_id is not None
        has_food = len(cf.concession_items) > 0

        if has_movie and has_food:
            cf.display_type = "VÉ PHIM + BẮP NƯỚC"
            cf.badge_class = "bg-warning-subtle text-warning" 
        elif has_movie:
            cf.display_type = "VÉ PHIM"
            cf.badge_class = "bg-primary-subtle text-primary" 
        else:
            cf.display_type = "BẮP NƯỚC"
            cf.badge_class = "bg-info-subtle text-info"    

        if cf.status == 'cancelled':
            cf.amt_color = "text-danger" 
        elif cf.status == 'used':
            cf.amt_color = "text-success"
        elif st_obj and st_obj.start_time > now:
            cf.amt_color = "text-info"   
        else:
            cf.amt_color = "text-success" 

    query = Booking.query
    if cinema_id:
        query = query.join(Showtime).join(Room).filter(Room.cinema_id == cinema_id)
    if filter_date:
        query = query.filter(db.func.date(Booking.booking_time) == filter_date)
    
    raw_data = query.order_by(Booking.booking_time.desc()).limit(100).all()
    
    bookings_list = [] 
    for b in raw_data:
        st = Showtime.query.get(b.showtime_id) if b.showtime_id else None
        items = BookingConcession.query.filter_by(booking_id=b.id).all()
        movie_title = "Không kèm vé phim"
        movie_duration = 120
        if b.movie_id:
            str_m_id = str(b.movie_id)
            extra = MovieExtra.query.get(str_m_id)
            
            if extra:
                movie_title = extra.title
                movie_duration = extra.runtime or 120
            elif len(str_m_id) < 10: 
                try:
                    movie_info = tmdb_movie_detail(b.movie_id, language='vi-VN')
                    if movie_info:
                        movie_title = movie_info.get('title')
                        movie_duration = movie_info.get('runtime') or 120
                except:
                    movie_title = f"TMDB ID: {b.movie_id}"
            else:
                movie_title = f"Phim nội bộ (ID: {str_m_id})"

        is_past = False
        is_food_expired = False
        if st:
            end_time = st.start_time + timedelta(minutes=movie_duration)
            if now > end_time:
                is_past = True
        elif not st:
            is_past = True 
            if b.seat_code == "ONLY_FOOD":
                expiry_time = b.booking_time + timedelta(days=7)
                if now > expiry_time:
                    is_food_expired = True

        if b.status == 'cancelled':
            status_text = "Vé đã hủy"
            status_class = "bg-danger"
        elif b.status == 'used': 
            status_text = "Vé đã nhận"
            status_class = "bg-success"
        elif b.seat_code == "ONLY_FOOD":
            if is_food_expired:
                status_text = "Hết hạn nhận"
                status_class = "bg-secondary"
            else:
                status_text = "Vé chưa sử dụng" 
                status_class = "bg-info"
        else:
            if is_past:
                status_text = "Vé quá giờ chiếu"
                status_class = "bg-secondary" 
            else:
                status_text = "Vé chưa sử dụng"
                status_class = "bg-info"
        
        if st:
            show_info = {
                "time": st.start_time.strftime('%H:%M %d/%m/%Y'),
                "cinema": st.room.cinema.name if (st.room and st.room.cinema) else "N/A",
                "room": st.room.name if st.room else "N/A",
                "cinema_id": st.room.cinema_id if st.room else None,
                "is_past": is_past
            }
        else:
            show_info = {"time": "Không kèm vé phim", "cinema": "", "room": "", "is_past": True}

        bookings_list.append({
            'id': b.id,
            'user': b.user,
            'movie_title': movie_title,
            'show_info': show_info,
            'seat_code': b.seat_code if b.seat_code else "ONLY_FOOD",
            'concession_items': items,
            'is_food_expired': is_food_expired,  
            'status': b.status,
            'status_text': status_text, 
            'status_class': status_class,
            'total_display': b.total_price,
            'booking_date': b.booking_time.strftime('%d/%m/%Y %H:%M') if b.booking_time else "N/A"
        })
    auto_seed_config = SystemConfig.query.filter_by(config_key='auto_seed').first()
    is_auto_active = auto_seed_config.is_active if auto_seed_config else False
    return render_template('admin.html', 
                            now=now,
                            revenue=real_revenue,
                            tmdb_key=tmdb_key,    
                            pending=pending_revenue,
                            cancelled_revenue=cancelled_revenue,  
                            cash_flow=cash_flow,     
                            period=period,
                            tickets=tickets, 
                            users=users_count,
                            rooms=rooms,
                            cinemas=cinemas,
                            bookings=bookings_list, 
                            concessions=concessions,
                            comments=comments,
                            top_selling_movies=top_selling_movies,
                            top_rate=top_rate,
                            is_auto_active=is_auto_active)
@admin_bp.route('/add-showtime', methods=['POST'])
def add_showtime():
    show_id = request.form.get('showtime_id')
    m_id = request.form.get('movie_id')
    r_id = request.form.get('room_id')
    s_time = request.form.get('start_time')
    price = request.form.get('price')

    if not all([m_id, r_id, s_time, price]):
        return redirect(url_for('admin.admin_dashboard'))

    try:
        start_time_obj = datetime.strptime(s_time, '%Y-%m-%dT%H:%M')
        price_val = float(price)
        movie_id_val = str(m_id).strip()

        if show_id and show_id.strip(): 
            show = Showtime.query.get(int(show_id))
            if show:
                show.movie_id = movie_id_val
                show.room_id = int(r_id)
                show.start_time = start_time_obj
                show.price = price_val
        else:
            new_show = Showtime(
                movie_id=movie_id_val,
                room_id=int(r_id),
                start_time=start_time_obj,
                price=price_val
            )
            db.session.add(new_show)
        
        db.session.commit()
    except Exception as e:
        db.session.rollback() 
        print(f"Lỗi: {e}")
    
    return redirect(url_for('admin.admin_dashboard'))

@admin_bp.route('/delete-showtime/<id>', methods=['POST'])
def delete_showtime(id):
    show = Showtime.query.get_or_404(id)
    db.session.delete(show)
    db.session.commit()
    return redirect(url_for('admin.admin_dashboard'))

@admin_bp.route('/update-showtime', methods=['POST'])
def update_showtime():
    try:
        show_id = request.form.get('showtime_id')
        show = Showtime.query.get_or_404(show_id)
        
        # Ép kiểu string để DB VARCHAR nhận diện đúng
        new_movie_id = str(request.form.get('movie_id')).strip()
        show.movie_id = new_movie_id
        
        show.room_id = int(request.form.get('room_id'))
        show.price = float(request.form.get('price'))
        
        s_time = request.form.get('start_time')
        if s_time:
            show.start_time = datetime.strptime(s_time, '%Y-%m-%dT%H:%M')
        
        db.session.commit()
        print(f"--- ĐÃ CẬP NHẬT THÀNH CÔNG ---")
        return "OK"
    except Exception as e:
        db.session.rollback()
        print(f"LỖI CỤ THỂ: {str(e)}")
        return f"Error: {str(e)}", 500

@admin_bp.route('/manual-auto-seed', methods=['POST'])
def manual_auto_seed():
    data = request.json
    cinema_id = data.get('cinema_id')
    date_str = data.get('date')
    movie_ids = data.get('movie_ids') 

    tmdb_key = current_app.config.get('TMDB_API_KEY')
    all_mixed_pool = []
    
    try:
        res = requests.get(f"https://api.themoviedb.org/3/movie/now_playing?api_key={tmdb_key}&language=vi-VN&page=1", timeout=5)
        tmdb_ids = [m['id'] for m in res.json().get('results', [])]
        temp_pool = list(set([str(tid) for tid in tmdb_ids] + [str(mid) for mid in movie_ids]))
        all_mixed_pool = random.sample(temp_pool, len(temp_pool)) 
        print(f">>> Pool đã trộn ngẫu nhiên: {all_mixed_pool[:5]}...") 
    except:
        all_mixed_pool = [str(mid) for mid in movie_ids]

    print(f"--- DEBUG TẠO LỊCH TỰ ĐỘNG ---")
    print(f"Đang tìm phòng cho rạp ID: {cinema_id}")

    if not date_str or not movie_ids or len(movie_ids) < 5:
        return jsonify({"success": False, "message": "Thiếu dữ liệu ngày hoặc phim!"})

    try:
        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        rooms = Room.query.filter_by(cinema_id=cinema_id).order_by(Room.name).all()

        print(f"Số lượng phòng tìm thấy trong DB: {len(rooms)}")
        if not rooms:
            return jsonify({"success": False, "message": "Rạp này không có phòng nào!"})

        day_start = datetime.combine(target_date, timeobj(0, 0))
        day_end = datetime.combine(target_date, timeobj(23, 59))
        room_ids = [r.id for r in rooms]
        
        Showtime.query.filter(
            Showtime.room_id.in_(room_ids),
            Showtime.start_time >= day_start,
            Showtime.start_time <= day_end
        ).delete(synchronize_session=False)

        db.session.flush()

        for i, room in enumerate(rooms):
            if i < 5:
                seed_room_for_day(room.id, target_date, str(movie_ids[i]))
            else:
                seed_room_for_day(room.id, target_date, None, is_mixed=True, mixed_movies=all_mixed_pool, start_idx=i)

        db.session.commit()
        return jsonify({"success": True, "message": f"Đã phủ kín lịch cho {len(rooms)} phòng thành công!"})

    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": f"Lỗi hệ thống: {str(e)}"})

@admin_bp.route('/delete-schedule', methods=['POST'])
def delete_schedule():
    
    try:
        data = request.json
        cinema_id = data.get('cinema_id')
        date_str = data.get('date')
        
        if not cinema_id or not date_str:
            return jsonify({"success": False, "message": "Thiếu thông tin rạp hoặc ngày!"})

        target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_start = datetime.combine(target_date, python_time(0, 0, 0))
        day_end = datetime.combine(target_date, python_time(23, 59, 59))

        rooms = Room.query.filter_by(cinema_id=cinema_id).all()
        if not rooms:
            return jsonify({"success": False, "message": "Rạp này không có phòng nào!"})
            
        room_ids = [r.id for r in rooms]

        deleted_count = Showtime.query.filter(
            Showtime.room_id.in_(room_ids),
            Showtime.start_time >= day_start,
            Showtime.start_time <= day_end
        ).delete(synchronize_session=False)

        db.session.commit()
        return jsonify({
            "success": True, 
            "message": f"Đã xóa thành công {deleted_count} suất chiếu ngày {date_str}."
        })
        
    except Exception as e:
        db.session.rollback()
        print(f"LỖI XÓA LỊCH: {str(e)}") 
        return jsonify({
            "success": False, 
            "message": f"Lỗi hệ thống: {str(e)}"
        })

@admin_bp.route('/toggle-auto-seed', methods=['POST'])
def toggle_auto_seed():
    data = request.json
    status = data.get('status') 
    
    config = SystemConfig.query.filter_by(config_key='auto_seed').first()
    if not config:
        config = SystemConfig(config_key='auto_seed', is_active=status)
        db.session.add(config)
    else:
        config.is_active = status
        
    db.session.commit()
    msg = "Đã BẬT tự động" if status else "Đã TẮT tự động"
    return jsonify({"success": True, "message": msg})

@admin_bp.route('/save-concession', methods=['POST'])
def save_concession():
    c_id = request.form.get('id')
    name = request.form.get('name')
    price = float(request.form.get('price'))
    img = request.form.get('img')
    desc = request.form.get('description')
    cat = request.form.get('category')

    if c_id:
        item = Concession.query.get(c_id)
        item.name = name
        item.price = price
        item.img = img
        item.description = desc
        item.category = cat
    else:
        new_item = Concession(name=name, price=price, img=img, description=desc, category=cat)
        db.session.add(new_item)
    
    db.session.commit()
    return redirect(url_for('admin.admin_dashboard'))

@admin_bp.route('/delete-concession/<int:id>')
def delete_concession(id):
    item = Concession.query.get_or_404(id)
    db.session.delete(item)
    db.session.commit()
    return redirect(url_for('admin.admin_dashboard'))
@admin_bp.route('/cancel_booking', methods=['POST']) 
def cancel_booking():
    booking_id = request.form.get('booking_id')
    if not booking_id:
        return jsonify({"success": False, "message": "Thiếu ID"}), 400
    
    try:
        booking = Booking.query.get_or_404(int(booking_id))
        st = Showtime.query.get(booking.showtime_id)
        
        if st and datetime.now() > (st.start_time - timedelta(minutes=30)):
            return jsonify({"success": False, "message": "Quá hạn hủy vé (30p)!"}), 400

        booking.status = 'cancelled'
        if hasattr(booking, 'concession_items'):
            for item in booking.concession_items:
                item.status = 'cancelled'
                
        db.session.commit()
        return jsonify({"success": True, "message": "Đã hủy thành công"})
        
    except Exception as e:
        db.session.rollback()
        return jsonify({"success": False, "message": str(e)}), 500
@admin_bp.route('/confirm_used/<code>', methods=['POST'])
def confirm_used(code):
    booking = Booking.query.filter_by(ticket_code=code).first()
    if not booking and code.isdigit():
        booking = Booking.query.get(int(code))
        
    if not booking:
        return jsonify({"status": "error", "message": "Vé không tồn tại"}), 404
    
    if booking.status == 'used':
        return jsonify({"status": "error", "message": "Vé này đã được sử dụng trước đó!"}), 400
        
    if booking.status != 'confirmed':
        return jsonify({"status": "error", "message": "Vé chưa được thanh toán hoặc đang bị hủy!"}), 400
    try:
        booking.status = 'used'
        
        items = BookingConcession.query.filter_by(booking_id=booking.id).all()
        for item in items:
            item.status = 'used' 
            
        db.session.commit()
        return jsonify({"status": "success", "message": "Xác nhận thành công!"})
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)})
@admin_bp.route('/pos_checkin')
def pos_checkin():
    return render_template('pos_checkin.html')

@admin_bp.route('/get_booking_info/<t_code>')
def get_booking_info(t_code):
    try:
        booking = Booking.query.filter_by(ticket_code=t_code.upper()).first()
        if not booking:
            return jsonify({"status": "error", "message": "Mã vé không tồn tại!"})
        movie_title = f"Phim (ID: {booking.movie_id})"

        extra_movie = MovieExtra.query.filter_by(movie_id=str(booking.movie_id)).first()
        if extra_movie:
            movie_title = extra_movie.title
        else:
            if booking.movie_id and str(booking.movie_id).isdigit() and len(str(booking.movie_id)) < 10:
                try:
                    movie_info = tmdb_movie_detail(booking.movie_id, language='vi-VN')
                    if movie_info and movie_info.get('title'):
                        movie_title = movie_info.get('title')
                except:
                    pass
        cinema_name = "Bee Movie"
        room_name = "N/A"
        show_time_str = "Không có suất chiếu"
        is_past = False

        if booking.showtime_id:
            st = Showtime.query.get(booking.showtime_id)
            if st:
                show_time_str = st.start_time.strftime('%H:%M %d/%m/%Y')
                is_past = st.start_time < datetime.now() 
                if st.room:
                    room_name = st.room.name
                    if st.room.cinema:
                        cinema_name = st.room.cinema.name

        status_map = {
            'confirmed': ('Hợp lệ', 'bg-success'),
            'used': ('Đã sử dụng', 'bg-secondary'),
            'pending': ('Chờ thanh toán', 'bg-warning'),
            'cancelled': ('Đã hủy', 'bg-danger')
        }
        st_text, st_class = status_map.get(booking.status, (booking.status, 'bg-info'))

        food_list = []
        if booking.concession_items:
            food_list = [f"{item.quantity}x {item.concession.name}" for item in booking.concession_items if item.concession]

        return jsonify({
            "status": "success",
            "customer_name": booking.user.username if booking.user else "Ẩn danh",
            "booking_status": booking.status,
            "status_text": st_text,
            "status_class": st_class,
            "payment_method": booking.payment_method,
            "total": f"{booking.total_price:,.0f}đ",
            "created_at": booking.booking_time.strftime('%d/%m/%Y %H:%M'),
            "movie_title": movie_title,
            "cinema": cinema_name,
            "room": room_name,
            "show_time": show_time_str,
            "seat_code": booking.seat_code if booking.seat_code else "FOOD",
            "food_list": food_list,
            "is_past": is_past
        })
    except Exception as e:
        import traceback
        traceback.print_exc() 
        return jsonify({"status": "error", "message": f"Server Error: {str(e)}"}), 500

@admin_bp.route('/api/tmdb-proxy/<int:movie_id>')
def tmdb_proxy(movie_id):
    """Lấy dữ liệu từ TMDB bằng Key lưu trên Server"""
    api_key = current_app.config.get('TMDB_API_KEY')
    url = f"https://api.themoviedb.org/3/movie/{movie_id}?api_key={api_key}&language=vi-VN"
    
    try:
        response = requests.get(url, timeout=5)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@admin_bp.before_request
def restrict_admin_access():
    if request.endpoint == 'static':
        return
    if not current_user.is_authenticated:
        flash("Vui lòng đăng nhập trước!", "warning")
        return redirect(url_for('auth.login'))
    if current_user.role != 'admin':
        flash("Bạn không có quyền truy cập khu vực này!", "danger")
        return redirect(url_for('main.index')) 
@admin_bp.route('/save-movie-extra', methods=['POST'])
def save_movie_extra():
    data = request.form
    m_id = data.get('movie_id')
    if not m_id:
        return "Thiếu Movie ID", 400
    
    extra = MovieExtra.query.get(m_id)
    if not extra:
        extra = MovieExtra(movie_id=m_id)
        db.session.add(extra)
    extra.title = data.get('title')
    extra.poster_url = data.get('poster_url')
    extra.trailer_id = data.get('trailer_id')
    extra.runtime = int(data.get('runtime')) if data.get('runtime') else None
    extra.overview = data.get('overview')
    extra.release_date = data.get('release_date')
    extra.backdrop_url = data.get('backdrop_url')
    extra.original_language = data.get('original_language')
    extra.director = data.get('director')
    extra.genres = data.get('genres')
    extra.cast = data.get('cast')
    db.session.commit()
    return f"""
    <script>
        alert('Đã lưu thành công thông tin ghi đè cho phim ID: {m_id}');
    </script>
    """
@admin_bp.route("/get-movie-extra/<movie_id>")
def get_movie_extra(movie_id):
    try:
        m_id = str(movie_id).strip()
        extra = MovieExtra.query.filter_by(movie_id=m_id).first()
        
        if extra:
            return {
                "exists": True,
                "is_extra": True,
                "movie_id": extra.movie_id, 
                "title": extra.title or "",
                "poster_url": extra.poster_url or "",
                "trailer_id": extra.trailer_id or "",
                "runtime": extra.runtime or 0,
                "overview": extra.overview or "",
                "release_date": str(extra.release_date) if extra.release_date else "",
                "backdrop_url": extra.backdrop_url or "",
                "original_language": extra.original_language or "",
                "director": extra.director or "",
                "genres": extra.genres or "",
                "cast": extra.cast or ""
            }
        
        return {"exists": False}

    except Exception as e:
        import traceback
        print("--- LỖI TẠI ROUTE GET-MOVIE-EXTRA ---")
        traceback.print_exc() 
        return {"exists": False, "error": str(e)}, 500
@admin_bp.route('/search-movie-extra')
def search_movie_extra():
    query = request.args.get('q', '').strip()
    if not query:
        return jsonify([])

    movies = MovieExtra.query.filter(MovieExtra.title.icontains(query)).limit(8).all()
    
    results = []
    for m in movies:
        results.append({
            'movie_id': m.movie_id,
            'title': m.title,
            'poster_url': m.poster_url,
            'release_date': str(m.release_date) if m.release_date else "N/A"
        })
        
    return jsonify(results)
@admin_bp.route('/delete-comment/<int:comment_id>', methods=['POST'])
@login_required 
def delete_comment(comment_id):
    comment = Comment.query.get(comment_id)
    
    if not comment:
        return jsonify({"success": False, "message": "Bình luận không tồn tại!"}), 404
    
    if current_user.role != 'admin': 
        return jsonify({"success": False, "message": "Ông không có quyền admin để xóa!"}), 403

    try:
        db.session.delete(comment)
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()        
        return jsonify({"success": False, "message": str(e)}), 500