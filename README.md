# RumorChat

## Disclaimer: This is a work in progress. Bugs and crashes are to be expected.

## Requirements

- Install Python 3.9
- Install [PostgreSQL](https://www.postgresql.org/download/windows/) 
- Create a .env file with the following information:
  - DB_NAME
  - DB_USER
  - DB_PASSWORD
  - SECRET_KEY (can be anything)
  **Example**:

  DB_NAME=rumorchatDB
  DB_USER=postgres
  DB_PASSWORD=rumorchat
  SECRET_KEY=abc
- Run the following commands:

```bash
# create a virtual environment 
python -m venv env 

# activate the virtual environment 
# if Linux
source env/bin/activate

# if Windows
.\env\Scripts\activate

# upgrade pip
python -m pip install --upgrade pip

# install requirements
pip install -r requirements.txt

# run main script
python main.py

```

## Screenshots

![Screenshot1](images\screenshot1.png)
![Screenshot2](images\screenshot2.png)

## Behaviour And Activity Flow

- Users pick a name and either create a room with a randomly generated id, or join an existing room.
- Data on user names and room ids persists in a PostgreSQL database (unless manually deleted).
- In the future, the PostgreSQL database could be hosted on a free cloud server.
- Basic error checking is done on joining/creating rooms in cases whereby rooms don't exist or a username already exists in that particular room.
  - However, try not to have duplicate names as there might be some bugs (especially, I suspect, when users with the same name joins different rooms).
- Once in a room, users can send timestamped messages to each other and see other live members in the public chatbox at the top half.
- At the bottom half, users have access to a private chatbot with individual sessions to switch between conversation histories.
- These sessions are saved and unique to the user's name. They also persist across all rooms for a particular name.
- It is envisioned that the private chatbot would connect to an external API. For now, this is simulated by a simple echo bot that repeats the user's message back to them after 5 seconds.

## Database Schema

```python
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
```
