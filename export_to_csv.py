import csv
import os
import sys
import shutil 
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine
from sqlalchemy.sql import text

# Import the app instance and your models from your main module
from main import app, db, Rooms, Messages, ChatbotMessages, Comments, CommentVotes, CommentReports

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost/{DB_NAME}"
engine = create_engine(DATABASE_URL)
# db = SQLAlchemy()
from main import db

def export_to_csv():
    # Push an application context
    with app.app_context():
        tables = [Rooms, Messages, ChatbotMessages, Comments, CommentVotes, CommentReports]
        filenames = []

        # Ensure the data directory exists
        os.makedirs('data', exist_ok=True)

        print("Starting export to CSV process...")

        for table in tables:
            query = table.query.all()
            # Save the CSVs inside the "data" folder
            filename = os.path.join('data', f"{table.__tablename__}.csv")
            filenames.append(filename)

            with open(filename, 'w', newline='') as csvfile:
                writer = csv.writer(csvfile)

                # Write header
                header = table.__table__.columns.keys()
                writer.writerow(header)

                for item in query:
                    writer.writerow([getattr(item, col) for col in header])

            print(f"Exported {table.__tablename__} to {filename}")

        return filenames

def zip_data_folder():
    shutil.make_archive("data", 'zip', "data")

def reset_database():
    with engine.connect() as connection:
        for table in [Rooms.__tablename__, Messages.__tablename__, ChatbotMessages.__tablename__, Comments.__tablename__, CommentVotes.__tablename__, CommentReports.__tablename__]:
            print(f"Dropping table {table}...")
            with connection.begin():
                connection.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE;"))
            print(f"Table {table} dropped.")
        
        # Now that tables are dropped, let's recreate them using Flask's app context.
        with app.app_context():
            print("Recreating tables...")
            db.create_all()
            print("Database reset completed!")

if __name__ == "__main__":
    # Initiate CSV export regardless of whether the reset-db flag is given
    print("Initiating CSV export...")
    csv_files = export_to_csv()

    print("\nZipping the data folder...")
    zip_data_folder()
    print("Data folder zipped as 'data.zip'")
    
    print("\nCSV export completed!")
    print("You can find the exported files at the following locations:")
    for file in csv_files:
        print(os.path.abspath(file))

    # Check if the reset-db flag is given
    if 'reset-db' in sys.argv:
        print("\nWARNING: You have opted to reset the database. This will DELETE all data!")
        choice = input("Are you sure you want to continue? [yes/no]: ")
        if choice.lower() == 'yes':
            reset_database()
        else:
            print("Database reset operation aborted by the user.")