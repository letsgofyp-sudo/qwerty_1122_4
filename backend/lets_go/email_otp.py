import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables (optional for security)
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD", "")
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", 587))


def send_email_otp(recipient_email: str, otp_code: str) -> bool:
    """
    Sends a One-Time Password (OTP) to the specified email address.

    Args:
        recipient_email (str): The recipient's email address.
        otp_code (str): The OTP code to send.

    Returns:
        bool: True if the email was sent successfully, False otherwise.
    """

    # Compose email content
    subject = "Your One-Time Password (OTP)"
    body = (
        f"Dear user,\n\n"
        f"Your One-Time Password (OTP) is: {otp_code}\n\n"
        f"This code is valid for 5 minutes.\n\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Regards,\nYour Company Name"
    )

    # Create the email message
    message = MIMEMultipart()
    message["From"] = SENDER_EMAIL
    message["To"] = recipient_email
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    try:
        # Connect to the SMTP server
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()  # Secure the connection
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(message)

        logger.info(f"OTP email sent successfully to {recipient_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send OTP to {recipient_email}. Error: {e}")
        return False

def send_email_otp_for_reset(recipient_email: str, otp_code: str) -> bool:
    """
    Sends a password reset OTP to the specified email address.
    """
    subject = "Reset Your Password - OTP Verification"
    body = (
        f"Dear user,\n\n"
        f"You have requested to reset your password.\n"
        f"Your OTP is: {otp_code}\n\n"
        f"This code is valid for 5 minutes.\n"
        f"If you did not request this, please ignore this email.\n\n"
        f"Regards,\nYour Company Name"
    )

    message = MIMEMultipart()
    message["From"] = SENDER_EMAIL
    message["To"] = recipient_email
    message["Subject"] = subject
    message.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(message)

        logger.info(f"Reset password OTP email sent to {recipient_email}")
        return True

    except Exception as e:
        logger.error(f"Failed to send reset OTP email. Error: {e}")
        return False
