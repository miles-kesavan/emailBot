import streamlit as st
from pathlib import Path
import imaplib
import email
from email.header import decode_header
import ssl
from bs4 import BeautifulSoup
import re
import os
import time
import logging
import requests
from dotenv import load_dotenv

# --- Configure Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

st.set_page_config(page_title="Chat with Email (Friendli AI)", page_icon='📧')
st.title('Chat with Recent Emails (Friendli)')
st.caption("Uses Friendli AI directly with fetched email content in the prompt.")

# --- Constants ---
MAX_EMAILS_TO_FETCH = 50
MAX_EMAIL_BODIES_IN_PROMPT = 10
MAX_BODY_CHARS_PER_EMAIL = 2000

FRIENDLI_API_URL = "https://api.friendli.ai/dedicated/v1/chat/completions"

# --- Initialize Session State ---
if 'email_connected' not in st.session_state:
    st.session_state.email_connected = False
if 'email_connection_error' not in st.session_state:
    st.session_state.email_connection_error = None
if 'email_credentials' not in st.session_state:
    st.session_state.email_credentials = {}
if 'fetched_emails' not in st.session_state:
    st.session_state.fetched_emails = []
if 'friendli_token' not in st.session_state:
    st.session_state.friendli_token = None
if 'messages' not in st.session_state:
    st.session_state.messages = []

# --- Helper Functions ---

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()

def decode_mime_header(header):
    if not header:
        return ""
    decoded_parts = decode_header(header)
    header_parts = []
    for part, encoding in decoded_parts:
        if isinstance(part, bytes):
            try:
                header_parts.append(part.decode(encoding or 'utf-8', errors='replace'))
            except Exception:
                header_parts.append(part.decode('utf-8', errors='replace'))
        else:
            header_parts.append(part)
    return "".join(header_parts)

def get_email_body(msg):
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    charset = part.get_content_charset()
                    payload = part.get_payload(decode=True)
                    body = payload.decode(charset or 'utf-8', errors='replace')
                    break
                except Exception as e:
                    logger.warning(f"Could not decode plain text: {e}")
        if not body:
            for part in msg.walk():
                content_type = part.get_content_type()
                content_disposition = str(part.get("Content-Disposition"))
                if content_type == "text/html" and "attachment" not in content_disposition:
                    try:
                        charset = part.get_content_charset()
                        payload = part.get_payload(decode=True)
                        html_content = payload.decode(charset or 'utf-8', errors='replace')
                        soup = BeautifulSoup(html_content, "html.parser")
                        body = soup.get_text(separator=" ", strip=True)
                        break
                    except Exception as e:
                        logger.warning(f"Could not decode HTML: {e}")
    else:
        content_type = msg.get_content_type()
        try:
            charset = msg.get_content_charset()
            payload = msg.get_payload(decode=True)
            if "text/plain" in content_type:
                body = payload.decode(charset or 'utf-8', errors='replace')
            elif "text/html" in content_type:
                html_content = payload.decode(charset or 'utf-8', errors='replace')
                soup = BeautifulSoup(html_content, "html.parser")
                body = soup.get_text(separator=" ", strip=True)
        except Exception as e:
            logger.warning(f"Could not decode single part: {e}")
    return clean_text(body)

def configure_and_fetch_emails(server, user, password):
    fetched_emails_list = []
    try:
        context = ssl.create_default_context()
        mail = imaplib.IMAP4_SSL(server, ssl_context=context)
        mail.login(user, password)
        mail.select("inbox")

        status, messages = mail.search(None, "ALL")
        if status != "OK":
            return [], "Failed to search emails."

        email_ids = messages[0].split()
        num_to_fetch = min(MAX_EMAILS_TO_FETCH, len(email_ids))
        latest_ids = email_ids[-num_to_fetch:]

        for email_id in reversed(latest_ids):
            status, msg_data = mail.fetch(email_id, "(RFC822)")
            if status == "OK":
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject = decode_mime_header(msg["subject"])
                        from_ = decode_mime_header(msg["from"])
                        date_ = msg["date"]
                        body = get_email_body(msg)
                        fetched_emails_list.append({
                            'id': email_id.decode(),
                            'subject': subject,
                            'from': from_,
                            'date': date_,
                            'body': body
                        })
        mail.logout()
        return fetched_emails_list, None
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)
        return [], f"Error: {e}"

def chat_with_friendli(user_query, email_context, friendli_token, model_name="c6xp7t1pxbvl"):
    headers = {
        "Authorization": f"Bearer flp_wi27kyKbSh5GHeDky8ctwjf4qA6aikHtBdjCiUcYQCUdc",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are an assistant helping a user with their recent emails. Only use the provided email context."},
            {"role": "user", "content": f"""Here are the recent emails:

{email_context}

User Question: {user_query}
"""},
        ],
        "temperature": 0.2,
        "stream": False,
    }

    response = requests.post(FRIENDLI_API_URL, headers=headers, json=payload)
    response.raise_for_status()
    data = response.json()
    return data['choices'][0]['message']['content']


# --- Sidebar ---
with st.sidebar:
    st.header("Email (IMAP) Connection")
    st.caption("Example servers: imap.gmail.com, outlook.office365.com")
    imap_server = st.text_input('IMAP Server Address')
    email_user = st.text_input('Email Address')
    email_pass = st.text_input('Password or App Password', type='password')

    st.header("Friendli AI Configuration")
    friendli_token = st.text_input('Friendli API Token', type='password')
    st.session_state.friendli_token = friendli_token

    connect_pressed = st.button("Connect & Fetch Emails")

    if connect_pressed:
        if not all([imap_server, email_user, email_pass, friendli_token]):
            st.error("Please fill all the fields.")
        else:
            with st.spinner("Connecting and fetching emails..."):
                st.session_state.email_credentials = {'server': imap_server, 'user': email_user, 'pass': email_pass}
                fetched_data, error_msg = configure_and_fetch_emails(imap_server, email_user, email_pass)

                if error_msg:
                    st.session_state.email_connection_error = error_msg
                    st.error(f"Failed: {error_msg}")
                else:
                    st.session_state.fetched_emails = fetched_data
                    st.session_state.email_connected = True
                    st.success(f"Fetched {len(fetched_data)} emails successfully!")

# --- Main Page ---
if not st.session_state.friendli_token:
    st.info("Please enter your Friendli API Token.")
    st.stop()

if not st.session_state.email_connected:
    st.info("Please connect to your email account.")
    st.stop()

fetched_emails = st.session_state.fetched_emails

email_summary_list = []
email_context_string = ""
for i, email_data in enumerate(fetched_emails):
    summary = f"{i+1}. Subject: {email_data['subject']} | From: {email_data['from']} | Date: {email_data['date']}"
    email_summary_list.append(summary)
    if i < MAX_EMAIL_BODIES_IN_PROMPT:
        body_snippet = email_data['body'][:MAX_BODY_CHARS_PER_EMAIL]
        if len(email_data['body']) > MAX_BODY_CHARS_PER_EMAIL:
            body_snippet += "..."
        email_context_string += f"--- Email {i+1} ---\nSubject: {email_data['subject']}\nFrom: {email_data['from']}\nDate: {email_data['date']}\nBody: {body_snippet}\n\n"

st.subheader(f"Context includes {len(fetched_emails)} emails")
with st.expander("Show Email Summaries"):
    st.text("\n".join(email_summary_list))

st.success("Ready to chat about your emails!")

if 'messages' not in st.session_state or st.sidebar.button('Clear Chat'):
    st.session_state.messages = [{'role': 'assistant', 'content': f"Ask me anything about the {len(fetched_emails)} emails."}]

for msg in st.session_state.messages:
    st.chat_message(msg['role']).write(msg['content'])

user_query = st.chat_input(placeholder="Ask a question about your emails...")

if user_query:
    st.session_state.messages.append({'role': 'user', 'content': user_query})
    st.chat_message('user').write(user_query)

    try:
        with st.spinner("Thinking..."):
            assistant_response = chat_with_friendli(user_query, email_context_string, st.session_state.friendli_token)
            st.session_state.messages.append({'role': 'assistant', 'content': assistant_response})
            st.chat_message('assistant').write(assistant_response)
    except Exception as e:
        st.error(f"Failed to get response: {e}")
