import smtplib
import sys
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from dotenv import load_dotenv

load_dotenv()

EMAIL_ADDRESS = os.getenv("SENDER_EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("SENDER_EMAIL_PASSWORD")

def send_email(recipient_email, subject, body, attachment_path):
    print(f"Preparing to send email to {recipient_email}...")

    # Create a multipart email
    msg = MIMEMultipart()
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = recipient_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Attach the file
    with open(attachment_path, 'rb') as file:
        print(f"Attaching {attachment_path} to the email...")
        part = MIMEBase('application', 'octet-stream')
        part.set_payload(file.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f"attachment; filename= {os.path.basename(attachment_path)}")
        msg.attach(part)

    # Send the email
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        print("Logging into the SMTP server...")
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        print("Sending the email...")
        server.sendmail(EMAIL_ADDRESS, recipient_email, msg.as_string())

    print(f"Email sent successfully to {recipient_email}!")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python script_name.py recipient_email_address")
        sys.exit(1)
        
    recipient = sys.argv[1]
    send_email(recipient, "Data Zip File", "Here's the data zip file you requested.", "data.zip")