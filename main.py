from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import SocketIO, emit, join_room, leave_room, send
import random
from string import ascii_uppercase
from datetime import datetime

app = Flask(__name__)
app.config["SECRET_KEY"] = "abc"

# initialisation object for socketio library
socketio = SocketIO(app)
# TODO: This is currently stored in RAM. Need to store in database (PostgresSQL).
rooms = {}

def generate_unique_code(length):
    while True:
        code = ""
        for _ in range(length):
            code += random.choice(ascii_uppercase)
        # return code if indeed unique   
        if code not in rooms:
            return code

# Routes

# Home Page
@app.route("/", methods=["GET", "POST"])
def home():
    # Clear session when user goes to home page
    # so that they can't navigate directly to chat page without entering name and room code
    session.clear()
    if request.method == "POST":
        # attempt to grab values from form; returns None if doesn't exist
        name = request.form.get("name")
        code = request.form.get("code")
        # False if doesn't exist
        join = request.form.get("join", False)
        create = request.form.get("create", False)
        
        if not name:
            # code=code and name=name to preserve values in form on render_template-initiated reload.
            return render_template("home.html", error="Please enter a name", code=code, name=name)
        
        if join != False and not code:
            return render_template("home.html", error="Please enter a room code", code=code, name=name)
    
        room = code
        if create != False:
            room = generate_unique_code(4)
            rooms[room] = {"members":[], "messages":[]}
            
        # if not create, we assume they are trying to join a room
        # Refuse to join if room doesn't exist or name already exists in room
        elif code not in rooms:
            return render_template("home.html", error="Room code does not exist", code=code, name=name)
        elif name in rooms[room]["members"]:
            # If the name already exists in the room's members
            return render_template("home.html", error="Name already exists in the room", code=code, name=name)

        # Session is a semi-permanent way to store information about user
        # Temporary secure data stored in the server; expires after awhile
        # Stored persistently between requests
        session["room"] = room
        session["name"] = name
        return redirect(url_for("room"))
    
    return render_template("home.html")

@app.route("/room")
def room():
    room = session.get("room")
    # Ensure user can only go to /room route if they either generated a new room
    # or joined an existing room from the home page
    if room is None or session.get("name") is None or room not in rooms:
        return redirect(url_for("home"))
    
    return render_template("room.html",code=room, messages=rooms[room]["messages"])

@socketio.on("message")
def message(data):
    room = session.get("room")
    if room not in rooms:
        return
    
    content = {
        "name":session.get("name"),
        "message":data["data"],
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    # On receiving data from a client, send it to all clients in the room
    send(content, to=room)
    rooms[room]["messages"].append(content)
    print(f"{session.get('name')} said: {data['data']} in room {room}")

# using the initialisation object for socketio
@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    if not room or not name:
        return
    if room not in rooms:
        # Leave room as it shouldn't exist
        leave_room(room)
        return
    
    
    join_room(room)
    content = {
        "name":name,
        "message":f"{name} has joined the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    send(content, to=room)
    
    # Prevents duplicate names in room upon refresh from same session
    if name not in rooms[room]["members"]:
        rooms[room]["members"].append(name)
    emit("memberChange", rooms[room]["members"], to=room)
    print(f"{name} has joined room {room}. Current Members: {rooms[room]['members']}")
    

@socketio.on("disconnect")
def disconnect():
    room = session.get("room")
    name = session.get("name")
    leave_room(room)
    
    # Remove room if no members left
    if room in rooms:
        rooms[room]["members"].remove(name)
        if len(rooms[room]["members"]) <= 0:
            del rooms[room]
    content = {
        "name":name,
        "message":f"{name} has left the room",
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    send(content, to=room)
    emit("memberChange", rooms[room]["members"], to=room)
    print(f"{name} has left room {room}")  

if __name__ == '__main__':
    socketio.run(app, debug=True)