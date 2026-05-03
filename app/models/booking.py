from app.extensions import db
from datetime import datetime

class Booking(db.Model):
    __tablename__ = 'bookings'
    id = db.Column(db.Integer, primary_key=True)
    ticket_code = db.Column(db.String(20), unique=True, nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    
    showtime_id = db.Column(db.String(50), db.ForeignKey('showtimes.id', ondelete='CASCADE'), nullable=True) 
    movie_id = db.Column(db.String(50), nullable=True)
    
    seat_code = db.Column(db.String(255), nullable=False)
    booking_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='pending')
    payment_method = db.Column(db.String(50), default='N/A')
    hold_expiry = db.Column(db.DateTime, nullable=True)
    total_price = db.Column(db.Float, default=0.0)
    user = db.relationship('User', back_populates='bookings')
    concession_items = db.relationship('BookingConcession', backref='parent_booking', cascade="all, delete-orphan")

class BookingConcession(db.Model):
    __tablename__ = 'booking_concessions'
    id = db.Column(db.Integer, primary_key=True)
    
    booking_id = db.Column(db.Integer, db.ForeignKey('bookings.id'), nullable=True) 
    
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    concession_id = db.Column(db.Integer, db.ForeignKey('concessions.id'), nullable=False)
    quantity = db.Column(db.Integer, default=1)
    payment_method = db.Column(db.String(50), default='N/A')
    booking_time = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(20), default='hold')
    hold_expiry = db.Column(db.DateTime, nullable=True)

    concession = db.relationship('Concession', backref='booking_items')