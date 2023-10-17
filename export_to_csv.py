import csv
import os
import sys
import shutil 
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import create_engine

# Import the app instance and your models from your main module
from main import app, Rooms, Messages, ChatbotMessages

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")

DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@localhost/{DB_NAME}"
engine = create_engine(DATABASE_URL)
db = SQLAlchemy()

def export_to_csv():
    # Push an application context
    with app.app_context():
        tables = [Rooms, Messages, ChatbotMessages]
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

if __name__ == "__main__":
    print("Initiating CSV export...")
    csv_files = export_to_csv()

    print("\nZipping the data folder...")
    zip_data_folder()
    print("Data folder zipped as 'data.zip'")

    print("\nCSV export completed!")
    print("You can find the exported files at the following locations:")
    for file in csv_files:
        print(os.path.abspath(file))