from app.extensions import db

class MovieExtra(db.Model):
    __tablename__ = 'movie_extra'
    
    movie_id = db.Column(db.String(50), primary_key=True)
    title = db.Column(db.String(255), nullable=True)
    release_date = db.Column(db.String(20), nullable=True) 
    runtime = db.Column(db.Integer, nullable=True) 
    original_language = db.Column(db.String(50), nullable=True)
    poster_url = db.Column(db.String(500), nullable=True)
    backdrop_url = db.Column(db.String(500), nullable=True) 
    trailer_id = db.Column(db.String(100), nullable=True) 
    overview = db.Column(db.Text, nullable=True)
    genres = db.Column(db.String(255), nullable=True)
    director = db.Column(db.String(255), nullable=True) 
    cast = db.Column(db.Text, nullable=True) 

    def __init__(self, **kwargs):
        super(MovieExtra, self).__init__(**kwargs)