from flask import Flask, render_template, request, session, redirect, url_for, jsonify
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
# TODO: For a particular name in a particular room, use database to store their prompts and responses with the private uncensored/jailbroken LLM.
# TODO: DB Schema for chatbot-message table: name, session, prompt, response, date
# TODO: When a new user is created, automatically create a new row in the chatbot-message table with the name and session number 1.
# TODO: Create a session button for the chatbot section. When clicked, intialises a new row in the chatbot-message database table with the name and session number.


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
    room_code = db.Column(db.String, db.ForeignKey('rooms.code'), nullable=False)
    name = db.Column(db.String, nullable=False)
    message = db.Column(db.String, nullable=False)
    date = db.Column(db.DateTime, nullable=False)
    
class ChatbotMessages(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String, nullable=False)
    owner = db.Column(db.String, nullable=False)
    session = db.Column(db.Integer, nullable=False)
    # prompt = db.Column(db.String, nullable=False)
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

def generate_identicon(name):
    # Generate a seed from the name to make sure each name has a unique pattern and color
    seed = int(md5(name.encode('utf-8')).hexdigest(), 16)
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
profile_pictures["Chatbot"] = generate_identicon("Chatbot")

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
        initial_chat_session = ChatbotMessages(name="Chatbot", owner=name, session=1, message=f"Started new session: 1", date=datetime.now())
        db.session.add(initial_chat_session)
        db.session.commit()
        return redirect(url_for("room"))
    
    return render_template("home.html", existing_rooms=existing_rooms)

@app.route("/room")
def room():
    room = session.get("room")
    name = session.get("name")
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
    
    # Query for chatbot messages in default session
    chatbot_messages = ChatbotMessages.query.filter_by(owner=name, session=1).all()
    chatbot_messages_list = [
        {
            "name": msg.name,
            "session": msg.session,
            "owner": msg.owner,
            "message": msg.message,
            "date": msg.date.strftime("%Y-%m-%d %H:%M:%S")
        }
        for msg in chatbot_messages
    ]
    max_session = 1  # Initialize to 1 as default session
    for chatbot_msg in chatbot_messages_list:
        if chatbot_msg["session"] > max_session:
            max_session = chatbot_msg["session"]
    return render_template("room.html",code=room, messages=messages_list,
                        chatbot_messages=chatbot_messages_list, profile_pictures=profile_pictures, 
                        name=name,max_session=max_session)


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
    msg = Messages(room_code=room, name=content["name"], message=content["message"], date=content["date"])
    db.session.add(msg)
    db.session.commit()
    print(f"{session.get('name')} said: {data['data']} in room {room}")

# using the initialisation object for socketio
@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    
    # Exit if the session is missing room or name
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
    
    # Save connect message to room's messages history
    msg = Messages(room_code=room, name=content["name"], message=content["message"], date=content["date"])
    db.session.add(msg)
    db.session.commit()
    
    members_list = room_info.members.split(",") if room_info.members else []
    # Add name to members list if name doesn't there exist; prevents duplicate names in room upon refresh from same session
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
    
    # Save disconnect message to room's messages history
    msg = Messages(room_code=room, name=content["name"], message=content["message"], date=content["date"])
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


###### Chatbot Routes ########
# TODO
@app.route('/get_sessions', methods=['POST'])
def get_sessions():
    name = request.json.get('name')
    # Query the database to get the unique session numbers for the user
    sessions = db.session.query(ChatbotMessages.session).filter(ChatbotMessages.owner == name).distinct().all()
    sessions = [s[0] for s in sessions]  # Flatten the list
    result = jsonify({'sessions': sessions})
    return result

# TODO
@app.route('/get_session_history', methods=['POST'])
def get_session_history():
    data = request.json
    name = data.get('name')
    session = data.get('session')
    # Query the database to get the chatbot messages for this session and user
    # For example:
    messages = ChatbotMessages.query.filter_by(owner=name, session=session).all()
    messages_data = [{"name": m.name, "owner": m.owner, "message": m.message, "date": m.date.strftime("%Y-%m-%d %H:%M:%S"),"profile_picture": profile_pictures.get(m.name, "")} for m in messages]
    return jsonify({'messages': messages_data})

# TODO
@app.route('/create_new_session', methods=['POST'])
def create_new_session():
    data = request.json
    name = data.get('name')
    
    # Query the database to find the latest session for this name
    # For example:
    last_session = db.session.query(db.func.max(ChatbotMessages.session)).filter(ChatbotMessages.owner == name).scalar() or 0
    new_session = last_session + 1
    # Create a new row in the chatbot_messages table with this name and last_session + 1
    new_session = ChatbotMessages(name="Chatbot", owner=name, session=new_session, message=f"Started new session: {new_session}", date=datetime.now())
    db.session.add(new_session)
    db.session.commit()
    
    return jsonify({'success': True})

@socketio.on("chatbot_req")
def chatbot_message(data):
    sid = request.sid
    name = session.get("name")
    session_id = data["session"]
    message = data["message"]
    chatbot_msg = ChatbotMessages(name=name, owner=name, session=session_id, message=message, date=datetime.now())
    db.session.add(chatbot_msg)
    db.session.commit()
    emit("chatbot_ack", {"name":name, "session": session_id, "message": message,
                            "profile_picture": profile_pictures.get(name, ""),
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                             room=sid)

@socketio.on("chatbot_prompt")
def chatbot_message(data):
    sid = request.sid
    name = session.get("name")
    session_id = data["session"]
    prompt = data["message"]

    # For now, we will spoof the chatbot response after 5 seconds
    import time
    time.sleep(5)
    response = f"Hello, I am your chatbot. You said: {prompt}"  # Replace this with API call, respond using the prompt
    chatbot_msg = ChatbotMessages(name="Chatbot", owner=name, session=session_id, message=response, date=datetime.now())
    db.session.add(chatbot_msg)
    db.session.commit()
    emit("chatbot_response", {"name":"Chatbot", "session": session_id,
                            "message": response, "profile_picture": profile_pictures.get("Chatbot", ""),
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                            room=sid)


if __name__ == '__main__':
    with app.app_context():
        # print("creating db")
        db.create_all()
    socketio.run(app, debug=True)