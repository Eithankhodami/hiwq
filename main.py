import os
import json
import logging
import calendar
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, CallbackQueryHandler, ContextTypes
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import gspread
import tempfile
import requests
import re
from dateutil.relativedelta import relativedelta

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
DATE, PLACE, AMOUNT, CATEGORY, RECEIPT_NUMBER, TAG, RECEIPT_UPLOAD = range(7)
SEARCH_DATE_RANGE, EDIT_FIELD, EDIT_VALUE, DELETE_CONFIRM = range(7, 11)
CALENDAR_MONTH_SELECTION, CALENDAR_DAY_SELECTION = range(11, 13)

# Predefined categories and tags with emojis
CATEGORIES = {
    '🍽️ Food': 'Food',
    '🚗 Transportation': 'Transportation',
    '🏠 Accommodation': 'Accommodation',
    '🪑 House furniture': 'House furniture',
    '📱 Electronics': 'Electronics',
    '🛒 Other': 'Other'
}

TAGS = {
    '💼 Business': 'Business',
    '👤 Personal': 'Personal',
    '🏡 House': 'House',
    '🎭 Entertainment': 'Entertainment',
    '🎁 Gift': 'Gift'
}

# Function to create a main menu keyboard
def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("➕ New Expense", callback_data="cmd_start")],
        [InlineKeyboardButton("📊 View Expenses", callback_data="cmd_view")],
        [InlineKeyboardButton("📅 View by Date", callback_data="cmd_view_date")],
        [InlineKeyboardButton("✏️ Edit Expense", callback_data="cmd_edit")],
        [InlineKeyboardButton("🗑️ Delete Expense", callback_data="cmd_delete")],
        [InlineKeyboardButton("📈 Summary Stats", callback_data="cmd_summary")]
    ]
    return InlineKeyboardMarkup(keyboard)

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
    """Start conversation with main menu."""
    user = update.effective_user
    
    # Check if this is a callback or direct command
    if update.callback_query:
        await update.callback_query.answer()
        message = update.callback_query.message
        await message.edit_text(
            f"Hi {user.first_name}! 📝 Let's record a new expense.\n\n"
            f"First, please choose a date:",
            reply_markup=get_calendar_keyboard()
        )
    else:
        await update.message.reply_text(
            f"Hi {user.first_name}! Welcome to your expense tracking assistant! 💰\n\n"
            f"What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END  # End here and wait for menu selection
    
    # Initialize user data dictionary
    context.user_data['expense'] = {}
    
    return CALENDAR_MONTH_SELECTION

def get_calendar_keyboard():
    """Generate a calendar keyboard for month selection."""
    now = datetime.now()
    keyboard = []
    # Show current month and 5 previous months
    for i in range(0, 6):
        month_date = now - relativedelta(months=i)
        month_name = month_date.strftime("%B %Y")
        keyboard.append([InlineKeyboardButton(
            month_name, 
            callback_data=f"month_{month_date.month}_{month_date.year}"
        )])
    
    # Add today button and manual entry option
    keyboard.append([
        InlineKeyboardButton("📅 Today", callback_data="date_today"),
        InlineKeyboardButton("🔤 Manual Entry", callback_data="date_manual")
    ])
    
    # Add back button
    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_calendar_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle month selection in calendar."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "date_today":
        # Use today's date
        date_str = datetime.now().strftime("%Y.%m.%d")
        context.user_data['expense']['date'] = date_str
        await query.edit_message_text(
            f"Date set to today: {date_str}\n\nNow, please enter the place/vendor:"
        )
        return PLACE
    
    if query.data == "date_manual":
        # Allow manual date entry
        await query.edit_message_text(
            "Please enter the date in format YYYY.MM.DD (e.g., 2025.01.01):"
        )
        return DATE
    
    if query.data == "back_to_menu":
        # Go back to main menu
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    # Extract month and year from callback data
    parts = query.data.split('_')
    if len(parts) == 3 and parts[0] == "month":
        try:
            month = int(parts[1])
            year = int(parts[2])
            
            # Create keyboard with days for selected month
            keyboard = get_days_keyboard(month, year)
            
            await query.edit_message_text(
                f"Select a day in {calendar.month_name[month]} {year}:",
                reply_markup=keyboard
            )
            
            # Store the month and year in context for later use
            context.user_data['calendar_month'] = month
            context.user_data['calendar_year'] = year
            
            return CALENDAR_DAY_SELECTION
        except Exception as e:
            logger.error(f"Error in calendar month handler: {e}")
            await query.edit_message_text(
                "There was an error with the calendar. Please try again or enter the date manually."
            )
            return DATE

def get_days_keyboard(month, year):
    """Generate keyboard with days for the given month and year."""
    # Get number of days in month
    _, num_days = calendar.monthrange(year, month)
    
    # Create a 7-column grid (for each day of week)
    keyboard = []
    week = []
    
    # Add empty buttons for days before the 1st of the month
    first_day_weekday = datetime(year, month, 1).weekday()
    for _ in range(first_day_weekday):
        week.append(InlineKeyboardButton(" ", callback_data="ignore"))
    
    # Add buttons for each day of the month
    for day in range(1, num_days + 1):
        date_str = f"{year}.{month:02d}.{day:02d}"
        week.append(InlineKeyboardButton(str(day), callback_data=f"day_{date_str}"))
        
        # Start a new row after Saturday (weekday 6)
        if (first_day_weekday + day) % 7 == 0 or day == num_days:
            keyboard.append(week)
            week = []
    
    # Add navigation buttons
    keyboard.append([
        InlineKeyboardButton("⬅️ Back to Months", callback_data="back_to_months"),
        InlineKeyboardButton("🏠 Main Menu", callback_data="back_to_menu")
    ])
    
    return InlineKeyboardMarkup(keyboard)

async def handle_calendar_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle day selection in calendar."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_months":
        # Go back to month selection
        await query.edit_message_text(
            "Please select a month:",
            reply_markup=get_calendar_keyboard()
        )
        return CALENDAR_MONTH_SELECTION
    
    if query.data == "back_to_menu":
        # Go back to main menu
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    if query.data.startswith("day_"):
        # Extract date from callback data
        date_str = query.data[4:]  # Remove the "day_" prefix
        context.user_data['expense']['date'] = date_str
        
        await query.edit_message_text(
            f"Date set to {date_str}\n\nNow, please enter the place/vendor:"
        )
        return PLACE

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
    
    await update.message.reply_text(f"📅 Date set to {date_str}. Now, please enter the place/vendor:")
    
    return PLACE

async def place_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the place and ask for amount."""
    context.user_data['expense']['place'] = update.message.text
    
    await update.message.reply_text(
        f"🏪 Place set to {update.message.text}. Now, please enter the amount (numbers only):"
    )
    
    return AMOUNT

async def amount_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the amount and ask for category."""
    try:
        amount = float(update.message.text)
        context.user_data['expense']['amount'] = amount
        
        # Create keyboard with category options
        keyboard = [
            [InlineKeyboardButton(category, callback_data=display_name)] 
            for category, display_name in CATEGORIES.items()
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(
            f"💰 Amount set to {amount}. Please select a category:",
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
        f"📂 Category set to {category}. Please enter the receipt number:"
    )
    
    return RECEIPT_NUMBER

async def receipt_number_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Store the receipt number and ask for tags."""
    context.user_data['expense']['receipt_number'] = update.message.text
    
    # Create keyboard with tag options
    keyboard = [
        [InlineKeyboardButton(tag, callback_data=display_name)] 
        for tag, display_name in TAGS.items()
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        f"🧾 Receipt number set to {update.message.text}. Please select a tag:",
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
        f"🏷️ Tag set to {tag}. Now, please upload a photo of your receipt (or type 'skip' to skip):"
    )
    
    return RECEIPT_UPLOAD

async def receipt_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receipt upload and save all data."""
    # Log to help with debugging
    logger.info(f"Receipt upload handler triggered. Message type: {type(update.message)}")
    if update.message.photo:
        logger.info(f"Photo received with {len(update.message.photo)} size options")
    elif update.message.text:
        logger.info(f"Text received: {update.message.text}")
    
    # Initialize Google services
    try:
        logger.info("Initializing Google services...")
        sheet, drive_service = init_google_services()
        logger.info("Google services initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing Google services: {e}")
        await update.message.reply_text(
            "❌ There was an error connecting to Google services. Please try again later."
        )
        return ConversationHandler.END
        
    expense_data = context.user_data['expense']
    
    # Handle receipt upload
    drive_file_link = ""
    drive_file_id = ""
    
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
            logger.info(f"Processing photo with file_id: {photo.file_id}")
            
            try:
                file = await context.bot.get_file(photo.file_id)
                logger.info(f"Got file info: {file.file_path}")
                
                # Create a temporary file
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as temp_file:
                    # Download the photo
                    photo_url = file.file_path
                    logger.info(f"Downloading from URL: {photo_url}")
                    response = requests.get(photo_url)
                    temp_file.write(response.content)
                    temp_file_path = temp_file.name
                    logger.info(f"Saved to temporary file: {temp_file_path}")
            except Exception as download_error:
                logger.error(f"Error downloading photo: {download_error}")
                await update.message.reply_text("❌ Error downloading your photo. Please try again.")
                return RECEIPT_UPLOAD
            
            # Upload to Google Drive
            try:
                file_metadata = {
                    'name': f"Receipt_{expense_data['date']}_{expense_data['place']}",
                    'parents': [FOLDER_ID]
                }
                
                logger.info(f"Uploading to Google Drive folder: {FOLDER_ID}")
                logger.info(f"File metadata: {file_metadata}")
                
                media = MediaFileUpload(
                    temp_file_path,
                    mimetype='image/jpeg',
                    resumable=True
                )
                
                # Send a message to indicate upload is in progress
                progress_message = await update.message.reply_text("📤 Uploading your receipt to Google Drive...")
                
                file = drive_service.files().create(
                    body=file_metadata,
                    media_body=media,
                    fields='id,webViewLink'
                ).execute()
                
                drive_file_link = file.get('webViewLink', '')
                drive_file_id = file.get('id', '')
                expense_data['upload'] = f"[View Receipt]({drive_file_link})"
                
                logger.info(f"File uploaded successfully. Link: {drive_file_link}")
                
                # Update progress message
                await progress_message.edit_text("✅ Receipt uploaded successfully!")
                
                # Clean up the temporary file
                os.unlink(temp_file_path)
                
            except Exception as upload_error:
                logger.error(f"Error uploading to Google Drive: {upload_error}")
                await update.message.reply_text("❌ Error uploading to Google Drive. Will save expense data without receipt.")
                expense_data['upload'] = "Upload failed - error"
                
        except Exception as e:
            logger.error(f"Error in receipt handling: {e}")
            await update.message.reply_text(
                "❌ There was an error processing your receipt. The expense data will still be saved."
            )
            expense_data['upload'] = "Processing error"
    
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
            expense_data['upload']
        ]

        # Append row and get its index
        result = sheet.append_row(row)
        row_index = len(sheet.get_all_values())  # Get the index of the newly added row
        expense_data['row_index'] = row_index
        
        receipt_msg = (
            f"📎 Receipt uploaded and linked." 
            if drive_file_link else 
            "📝 No receipt uploaded."
        )
        
        # Confirm to the user
        await update.message.reply_text(
            f"✅ Expense saved successfully!\n\n"
            f"📅 Date: {expense_data['date']}\n"
            f"🏪 Place: {expense_data['place']}\n"
            f"💰 Amount: {expense_data['amount']}\n"
            f"📂 Category: {expense_data['category']}\n"
            f"🧾 Receipt #: {expense_data['receipt_number']}\n"
            f"🏷️ Tag: {expense_data['tag']}\n"
            f"{receipt_msg}\n\n"
            f"What would you like to do next?",
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
    except Exception as e:
        logger.error(f"Error saving to Google Sheet: {e}")
        await update.message.reply_text(
            "❌ There was an error saving your expense data. Please try again later."
        )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END

async def handle_menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle menu commands."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cmd_start":
        # Start new expense
        await query.edit_message_text(
            "Let's record a new expense. Please choose a date:",
            reply_markup=get_calendar_keyboard()
        )
        # Initialize user data dictionary
        context.user_data['expense'] = {}
        return CALENDAR_MONTH_SELECTION
    
    elif query.data == "cmd_view":
        # View recent expenses
        await view_expenses(query, context)
        return ConversationHandler.END
    
    elif query.data == "cmd_view_date":
        # View expenses by date
        keyboard = [
            [
                InlineKeyboardButton("📅 Today", callback_data="view_today"),
                InlineKeyboardButton("📅 Yesterday", callback_data="view_yesterday")
            ],
            [
                InlineKeyboardButton("📅 This Week", callback_data="view_this_week"),
                InlineKeyboardButton("📅 This Month", callback_data="view_this_month")
            ],
            [
                InlineKeyboardButton("📅 Custom Range", callback_data="view_custom"),
                InlineKeyboardButton("⬅️ Back", callback_data="back_to_menu")
            ]
        ]
        await query.edit_message_text(
            "Select a time period to view expenses:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END  # Will handle further interaction in callback
    
    elif query.data == "cmd_edit":
        # Edit an expense
        await show_expenses_for_edit(query, context)
        return ConversationHandler.END
    
    elif query.data == "cmd_delete":
        # Delete an expense
        await show_expenses_for_delete(query, context)
        return ConversationHandler.END
    
    elif query.data == "cmd_summary":
        # Show summary stats
        await show_summary_stats(query, context)
        return ConversationHandler.END
    
    return ConversationHandler.END

async def view_expenses(query, context, date_filter=None):
    """Show recent expenses."""
    try:
        sheet, _ = init_google_services()
        data = sheet.get_all_values()
        
        # Skip header row
        if len(data) > 0:
            headers = data[0]
            expenses = data[1:]
        else:
            await query.edit_message_text(
                "No expenses found. Add some expenses first!",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Apply date filter if provided
        filtered_expenses = []
        if date_filter:
            for expense in expenses:
                try:
                    expense_date = datetime.strptime(expense[0], "%Y.%m.%d")
                    if date_filter(expense_date):
                        filtered_expenses.append(expense)
                except (ValueError, IndexError):
                    # Skip invalid dates
                    continue
        else:
            # Just get the 5 most recent expenses
            filtered_expenses = expenses[-5:] if len(expenses) > 5 else expenses
        
        if not filtered_expenses:
            await query.edit_message_text(
                "No expenses found for the selected period.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Prepare message
        message = "📋 *Recent Expenses:*\n\n"
        for i, expense in enumerate(reversed(filtered_expenses), 1):
            # Format amount with currency symbol and 2 decimal places
            try:
                amount = float(expense[2])
                amount_str = f"{amount:.2f}"
            except (ValueError, IndexError):
                amount_str = expense[2] if len(expense) > 2 else "N/A"
            
            # Format date
            date_str = expense[0] if len(expense) > 0 else "N/A"
            
            # Format receipt link
            receipt_link = expense[6] if len(expense) > 6 else "No receipt"
            
            message += (
                f"*{i}. {date_str} - {expense[1]}*\n"
                f"💰 Amount: {amount_str}\n"
                f"📂 Category: {expense[3]}\n"
                f"🏷️ Tag: {expense[5]}\n"
                f"📎 Receipt: {receipt_link}\n\n"
            )
        
        # Add button to return to menu
        keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    except Exception as e:
        logger.error(f"Error viewing expenses: {e}")
        await query.edit_message_text(
            f"❌ Error retrieving expenses: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_view_date_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle date range selection for viewing expenses."""
    query = update.callback_query
    await query.answer()
    
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    
    if query.data == "view_today":
        # Filter for today
        date_filter = lambda d: d.date() == today.date()
        await view_expenses(query, context, date_filter)
    
    elif query.data == "view_yesterday":
        # Filter for yesterday
        yesterday = today - timedelta(days=1)
        date_filter = lambda d: d.date() == yesterday.date()
        await view_expenses(query, context, date_filter)
    
    elif query.data == "view_this_week":
        # Filter for this week (last 7 days)
        week_ago = today - timedelta(days=7)
        date_filter = lambda d: week_ago.date() <= d.date() <= today.date()
        await view_expenses(query, context, date_filter)
    
    elif query.data == "view_this_month":
        # Filter for this month
        month_start = today.replace(day=1)
        date_filter = lambda d: month_start.date() <= d.date() <= today.date()
        await view_expenses(query, context, date_filter)
    
    elif query.data == "view_custom":
        # Ask for custom date range
        await query.edit_message_text(
            "Please enter the start date for your search (format: YYYY.MM.DD):"
        )
        context.user_data['search_state'] = 'start_date'
        return SEARCH_DATE_RANGE
    
    elif query.data == "back_to_menu":
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
    
    return ConversationHandler.END

async def search_date_range(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle custom date range input."""
    text = update.message.text
    
    if context.user_data.get('search_state') == 'start_date':
        # Process start date
        try:
            start_date = datetime.strptime(text, "%Y.%m.%d")
            context.user_data['start_date'] = start_date
            context.user_data['search_state'] = 'end_date'
            
            await update.message.reply_text(
                f"Start date set to {text}. Now please enter the end date (format: YYYY.MM.DD):"
            )
            return SEARCH_DATE_RANGE
            
        except ValueError:
            await update.message.reply_text(
                "Invalid date format. Please use YYYY.MM.DD (e.g., 2025.01.01):"
            )
            return SEARCH_DATE_RANGE
    
    elif context.user_data.get('search_state') == 'end_date':
        # Process end date and show results
        try:
            end_date = datetime.strptime(text, "%Y.%m.%d")
            start_date = context.user_data.get('start_date')
            
            if end_date < start_date:
                await update.message.reply_text(
                    "End date cannot be before start date. Please enter a valid end date:"
                )
                return SEARCH_DATE_RANGE
            
            # Define filter function for date range
            date_filter = lambda d: start_date.date() <= d.date() <= end_date.date()
            
            # Use reply message for results
            message = await update.message.reply_text("🔍 Searching for expenses...")
            
            # Create a fake query object for view_expenses function
            class FakeQuery:
                def __init__(self, message):
                    self.message = message
                    
                async def edit_message_text(self, *args, **kwargs):
                    return await message.edit_text(*args, **kwargs)
            
            fake_query = FakeQuery(message)
            await view_expenses(fake_query, context, date_filter)
            
            # Clear search state
            context.user_data.pop('search_state', None)
            context.user_data.pop('start_date', None)
            
            return ConversationHandler.END
            
        except ValueError:
            await update.message.reply_text(
                "Invalid date format. Please use YYYY.MM.DD (e.g., 2025.01.01):"
            )
            return SEARCH_DATE_RANGE
    
    return ConversationHandler.END

async def show_expenses_for_edit(query, context):
    """Show a list of recent expenses for editing."""
    try:
        sheet, _ = init_google_services()
        data = sheet.get_all_values()
        
        # Skip header row
        if len(data) > 0:
            headers = data[0]
            expenses = data[1:]
        else:
            await query.edit_message_text(
                "No expenses found. Add some expenses first!",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Get the 10 most recent expenses
        recent_expenses = expenses[-10:] if len(expenses) > 10 else expenses
        
        if not recent_expenses:
            await query.edit_message_text(
                "No expenses found to edit.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Create buttons for each expense
        keyboard = []
        for i, expense in enumerate(reversed(recent_expenses), 1):
            # Get row index in the sheet (adding 1 for header row)
            row_index = len(expenses) - i + 1
            
            # Create a button with date, place and amount
            try:
                date = expense[0]
                place = expense[1]
                amount = float(expense[2])
                button_text = f"{date} - {place} (💰 {amount:.2f})"
            except (ValueError, IndexError):
                button_text = f"Expense #{i}"
                
            keyboard.append([InlineKeyboardButton(
                button_text, 
                callback_data=f"edit_{row_index + 1}"  # +1 for header row
            )])
        
        # Add back button
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
        
        await query.edit_message_text(
            "Select an expense to edit:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing expenses for edit: {e}")
        await query.edit_message_text(
            f"❌ Error retrieving expenses: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_edit_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle selection of an expense to edit."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    try:
        # Extract the row index from callback data
        row_index = int(query.data.split('_')[1])
        context.user_data['edit_row_index'] = row_index
        
        # Get the expense data
        sheet, _ = init_google_services()
        expense = sheet.row_values(row_index)
        
        if not expense or len(expense) < 7:
            await query.edit_message_text(
                "❌ Error: Could not retrieve the selected expense.",
                reply_markup=get_main_menu_keyboard()
            )
            return ConversationHandler.END
        
        # Store the current values for reference
        context.user_data['edit_expense'] = expense
        
        # Show the expense details and field selection buttons
        message = (
            f"*Editing Expense:*\n\n"
            f"1️⃣ Date: {expense[0]}\n"
            f"2️⃣ Place: {expense[1]}\n"
            f"3️⃣ Amount: {expense[2]}\n"
            f"4️⃣ Category: {expense[3]}\n"
            f"5️⃣ Receipt #: {expense[4]}\n"
            f"6️⃣ Tag: {expense[5]}\n"
            f"7️⃣ Receipt: {expense[6]}\n\n"
            f"Select a field to edit:"
        )
        
        # Create buttons for each field
        keyboard = [
            [
                InlineKeyboardButton("1️⃣ Date", callback_data="edit_field_0"),
                InlineKeyboardButton("2️⃣ Place", callback_data="edit_field_1")
            ],
            [
                InlineKeyboardButton("3️⃣ Amount", callback_data="edit_field_2"),
                InlineKeyboardButton("4️⃣ Category", callback_data="edit_field_3")
            ],
            [
                InlineKeyboardButton("5️⃣ Receipt #", callback_data="edit_field_4"),
                InlineKeyboardButton("6️⃣ Tag", callback_data="edit_field_5")
            ],
            [
                InlineKeyboardButton("7️⃣ Upload New Receipt", callback_data="edit_field_6"),
                InlineKeyboardButton("⬅️ Back", callback_data="back_to_edit_list")
            ]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        
        return EDIT_FIELD
        
    except Exception as e:
        logger.error(f"Error in edit selection: {e}")
        await query.edit_message_text(
            f"❌ Error processing selection: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def handle_edit_field_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle selection of field to edit."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_edit_list":
        # Go back to the list of expenses
        message = await query.edit_message_text("Loading expenses...")
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
                
            async def edit_message_text(self, *args, **kwargs):
                return await message.edit_text(*args, **kwargs)
        
        fake_query = FakeQuery(message)
        await show_expenses_for_edit(fake_query, context)
        return ConversationHandler.END
    
    try:
        # Extract the field index from callback data
        field_index = int(query.data.split('_')[2])
        context.user_data['edit_field_index'] = field_index
        
        # Handle special cases
        if field_index == 3:  # Category
            # Show category selection
            keyboard = [
                [InlineKeyboardButton(category, callback_data=f"setcat_{display_name}")] 
                for category, display_name in CATEGORIES.items()
            ]
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_fields")])
            
            await query.edit_message_text(
                "Select a new category:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return EDIT_VALUE
            
        elif field_index == 5:  # Tag
            # Show tag selection
            keyboard = [
                [InlineKeyboardButton(tag, callback_data=f"settag_{display_name}")] 
                for tag, display_name in TAGS.items()
            ]
            keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data="back_to_fields")])
            
            await query.edit_message_text(
                "Select a new tag:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return EDIT_VALUE
            
        elif field_index == 0:  # Date
            # Show calendar for date selection
            await query.edit_message_text(
                "Please select a new date:",
                reply_markup=get_calendar_keyboard()
            )
            context.user_data['edit_mode'] = True
            return CALENDAR_MONTH_SELECTION
            
        elif field_index == 6:  # Receipt
            await query.edit_message_text(
                "Please upload a new receipt photo or type 'skip' to cancel:"
            )
            context.user_data['edit_mode'] = True
            return RECEIPT_UPLOAD
            
        else:
            # For other fields, ask for text input
            field_names = ["date", "place", "amount", "category", "receipt number", "tag", "receipt"]
            
            await query.edit_message_text(
                f"Please enter a new value for {field_names[field_index]}:"
            )
            return EDIT_VALUE
            
    except Exception as e:
        logger.error(f"Error in edit field selection: {e}")
        await query.edit_message_text(
            f"❌ Error selecting field: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def handle_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle the new value for editing."""
    # Handle callback query (for category, tag, calendar)
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        
        if query.data == "back_to_fields":
            # Go back to field selection
            row_index = context.user_data.get('edit_row_index')
            expense = context.user_data.get('edit_expense')
            
            if not row_index or not expense:
                await query.edit_message_text(
                    "❌ Error: Lost context. Please start over.",
                    reply_markup=get_main_menu_keyboard()
                )
                return ConversationHandler.END
            
            # Show the expense details and field selection buttons again
            message = (
                f"*Editing Expense:*\n\n"
                f"1️⃣ Date: {expense[0]}\n"
                f"2️⃣ Place: {expense[1]}\n"
                f"3️⃣ Amount: {expense[2]}\n"
                f"4️⃣ Category: {expense[3]}\n"
                f"5️⃣ Receipt #: {expense[4]}\n"
                f"6️⃣ Tag: {expense[5]}\n"
                f"7️⃣ Receipt: {expense[6]}\n\n"
                f"Select a field to edit:"
            )
            
            keyboard = [
                [
                    InlineKeyboardButton("1️⃣ Date", callback_data="edit_field_0"),
                    InlineKeyboardButton("2️⃣ Place", callback_data="edit_field_1")
                ],
                [
                    InlineKeyboardButton("3️⃣ Amount", callback_data="edit_field_2"),
                    InlineKeyboardButton("4️⃣ Category", callback_data="edit_field_3")
                ],
                [
                    InlineKeyboardButton("5️⃣ Receipt #", callback_data="edit_field_4"),
                    InlineKeyboardButton("6️⃣ Tag", callback_data="edit_field_5")
                ],
                [
                    InlineKeyboardButton("7️⃣ Upload New Receipt", callback_data="edit_field_6"),
                    InlineKeyboardButton("⬅️ Back", callback_data="back_to_edit_list")
                ]
            ]
            
            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
            
            return EDIT_FIELD
            
        elif query.data.startswith("setcat_"):
            # Update category
            new_value = query.data.split("_")[1]
            field_index = context.user_data.get('edit_field_index')
            row_index = context.user_data.get('edit_row_index')
            
            # Update in Google Sheet
            return await update_expense_field(query, context, row_index, field_index, new_value)
            
        elif query.data.startswith("settag_"):
            # Update tag
            new_value = query.data.split("_")[1]
            field_index = context.user_data.get('edit_field_index')
            row_index = context.user_data.get('edit_row_index')
            
            # Update in Google Sheet
            return await update_expense_field(query, context, row_index, field_index, new_value)
            
        elif query.data == "back_to_menu":
            await query.edit_message_text(
                "What would you like to do?",
                reply_markup=get_main_menu_keyboard()
            )
            return ConversationHandler.END
            
        # Handle calendar selection
        elif query.data.startswith("day_") and context.user_data.get('edit_mode'):
            date_str = query.data[4:]  # Remove "day_" prefix
            field_index = context.user_data.get('edit_field_index')
            row_index = context.user_data.get('edit_row_index')
            
            # Update in Google Sheet
            return await update_expense_field(query, context, row_index, field_index, date_str)
    
    # Handle text input for other fields
    else:
        text = update.message.text
        field_index = context.user_data.get('edit_field_index')
        row_index = context.user_data.get('edit_row_index')
        
        # Validate input based on field type
        if field_index == 2:  # Amount
            try:
                # Convert to float to validate
                amount = float(text)
                # Format as string with 2 decimal places
                text = f"{amount:.2f}"
            except ValueError:
                await update.message.reply_text(
                    "Invalid amount. Please enter a number:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("⬅️ Cancel", callback_data="back_to_fields")
                    ]])
                )
                return EDIT_VALUE
        
        # Create a fake query for update_expense_field
        message = await update.message.reply_text("Updating expense...")
        
        class FakeQuery:
            def __init__(self, message):
                self.message = message
                
            async def edit_message_text(self, *args, **kwargs):
                return await message.edit_text(*args, **kwargs)
                
        fake_query = FakeQuery(message)
        return await update_expense_field(fake_query, context, row_index, field_index, text)

async def update_expense_field(query, context, row_index, field_index, new_value):
    """Update a field in the expense sheet."""
    try:
        sheet, _ = init_google_services()
        
        # Get current row
        current_values = sheet.row_values(row_index)
        
        # Update the specific cell
        sheet.update_cell(row_index, field_index + 1, new_value)  # +1 because sheets are 1-indexed
        
        # Get updated row to confirm changes
        updated_values = sheet.row_values(row_index)
        
        # Show success message with updated expense
        message = (
            f"✅ Expense updated successfully!\n\n"
            f"📅 Date: {updated_values[0]}\n"
            f"🏪 Place: {updated_values[1]}\n"
            f"💰 Amount: {updated_values[2]}\n"
            f"📂 Category: {updated_values[3]}\n"
            f"🧾 Receipt #: {updated_values[4]}\n"
            f"🏷️ Tag: {updated_values[5]}\n"
            f"📎 Receipt: {updated_values[6]}\n\n"
            f"What would you like to do next?"
        )
        
        await query.edit_message_text(
            message,
            reply_markup=get_main_menu_keyboard(),
            parse_mode='Markdown'
        )
        
        # Clear edit context
        context.user_data.pop('edit_row_index', None)
        context.user_data.pop('edit_field_index', None)
        context.user_data.pop('edit_expense', None)
        context.user_data.pop('edit_mode', None)
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Error updating expense: {e}")
        await query.edit_message_text(
            f"❌ Error updating expense: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def show_expenses_for_delete(query, context):
    """Show a list of recent expenses for deletion."""
    try:
        sheet, _ = init_google_services()
        data = sheet.get_all_values()
        
        # Skip header row
        if len(data) > 0:
            headers = data[0]
            expenses = data[1:]
        else:
            await query.edit_message_text(
                "No expenses found. Add some expenses first!",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Get the 10 most recent expenses
        recent_expenses = expenses[-10:] if len(expenses) > 10 else expenses
        
        if not recent_expenses:
            await query.edit_message_text(
                "No expenses found to delete.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Create buttons for each expense
        keyboard = []
        for i, expense in enumerate(reversed(recent_expenses), 1):
            # Get row index in the sheet (adding 1 for header row)
            row_index = len(expenses) - i + 1
            
            # Create a button with date, place and amount
            try:
                date = expense[0]
                place = expense[1]
                amount = float(expense[2])
                button_text = f"{date} - {place} (💰 {amount:.2f})"
            except (ValueError, IndexError):
                button_text = f"Expense #{i}"
                
            keyboard.append([InlineKeyboardButton(
                button_text, 
                callback_data=f"delete_{row_index + 1}"  # +1 for header row
            )])
        
        # Add back button
        keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")])
        
        await query.edit_message_text(
            "Select an expense to delete:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except Exception as e:
        logger.error(f"Error showing expenses for delete: {e}")
        await query.edit_message_text(
            f"❌ Error retrieving expenses: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )

async def handle_delete_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle selection of an expense to delete."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_menu":
        await query.edit_message_text(
            "What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    try:
        # Extract the row index from callback data
        row_index = int(query.data.split('_')[1])
        context.user_data['delete_row_index'] = row_index
        
        # Get the expense data
        sheet, _ = init_google_services()
        expense = sheet.row_values(row_index)
        
        if not expense or len(expense) < 3:
            await query.edit_message_text(
                "❌ Error: Could not retrieve the selected expense.",
                reply_markup=get_main_menu_keyboard()
            )
            return ConversationHandler.END
        
        # Store the expense for reference
        context.user_data['delete_expense'] = expense
        
        # Show confirmation dialog
        try:
            date = expense[0]
            place = expense[1]
            amount = float(expense[2])
            message = f"Are you sure you want to delete this expense?\n\n📅 {date} - 🏪 {place} - 💰 {amount:.2f}"
        except (ValueError, IndexError):
            message = "Are you sure you want to delete this expense?"
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Yes, delete it", callback_data="confirm_delete"),
                InlineKeyboardButton("❌ No, cancel", callback_data="cancel_delete")
            ]
        ]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
        return DELETE_CONFIRM
        
    except Exception as e:
        logger.error(f"Error in delete selection: {e}")
        await query.edit_message_text(
            f"❌ Error processing selection: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END

async def handle_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle delete confirmation."""
    query = update.callback_query
    await query.answer()
    
    if query.data == "cancel_delete":
        # Cancel deletion and go back to menu
        await query.edit_message_text(
            "Deletion cancelled. What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
        return ConversationHandler.END
    
    elif query.data == "confirm_delete":
        # Proceed with deletion
        row_index = context.user_data.get('delete_row_index')
        
        try:
            sheet, _ = init_google_services()
            
            # Get the expense data before deleting (for reference)
            expense = context.user_data.get('delete_expense')
            
            # Delete the row
            sheet.delete_row(row_index)
            
            # Show success message
            if expense and len(expense) >= 3:
                try:
                    date = expense[0]
                    place = expense[1]
                    amount = float(expense[2])
                    message = f"✅ Expense deleted successfully!\n\n📅 {date} - 🏪 {place} - 💰 {amount:.2f}"
                except (ValueError, IndexError):
                    message = "✅ Expense deleted successfully!"
            else:
                message = "✅ Expense deleted successfully!"
            
            await query.edit_message_text(
                message + "\n\nWhat would you like to do next?",
                reply_markup=get_main_menu_keyboard()
            )
            
        except Exception as e:
            logger.error(f"Error deleting expense: {e}")
            await query.edit_message_text(
                f"❌ Error deleting expense: {str(e)}",
                reply_markup=get_main_menu_keyboard()
            )
        
        # Clear delete context
        context.user_data.pop('delete_row_index', None)
        context.user_data.pop('delete_expense', None)
        
        return ConversationHandler.END
    
    return ConversationHandler.END

async def show_summary_stats(query, context):
    """Show summary statistics of expenses."""
    try:
        sheet, _ = init_google_services()
        data = sheet.get_all_values()
        
        # Skip header row
        if len(data) > 0:
            headers = data[0]
            expenses = data[1:]
        else:
            await query.edit_message_text(
                "No expenses found. Add some expenses first!",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        if not expenses:
            await query.edit_message_text(
                "No expenses found to summarize.",
                reply_markup=get_main_menu_keyboard()
            )
            return
        
        # Calculate statistics
        total_expenses = len(expenses)
        
        # Calculate total amount
        total_amount = 0
        for expense in expenses:
            try:
                amount = float(expense[2])
                total_amount += amount
            except (ValueError, IndexError):
                pass
        
        # Group by category
        categories = {}
        for expense in expenses:
            if len(expense) > 3:
                category = expense[3]
                try:
                    amount = float(expense[2])
                    categories[category] = categories.get(category, 0) + amount
                except (ValueError, IndexError):
                    pass
        
        # Group by tag
        tags = {}
        for expense in expenses:
            if len(expense) > 5:
                tag = expense[5]
                try:
                    amount = float(expense[2])
                    tags[tag] = tags.get(tag, 0) + amount
                except (ValueError, IndexError):
                    pass
        
        # Group by month
        months = {}
        for expense in expenses:
            try:
                date = datetime.strptime(expense[0], "%Y.%m.%d")
                month_key = date.strftime("%Y-%m")
                amount = float(expense[2])
                months[month_key] = months.get(month_key, 0) + amount
            except (ValueError, IndexError):
                pass
        
        # Prepare message
        message = "📊 *Expense Summary*\n\n"
        
        message += f"*Total Expenses:* {total_expenses}\n"
        message += f"*Total Amount:* {total_amount:.2f}\n\n"
        
        if categories:
            message += "*By Category:*\n"
            for category, amount in sorted(categories.items(), key=lambda x: x[1], reverse=True):
                message += f"- {category}: {amount:.2f}\n"
            message += "\n"
        
        if tags:
            message += "*By Tag:*\n"
            for tag, amount in sorted(tags.items(), key=lambda x: x[1], reverse=True):
                message += f"- {tag}: {amount:.2f}\n"
            message += "\n"
        
        if months:
            message += "*By Month:*\n"
            for month, amount in sorted(months.items(), reverse=True):
                month_date = datetime.strptime(month, "%Y-%m")
                month_name = month_date.strftime("%B %Y")
                message += f"- {month_name}: {amount:.2f}\n"
        
        # Add button to return to menu
        keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data="back_to_menu")]]
        
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    
    except Exception as e:
        logger.error(f"Error showing summary: {e}")
        await query.edit_message_text(
            f"❌ Error generating summary: {str(e)}",
            reply_markup=get_main_menu_keyboard()
        )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel and end the conversation."""
    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Operation cancelled. What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            "Operation cancelled. What would you like to do?",
            reply_markup=get_main_menu_keyboard()
        )
    
    # Clear user data
    context.user_data.clear()
    
    return ConversationHandler.END

async def command_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the main menu on /menu command."""
    await update.message.reply_text(
        "📋 *Expense Tracker Menu*\n\n"
        "What would you like to do?",
        reply_markup=get_main_menu_keyboard(),
        parse_mode='Markdown'
    )

def main() -> None:
    """Run the bot."""
    # Get the token from environment variable
    token = os.environ.get("TELEGRAM_TOKEN", "7939992556:AAGUaWdemNJ31KiHBBvFhbuPFmZO1kUtWwo")
    
    # Create the Application with appropriate settings to avoid conflicts
    application = Application.builder().token(token).concurrent_updates(True).build()

    # Add main menu command handler
    application.add_handler(CommandHandler("menu", command_menu))

    # Add conversation handler for the expense tracking workflow
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(handle_menu_command, pattern=r"^cmd_"),
            CallbackQueryHandler(handle_view_date_callback, pattern=r"^view_"),
            CallbackQueryHandler(handle_edit_selection, pattern=r"^edit_\d+$"),
            CallbackQueryHandler(handle_delete_selection, pattern=r"^delete_\d+$"),
        ],
        states={
            CALENDAR_MONTH_SELECTION: [
                CallbackQueryHandler(handle_calendar_month, pattern=r"^(month_|date_|back_)")
            ],
            CALENDAR_DAY_SELECTION: [
                CallbackQueryHandler(handle_calendar_day, pattern=r"^(day_|back_)")
            ],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, date_input)],
            PLACE: [MessageHandler(filters.TEXT & ~filters.COMMAND, place_input)],
            AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, amount_input)],
            CATEGORY: [CallbackQueryHandler(category_callback)],
            RECEIPT_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receipt_number_input)],
            TAG: [CallbackQueryHandler(tag_callback)],
            RECEIPT_UPLOAD: [MessageHandler(filters.PHOTO | filters.TEXT & ~filters.COMMAND, receipt_upload)],
            SEARCH_DATE_RANGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, search_date_range)],
            EDIT_FIELD: [CallbackQueryHandler(handle_edit_field_selection, pattern=r"^(edit_field_|back_)")],
            EDIT_VALUE: [
                CallbackQueryHandler(handle_edit_value, pattern=r"^(setcat_|settag_|back_|day_)"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_edit_value)
            ],
            DELETE_CONFIRM: [CallbackQueryHandler(handle_delete_confirm, pattern=r"^(confirm_|cancel_)")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(start, pattern=r"^cmd_start$"),
            CallbackQueryHandler(cancel, pattern=r"^back_to_menu$"),
        ],
        per_chat=True,
        per_user=True,
        per_message=False,
    )

    application.add_handler(conv_handler)

    # Handle callback queries not handled by the conversation
    application.add_handler(
        CallbackQueryHandler(handle_menu_command, pattern=r"^cmd_")
    )
    application.add_handler(
        CallbackQueryHandler(handle_view_date_callback, pattern=r"^view_")
    )
    application.add_handler(
        CallbackQueryHandler(cancel, pattern=r"^back_to_menu$")
    )

    # Add error handler
    application.add_error_handler(error_handler)

    # Run the bot
    application.run_polling()

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors in the dispatcher."""
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    # Send message to the user
    if update and isinstance(update, Update) and update.effective_message:
        if update.callback_query:
            await update.callback_query.message.reply_text(
                "Sorry, an error occurred while processing your request. Please try again."
            )
        else:
            await update.effective_message.reply_text(
                "Sorry, an error occurred while processing your request. Please try again."
            )

if __name__ == "__main__":
    main()