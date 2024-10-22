import os
import base64
import json
import smtplib
from email.message import EmailMessage
from googleads import ad_manager
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import logging

# Set up logging
logging.basicConfig(level=logging.INFO)

# Load required environment variables and check for their presence
def validate_environment_variables(var_names):
    for var in var_names:
        value = os.environ.get(var)
        if not value:
            logging.error(f"{var} environment variable not set.")
            raise ValueError(f"Missing {var}")

def load_google_sheets_credentials():
    return os.environ['GOOGLE_APPLICATION_CREDENTIALS']

def load_google_ads_credentials():
    google_ads_file = os.environ.get('GOOGLE_APPLICATION_GOOGLEADS')
    if not google_ads_file:
        logging.error("GOOGLE_APPLICATION_GOOGLEADS environment variable not set.")
        raise ValueError("Missing GOOGLE_APPLICATION_GOOGLEADS")
    
    # Check if the file exists
    if not os.path.isfile(google_ads_file):
        logging.error(f"Google Ads credentials file not found: {google_ads_file}")
        raise ValueError(f"File not found: {google_ads_file}")

    return google_ads_file

# Fetches the line items and thresholds from Google Sheets.
def get_google_sheets_data(sheet_url, sheet_name):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(load_google_sheets_credentials(), scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)
    data = sheet.get_all_records()
    logging.info(f"Fetched {len(data)} records from Google Sheets.")
    return data

# Updates Google Sheets to remove completed line item IDs.
def update_google_sheets(sheet_url, sheet_name, completed_ids):
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    creds = ServiceAccountCredentials.from_json_keyfile_name(load_google_sheets_credentials(), scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_url(sheet_url).worksheet(sheet_name)

    current_data = sheet.get_all_records()
    updated_data = [record for record in current_data if str(record['Line Item ID']) not in completed_ids]

    # Clear the existing data and update with the new data
    sheet.clear()
    if current_data:
        sheet.append_row(list(current_data[0].keys()))  # Append headers
        for data in updated_data:
            sheet.append_row(list(data.values()))
    logging.info("Google Sheets updated to remove completed line items.")

# Retrieve the stats and status for a specific line item.
def get_line_item_info(client, line_item_id):
    line_item_service = client.GetService('LineItemService', version='v202408')

    statement = (ad_manager.StatementBuilder()
                 .Where('id = :id')
                 .WithBindVariable('id', line_item_id)
                 .Limit(1))

    response = line_item_service.getLineItemsByStatement(statement.ToStatement())

    if 'results' in response and len(response['results']) > 0:
        line_item = response['results'][0]
        impressions = line_item.stats.impressionsDelivered if hasattr(line_item, 'stats') and line_item.stats else 0
        status = line_item.status if hasattr(line_item, 'status') else None
        return impressions, status
    return 0, None

# Pause the specified line item if it is in a state that allows it to be paused.
def pause_line_item(client, line_item_id):
    line_item_service = client.GetService('LineItemService', version='v202408')
    statement = (ad_manager.StatementBuilder()
                 .Where('id = :id')
                 .WithBindVariable('id', line_item_id)
                 .Limit(1))

    line_items = line_item_service.getLineItemsByStatement(statement.ToStatement())

    if 'results' in line_items and len(line_items['results']) > 0:
        line_item = line_items['results'][0]
        if line_item.status == 'ACTIVE':
            action = {'xsi_type': 'PauseLineItems'}
            line_item_service.performLineItemAction(action, statement.ToStatement())
            logging.info(f"Line item {line_item_id} has been paused.")
        else:
            logging.warning(f"Line item {line_item_id} is in status '{line_item.status}', cannot be paused.")
    else:
        logging.error(f"Line item {line_item_id} not found.")

# Send an email to notify about the line item status.
def send_email(line_item_id, impressions, threshold, status):
    validate_environment_variables(['EMAIL_PASSWORD'])
    EMAIL_PASSWORD = os.environ['EMAIL_PASSWORD']

    sender_email = "anurag.mishra1@timesinternet.in"
    recipient_emails = ["nitesh.pandey1@timesinternet.in"]

    email_subject = f"Line Item {line_item_id} Status Update"
    if status == 'COMPLETED':
        email_body = f"""
        Hi,

        The line item {line_item_id} has completed its delivery with {impressions} impressions.
        The line item will not be paused as it is completed.
        """
    else:
        email_body = f"""
        Hi,

        The line item {line_item_id} has delivered {impressions} impressions.
        Monitoring continues. The line item will be paused upon reaching the threshold of {threshold}.
        """

    msg = EmailMessage()
    msg['Subject'] = email_subject
    msg['From'] = sender_email
    msg['To'] = ", ".join(recipient_emails)
    msg.set_content(email_body)

    try:
        with smtplib.SMTP('smtp.gmail.com', 587) as server:
            server.starttls()
            server.login(sender_email, EMAIL_PASSWORD)
            server.send_message(msg)
        logging.info(f"Email sent to {', '.join(recipient_emails)}")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")

def main():
    validate_environment_variables(['GOOGLE_APPLICATION_CREDENTIALS', 'GOOGLE_APPLICATION_GOOGLEADS', 'EMAIL_PASSWORD'])
    client = ad_manager.AdManagerClient.LoadFromStorage(load_google_ads_credentials())
   
    sheet_url = 'https://docs.google.com/spreadsheets/d/1m4fIYSVMn4rZw4atrYwQYtkV_NXY9KVHKcGwKf4FSAU/edit?gid=0#gid=0'
    sheet_name = 'LineItemAndThreshold'
    line_items_data = get_google_sheets_data(sheet_url, sheet_name)
   
    completed_ids = []  # List to keep track of completed line items

    # Loop through each line item and check if it needs to be paused
    for record in line_items_data:
        line_item_id = str(record['Line Item ID'])
        threshold = int(record['Impression Threshold'])
       
        impressions, line_item_status = get_line_item_info(client, line_item_id)
       
        if line_item_status == 'COMPLETED':
            logging.info(f"Line item {line_item_id} is completed. Removing from monitoring.")
            completed_ids.append(line_item_id)
            send_email(line_item_id, impressions, threshold, line_item_status)
        elif isinstance(impressions, int) and impressions >= threshold:
            logging.info(f"Impressions: {impressions}. Pausing line item {line_item_id}.")
            if line_item_status == 'ACTIVE':
                pause_line_item(client, line_item_id)
            else:
                logging.warning(f"Line item {line_item_id} is in status '{line_item_status}', cannot be paused.")
            send_email(line_item_id, impressions, threshold, line_item_status)
        else:
            logging.info(f"Impressions: {impressions}. No need to pause yet.")
            send_email(line_item_id, impressions, threshold, line_item_status)

    # Update Google Sheets to remove completed line items
    if completed_ids:
        update_google_sheets(sheet_url, sheet_name, completed_ids)

if __name__ == "__main__":
    main()
