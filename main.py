from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room, send
import random
from string import ascii_uppercase, ascii_letters, digits
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy_utils import database_exists, create_database
from PIL import Image, ImageDraw
import base64
from io import BytesIO
from hashlib import md5
import os
from dotenv import load_dotenv


# Load environment variables from the .env file
load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost/{DB_NAME}"

app = Flask(__name__)


engine = create_engine(DATABASE_URL)

# Create database if it does not exist
if not database_exists(engine.url):
    create_database(engine.url)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# TODO: Add randomly generated profile pictures for users
# TODO: Incorporate a separate, private client-side chat box that logs messages to the database
# TODO: Incoporate uncensored large-language model for the user to chat to in that separate chat box, for rumor-generation/detection purposes; preferably allow streaming of model responses.
# TODO: For a particular username in a particular room, use database to store their prompts and responses with the private uncensored/jailbroken LLM.
# TODO: DB Schema for chatbot-message table: username, session, prompt, response, date
# TODO: When a new user is created, automatically create a new row in the chatbot-message table with the username and session number 1.
# TODO: Create a session button for the chatbot section. When clicked, intialises a new row in the chatbot-message database table with the username and session number.


# initialisation object for database
db = SQLAlchemy(app)

# Database Schema
class Rooms(db.Model):
    code = db.Column(db.String, primary_key=True)
    members = db.Column(db.String) # Storing members as a comma-separated string

    # Relationship
    messages = db.relationship('Messages', backref='room_info', lazy=True)

class Messages(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    current_room_code = db.Column(db.String, db.ForeignKey('rooms.code'), nullable=True)
    original_room_code = db.Column(db.String, nullable=False)
    name = db.Column(db.String, nullable=False)
    message = db.Column(db.String, nullable=False)
    date = db.Column(db.DateTime, nullable=False)

# initialisation object for socketio library
socketio = SocketIO(app)

# rooms = {}
profile_pictures = {}


###### Utility Functions ########
def generate_unique_code(length):
    while True:
        code = ""
        for _ in range(length):
            code += random.choice(ascii_uppercase + digits)
        # return code if indeed unique   
        room_info = Rooms.query.filter_by(code=code).first()
        if room_info is None:
            return code

def generate_identicon(username):
    # Generate a seed from the username to make sure each username has a unique pattern and color
    seed = int(md5(username.encode('utf-8')).hexdigest(), 16)
    random.seed(seed)

    # Create a 16x16 image with a white background
    image = Image.new('RGB', (16, 16), color='white')
    d = ImageDraw.Draw(image)

    # Generate a unique but visible color based on the seed
    color = (random.randint(50, 200), random.randint(50, 200), random.randint(50, 200))

    # Generate a random pattern.
    # Only half of the image needs to be generated due to symmetry.
    for x in range(0, 8):
        for y in range(0, 16):
            if random.choice([True, False]):
                d.point((x, y), fill=color)
                d.point((15 - x, y), fill=color)  # Symmetric point

    buffered = BytesIO()
    image.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()

    return img_str
#################################
profile_pictures["Room"] = generate_identicon("Room")

# Routes

# Home Page
@app.route("/", methods=["GET", "POST"])
def home():
    # Clear session when user goes to home page
    # so that they can't
    # navigate directly to chat page without entering name and room code
    session.clear()
    # Query the database for all room codes
    existing_rooms = Rooms.query.with_entities(Rooms.code).all()
    # Convert query results to a list of strings
    existing_rooms = [room.code for room in existing_rooms]

    if request.method == "POST":
        # attempt to grab values from form; returns None if doesn't exist
        name = request.form.get("name")
        code = request.form.get("code")
        # False if doesn't exist
        join = request.form.get("join", False)
        create = request.form.get("create", False)
        
        # Check if name contains symbols that are not typically safe
        if not name or not all(char in ascii_letters + digits + " " for char in name):
            # code=code and name=name to preserve values in form on render_template-initiated reload.
            return render_template("home.html", error="Please enter a valid name (letters, numbers and space only)", code=code, name=name, existing_rooms=existing_rooms)
        
        if join != False and not code:
            return render_template("home.html", error="Please enter a room code", code=code, name=name, existing_rooms=existing_rooms)
    
        # room = code
        room_info = Rooms.query.filter_by(code=code).first()
        if room_info:
            members_list = room_info.members.split(",") if room_info.members else []
        if create != False:
            code  = generate_unique_code(6)
            # rooms[room] = {"members":[], "messages":[]}
            new_room = Rooms(code=code , members="")
            db.session.add(new_room)
            db.session.commit()
            
        # if not create, we assume they are trying to join a room
        
        
        # Refuse to join if room doesn't exist or name already exists in room
        elif room_info is None:
            return render_template("home.html", error="Room code does not exist", code=code, name=name, existing_rooms=existing_rooms)
        elif name in members_list:
            # If the name already exists in the room's members
            return render_template("home.html", error="Name already exists in the room", code=code, name=name, existing_rooms=existing_rooms)

        # Session is a semi-permanent way to store information about user
        # Temporary secure data stored in the server; expires after awhile
        # Stored persistently between requests
        session["room"] = code
        session["name"] = name
        return redirect(url_for("room"))
    
    return render_template("home.html", existing_rooms=existing_rooms)

@app.route("/room")
def room():
    room = session.get("room")
    # Ensure user can only go to /room route if they either generated a new room
    # or joined an existing room from the home page
    room_info = Rooms.query.filter_by(code=room).first()
    if room is None or session.get("name") is None or room_info is None:
        return redirect(url_for("home"))
    # Extracting messages on room info from database
    messages_list = [
        {
            "name": message.name,
            "message": message.message,
            "date": message.date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for message in room_info.messages
    ]
    return render_template("room.html",code=room, messages=messages_list, profile_pictures=profile_pictures)

@socketio.on("message")
def message(data):
    room = session.get("room")
    room_info = Rooms.query.filter_by(code=room).first()
    if room_info is None:
        return
    
    content = {
        "name":session.get("name"),
        "message":data["data"],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get(session.get("name"), "")
    }
    
    # On receiving data from a client, send it to all clients in the room
    send(content, to=room)
    
    # Save message to room's messages history
    msg = Messages(current_room_code=room, original_room_code=room, name=content["name"], message=content["message"], date=content["date"])
    db.session.add(msg)
    db.session.commit()
    print(f"{session.get('name')} said: {data['data']} in room {room}")

# using the initialisation object for socketio
@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    
    if not room or not name:
        return
    
    room_info = Rooms.query.filter_by(code=room).first()
    if room_info is None:
        # Leave room as it shouldn't exist
        leave_room(room)
        return

    if name not in profile_pictures:
        profile_pictures[name] = generate_identicon(name)
        
    join_room(room)
    content = {
        "name":"Room",
        "message":f"{name} has joined the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get("Room", "")
    }
    send(content, to=room)
    
    # Save message to room's messages history
    # rooms[room]["messages"].append(content)
    msg = Messages(current_room_code=room, original_room_code=room, name=content["name"], message=content["message"], date=content["date"])
    db.session.add(msg)
    db.session.commit()
    
    members_list = room_info.members.split(",") if room_info.members else []
    # Add if name doesn't exist; prevents duplicate names in room upon refresh from same session
    if name not in members_list:
        members_list.append(name)
    room_info.members = ",".join(members_list)
    db.session.commit()
    emit("memberChange", members_list, to=room)
    print(f"{name} has joined room {room}. Current Members: {members_list}")
    

@socketio.on("disconnect")
def disconnect():
    room = session.get("room")
    name = session.get("name")
    leave_room(room)
    print(f"{name} has left room {room}") 
    room_info = Rooms.query.filter_by(code=room).first()
    
    content = {
        "name":"Room",
        "message":f"{name} has left the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get("Room", "")
    }
    
    # Save message to room's messages history
    msg = Messages(current_room_code=room, original_room_code=room, name=content["name"], message=content["message"], date=content["date"])
    db.session.add(msg)
    db.session.commit()
    
    
    members_list = []
    if room_info:
        members_list = room_info.members.split(",") if room_info.members else []
        if name in members_list:
            members_list.remove(name)
        room_info.members = ",".join(members_list)
        db.session.commit()
        
        # Delete room if no members left (REMOVED FOR NOW; LET'S PERSIST ROOMS)
        # if not members_list:
        #     db.session.delete(room_info)
        #     db.session.commit()
        #     return
            
    send(content, to=room)
    emit("memberChange", members_list, to=room) 
     

if __name__ == '__main__':
    with app.app_context():
        # print("creating db")
        db.create_all()
    socketio.run(app, debug=True)