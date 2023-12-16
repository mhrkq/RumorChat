import eventlet

eventlet.monkey_patch()

import logging

from flask_cors import CORS

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
import requests
import argparse
from time import time
from collections import defaultdict
from datetime import datetime, timedelta
from threading import Lock


def parse_arguments():
    parser = argparse.ArgumentParser(description="Run Flask App")
    parser.add_argument(
        "--logging", action="store_true", help="Enable logging print statements"
    )
    # Parse known arguments and ignore unknown
    args, _ = parser.parse_known_args()
    return args


args = parse_arguments()
LOGGING = args.logging

# Set up the logging
logging.basicConfig(level=logging.DEBUG)

# Adjust the logging level for Flask-SocketIO
engineio_logger = logging.getLogger("engineio.server")
engineio_logger.setLevel(logging.DEBUG)
socketio_logger = logging.getLogger("socketio.server")
socketio_logger.setLevel(logging.DEBUG)


# Load environment variables from the .env file
load_dotenv()

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost/{DB_NAME}"

CHATBOT_HOST = "172.17.0.1:6000"
CHATBOT_URI = f"http://{CHATBOT_HOST}/api/v1/chat"

# k is the number of messages to retrieve on each new session
k = 5

# Global counter for chatbot requests in progress and its lock
chatbot_requests_in_progress = 0
chatbot_lock = Lock()

app = Flask(__name__)
CORS(app)

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
    members = db.Column(db.String)  # Storing members as a comma-separated string
    # Relationship
    messages = db.relationship("Messages", backref="room_info", lazy=True)


class Messages(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String, db.ForeignKey("rooms.code"), nullable=False)
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

class Comments(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    room_code = db.Column(db.String, db.ForeignKey("rooms.code"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('comments.id'), nullable=True)  # For hierarchical structure
    username = db.Column(db.String, nullable=False)
    text = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    votes = db.Column(db.Integer, default=0)
    # Hierarchical relationship to enable tree-like structure of comments
    replies = db.relationship('Comments', backref=db.backref('parent', remote_side=[id]), lazy='dynamic')
    
class CommentVotes(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    comment_id = db.Column(db.Integer, db.ForeignKey('comments.id'), nullable=False)
    username = db.Column(db.String, nullable=False)
    vote = db.Column(db.Integer, nullable=False)  # 1 for upvote, -1 for downvote

    def __repr__(self):
        return f'<CommentVotes {self.username} {self.vote}>'


# initialisation object for socketio library
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

# Dictionary to cache profile pictures for each user. Key: Name, Value: Base64-encoded image
profile_pictures = {}

# To track the last heartbeat from each member in each room
last_heartbeat = defaultdict(dict)


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
    seed = int(md5(name.encode("utf-8")).hexdigest(), 16)
    random.seed(seed)

    # Create a 16x16 image with a white background
    image = Image.new("RGB", (16, 16), color="white")
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
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for home()")
    # Clear session when user goes to home page
    # so that they can't navigate directly to chat page without entering name and room code
    session.clear()
    # Query the database for all room codes
    existing_rooms = Rooms.query.with_entities(Rooms.code).all()
    # Convert query results to a list of strings
    existing_rooms = [room.code for room in existing_rooms]

    if LOGGING:
        print(
            f"Time taken to query existing rooms in home(): {time() - start_time} seconds"
        )
        start_time = time()

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
            return render_template(
                "home.html",
                error="Please enter a valid name (letters, numbers and space only)",
                code=code,
                name=name,
                existing_rooms=existing_rooms,
            )

        # If trying to join a room, but no room code is entered
        if join != False and not code:
            return render_template(
                "home.html",
                error="Please enter a room code",
                code=code,
                name=name,
                existing_rooms=existing_rooms,
            )

        # Check for room info in database
        room_info = Rooms.query.filter_by(code=code).first()
        if LOGGING:
            print(
                f"Time taken to query room info in home(): {time() - start_time} seconds"
            )
            start_time = time()
        if room_info:
            # Create a list of members in the room if room info present
            members_list = room_info.members.split(",") if room_info.members else []
        # If attempting to create a new room
        if create != False:
            code = generate_unique_code(6)
            new_room = Rooms(code=code, members="")
            db.session.add(new_room)
            db.session.commit()
            if LOGGING:
                print(
                    f"Time taken to commit new room in home(): {time() - start_time} seconds"
                )
                start_time = time()

        # if not create, we assume they are trying to join a room

        # Refuse to join if room doesn't exist or name already exists in room
        elif room_info is None:
            return render_template(
                "home.html",
                error="Room code does not exist",
                code=code,
                name=name,
                existing_rooms=existing_rooms,
            )
        elif name in members_list:
            # If the name already exists in the room's members
            return render_template(
                "home.html",
                error="Name already exists in the room",
                code=code,
                name=name,
                existing_rooms=existing_rooms,
            )

        # Session is a semi-permanent way to store information about user
        # Temporary secure data stored in the server; expires after awhile
        # Stored persistently between requests
        session["room"] = code
        session["name"] = name

        # Check if there's already a preexisting initial_chat_session for this user
        existing_session = ChatbotMessages.query.filter_by(
            owner=name, session=1
        ).first()
        if LOGGING:
            print(
                f"Time taken to query existing session in home(): {time() - start_time} seconds"
            )
            start_time = time()

        if not existing_session:
            # Only create and add the initial_chat_session if it doesn't already exist
            initial_chat_session = ChatbotMessages(
                name="Chatbot",
                owner=name,
                session=1,
                message=f"Started new session: 1",
                date=datetime.now(),
            )
            db.session.add(initial_chat_session)
            db.session.commit()
            if LOGGING:
                print(
                    f"Time taken to commit initial chat session in home(): {time() - start_time} seconds"
                )
                start_time = time()
        return redirect(url_for("room"))
    if LOGGING:
        print(
            f"Total time taken to complete home() function: {time() - start_time} seconds"
        )
    return render_template("home.html", existing_rooms=existing_rooms)


@app.route("/room")
def room():
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for room()")
    room = session.get("room")
    name = session.get("name")
    # Ensure user can only go to /room route if they either generated a new room
    # or joined an existing room from the home page
    room_info = Rooms.query.filter_by(code=room).first()
    if LOGGING:
        print(f"Time taken to query room_info in room(): {time() - start_time} seconds")
        start_time = time()
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
    if LOGGING:
        print(
            f"Time taken to generate_identicon in room(): {time() - start_time} seconds"
        )
        start_time = time()
    
    # Query for comments in the current room
    # comments = Comments.query.filter_by(room_code=room).order_by(Comments.timestamp).all()
    # comments_data = []
    # for comment in comments:
    #     user_vote_obj = CommentVotes.query.filter_by(comment_id=comment.id, username=name).first()
    #     user_vote = 0  # Default to no vote
    #     if user_vote_obj:
    #         user_vote = user_vote_obj.vote
    #     comments_data.append({
    #         "id": comment.id,
    #         "text": comment.text,
    #         "username": comment.username,
    #         "timestamp": comment.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
    #         "votes": comment.votes,
    #         "userVote": user_vote,
    #         "profile_picture": profile_pictures.get(comment.username, "")
    #     })
    
    # Recursive Query for comments in the current room
    comments_data = fetch_comments_with_replies(session.get("room"), comment_id=None)
        
    

    # Query for chatbot messages in default session (session 1)
    chatbot_messages = ChatbotMessages.query.filter_by(owner=name, session=1).all()
    chatbot_messages_list = [
        {
            "name": msg.name,
            "session": msg.session,
            "owner": msg.owner,
            "message": msg.message,
            "date": msg.date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for msg in chatbot_messages
    ]

    if LOGGING:
        print(
            f"Time taken to query and filter ChatbotMessages in room(): {time() - start_time} seconds"
        )
        start_time = time()

    max_session = 1  # Initialize to 1 as default session
    for chatbot_msg in chatbot_messages_list:
        if chatbot_msg["session"] > max_session:
            max_session = chatbot_msg["session"]

    # remove_inactive_members_from_db(room)
    if LOGGING:
        print(f"Time taken to finish room(): {time() - start_time} seconds")
    print(f"comments_data sent to client: {comments_data}")
    return render_template(
        "room.html",
        code=room,
        messages=messages_list,
        chatbot_messages=chatbot_messages_list,
        comments=comments_data, 
        profile_pictures=profile_pictures,
        name=name,
        max_session=max_session,
    )


# Message event occurs when user sends a message
@socketio.on("message")
def message(data):
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for message()")
    room = session.get("room")
    room_info = Rooms.query.filter_by(code=room).first()
    if LOGGING:
        print(
            f"Time taken to query room info in message(): {time() - start_time} seconds"
        )
        start_time = time()
    if room_info is None:
        return

    content = {
        "name": session.get("name"),
        "message": data["data"],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get(session.get("name"), ""),
    }

    # On receiving data from a client, send it to all clients in the room
    send(content, to=room)
    if LOGGING:
        print(
            f"Time taken to send data to all clients message(): {time() - start_time} seconds"
        )
        start_time = time()

    # Save message to room's messages history
    msg = Messages(
        room_code=room,
        name=content["name"],
        message=content["message"],
        date=content["date"],
    )
    db.session.add(msg)
    db.session.commit()
    if LOGGING:
        print(
            f"Time taken to commit message to history in message(): {time() - start_time} seconds"
        )
        start_time = time()
    print(f"{session.get('name')} said: {data['data']} in room {room}")
    
################# ADDING COMMENTS #################
@socketio.on("submit_comment")
def handle_comment(data):
    if LOGGING:
        start_time = time()
        print(f"Time started for handle_comment()")

    room = session.get("room")
    username = session.get("name")
    text = data["text"]
    parent_id = data.get("parent_id")  # None for root comments

    if not room or not username:
        print(f"Room or username not found in session. Room: {room}, Username: {username}")
        return
    
    if parent_id:
        parent_comment = Comments.query.get(parent_id)
        if not parent_comment:
            print(f"Parent comment does not exist. Parent ID: {parent_id}")
            return  # Parent comment does not exist

    comment = Comments(room_code=room, username=username, text=text, parent_id=parent_id)
    db.session.add(comment)
    db.session.commit()
    
    # Query the vote count for the newly added comment
    vote_count = CommentVotes.query.with_entities(db.func.sum(CommentVotes.vote)).filter_by(comment_id=comment.id).scalar() or 0

    if LOGGING:
        print(f"Time taken to add and commit new comment: {time() - start_time} seconds")
        start_time = time()

    # After committing the new comment to the database
    emit("new_comment", {
        "id": comment.id,
        "text": text,
        "username": username,
        "timestamp": comment.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "votes": vote_count,  # Actual votes count
        "profile_picture": profile_pictures.get(username, ""),
        "parent_id": parent_id
    }, room=room)

    if LOGGING:
        print(f"Time taken to emit new_comment event: {time() - start_time} seconds")
        
@socketio.on("vote_comment")
def handle_vote(data):
    comment_id = data["comment_id"]
    vote = data["vote"]  # Assume 1 for upvote, -1 for downvote
    username = session.get("name")

    existing_vote = CommentVotes.query.filter_by(comment_id=comment_id, username=username).first()

    if existing_vote:
        if existing_vote.vote == vote:
            # User clicked the same vote again, rescind the vote
            db.session.delete(existing_vote)
            vote = 0  # No vote from this user
        else:
            # Change the vote direction
            existing_vote.vote = vote
    else:
        # New vote
        new_vote = CommentVotes(comment_id=comment_id, username=username, vote=vote)
        db.session.add(new_vote)

    # Calculate the updated vote count
    updated_votes = CommentVotes.query.with_entities(db.func.sum(CommentVotes.vote)).filter_by(comment_id=comment_id).scalar() or 0

    # Update the vote count in Comments table
    comment = Comments.query.get(comment_id)
    if comment:
        comment.votes = updated_votes
        db.session.commit()

    # Determine the user's current vote status
    user_vote = 1 if vote == 1 else -1 if vote == -1 else 0

    emit("update_vote", {"comment_id": comment_id, "votes": updated_votes, "userVote": user_vote}, room=session.get("room"))

# HANDLING REPLIES
def fetch_comments_with_replies(room_code, comment_id=None):
    comments = Comments.query.filter_by(parent_id=comment_id, room_code=room_code).order_by(Comments.timestamp).all()
    comments_data = []
    for comment in comments:
        replies = fetch_comments_with_replies(room_code, comment_id=comment.id)
        user_vote_obj = CommentVotes.query.filter_by(comment_id=comment.id, username=session.get("name")).first()
        user_vote = user_vote_obj.vote if user_vote_obj else 0
        comments_data.append({
            "id": comment.id,
            "text": comment.text,
            "username": comment.username,
            "timestamp": comment.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "votes": comment.votes,
            "userVote": user_vote,
            "profile_picture": profile_pictures.get(comment.username, ""),
            "replies": replies
        })
    return comments_data


##################################################


# Connect occurs when user enters the room; no authentication required
@socketio.on("connect")
def connect(auth):
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for connect()")
    room = session.get("room")
    name = session.get("name")

    # Exit if the session is missing room or name
    if not room or not name:
        return

    room_info = Rooms.query.filter_by(code=room).first()
    if LOGGING:
        print(
            f"Time taken to query existing rooms in connect(): {time() - start_time} seconds"
        )
        start_time = time()
    if room_info is None:
        # Leave room as it shouldn't exist
        leave_room(room)
        return

    if name not in profile_pictures:
        profile_pictures[name] = generate_identicon(name)

    join_room(room)
    content = {
        "name": "Room",
        "message": f"{name} has joined the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get("Room", ""),
    }
    send(content, to=room)
    if LOGGING:
        print(
            f"Time taken to generate_identicon, join_room and send content in connect(): {time() - start_time} seconds"
        )
        start_time = time()

    # Save connect message to room's messages history
    msg = Messages(
        room_code=room,
        name=content["name"],
        message=content["message"],
        date=content["date"],
    )
    db.session.add(msg)
    db.session.commit()
    if LOGGING:
        print(
            f"Time taken to commit messages to history in connect(): {time() - start_time} seconds"
        )
        start_time = time()

    members_list = room_info.members.split(",") if room_info.members else []
    # Add name to members list if name doesn't there exist; prevents duplicate names in room upon refresh from same session
    if name not in members_list:
        members_list.append(name)
    room_info.members = ",".join(members_list)
    if LOGGING:
        print(
            f"Time taken to add members to room_info in connect(): {time() - start_time} seconds"
        )
        start_time = time()

    db.session.commit()

    if LOGGING:
        print(
            f"Time taken to commit added members to room_info in connect(): {time() - start_time} seconds"
        )
        start_time = time()

    # Inform clients that the member list has changed
    emit("memberChange", members_list, to=room)

    if LOGGING:
        print(
            f"Time taken to commit added members to room_info in connect(): {time() - start_time} seconds"
        )
    print(f"{name} has joined room {room}. Current Members: {members_list}")


# Disconnect occurs when user closes the tab or refreshes the page
@socketio.on("disconnect")
def disconnect():
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for disconnect()")
    room = session.get("room")
    name = session.get("name")
    leave_room(room)
    print(f"{name} has left room {room}")
    room_info = Rooms.query.filter_by(code=room).first()
    if LOGGING:
        print(
            f"Time taken to leave room and query + filter room_info in disconnect(): {time() - start_time} seconds"
        )
        start_time = time()

    content = {
        "name": "Room",
        "message": f"{name} has left the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "profile_picture": profile_pictures.get("Room", ""),
    }

    # Save disconnect message to room's messages history
    msg = Messages(
        room_code=room,
        name=content["name"],
        message=content["message"],
        date=content["date"],
    )
    db.session.add(msg)
    db.session.commit()
    if LOGGING:
        print(
            f"Time taken to save and commit disconnect message in disconnect(): {time() - start_time} seconds"
        )
        start_time = time()

    members_list = []
    # Remove name from members list of the room (in the database) on disconnect
    if room_info:
        members_list = room_info.members.split(",") if room_info.members else []
        if name in members_list:
            members_list.remove(name)
        room_info.members = ",".join(members_list)
        db.session.commit()
        if LOGGING:
            print(
                f"Time taken to remove members from members list and commit change in disconnect(): {time() - start_time} seconds"
            )
            start_time = time()

    send(content, to=room)
    # Inform clients that the member list has changed
    emit("memberChange", members_list, to=room)
    if LOGGING:
        print(
            f"Time taken to send data, emit new members list and conclude disconnect(): {time() - start_time} seconds"
        )


###### Chatbot Routes ########
# For use with AJAX (Asynchronous Javascript and XML) requests
# Query the database to get the unique session numbers for the user
@app.route("/get_sessions", methods=["POST"])
def get_sessions():
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for get_sessions()")
    name = request.json.get("name")
    # Query the database to get the unique session numbers for the user
    sessions = (
        db.session.query(ChatbotMessages.session)
        .filter(ChatbotMessages.owner == name)
        .distinct()
        .all()
    )
    if LOGGING:
        print(
            f"Time taken to query sessions in get_sessions(): {time() - start_time} seconds"
        )
        start_time = time()
    sessions = [s[0] for s in sessions]  # Flatten the list
    result = jsonify({"sessions": sessions})
    if LOGGING:
        print(f"Time taken to conclude get_sessions(): {time() - start_time} seconds")
    return result


# For use with AJAX requests
# Query the database to get the chatbot messages for this session and user
@app.route("/get_session_history", methods=["POST"])
def get_session_history():
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for get_session_history()")
    data = request.json
    name = data.get("name")
    session = data.get("session")
    # Query the database to get the chatbot messages for this session and user
    messages = ChatbotMessages.query.filter_by(owner=name, session=session).all()
    if LOGGING:
        print(
            f"Time taken to query ChatbotMessages in get_session_history(): {time() - start_time} seconds"
        )
        start_time = time()
    messages_data = [
        {
            "name": m.name,
            "owner": m.owner,
            "message": m.message,
            "date": m.date.strftime("%Y-%m-%d %H:%M:%S"),
            "profile_picture": profile_pictures.get(m.name, ""),
        }
        for m in messages
    ]
    messages = ChatbotMessages.query.filter_by(owner=name, session=session).all()
    if LOGGING:
        print(
            f"Time taken to conclude get_session_history(): {time() - start_time} seconds"
        )
    return jsonify({"messages": messages_data})


# For use with AJAX requests
# Self-explanatory; creates a new session for the user
@app.route("/create_new_session", methods=["POST"])
def create_new_session():
    if LOGGING:
        start_time = time()  # Start time of request
        print(f"Time started for create_new_session()")
    data = request.json
    name = data.get("name")
    # Query the database to find the latest session for this name
    last_session = (
        db.session.query(db.func.max(ChatbotMessages.session))
        .filter(ChatbotMessages.owner == name)
        .scalar()
        or 0
    )
    new_session = last_session + 1
    if LOGGING:
        print(
            f"Time taken to query the database to find the latest session for the name: {name} in create_new_session(): {time() - start_time} seconds"
        )
    # Create a new row in the chatbot_messages table with this name and last_session + 1
    new_session = ChatbotMessages(
        name="Chatbot",
        owner=name,
        session=new_session,
        message=f"Started new session: {new_session}",
        date=datetime.now(),
    )
    db.session.add(new_session)
    db.session.commit()
    if LOGGING:
        print(
            f"Time taken to add, commit new session with ChatbotMessages and conclude create_new_session(): {time() - start_time} seconds"
        )

    return jsonify({"success": True})


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
        full_prompt = f"Context: Here are the last {k} messages from various users in the public chatroom. (Note that my username is {name}): \n"
        # Prepending the last k messages to the prompt with the desired format
        prepended_msg = "\n".join(
            [f"{msg['name']}: {msg['message']}" for msg in last_k_msgs]
        )
        full_prompt += prepended_msg + "\n"
        full_prompt += f"Given the above context, follow these instructions: {message}"
        message = full_prompt

    chatbot_msg = ChatbotMessages(
        name=name, owner=name, session=session_id, message=message, date=datetime.now()
    )
    db.session.add(chatbot_msg)
    db.session.commit()
    emit(
        "chatbot_ack",
        {
            "name": name,
            "session": session_id,
            "message": message,
            "profile_picture": profile_pictures.get(name, ""),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "requests_in_progress": chatbot_requests_in_progress,
        },
        room=sid,
    )


# Function to retrieve the last k messages
def retrieve_last_k_msg(k, room_code):
    # Querying for the messages excluding those sent by "Room"
    print(f"retriving last k messages for {room_code}")
    last_k_messages = (
        Messages.query.filter_by(room_code=room_code)
        .filter(Messages.name != "Room")
        .order_by(Messages.date.desc())
        .limit(k)
        .all()
    )
    print(f"done retrieving for  {room_code}")
    # Constructing the list of dictionaries
    messages_list = [
        {
            "name": msg.name,
            "message": msg.message,
            "date": msg.date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for msg in reversed(last_k_messages)
    ]  # reversing to get the oldest message first

    return messages_list


def retrieve_chatbot_history(owner, session_id):
    # Querying for the messages from the Chatbot for a given session id
    chatbot_messages = (
        ChatbotMessages.query.filter_by(owner=owner, session=session_id)
        .order_by(ChatbotMessages.date.asc())
        .all()
    )

    # If there's only one message in that session, return None
    if len(chatbot_messages) <= 1:
        return None

    # Excluding the very first message in that session
    chatbot_messages_list = [
        {
            "name": msg.name,
            "message": msg.message,
            "date": msg.date.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for msg in chatbot_messages[1:]
    ]  # starts from the third message

    return chatbot_messages_list


def form_message_pairs(chatbot_history):
    if not chatbot_history:
        return []

    history_visible = []
    user_message = None

    for msg in chatbot_history:
        if (
            msg["name"] != "Chatbot" and user_message is None
        ):  # Capturing the user message
            user_message = msg["message"]
        elif (
            msg["name"] == "Chatbot" and user_message
        ):  # Pairing it with the chatbot response
            history_visible.append([user_message, msg["message"]])
            user_message = None  # Resetting for the next user message

    return history_visible


# Function to simulate the delay for the chatbot response
def background_task(name, sid, session_id, room_code, prompt):
    global chatbot_requests_in_progress
    with chatbot_lock:
        chatbot_requests_in_progress += 1
    print(f"Started timing background task for {name}'s chatbot request")
    start_time = time()
    with app.app_context():
        # k is the number of messages to retrieve

        full_prompt = ""
        history = {"internal": [], "visible": []}
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
            prepended_msg = "\n".join(
                [f"{msg['name']}: {msg['message']}" for msg in last_k_msgs]
            )
            full_prompt += prepended_msg + "\n"
            full_prompt += (
                f"Given the above context, follow these instructions: {prompt}"
            )
            # full_prompt += f"Here is the user's ({name}'s) latest prompt: {prompt}"

        else:
            # chatbot_history_msg = '\n'.join([f"{msg['name']}: {msg['message']}" for msg in chatbot_history])
            history["internal"] = form_message_pairs(chatbot_history)
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
        print(f"Sending {name}'s request to chatbot api: {full_prompt}")
        request_data = {
            "user_input": full_prompt,
            "max_new_tokens": 500,
            "auto_max_new_tokens": False,
            "max_tokens_second": 0,
            "history": history,
            "mode": "instruct",  # Valid options: 'chat', 'chat-instruct', 'instruct'
            "character": "Example",
            "instruction_template": "Vicuna-v1.1",  # Will get autodetected if unset
            "your_name": "You",
            # 'name1': 'name of user', # Optional
            # 'name2': 'name of character', # Optional
            # 'context': 'character context', # Optional
            # 'greeting': 'greeting', # Optional
            "name1_instruct": "USER:",  # Optional
            "name2_instruct": "ASSISTANT:",  # Optional
            "context_instruct": "A chat between a curious user and an artificial intelligence assistant. The assistant gives helpful, detailed, and polite answers to the user's questions.\n\n",  # Optional
            "turn_template": "<|user|> <|user-message|>\n<|bot|> <|bot-message|></s>\n",  # Optional
            "regenerate": False,
            "_continue": False,
            "chat_instruct_command": 'Continue the chat dialogue below. Write a single reply for the character "<|character|>".\n\n<|prompt|>',
            # Generation params. If 'preset' is set to different than 'None', the values
            # in presets/preset-name.yaml are used instead of the individual numbers.
            "preset": "None",
            "do_sample": True,
            "temperature": 0.7,
            "top_p": 0.1,
            "typical_p": 1,
            "epsilon_cutoff": 0,  # In units of 1e-4
            "eta_cutoff": 0,  # In units of 1e-4
            "tfs": 1,
            "top_a": 0,
            "repetition_penalty": 1.18,
            "repetition_penalty_range": 0,
            "top_k": 40,
            "min_length": 0,
            "no_repeat_ngram_size": 0,
            "num_beams": 1,
            "penalty_alpha": 0,
            "length_penalty": 1,
            "early_stopping": False,
            "mirostat_mode": 0,
            "mirostat_tau": 5,
            "mirostat_eta": 0.1,
            "grammar_string": "",
            "guidance_scale": 1,
            "negative_prompt": "",
            "seed": -1,
            "add_bos_token": True,
            "truncation_length": 2048,
            "ban_eos_token": False,
            "custom_token_bans": "",
            "skip_special_tokens": True,
            "stopping_strings": [],
        }

        try:
            response = requests.post(CHATBOT_URI, json=request_data)

            # Check if the response is successful and extract the chatbot's reply
            if response.status_code == 200:
                results = response.json()["results"]
                chatbot_reply = results[0]["history"]["visible"][-1][1]
            else:
                print(response)
                chatbot_reply = f"Sorry, I couldn't process your request due to response.status_code: {response.status_code}. You said: {prompt}"
        except Exception as e:
            print("Exception occured: ", e)
            chatbot_reply = f"Sorry, I couldn't process your request due to an Exception. You said: {prompt}"

        ############################
        response = chatbot_reply
        chatbot_msg = ChatbotMessages(
            name="Chatbot",
            owner=name,
            session=session_id,
            message=response,
            date=datetime.now(),
        )
        db.session.add(chatbot_msg)
        db.session.commit()

        socketio.emit(
            "chatbot_response",
            {
                "name": "Chatbot",
                "session": session_id,
                "message": response,
                "profile_picture": profile_pictures.get("Chatbot", ""),
                "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
            room=sid,
        )
    # Decrementing the counter when the response is processed
    with chatbot_lock:
        chatbot_requests_in_progress -= 1
    print(f"Time taken to finish chatbot request: {time() - start_time} seconds")


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


#################################
# For cleanups of inactive members in active rooms


@socketio.on("heartbeat")
def heartbeat(data):
    room = data["room"]
    name = data["name"]

    # Update the last heartbeat time
    last_heartbeat[room][name] = datetime.now()


def cleanup_inactive_members():
    print("Cleanup task started")
    while True:
        current_time = datetime.now()

        for room, members in last_heartbeat.items():
            for member, last_time in list(members.items()):
                if (current_time - last_time) > timedelta(minutes=2):
                    # This member has been inactive for more than two minutes
                    room_info = Rooms.query.filter_by(code=room).first()
                    if room_info and member in room_info.members.split(","):
                        # Remove member from members list and commit
                        members_list = room_info.members.split(",")
                        members_list.remove(member)
                        room_info.members = ",".join(members_list)
                        db.session.commit()

                        # Notify other members
                        content = {
                            "name": "Room",
                            "message": f"{member} has been removed due to inactivity",
                            "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "profile_picture": profile_pictures.get("Room", ""),
                        }
                        # send(content, to=room)
                        emit("memberChange", members_list, to=room)
                    # Remove the member from our tracking dict
                    del last_heartbeat[room][member]
                    print(f"Removed {member} from {room} due to inactivity")

        # Sleep for a minute before checking again
        eventlet.sleep(60)


def remove_inactive_members_from_db(room_code):
    current_time = datetime.now()
    room_info = Rooms.query.filter_by(code=room_code).first()

    if room_info:
        db_members_list = room_info.members.split(",") if room_info.members else []
        active_members = []

        for member in db_members_list:
            last_time = last_heartbeat.get(room_code, {}).get(member)

            if last_time and (current_time - last_time) <= timedelta(minutes=1):
                active_members.append(member)
            else:
                # This member is considered inactive. Notify other members.
                content = {
                    "name": "Room",
                    "message": f"{member} was removed due to inactivity",
                    "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "profile_picture": profile_pictures.get("Room", ""),
                }
                print(f"Removed {member} due to inactivity")
                # send(content, to=room_code)
                socketio.emit("memberChange", active_members, to=room_code)

        # Update members list in database
        room_info.members = ",".join(active_members)
        db.session.commit()


# Start the cleanup task
# eventlet.spawn(cleanup_inactive_members)


if __name__ == "__main__":
    if LOGGING:
        print("Logging enabled")
    else:
        print("Logging disabled")
    with app.app_context():
        # Create all tables in the database if they don't exist
        db.create_all()
    # eventlet.wsgi.server(eventlet.listen(('0.0.0.0', 8080)), app, debug=True)
    socketio.run(app, host="0.0.0.0", port=8080, debug=True)
