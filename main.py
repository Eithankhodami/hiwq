import os
import json
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import gspread
import tempfile
import requests

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
DATE, PLACE, AMOUNT, CATEGORY, RECEIPT_NUMBER, TAG, RECEIPT_UPLOAD = range(7)

# Predefined categories and tags
CATEGORIES = ['Food', 'Transportation', 'Accommodation', 'House furniture', 'Electronics', 'Other']
TAGS = ['Business', 'Personal', 'House', 'Entertainment', 'Gift']

# Load environment variables or use config
def get_credentials():
    """Get Google credentials from environment variable"""
    if os.environ.get('GOOGLE_CREDENTIALS'):
        return json.loads(os.environ.get('GOOGLE_CREDENTIALS'))
    else:
        # For local development with a credentials file
        with open('credentials.json', 'r') as f:
            return json.load(f)

def init_google_services():
    """Initialize Google Sheets and Drive services"""
    credentials_dict = get_credentials()
    credentials = service_account.Credentials.from_service_account_info(
        credentials_dict, 
        scopes=['https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive']
    )
    
    # Initialize Google Sheets client
    gc = gspread.authorize(credentials)
    sheet = gc.open("Secondment Sheet").sheet1
    
    # Initialize Google Drive service
    drive_service = build('drive', 'v3', credentials=credentials)
    
    return sheet, drive_service

# Get folder ID from URL or environment
FOLDER_ID = "10oET0doyJHwuF68Xi4su8A9xXHTPeRkN"  # Extract from your URL or set as env var

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start conversation and ask for date."""
    user = update.effective_user
    await update.message.reply_text(
        f"Hi {user.first_name}! I'm your expense tracking bot. Let's record a new expense.\n\n"
        f"First, please enter the date (YYYY.MM.DD) or send 'today' for today's date:"
    )
    
    # Initialize user data dictionary
    context.user_data['expense'] = {}
    
    return DATE

async def date_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the date and ask for place."""
    text = update.message.text
    
    # Format the date
    if text.lower() == 'today':
        date_str = datetime.now().strftime("%Y.%m.%d")
    else:
        # Validate date format
        try:
            input_date = datetime.strptime(text, "%Y.%m.%d")
            date_str = input_date.strftime("%Y.%m.%d")
        except ValueError:
            await update.message.reply_text(
                "Please use the format YYYY.MM.DD (e.g., 2025.01.01) or type 'today':"
            )
            return DATE
    
    # Store the date
    context.user_data['expense']['date'] = date_str
    
    await update.message.reply_text(f"Date set to {date_str}. Now, please enter the place/vendor:")
    
    return PLACE

async def place_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the place and ask for amount."""
    context.user_data['expense']['place'] = update.message.text
    
    await update.message.reply_text(
        f"Place set to {update.message.text}. Now, please enter the amount (numbers only):"
    )
    
    return AMOUNT

async def amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the amount and ask for category."""
    try:
        amount = float(update.message.text)
        context.user_data['expense']['amount'] = amount
        
        # Create keyboard with category options
        keyboard = [
            [InlineKeyboardButton(category, callback_data=category)] 
            for category in CATEGORIES
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"Amount set to {amount}. Please select a category:",
            reply_markup=reply_markup
        )
        
        return CATEGORY
    except ValueError:
        await update.message.reply_text("Please enter a valid number for the amount:")
        return AMOUNT

async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the selected category and ask for receipt number."""
    query = update.callback_query
    await query.answer()
    
    category = query.data
    context.user_data['expense']['category'] = category
    
    await query.edit_message_text(
        f"Category set to {category}. Please enter the receipt number:"
    )
    
    return RECEIPT_NUMBER

async def receipt_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the receipt number and ask for tags."""
    context.user_data['expense']['receipt_number'] = update.message.text
    
    # Create keyboard with tag options
    keyboard = [
        [InlineKeyboardButton(tag, callback_data=tag)] 
        for tag in TAGS
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"Receipt number set to {update.message.text}. Please select a tag:",
        reply_markup=reply_markup
    )
    
    return TAG

async def tag_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the selected tag and ask for receipt upload."""
    query = update.callback_query
    await query.answer()
    
    tag = query.data
    context.user_data['expense']['tag'] = tag
    
    await query.edit_message_text(
        f"Tag set to {tag}. Now, please upload a photo of your receipt (or type 'skip' to skip):"
    )
    
    return RECEIPT_UPLOAD

async def receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receipt upload and save all data."""
    # Initialize Google services
    try:
        sheet, drive_service = init_google_services()
    except Exception as e:
        logger.error(f"Error initializing Google services: {e}")
        await update.message.reply_text(
            "There was an error connecting to Google services. Please try again later."
        )
        return ConversationHandler.END
        
    expense_data = context.user_data['expense']
    
    # Handle receipt upload
    drive_file_link = ""
    if update.message.text and update.message.text.lower() == 'skip':
        expense_data['upload'] = "No receipt"
    else:
        try:
            if not update.message.photo:
                await update.message.reply_text(
                    "Please upload a photo or type 'skip'. Send the receipt as an image:"
                )
                return RECEIPT_UPLOAD
                
            # Get the largest photo
            photo = update.message.photo[-1]
            file = await context.bot.get_file(photo.file_id)
            
            # Create a temporary file
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                # Download the photo
                photo_url = file.file_path
                response = requests.get(photo_url)
                temp_file.write(response.content)
                temp_file_path = temp_file.name
            
            # Upload to Google Drive
            file_metadata = {
                'name': f"Receipt_{expense_data['date']}_{expense_data['place']}",
                'parents': [FOLDER_ID]
            }
            
            media = MediaFileUpload(
                temp_file_path,
                mimetype='image/jpeg',
                resumable=True
            )
            
            file = drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id,webViewLink'
            ).execute()
            
            drive_file_link = file.get('webViewLink', '')
            expense_data['upload'] = drive_file_link
            
            # Clean up the temporary file
            os.unlink(temp_file_path)
            
        except Exception as e:
            logger.error(f"Error uploading receipt: {e}")
            await update.message.reply_text(
                "There was an error uploading your receipt. The expense data will still be saved."
            )
            expense_data['upload'] = "Upload failed"
    
    # Save to Google Sheet
    try:
        # Format the data for the sheet
        row = [
            expense_data['date'],
            expense_data['place'],
            expense_data['amount'],
            expense_data['category'],
            expense_data['receipt_number'],
            expense_data['tag'],
            expense_data.get('upload', '')
        ]
        
        # Append the row to the sheet
        sheet.append_row(row)
        
        receipt_msg = (
            f"Receipt uploaded and linked in the sheet." 
            if drive_file_link else 
            "No receipt uploaded."
        )
        
        # Confirm to the user
        await update.message.reply_text(
            f"✅ Expense saved successfully!\n\n"
            f"Date: {expense_data['date']}\n"
            f"Place: {expense_data['place']}\n"
            f"Amount: {expense_data['amount']}\n"
            f"Category: {expense_data['category']}\n"
            f"Receipt #: {expense_data['receipt_number']}\n"
            f"Tag: {expense_data['tag']}\n"
            f"{receipt_msg}\n\n"
            f"To log another expense, use the /start command."
        )
        
    except Exception as e:
        logger.error(f"Error saving to Google Sheet: {e}")
        await update.message.reply_text(
            "There was an error saving your expense data. Please try again later."
        )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel and end the conversation."""
    await update.message.reply_text(
        "Expense tracking cancelled. Use /start to track a new expense."
    )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END

def main() -> None:
    """Run the bot."""
    # Get the token from environment variable
    token = os.environ.get("TELEGRAM_TOKEN", "7939992556:AAGUaWdemNJ31KiHBBvFhbuPFmZO1kUtWwo")
    
    # Create the Application
    application = Application.builder().token(token).build()

    # Add conversation handler
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_input)],
            PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, place_input)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_input)],
            CATEGORY: [CallbackQueryHandler(category_callback)],
            RECEIPT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_number_input)],
            TAG: [CallbackQueryHandler(tag_callback)],
            RECEIPT_UPLOAD: [MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, receipt_upload)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)

    # Run the bot
    application.run_polling()

if __name__ == "__main__":
    main()