from app.extensions import db
from datetime import datetime

class Showtime(db.Model):
    __tablename__ = 'showtimes'
    id = db.Column(db.Integer, primary_key=True)
    movie_id = db.Column(db.Integer, nullable=False)
    room_id = db.Column(db.Integer, db.ForeignKey('rooms.id'), nullable=False)
    start_time = db.Column(db.DateTime, nullable=False)
    price = db.Column(db.Integer, default=75000)

    @property
    def final_price(self):
        extra = 0
        if self.start_time.weekday() in [5, 6]:
            extra = 20000
        holidays = ["01-01", "30-04", "01-05", "02-09"]
        if self.start_time.strftime("%d-%m") in holidays:
            extra = 20000
        return self.price + extra
