import base64
from os import environ, getcwd
from datetime import datetime
from random import choice
from urllib.parse import urlparse, urljoin

import qrcode
from flask import Flask, redirect, request, url_for, abort
from flask_bcrypt import Bcrypt
from flask_login import (
    LoginManager,
    login_required,
    login_user,
    logout_user,
    current_user,
    UserMixin,
)
from flask_sqlalchemy import SQLAlchemy
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Attachment, Content, Mail
from sqlalchemy import desc, exc

app = Flask(__name__, static_folder=getcwd())
db = SQLAlchemy(app)
app.secret_key = "qwertyuiop"
bcrypt = Bcrypt(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"
app.config["SQLALCHEMY_DATABASE_URI"] = environ["DATABASE_URL"]

SENDGRID_API_KEY = environ["SENDGRID_API_KEY"]
sg = SendGridAPIClient(SENDGRID_API_KEY)


class P5November2019(db.Model):
    """
    Database model class
    """

    __tablename__ = "p5_november_2019"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(30))
    email = db.Column(db.String(50), unique=True)
    phone = db.Column(db.String(10), unique=True)


class User(db.Model, UserMixin):
    label = db.Column(db.String(10), unique=True)
    username = db.Column(db.String(30), primary_key=True)
    password = db.Column(db.String(100))
    email = db.Column(db.String(50), unique=True)
    phone = db.Column(db.String(10), unique=True)
    user_type = db.Column(db.String(1))

    def get_id(self):
        return self.username if self is not None else None


@login_manager.user_loader
def load_user(username):
    return db.session.query(User).get(username)


@login_manager.request_loader
def load_user_from_request(request):
    auth = request.headers.get("Authorization")
    if auth:
        auth = auth.replace("Basic ", "", 1)
        base64.b64decode(auth).decode("utf-8")
        username, password = auth.split("|")
        users = db.session.query(User).get(username)
        for user in users:
            if bcrypt.check_password_hash(user.password, password):
                return user

    return None


def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ("http", "https") and ref_url.netloc == test_url.netloc


@app.route("/")
def root():
    return app.send_static_file("index.html")


@app.route("/logout")
def logout():
    logout_user()


def userlogin(user):
    login_user(user)
    if user.type == "O":
        page = "organiser"
    elif user.type == "U":
        page = "user"
    return redirect(url_for(page))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        try:
            label = request.form["label"]
            user = db.session.query(User).filter_by(User.label == label).first()
            if user is None:
                return f"User with label {label} is not in the database!"
            return userlogin(user)
        except KeyError:
            try:
                username = request.form["username"]
                password = request.form["password"]
                user = db.session.query(User).get(username)
                if bcrypt.check_password_hash(user.password, password):
                    return userlogin(user)
                return f"Wrong password for {user}!"
            except KeyError:
                return "Please enter all required details!", 400


@app.route("/organiser")
@login_required
def organiser():
    return app.send_static_file("organizer.html")


@app.route("/submit", methods=["POST"])
def submit():
    """Take data from the form, generate, display, and email QR code to user."""
    table = P5November2019

    if table is None:
        print(request.form["db"])
        return "Error occurred. Kindly contact someone from the team and we will have this resolved ASAP"

    event_name = request.form["event"]

    id = get_current_id(table)

    data = {}

    user = table(**data, id=id)

    img = generate_qr(user)
    img.save("qr.png")
    img_data = open("qr.png", "rb").read()
    encoded = base64.b64encode(img_data).decode()

    try:
        db.session.add(user)
        db.session.commit()
    except exc.IntegrityError as e:
        print(e)
        return """It appears there was an error while trying to enter your data into our database.<br/>Kindly contact someone from the team and we will have this resolved ASAP"""

    name = user.name
    from_email = environ["FROM_EMAIL"]
    to_email = [(user.email, name)]

    try:
        date = request.form["date"]
    except KeyError:
        date = datetime.now().strftime("%B,%Y")
    subject = "Registration for {} - {} - ID {}".format(event_name, date, id)
    message = """<img src='https://drive.google.com/uc?id=12VCUzNvU53f_mR7Hbumrc6N66rCQO5r-&export=download' style="width:30%;height:50%">
<hr>
{}, your registration is done!
<br/>
A QR code has been attached below!
<br/>
You're <b>required</b> to present this on the day of the event.""".format(
        name
    )
    if "email_content" in request.form and "email_content_fields" in request.form:
        d = {}
        for f in request.form["email_content_fields"].split(","):
            d[f] = request.form[f]

        message = request.form["email_content"].format(**d)
    try:
        message += "<br/>" + request.form["extra_message"]
    except KeyError:
        pass
    content = Content("text/html", message)
    mail = Mail(from_email, to_email, subject, html_content=content)
    mail.add_attachment(Attachment(encoded, "qr.png", "image/png"))

    try:
        response = SendGridAPIClient(SENDGRID_API_KEY).send(mail)
        print(response.status_code)
        print(response.body)
        print(response.headers)
    except Exception as e:
        print(e)

    return 'Please save this QR Code. It has also been emailed to you.<br><img src=\
            "data:image/png;base64, {}"/>'.format(
        encoded
    )


@app.route("/registrations")
@login_required
def registrations():
    return db.session.query().all()


def get_current_id(table: db.Model):
    """Function to return the latest ID based on the database entries. 1 if DB is empty."""
    try:
        id = db.session.query(table).order_by(desc(table.id)).first().id
    except Exception:
        id = 0
    return int(id) + 1


def generate_qr(user):
    """Function to generate and return a QR code based on the given data."""
    data = ""
    for k, v in user.__dict__.items():
        if k == "_sa_instance_state":
            continue
        data += f"{v}|"
    return qrcode.make(data[:-1])
