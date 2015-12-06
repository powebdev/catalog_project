from flask import Flask, render_template, request, url_for
from flask import redirect, jsonify, make_response, flash
from flask import session as login_session
app = Flask(__name__)

from sqlalchemy import create_engine, desc
from sqlalchemy.orm import sessionmaker, joinedload
from database_setup import Base, Genre, Game, User
from datetime import datetime
from werkzeug.contrib.atom import AtomFeed

import random
import string
from oauth2client.client import flow_from_clientsecrets
from oauth2client.client import FlowExchangeError
from oauth2client.client import OAuth2Credentials
import httplib2
import json
import requests

CLIENT_ID = json.loads(
    open('client_secrets.json', 'r').read())['web']['client_id']

engine = create_engine('sqlite:///videogame.db')
Base.metadata.bind = engine

DBSession = sessionmaker(bind=engine)
session = DBSession()


@app.route('/login')
def show_login():
    """Returns the login page."""
    state = make_csrf_token()
    login_session['state'] = state
    return render_template('login.html', STATE=state)


@app.route('/gconnect', methods=['POST'])
def gconnect():
    """Performs login operation using 3rd party authentication service.
    As shown in the oauth course
    """
    if request.args.get('state') != login_session['state']:
        response = make_response(json.dumps('Invalid state parameter'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    code = request.data
    try:
        # Upgrade the authorization code into a credentials object
        oauth_flow = flow_from_clientsecrets('client_secrets.json', scope='')
        oauth_flow.redirect_uri = 'postmessage'
        credentials = oauth_flow.step2_exchange(code)
    except FlowExchangeError:
        response = make_response(json.dumps(
            'Failed to upgrade the authorization code.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response
    access_token = credentials.access_token
    url = ('https://www.googleapis.com/oauth2/v1/tokeninfo?access_token=%s'
           % access_token)
    h = httplib2.Http()
    result = json.loads(h.request(url, 'GET')[1])

    # if there was an error in the access token info, abort.
    if result.get('error') is not None:
        response = make_response(json.dumps(result.get('error')), 500)
        response.headers['Content-Type'] = 'application/json'

    # verify that the access token is used for the intended user.
    gplus_id = credentials.id_token['sub']
    if result['user_id'] != gplus_id:
        response = make_response(
            json.dumps("Token's user ID doesn't match given user ID."),
            401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # verify that the access token is valid for this app.
    if result['issued_to'] != CLIENT_ID:
        response = make_response(
            json.dumps("Token's client ID does not match app's."),
            401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Check to see if user is already logged in.
    stored_credentials = login_session.get('credentials')
    stored_gplus_id = login_session.get('gplus_id')
    if stored_credentials is not None and gplus_id == stored_gplus_id:
        response = make_response(json.dumps(
            'Current user is already connected.'), 200)
        response.headers['Content-Type'] = 'application/json'

    # store the access token in the session for later use.
    login_session['credentials'] = credentials.to_json()
    login_session['gplus_id'] = gplus_id

    # get user info
    userinfo_url = "https://www.googleapis.com/oauth2/v1/userinfo"
    params = {'access_token': credentials.access_token, 'alt': 'json'}
    answer = requests.get(userinfo_url, params=params)

    data = json.loads(answer.text)

    login_session['username'] = data["name"]
    login_session['picture'] = data["picture"]
    login_session['email'] = data["email"]

    user_id = get_user_id(data["email"])
    if user_id is None:
        user_id = create_user(login_session)
    login_session['user_id'] = user_id

    output = ''
    output += '<h1>Welcome, '
    output += login_session['username']
    output += '!</h1>'
    output += '<img src="'
    output += login_session['picture']
    output += ' " style = "width: 300px; height: 300px;'
    output += 'border-radius: 150px;-webkit-border-radius: 150px;'
    output += '-moz-border-radius: 150px;"> '

    return output


# DISCONNECT - Revoke a current user's token and reset their login_session.
@app.route('/gdisconnect')
def gdisconnect():
    """Performs logout operation from 3rd party authentication service
        As shown in the oauth course
    """
    # Only disconnect a connected user.
    credentials = OAuth2Credentials.from_json(login_session.get('credentials'))

    if credentials is None:
        response = make_response(json.dumps(
            'Current user not connected.'), 401)
        response.headers['Content-Type'] = 'application/json'
        return response

    # Execute HTTP GET request to revoke current token.
    access_token = credentials.access_token
    url = ('https://accounts.google.com/o/oauth2/revoke?token=%s'
           % access_token)
    h = httplib2.Http()
    result = h.request(url, 'GET')[0]

    if result['status'] == '200':
        del login_session['credentials']
        del login_session['gplus_id']
        del login_session['username']
        del login_session['email']
        del login_session['picture']
        del login_session['user_id']

        response = make_response(json.dumps('Successfully disconnected.'), 200)
        response.headers['Content-Type'] = 'application/json'
        # return response
        return redirect('/catalog')
    else:
        # For invalid token
        response = make_response(
            json.dumps('Failed to revoke for given user.', 400))
        response.headers['Content-Type'] = 'application/json'
        return response


@app.route('/')
@app.route('/catalog/')
def show_home():
    """Displays the frontpage with a list of newly added games.
    """
    all_genres = get_all_genres()
    newly_added_games = get_new_games()
    return render_template('catalog.html',
                           genre_info=all_genres,
                           new_items=newly_added_games)


@app.route('/catalog/new/', methods=['GET', 'POST'])
def add_new_game():
    """Displays the  page and handle the POST request for create operation.
    """
    if 'username' not in login_session:
        return redirect('/login')
    if request.method == 'POST':
        game_title = request.form['game_name']
        game_developed_by = request.form['game_developed']
        game_published_by = request.form['game_published']
        game_release_year = request.form['game_release_year']
        game_description = request.form['game_description']
        game_genre = request.form['genres']
        game_image = request.form['game_image_url']
        time_now = datetime.now()
        added_by_user_id = login_session['user_id']

        if game_genre == "New Genre":
            selected_genre_id = create_genre(request.form['new_genre'])
        else:
            selected_genre_id = get_genre_id(game_genre)

        game_info = create_game_info_dict(game_name=game_title,
                                          developer=game_developed_by,
                                          publisher=game_published_by,
                                          image_url=game_image,
                                          genre_id=selected_genre_id,
                                          user_id=added_by_user_id,
                                          description=game_description,
                                          release_year=game_release_year,
                                          time_now=time_now)

        new_game_id = create_game(game_info)
        if new_game_id is None:
            flash("Something went wrong when tried to add game to database",
                  "error")
        else:
            flash(get_game_info(new_game_id).name+" was added to the database")

        return redirect(url_for('show_home'))

    genre_list = get_all_genres()
    return render_template('new_game.html', genre_info=genre_list)


@app.route('/catalog/<int:genre_id>/')
@app.route('/catalog/<int:genre_id>/games/')
def show_game_list(genre_id):
    """Displays all games in selected genre.
    Args:
      genre_id: the id of the game's genre.
    """
    genre_list = get_all_genres()
    all_games = get_all_games_in_genre(genre_id)
    seleced_genre = get_genre_info(genre_id)
    return render_template('game_list.html',
                           genre_info=genre_list,
                           game_info=all_games,
                           game_genre=seleced_genre)


@app.route('/catalog/<int:genre_id>/games/<int:game_id>/')
def show_game(genre_id, game_id):
    """Displays information for requested game.
    Args:
      genre_id: the id of the game's genre.
      game_id: the id of the game to search for in DB.
    """
    game_result = get_game_info(game_id)
    ownership = False

    if 'username' in login_session:
        user_id = get_user_id(login_session['email'])
        if user_id == game_result.user_id:
            ownership = True

    return render_template('game_detail.html',
                           game_info=game_result,
                           is_user=ownership)


@app.route('/catalog/<int:genre_id>/games/<int:game_id>/edit/',
           methods=['GET', 'POST'])
def edit_game(genre_id, game_id):
    """Displays the  page and handle the POST request for update operation.
    Args:
      genre_id: the id of the game's genre.
      game_id: the id of the game to search for in DB.
    """
    if 'username' not in login_session:
        return redirect('/login')
    game_result = get_game_info(game_id)
    current_user_id = get_user_id(login_session['email'])
    if request.method == 'POST':

        new_name = request.form['game_name']
        new_description = request.form['game_description']
        new_developed_by = request.form['game_developed']
        new_published_by = request.form['game_published']
        new_year = request.form['game_release_year']
        new_image_url = request.form['game_image_url']
        game_genre = request.form['genres']
        if new_name != "":
            game_result.name = new_name

        game_result.description = new_description
        game_result.developed_by = new_developed_by
        game_result.published_by = new_published_by
        game_result.release_year = new_year
        game_result.image_url = new_image_url

        if game_genre == "New Genre":
            selected_genre_id = create_genre(request.form['new_genre'])
        else:
            selected_genre_id = get_genre_id(game_genre)

        if selected_genre_id is not None:
                game_result.genre_id = selected_genre_id
        update_row(game_result)
        new_game_result = get_all_games_in_genre(genre_id)
        if len(new_game_result) == 0:
            genre_result = get_genre_info(genre_id)
            delete_row(genre_result)
        flash("Changes to " + game_result.name + " has been made")
        return redirect(url_for('show_home'))
    else:
        if game_result.user_id != current_user_id:
            return render_template('deadend.html')
        else:
            return render_template('edit_game.html',
                                   genre_info=get_all_genres(),
                                   genre_id=genre_id,
                                   game_id=game_id,
                                   game_info=game_result)


@app.route('/catalog/<int:genre_id>/games/<int:game_id>/delete/',
           methods=['GET', 'POST'])
def delete_game(genre_id, game_id):
    """Displays the  page and handle the POST request for delete operation.
    Args:
      genre_id: the id of the game's genre.
      game_id: the id of the game to search for in DB.
    """
    if 'username' not in login_session:
        return redirect('/login')
    game_result = get_game_info(game_id)
    current_user_id = get_user_id(login_session['email'])

    if request.method == 'POST':
        client_token = request.form['csrf_token']
        if login_session['csrf_token'] != client_token:
            response = make_response(json.dumps('Invalid state parameter'),
                                     401)
            response.headers['Content-Type'] = 'application/json'
            return response

        delete_row(game_result)
        new_game_result = get_all_games_in_genre(genre_id)
        if len(new_game_result) == 0:
            genre_result = get_genre_info(genre_id)
            delete_row(genre_result)
        flash(game_result.name + " was deleted from database")
        return redirect(url_for('show_home'))
    else:
        if game_result.user_id != current_user_id:
            return render_template('deadend.html')
        else:
            state = make_csrf_token()
            login_session['csrf_token'] = state
            return render_template('delete_game.html',
                                   genre_id=genre_id,
                                   game_id=game_id,
                                   game_info=game_result,
                                   CSRF_TOKEN=state)


@app.route('/catalog/JSON/')
def all_genres_JSON():
    """Handles API request for all genre entries in the DB.

    The function returns the information requested in JSON format.
    """
    genre_result = get_all_genres()
    return jsonify(Genres=[item.serialize for item in genre_result])


@app.route('/catalog/<int:genre_id>/games/JSON/')
def games_from_one_genre_JSON(genre_id):
    """Handles API request for all game entries within a genre

    The function returns the information requested in JSON format.
    Args:
      genre_id: the id of the genre to search for in the DB.
    """
    games_result = get_all_games_in_genre(genre_id)
    return jsonify(Games=[item.serialize for item in games_result])


@app.route('/catalog/<int:genre_id>/games/<int:game_id>/JSON/')
def one_game_in_genre_JSON(genre_id, game_id):
    """Handles API request for a single game entry in the DB

    The function returns the information requested in JSON format.
    Args:
      genre_id: the id of the game's genre.
      game_id: the id of the game to search for in DB.
    """
    game_result = get_game_info(game_id)
    return jsonify(Game=game_result.serialize)


@app.route('/catalog/recent_feed/')
def new_game_feed():
    """Displays a page for user to subscribe to the AtomFeed
       of newly added games.
    """
    feed = AtomFeed("Recently Added Games",
                    feed_url=request.url,
                    url=request.host_url)
    new_games = get_new_games()
    for game in new_games:
        user_name = get_user_info(game.user_id).name
        game_url = make_game_ext_url(game.genre_id, game.id)

        feed.add(game.name,
                 url=game_url,
                 updated=game.date_added,
                 author=user_name)
    return feed.get_response()


def make_game_ext_url(genre_id, game_id):
    """Helper function for AtomFeed

    The function returns the url for the game detai page for the selected game.
    Args:
      genre_id: the id of the game's genre.
      game_id: the id of the game to search for in DB.
    """
    return url_for('show_game', genre_id=genre_id, game_id=game_id)


def get_all_genres():
    """Returns all game genres currently in the database."""
    return session.query(Genre).all()


def get_all_games():
    """Returns all games currently in the database."""
    return session.query(Game).all()


def get_all_games_in_genre(genre_id):
    """Returns all games from one genre with genre_id
    Args:
      genre_id: the id of the genre entry in the DB to search for.
    """
    return session.query(Game).filter_by(genre_id=genre_id).all()


def get_game_info(game_id):
    """Returns a Game object with id of game_id
    Args:
      game_id: the id of the game entry in the DB to search for
    """
    return (session.query(Game)
            .options(joinedload('genre'))
            .filter_by(id=game_id).one())


def create_game_info_dict(game_name, image_url, developer, publisher, genre_id,
                          user_id, description, release_year, time_now):
    """Returns a dictionary containing relavent information of the game
    Args:
      game_name: title of the game.
      image_url: URL for the game art.
      developer: the developer of the game.
      publisher: the publisher of the game.
      genre_id: the id of the game's genre.
      user_id: the id of the user that's creating this entry.
      description: a short decription of the game.
      release_year: the year the game was released.
      time_now: a Python Datetime object used to indicate
                when the DB entry was created.
    """
    game_info = {}
    game_info['name'] = game_name
    game_info['image_url'] = image_url
    game_info['published_by'] = publisher
    game_info['developed_by'] = developer
    game_info['genre_id'] = genre_id
    game_info['user_id'] = user_id
    game_info['description'] = description
    game_info['release_year'] = release_year
    game_info['date_added'] = time_now

    return game_info


def create_game(game_info):
    """Adds a game entry into the DB and return the id of the game
    Args:
      game_info: a dictionary returned by the fucntion create_game_info_dict,
                 which contains information to be added into the DB.
    """
    if game_info['name'] == "" or game_info['genre_id'] is None:
        flash("Both game name and game genre are required", "error")
        return None
    new_game = Game(name=game_info['name'],
                    description=game_info['description'],
                    developed_by=game_info['developed_by'],
                    published_by=game_info['published_by'],
                    release_year=game_info['release_year'],
                    image_url=game_info['image_url'],
                    genre_id=game_info['genre_id'],
                    user_id=game_info['user_id'],
                    date_added=game_info['date_added'])
    session.add(new_game)
    session.commit()
    game = (session.query(Game).filter_by(name=game_info['name'],
                                          user_id=game_info['user_id']).one())
    return game.id


def create_user(login_session):
    """Adds a user to the DB.
    Args:
      login_session: a dictionary containing the user information
                     to be added into the DB.
    """
    new_user = User(name=login_session['username'],
                    email=login_session['email'],
                    picture=login_session['picture'])
    session.add(new_user)
    session.commit()
    user = (session.query(User)
            .filter_by(email=login_session['email']).one())
    return user.id


def get_user_info(user_id):
    """Returns a user object with id of user_id
    Args:
      user_id: the id of the user entry in the DB to search for
    """
    try:
        user = session.query(User).filter_by(id=user_id).one()
        return user
    except:
        return None


def get_user_id(email):
    """Returns the id of the user DB with matching email
    Args:
      email: the email of the user entry in the DB to search for
    """
    try:
        user = session.query(User).filter_by(email=email).one()
        return user.id
    except:
        return None


def create_genre(genre_name):
    """Adds a genre to the DB
    Args:
      genre_name: name of the genre to be added
    """
    if genre_name == "":
        return None
    if not get_genre_id(genre_name):
        new_genre = Genre(name=genre_name)
        session.add(new_genre)
        session.commit()
    genre = (session.query(Genre)
             .filter_by(name=genre_name).one())
    return genre.id


def get_genre_info(genre_id):
    """Returns a genre object with id of genre_id
    Args:
      genre_id: the id of the genre entry in the DB to search for
    """
    genre = session.query(Genre).filter_by(id=genre_id).one()
    return genre


def get_genre_id(genre_name):
    """Returns the id of the genre DB with matching name
    Args:
      genre_name: the name of the genre entry in the DB to search for.
    """
    try:
        genre = session.query(Genre).filter_by(name=genre_name).one()
        return genre.id
    except:
        return None


def delete_row(row):
    """Delete a record in the DB that's the row object
    Args:
      row: row in DB to be deleted
    """
    session.delete(row)
    session.commit()


def update_row(row):
    """Update a record in the DB that's the row object
    Args:
      row: row in DB to be updated
    """
    session.add(row)
    session.commit()


def make_csrf_token():
    """Helper function to make randomized CSRF token.
    """
    state = ''.join(random.choice(string.ascii_uppercase + string.digits)
                    for x in xrange(32))
    return state


def get_new_games():
    """Returns the last tex games which were added to the database."""
    newly_added_games = (session.query(Game).order_by(desc(Game.date_added))
                         .limit(10).all())
    return newly_added_games


if __name__ == '__main__':
    app.secret_key = 'super_secret_key'
    app.debug = True
    app.run(host='0.0.0.0', port=5000)
