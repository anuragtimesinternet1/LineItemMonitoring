import os
import base64
import json
import smtplib
from email.message import EmailMessage
from googleads import ad_manager
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging
import yaml

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load Google Sheets API credentials from environment variable
def load_google_sheets_credentials():
    credentials_file = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not credentials_file:
        logging.error("GOOGLE_APPLICATION_CREDENTIALS environment variable not set.")
        raise ValueError("Missing GOOGLE_APPLICATION_CREDENTIALS")
    return credentials_file

def load_google_ads_credentials():
    google_ads_encoded = os.environ.get('GOOGLE_APPLICATION_GOOGLEADS')
    if not google_ads_encoded:
        logging.error("GOOGLE_APPLICATION_GOOGLEADS environment variable not set.")
        raise ValueError("Missing GOOGLE_APPLICATION_GOOGLEADS")

    try:
        # Decode the base64-encoded credentials
        decoded_content = base64.b64decode(google_ads_encoded).decode('utf-8')
        logging.info("Successfully decoded Google Ads credentials.")
        
        # Load the YAML from the decoded string
        client = ad_manager.AdManagerClient.LoadFromString(decoded_content)
        return client
    except (base64.binascii.Error, UnicodeDecodeError) as e:
        logging.error(f"Error decoding Google Ads credentials: {e}")
        raise ValueError("Failed to decode GOOGLE_APPLICATION_GOOGLEADS credentials.")
    except yaml.YAMLError as e:
        logging.error(f"Error loading YAML from decoded credentials: {e}")
        raise ValueError("Invalid YAML format in GOOGLE_APPLICATION_GOOGLEADS credentials.")

# Fetches the line items and thresholds from Google Sheets.
def get_google_sheets_data(sheet_url, sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(load_google_sheets_credentials(), scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)
    data = sheet.get_all_records()
    return data

# (Other functions remain unchanged...)

def main():
    load_google_sheets_credentials()  # Load Google Sheets credentials
    client = load_google_ads_credentials()  # Load Google Ads credentials
   
    # Fetch line items and thresholds from Google Sheets
    sheet_url = 'https://docs.google.com/spreadsheets/d/1m4fIYSVMn4rZw4atrYwQYtkV_NXY9KVHKcGwKf4FSAU/edit?gid=0#gid=0'
    sheet_name = 'LineItemAndThreshold'
    line_items_data = get_google_sheets_data(sheet_url, sheet_name)
   
    completed_ids = []  # List to keep track of completed line items

    # Loop through each line item and check if it needs to be paused
    for record in line_items_data:
        line_item_id = str(record['Line Item ID'])
        threshold = int(record['Impression Threshold'])
       
        impressions = get_line_item_stats(client, line_item_id)
       
        # Get line item status before deciding to pause
        line_item_status = get_line_item_status(client, line_item_id)
       
        if line_item_status == 'COMPLETED':
            logging.info(f"Line item {line_item_id} is completed. Removing from monitoring.")
            completed_ids.append(line_item_id)  # Add to completed list
            send_email(line_item_id, impressions, threshold, line_item_status)
        elif isinstance(impressions, int) and impressions >= threshold:
            logging.info(f"Impressions: {impressions}. Pausing line item {line_item_id}.")
            if line_item_status == 'ACTIVE':
                pause_line_item(client, line_item_id)
            else:
                logging.warning(f"Line item {line_item_id} is in status '{line_item_status}', cannot be paused.")
            send_email(line_item_id, impressions, threshold, line_item_status)  # Send email when threshold is hit
        else:
            logging.info(f"Impressions: {impressions}. No need to pause yet.")
            send_email(line_item_id, impressions, threshold, line_item_status)  # Send status update email

    # Update Google Sheets to remove completed line items
    if completed_ids:
        update_google_sheets(sheet_url, sheet_name, completed_ids)

if __name__ == "__main__":
    main()
