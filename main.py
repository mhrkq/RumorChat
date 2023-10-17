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
import eventlet
import requests
eventlet.monkey_patch()


# Load environment variables from the .env file
load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost/{DB_NAME}"

CHATBOT_HOST = 'localhost:5000'
CHATBOT_URI = f'http://{CHATBOT_HOST}/api/v1/chat'

# k is the number of messages to retrieve on each new session
k = 5

app = Flask(__name__)


engine = create_engine(DATABASE_URL)

# Create database if it does not exist
if not database_exists(engine.url):
    create_database(engine.url)

app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

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
    message = db.Column(db.String, nullable=False)
    date = db.Column(db.DateTime, nullable=False)

# initialisation object for socketio library
socketio = SocketIO(app)

# Dictionary to cache profile pictures for each user. Key: Name, Value: Base64-encoded image
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
        
        # If trying to join a room, but no room code is entered
        if join != False and not code:
            return render_template("home.html", error="Please enter a room code", code=code, name=name, existing_rooms=existing_rooms)
    
        # Check for room info in database
        room_info = Rooms.query.filter_by(code=code).first()
        if room_info:
            # Create a list of members in the room if room info present
            members_list = room_info.members.split(",") if room_info.members else []
        # If attempting to create a new room
        if create != False:
            code  = generate_unique_code(6)
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
        
        # Check if there's already a preexisting initial_chat_session for this user
        existing_session = ChatbotMessages.query.filter_by(owner=name, session=1).first()

        if not existing_session:
            # Only create and add the initial_chat_session if it doesn't already exist
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
    
    # Check and generate identicon for each name in messages_list
    for message in messages_list:
        msg_name = message["name"]
        if msg_name not in profile_pictures:
            profile_pictures[msg_name] = generate_identicon(msg_name)
    
    # Query for chatbot messages in default session (session 1)
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

# Message event occurs when user sends a message
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

# Connect occurs when user enters the room; no authentication required
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
    # Inform clients that the member list has changed
    emit("memberChange", members_list, to=room)
    print(f"{name} has joined room {room}. Current Members: {members_list}")
    
# Disconnect occurs when user closes the tab or refreshes the page
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
    # Remove name from members list of the room (in the database) on disconnect
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
    # Inform clients that the member list has changed
    emit("memberChange", members_list, to=room) 


###### Chatbot Routes ########
# For use with AJAX (Asynchronous Javascript and XML) requests
# Query the database to get the unique session numbers for the user
@app.route('/get_sessions', methods=['POST'])
def get_sessions():
    name = request.json.get('name')
    # Query the database to get the unique session numbers for the user
    sessions = db.session.query(ChatbotMessages.session).filter(ChatbotMessages.owner == name).distinct().all()
    sessions = [s[0] for s in sessions]  # Flatten the list
    result = jsonify({'sessions': sessions})
    return result

# For use with AJAX requests
# Query the database to get the chatbot messages for this session and user
@app.route('/get_session_history', methods=['POST'])
def get_session_history():
    data = request.json
    name = data.get('name')
    session = data.get('session')
    # Query the database to get the chatbot messages for this session and user
    messages = ChatbotMessages.query.filter_by(owner=name, session=session).all()
    messages_data = [{"name": m.name, "owner": m.owner, "message": m.message, "date": m.date.strftime("%Y-%m-%d %H:%M:%S"),"profile_picture": profile_pictures.get(m.name, "")} for m in messages]
    return jsonify({'messages': messages_data})

# For use with AJAX requests
# Self-explanatory; creates a new session for the user
@app.route('/create_new_session', methods=['POST'])
def create_new_session():
    data = request.json
    name = data.get('name')
    # Query the database to find the latest session for this name
    last_session = db.session.query(db.func.max(ChatbotMessages.session)).filter(ChatbotMessages.owner == name).scalar() or 0
    new_session = last_session + 1
    # Create a new row in the chatbot_messages table with this name and last_session + 1
    new_session = ChatbotMessages(name="Chatbot", owner=name, session=new_session, message=f"Started new session: {new_session}", date=datetime.now())
    db.session.add(new_session)
    db.session.commit()
    
    return jsonify({'success': True})

# For use with AJAX requests
# Occurs when user sends a message (acts as a request) to the chatbot; acknowledges with the same message
@socketio.on("chatbot_req")
def chatbot_message(data):
    sid = request.sid
    name = session.get("name")
    session_id = data["session"]
    message = data["message"]
    room = session.get("room")
    chatbot_history = retrieve_chatbot_history(name, session_id)
    if not chatbot_history:
        print("chatbot history is empty for chatbot_req")
        # Retrieving the last k messages; for example, let's take k as 5
        last_k_msgs = retrieve_last_k_msg(k, room)
        full_prompt = f"Context: Here are the last {k} messages from various users in the public chatroom. (Note that my username is '{name}'): \n"
        # Prepending the last k messages to the prompt with the desired format
        prepended_msg = '\n'.join([f"{msg['name']}: {msg['message']}" for msg in last_k_msgs])
        full_prompt += prepended_msg + "\n"
        full_prompt += f"Given the above context, follow these instructions: {message}"
        message = full_prompt
    
    
    chatbot_msg = ChatbotMessages(name=name, owner=name, session=session_id, message=message, date=datetime.now())
    db.session.add(chatbot_msg)
    db.session.commit()
    print("succesfully commited on chatbot_req")
    emit("chatbot_ack", {"name":name, "session": session_id, "message": message,
                        "profile_picture": profile_pictures.get(name, ""),
                        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                        room=sid)

# Function to retrieve the last k messages
def retrieve_last_k_msg(k, room_code):
    # Querying for the messages excluding those sent by "Room"
    print(f"retriving last k messages for {room_code}")
    last_k_messages = Messages.query.filter_by(room_code=room_code).filter(Messages.name != "Room").order_by(Messages.date.desc()).limit(k).all()
    print(f"done retrieving for  {room_code}")
    # Constructing the list of dictionaries
    messages_list = [{
        "name": msg.name,
        "message": msg.message,
        "date": msg.date.strftime("%Y-%m-%d %H:%M:%S")
    } for msg in reversed(last_k_messages)] # reversing to get the oldest message first
    
    return messages_list

def retrieve_chatbot_history(owner, session_id):
    # Querying for the messages from the Chatbot for a given session id
    chatbot_messages = ChatbotMessages.query.filter_by(owner=owner, session=session_id).order_by(ChatbotMessages.date.asc()).all()
    
    # If there's only one message in that session, return None
    if len(chatbot_messages) <= 1:
        return None

    # Excluding the very first message in that session
    chatbot_messages_list = [{
        "name": msg.name,
        "message": msg.message,
        "date": msg.date.strftime("%Y-%m-%d %H:%M:%S")
    } for msg in chatbot_messages[1:]]  # starts from the third message
    
    return chatbot_messages_list

def form_message_pairs(chatbot_history):
    if not chatbot_history:
        return []
    
    history_visible = []
    user_message = None
    
    for msg in chatbot_history:
        if msg['name'] != "Chatbot" and user_message is None:  # Capturing the user message
            user_message = msg['message']
        elif msg['name'] == "Chatbot" and user_message:  # Pairing it with the chatbot response
            history_visible.append([user_message, msg['message']])
            user_message = None  # Resetting for the next user message
    
    return history_visible

# Function to simulate the delay for the chatbot response
def background_task(name, sid, session_id, room_code, prompt):
    with app.app_context():
        # k is the number of messages to retrieve
        
        full_prompt = ""
        history = {'internal': [], 'visible': []}    
        chatbot_history = retrieve_chatbot_history(name, session_id)
        
        # TODO: Copy the retrieval of the chatbot history to the chatbot_req event handler as well to inform the user of what
        # information the chatbot is consuming for that (newly created) session.
        # Subsequent messages after the first (in a particular session) will retrieve the entire chatbot session message history from the database.
        if not chatbot_history:
            print("No chabot history found")
            # Retrieving the last k messages; for example, let's take k as 5
            last_k_msgs = retrieve_last_k_msg(k, room_code)
            full_prompt += f"Context: Here are the last {len(last_k_msgs)} messages from various users in the public chatroom. (Note that my username is '{name}'): \n"
            # Prepending the last k messages to the prompt with the desired format
            prepended_msg = '\n'.join([f"{msg['name']}: {msg['message']}" for msg in last_k_msgs])
            full_prompt += prepended_msg + "\n"
            full_prompt += f"Given the above context, follow these instructions: {prompt}"
            # full_prompt += f"Here is the user's ({name}'s) latest prompt: {prompt}"

        else:
            # chatbot_history_msg = '\n'.join([f"{msg['name']}: {msg['message']}" for msg in chatbot_history])
            history["internal"]= form_message_pairs(chatbot_history)
            # prepended_msg = chatbot_history_msg + "\n" + prepended_msg
            # full_prompt += chatbot_history_msg + "\n"
            # full_prompt +=  f"Here is the user's ({name}'s) latest prompt: {prompt}"
            full_prompt = prompt
        
        # For now, we will spoof the chatbot response after 5 seconds
        ############################
        # TODO: Replace this with API call, respond using the prompt
        ############################
        # import time
        # time.sleep(5)
        # response = f"Hello, I am your chatbot. Here is your full prompt: \n {full_prompt}"  # Replace this with API call, respond using the prompt
        print(f"sending request to chatbot api: {full_prompt}")
        request_data = {
        'user_input': full_prompt,
        'max_new_tokens': 500,
        'auto_max_new_tokens': False,
        'max_tokens_second': 0,
        'history': history,
        'mode': 'instruct',  # Valid options: 'chat', 'chat-instruct', 'instruct'
        'character': 'Example',
        'instruction_template': 'Vicuna-v1.1',  # Will get autodetected if unset
        'your_name': 'You',
        # 'name1': 'name of user', # Optional
        # 'name2': 'name of character', # Optional
        # 'context': 'character context', # Optional
        # 'greeting': 'greeting', # Optional
        # 'name1_instruct': 'You', # Optional
        # 'name2_instruct': 'Assistant', # Optional
        # 'context_instruct': 'context_instruct', # Optional
        # 'turn_template': 'turn_template', # Optional
        'regenerate': False,
        '_continue': False,
        'chat_instruct_command': 'Continue the chat dialogue below. Write a single reply for the character "<|character|>".\n\n<|prompt|>',

        # Generation params. If 'preset' is set to different than 'None', the values
        # in presets/preset-name.yaml are used instead of the individual numbers.
        'preset': 'None',
        'do_sample': True,
        'temperature': 0.7,
        'top_p': 0.1,
        'typical_p': 1,
        'epsilon_cutoff': 0,  # In units of 1e-4
        'eta_cutoff': 0,  # In units of 1e-4
        'tfs': 1,
        'top_a': 0,
        'repetition_penalty': 1.18,
        'repetition_penalty_range': 0,
        'top_k': 40,
        'min_length': 0,
        'no_repeat_ngram_size': 0,
        'num_beams': 1,
        'penalty_alpha': 0,
        'length_penalty': 1,
        'early_stopping': False,
        'mirostat_mode': 0,
        'mirostat_tau': 5,
        'mirostat_eta': 0.1,
        'grammar_string': '',
        'guidance_scale': 1,
        'negative_prompt': '',

        'seed': -1,
        'add_bos_token': True,
        'truncation_length': 2048,
        'ban_eos_token': False,
        'custom_token_bans': '',
        'skip_special_tokens': True,
        'stopping_strings': []
        }
        
        try:
            response = requests.post(CHATBOT_URI, json=request_data)
            
            # Check if the response is successful and extract the chatbot's reply
            if response.status_code == 200:
                results = response.json()['results']
                chatbot_reply = results[0]['history']['visible'][-1][1]
            else:
                chatbot_reply = f"Sorry, I couldn't process your request (likely due to an API connection error). You said: {prompt}"
        except Exception as e:
            print("Exception occured: ", e)
            chatbot_reply = f"Sorry, I couldn't process your request (likely due to an API connection error). You said: {prompt}"
        
        ############################
        response = chatbot_reply
        chatbot_msg = ChatbotMessages(name="Chatbot", owner=name, session=session_id, message=response, date=datetime.now())
        db.session.add(chatbot_msg)
        db.session.commit()

        socketio.emit("chatbot_response", {"name":"Chatbot", "session": session_id,
                                "message": response, "profile_picture": profile_pictures.get("Chatbot", ""),
                                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                                room=sid)



# Also occurs when user sends a message (acts as a request) to the chatbot; responds with a message from an LLM model
@socketio.on("chatbot_prompt")
def chatbot_message(data):
    sid = request.sid
    name = session.get("name")
    session_id = data["session"]
    prompt = data["message"]
    room = session.get("room")

    # Run the background task without blocking
    socketio.start_background_task(background_task, name, sid, session_id, room, prompt)


if __name__ == '__main__':
    with app.app_context():
        # Create all tables in the database if they don't exist
        db.create_all()
    # socketio.run(app, debug=True)
    eventlet.wsgi.server(eventlet.listen(('127.0.0.1', 8080)), app, debug=True)