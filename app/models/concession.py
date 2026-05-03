from app.extensions import db

class Concession(db.Model):
    __tablename__ = 'concessions'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    price = db.Column(db.Float, nullable=False)
    img = db.Column(db.String(255))
    description = db.Column(db.Text)
    category = db.Column(db.String(20))