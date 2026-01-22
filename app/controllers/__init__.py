from .auth_controller import auth_routes 
from .movie_controller import movie_routes
from .booking_controller import booking_routes, register_movie_routes

def register_controllers(app):
    auth_routes(app)
    register_movie_routes(app)
    movie_routes(app)
    booking_routes(app)
